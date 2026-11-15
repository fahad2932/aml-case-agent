"""Synthetic bank data with planted money laundering typologies.

Everything here is generated. No real customer, account or transaction is used.
Each alert has a ground-truth label that is written to a separate labels file,
which the agent's tools can never read.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import sqlite3
from pathlib import Path

WATCHLIST_COUNTRIES = ["MM", "PA", "NG", "TR", "AE"]  # illustrative, configurable
TYPOLOGIES = ["structuring", "pass_through", "funnel_account", "high_risk_corridor", "none"]

SCHEMA = """
CREATE TABLE customers (
  id TEXT PRIMARY KEY, name TEXT, type TEXT, occupation TEXT,
  declared_monthly_income REAL, expected_countries TEXT, onboarded TEXT, kyc_risk TEXT);
CREATE TABLE accounts (id TEXT PRIMARY KEY, customer_id TEXT, opened TEXT);
CREATE TABLE transactions (
  id TEXT PRIMARY KEY, ts TEXT, account_id TEXT, direction TEXT, amount REAL,
  channel TEXT, counterparty TEXT, counterparty_country TEXT, description TEXT);
CREATE TABLE alerts (
  id TEXT PRIMARY KEY, account_id TEXT, rule TEXT, created_at TEXT,
  window_start TEXT, window_end TEXT);
CREATE INDEX tx_acct ON transactions(account_id, ts);
"""

FIRST = ["Amir", "Lena", "Jonas", "Priya", "Marta", "Tomas", "Nadia", "Oskar", "Ines", "Kofi",
         "Hana", "Luca", "Sofia", "Emre", "Dara", "Milan", "Aiko", "Ruth", "Pavel", "Zara"]
LAST = ["Novak", "Haddad", "Lindqvist", "Okafor", "Ferreira", "Kowalski", "Tanaka", "Reyes",
        "Aydin", "Moreau", "Jensen", "Popescu", "Iyer", "Costa", "Weiss", "Dlamini"]


class Gen:
    def __init__(self, seed: int, alert_date: dt.date):
        self.r = random.Random(seed)
        self.alert_date = alert_date
        self.win_start = alert_date - dt.timedelta(days=30)
        self.hist_start = alert_date - dt.timedelta(days=270)
        self.customers, self.accounts, self.txns, self.alerts, self.labels = [], [], [], [], {}
        self._cp = 0

    # ---- helpers -------------------------------------------------------
    def cp(self, prefix="CP"):
        self._cp += 1
        return f"{prefix}{self._cp:05d}"

    def ts(self, day: dt.date, hour=None):
        h = self.r.randint(8, 19) if hour is None else hour
        return dt.datetime(day.year, day.month, day.day, h, self.r.randint(0, 59)).isoformat(sep=" ")

    def tx(self, acct, when, direction, amount, channel, counterparty, country="US", desc=""):
        self.txns.append([None, when, acct, direction, round(amount, 2), channel, counterparty, country, desc])

    def days(self, start, end):
        d = start
        while d <= end:
            yield d
            d += dt.timedelta(days=1)

    def customer(self, ctype, occupation, income, expected="US", risk="low", months_open=30):
        i = len(self.customers) + 1
        cid, aid = f"C{i:04d}", f"A{i:04d}"
        name = (f"{self.r.choice(FIRST)} {self.r.choice(LAST)}" if ctype == "individual"
                else f"{self.r.choice(LAST)} {occupation.title()} LLC")
        opened = (self.alert_date - dt.timedelta(days=30 * months_open)).isoformat()
        self.customers.append([cid, name, ctype, occupation, income, expected, opened, risk])
        self.accounts.append([aid, cid, opened])
        return aid

    def alert(self, acct, rule, typology):
        aid = f"AL{len(self.alerts) + 1:04d}"
        self.alerts.append([aid, acct, rule, self.alert_date.isoformat(),
                            self.win_start.isoformat(), self.alert_date.isoformat()])
        self.labels[aid] = {"typology": typology,
                            "disposition": "close" if typology == "none" else "escalate"}

    # ---- ordinary behaviour -------------------------------------------
    def salaried_history(self, acct, income, start=None):
        employer = self.cp("EMP")
        for d in self.days(start or self.hist_start, self.alert_date):
            if d.day == 1:
                self.tx(acct, self.ts(d, 9), "in", income, "ach", employer, desc="PAYROLL")
            if d.day == 3:
                self.tx(acct, self.ts(d, 10), "out", income * 0.3, "ach", "LANDLORD", desc="RENT")
            if self.r.random() < 0.55:
                self.tx(acct, self.ts(d), "out", self.r.uniform(8, income * 0.04), "card",
                        self.r.choice(["GROCER", "FUEL", "PHARMACY", "CAFE", "TRANSIT"]), desc="CARD PURCHASE")

    def business_history(self, acct, monthly, cash_share=0.0, suppliers=None, start=None):
        suppliers = suppliers or [(self.cp("SUP"), "US") for _ in range(3)]
        daily = monthly / 26
        for d in self.days(start or self.hist_start, self.alert_date):
            if d.weekday() == 6:
                continue
            if cash_share and self.r.random() < 0.8:
                self.tx(acct, self.ts(d, 17), "in", daily * cash_share * self.r.uniform(0.5, 1.6), "cash",
                        f"BRANCH-{self.r.randint(1, 2):02d}", desc="CASH DEPOSIT TAKINGS")
            self.tx(acct, self.ts(d), "in", daily * (1 - cash_share) * self.r.uniform(0.6, 1.4), "card",
                    "CARD-ACQUIRER", desc="CARD SETTLEMENT")
            if d.weekday() == 2:
                sup, ctry = self.r.choice(suppliers)
                self.tx(acct, self.ts(d), "out", monthly * 0.15 * self.r.uniform(0.7, 1.3),
                        "wire" if ctry != "US" else "ach", sup, ctry, f"INVOICE {self.r.randint(1000, 9999)}")

    # ---- planted typologies and their look-alike false positives ------
    def structuring(self):
        income = self.r.choice([3200, 4100, 5200])
        a = self.customer("individual", self.r.choice(["driver", "barber", "sales assistant"]), income)
        self.salaried_history(a, income)
        for _ in range(self.r.randint(9, 14)):
            d = self.win_start + dt.timedelta(days=self.r.randint(1, 24))
            self.tx(a, self.ts(d), "in", self.r.uniform(8200, 9900), "cash",
                    f"BRANCH-{self.r.randint(1, 9):02d}", desc="CASH DEPOSIT")
        for _ in range(2):
            d = self.win_start + dt.timedelta(days=self.r.randint(25, 29))
            self.tx(a, self.ts(d), "out", self.r.uniform(35000, 48000), "wire", self.cp(), "US", "TRANSFER")
        self.alert(a, "cash_threshold_pattern", "structuring")

    def cash_business_fp(self):
        monthly = self.r.choice([110000, 150000, 190000])
        a = self.customer("business", self.r.choice(["restaurant", "grocery", "car wash"]), monthly)
        self.business_history(a, monthly, cash_share=0.55)
        self.alert(a, "cash_threshold_pattern", "none")

    def pass_through(self):
        income = self.r.choice([2800, 3900])
        a = self.customer("individual", self.r.choice(["freelancer", "consultant"]), income, months_open=4)
        self.salaried_history(a, income, start=self.alert_date - dt.timedelta(days=110))
        for _ in range(self.r.randint(3, 5)):
            d = self.win_start + dt.timedelta(days=self.r.randint(1, 26))
            amt = self.r.uniform(40000, 150000)
            hour = self.r.randint(8, 12)
            self.tx(a, self.ts(d, hour), "in", amt, "wire", self.cp(), self.r.choice(["US", "GB", "DE"]), "PAYMENT")
            out_day = d + dt.timedelta(days=self.r.choice([0, 1]))
            self.tx(a, self.ts(out_day, hour + 5), "out", amt * self.r.uniform(0.93, 0.99), "wire",
                    self.cp(), self.r.choice(["US", "CY", "HK"]), "TRANSFER")
        self.alert(a, "rapid_movement", "pass_through")

    def property_sale_fp(self):
        income = self.r.choice([6100, 7400])
        a = self.customer("individual", self.r.choice(["engineer", "nurse", "teacher"]), income)
        self.salaried_history(a, income)
        d = self.win_start + dt.timedelta(days=self.r.randint(3, 20))
        amt = self.r.uniform(210000, 380000)
        self.tx(a, self.ts(d, 10), "in", amt, "wire", "HARTWELL ESCROW SERVICES", "US", "PROPERTY SALE PROCEEDS")
        self.tx(a, self.ts(d + dt.timedelta(days=1), 11), "out", amt * self.r.uniform(0.9, 0.97), "wire",
                "FIRST HOME MORTGAGE CO", "US", "MORTGAGE PAYOFF")
        self.alert(a, "rapid_movement", "none")

    def funnel(self):
        a = self.customer("individual", self.r.choice(["student", "unemployed"]), 800, months_open=6)
        self.salaried_history(a, 800, start=self.alert_date - dt.timedelta(days=170))
        total = 0.0
        first = self.r.randint(1, 12)
        for _ in range(self.r.randint(25, 45)):
            d = self.win_start + dt.timedelta(days=first + self.r.randint(0, 9))
            amt = self.r.uniform(200, 1500)
            total += amt
            self.tx(a, self.ts(d), "in", amt, "ach", self.cp("P2P"), "US", "TRANSFER FROM FRIEND")
        for share in (0.5, 0.42):
            d = self.win_start + dt.timedelta(days=first + self.r.randint(10, 13))
            self.tx(a, self.ts(d), "out", total * share, "wire", self.cp(), self.r.choice(["NG", "TR", "AE"]), "FAMILY SUPPORT")
        self.alert(a, "many_to_one", "funnel_account")

    def online_seller_fp(self):
        monthly = self.r.choice([18000, 26000])
        a = self.customer("business", "online seller", monthly)
        sup = [(self.cp("SUP"), "US")]
        for d in self.days(self.hist_start, self.alert_date):
            for _ in range(self.r.randint(0, 3)):
                self.tx(a, self.ts(d), "in", self.r.uniform(60, 700), "ach", self.cp("BUY"), "US",
                        f"ORDER {self.r.randint(10000, 99999)}")
            if d.weekday() == 4:
                self.tx(a, self.ts(d), "out", monthly * 0.14 * self.r.uniform(0.7, 1.3), "ach", sup[0][0], "US",
                        f"INVOICE {self.r.randint(1000, 9999)}")
        self.alert(a, "many_to_one", "none")

    def high_risk(self):
        income = self.r.choice([3500, 4600])
        a = self.customer("individual", self.r.choice(["clerk", "technician"]), income)
        self.salaried_history(a, income)
        ctry = self.r.choice(WATCHLIST_COUNTRIES)
        for _ in range(self.r.randint(4, 7)):
            d = self.win_start + dt.timedelta(days=self.r.randint(1, 27))
            amt = self.r.uniform(9000, 45000)
            self.tx(a, self.ts(d, 9), "in", amt * 1.02, self.r.choice(["cash", "ach"]), self.cp(), "US", "DEPOSIT")
            self.tx(a, self.ts(d + dt.timedelta(days=1), 14), "out", amt, "wire", self.cp(), ctry,
                    self.r.choice(["CONSULTING", "FAMILY SUPPORT", "GOODS"]))
        self.alert(a, "high_risk_country", "high_risk_corridor")

    def importer_fp(self):
        monthly = self.r.choice([140000, 220000])
        ctry = self.r.choice(WATCHLIST_COUNTRIES)
        a = self.customer("business", "import export", monthly, expected=f"US,{ctry}", risk="medium")
        self.business_history(a, monthly, suppliers=[(self.cp("SUP"), ctry), (self.cp("SUP"), "US")])
        self.alert(a, "high_risk_country", "none")

    # ---- build ---------------------------------------------------------
    def build(self, per_kind: int):
        kinds = [self.structuring, self.cash_business_fp, self.pass_through, self.property_sale_fp,
                 self.funnel, self.online_seller_fp, self.high_risk, self.importer_fp]
        order = [k for k in kinds for _ in range(per_kind)]
        self.r.shuffle(order)
        for k in order:
            k()
        self.txns.sort(key=lambda t: t[1])
        for i, t in enumerate(self.txns, 1):
            t[0] = f"T{i:07d}"


def generate(db_path: str | Path, labels_path: str | Path, seed: int = 7, per_kind: int = 5,
             alert_date: dt.date = dt.date(2026, 10, 30)) -> dict:
    db_path, labels_path = Path(db_path), Path(labels_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    g = Gen(seed, alert_date)
    g.build(per_kind)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?)", g.customers)
    con.executemany("INSERT INTO accounts VALUES (?,?,?)", g.accounts)
    con.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?)", g.txns)
    con.executemany("INSERT INTO alerts VALUES (?,?,?,?,?,?)", g.alerts)
    con.commit()
    con.close()
    labels_path.write_text(json.dumps(g.labels, indent=1))
    return {"alerts": len(g.alerts), "transactions": len(g.txns), "customers": len(g.customers)}


if __name__ == "__main__":
    print(generate("data/bank.db", "data/labels.json"))
