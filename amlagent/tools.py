"""Read-only investigation tools over the bank database.

The same functions are used directly by the agent, exposed over MCP by
`mcp_server.py`, and used by the verifier to recompute what a report claims.
None of them can see the ground-truth labels.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from collections import defaultdict

DB_PATH = os.environ.get("AML_DB", "data/bank.db")


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{os.environ.get('AML_DB', DB_PATH)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _rows(sql: str, args=()) -> list[dict]:
    with _con() as con:
        return [dict(r) for r in con.execute(sql, args)]


def get_alert(alert_id: str) -> dict:
    """Return the alert: the account it fired on, the rule name and the review window."""
    rows = _rows("SELECT * FROM alerts WHERE id=?", (alert_id,))
    return rows[0] if rows else {"error": f"unknown alert {alert_id}"}


def get_customer_profile(account_id: str) -> dict:
    """Return the know-your-customer profile of the account holder: type, occupation,
    declared monthly income or turnover, expected countries and onboarding date."""
    rows = _rows("SELECT c.*, a.id AS account_id FROM customers c JOIN accounts a "
                 "ON a.customer_id=c.id WHERE a.id=?", (account_id,))
    return rows[0] if rows else {"error": f"unknown account {account_id}"}


def list_transactions(account_id: str, start: str, end: str, direction: str | None = None,
                      channel: str | None = None, min_amount: float | None = None,
                      max_amount: float | None = None, limit: int = 40) -> dict:
    """List transactions on an account between two dates (YYYY-MM-DD, inclusive).
    Optional filters: direction ('in' or 'out'), channel ('cash', 'wire', 'ach', 'card'),
    min_amount, max_amount. Returns at most `limit` rows plus the total match count."""
    sql = "SELECT * FROM transactions WHERE account_id=? AND ts>=? AND ts<?"
    args: list = [account_id, start, _next_day(end)]
    for col, val in (("direction", direction), ("channel", channel)):
        if val:
            sql += f" AND {col}=?"
            args.append(val)
    if min_amount is not None:
        sql += " AND amount>=?"
        args.append(min_amount)
    if max_amount is not None:
        sql += " AND amount<=?"
        args.append(max_amount)
    rows = _rows(sql + " ORDER BY ts", args)
    return {"total_matches": len(rows), "transactions": [_compact(r) for r in rows[:limit]]}


def summarize_transactions(account_id: str, start: str, end: str, group_by: str = "channel") -> dict:
    """Aggregate an account's transactions between two dates. group_by is one of
    'channel', 'counterparty', 'counterparty_country' or 'month'. Each group gives
    direction, count, total, and the transaction ids so that claims can cite them."""
    if group_by not in ("channel", "counterparty", "counterparty_country", "month"):
        return {"error": "group_by must be channel, counterparty, counterparty_country or month"}
    rows = list_transactions(account_id, start, end, limit=10**9)["transactions"]
    groups: dict = defaultdict(lambda: {"count": 0, "total": 0.0, "transaction_ids": []})
    for r in rows:
        key = r["ts"][:7] if group_by == "month" else r[group_by]
        g = groups[(key, r["direction"])]
        g["count"] += 1
        g["total"] = round(g["total"] + r["amount"], 2)
        g["transaction_ids"].append(r["id"])
    out = [{group_by: k, "direction": d, **v} for (k, d), v in groups.items()]
    out.sort(key=lambda g: -g["total"])
    for g in out[25:]:
        g["transaction_ids"] = g["transaction_ids"][:5]
    return {"groups": out[:40], "group_count": len(out)}


def find_linked_flows(account_id: str, start: str, end: str, max_hours: int = 48,
                      tolerance: float = 0.1) -> dict:
    """Find money that came in and left again quickly: each incoming transaction of
    5,000 or more that is followed within `max_hours` by an outgoing transaction of
    a similar amount (within `tolerance`, as a fraction)."""
    rows = list_transactions(account_id, start, end, limit=10**9)["transactions"]
    outs = [r for r in rows if r["direction"] == "out"]
    used, pairs = set(), []
    for i in (r for r in rows if r["direction"] == "in" and r["amount"] >= 5000):
        t_in = dt.datetime.fromisoformat(i["ts"])
        for o in outs:
            hours = (dt.datetime.fromisoformat(o["ts"]) - t_in).total_seconds() / 3600
            if o["id"] not in used and 0 <= hours <= max_hours and abs(o["amount"] - i["amount"]) <= tolerance * i["amount"]:
                used.add(o["id"])
                pairs.append({"in": i, "out": o, "hours_between": round(hours, 1)})
                break
    return {"pairs": pairs}


def baseline_activity(account_id: str, before: str, months: int = 6) -> dict:
    """Describe the account's normal behaviour in the `months` before a date: monthly
    totals by direction and channel, so the review window can be compared to history."""
    start = (dt.date.fromisoformat(before) - dt.timedelta(days=30 * months)).isoformat()
    end = (dt.date.fromisoformat(before) - dt.timedelta(days=1)).isoformat()
    rows = list_transactions(account_id, start, end, limit=10**9)["transactions"]
    agg: dict = defaultdict(lambda: {"count": 0, "total": 0.0})
    countries = set()
    for r in rows:
        a = agg[(r["direction"], r["channel"])]
        a["count"] += 1
        a["total"] += r["amount"]
        countries.add(r["counterparty_country"])
    return {"period": [start, end], "months": months, "counterparty_countries": sorted(countries),
            "by_direction_channel": [
                {"direction": d, "channel": c, "monthly_count": round(v["count"] / months, 1),
                 "monthly_total": round(v["total"] / months, 2)} for (d, c), v in sorted(agg.items())]}


def _compact(r: dict) -> dict:
    return {"id": r["id"], "ts": r["ts"][:16], "direction": r["direction"], "amount": r["amount"],
            "channel": r["channel"], "counterparty": r["counterparty"],
            "counterparty_country": r["counterparty_country"], "description": r["description"]}


def _all(account_id: str, start: str, end: str) -> list[dict]:
    sql = "SELECT * FROM transactions WHERE account_id=? AND ts>=? AND ts<? ORDER BY ts"
    return _rows(sql, (account_id, start, _next_day(end)))


def _groups(rows: list[dict], cap: int = 150) -> list[dict]:
    g: dict = defaultdict(list)
    for r in rows:
        g[(r["direction"], r["channel"])].append(r)
    out = []
    for (d, c), rs in sorted(g.items()):
        item = {"direction": d, "channel": c, "count": len(rs), "total": round(sum(r["amount"] for r in rs), 2),
                "transaction_ids": [r["id"] for r in rs[:cap]]}
        if len(rs) > cap:
            item["ids_truncated"] = True
        out.append(item)
    return out


def case_overview(alert_id: str) -> dict:
    """Start here. One call that returns everything an analyst opens first: the alert, the customer
    profile, the review window against the 30 days before it (counts, totals and transaction ids by
    direction and channel), counterparty countries, cash deposits just under 10,000, money that came
    in and left within 48 hours, and the largest transactions. Cite the transaction ids it gives."""
    alert = get_alert(alert_id)
    if "error" in alert:
        return alert
    acct, ws, we = alert["account_id"], alert["window_start"], alert["window_end"]
    prev_start = (dt.date.fromisoformat(ws) - dt.timedelta(days=30)).isoformat()
    prev_end = (dt.date.fromisoformat(ws) - dt.timedelta(days=1)).isoformat()
    win, prev = _all(acct, ws, we), _all(acct, prev_start, prev_end)
    near = [r for r in win if r["direction"] == "in" and r["channel"] == "cash" and 8000 <= r["amount"] < 10000]
    senders = {r["counterparty"] for r in win if r["direction"] == "in"}
    countries: dict = defaultdict(lambda: {"count": 0, "total": 0.0})
    for r in win:
        if r["direction"] == "out" and r["channel"] == "wire":
            c = countries[r["counterparty_country"]]
            c["count"] += 1
            c["total"] = round(c["total"] + r["amount"], 2)
    pairs = find_linked_flows(acct, ws, we)["pairs"]
    return {
        "alert": alert,
        "customer": get_customer_profile(acct),
        "review_window": {"start": ws, "end": we, "by_direction_and_channel": _groups(win)},
        "previous_30_days": {"start": prev_start, "end": prev_end, "by_direction_and_channel": _groups(prev)},
        "distinct_incoming_counterparties_in_window": len(senders),
        "outgoing_wires_by_country_in_window": dict(countries),
        "cash_deposits_just_under_10000": {
            "count": len(near), "total": round(sum(r["amount"] for r in near), 2),
            "distinct_branches": len({r["counterparty"] for r in near}),
            "transaction_ids": [r["id"] for r in near][:150]},
        "in_then_out_within_48h": [{"in": _compact(p["in"]), "out": _compact(p["out"]),
                                    "hours_between": p["hours_between"]} for p in pairs[:10]],
        "largest_transactions": [_compact(r) for r in sorted(win, key=lambda r: -r["amount"])[:8]],
    }


def _next_day(day: str) -> str:
    return (dt.date.fromisoformat(day[:10]) + dt.timedelta(days=1)).isoformat()


ALL_TOOLS = [case_overview, get_alert, get_customer_profile, list_transactions, summarize_transactions,
             find_linked_flows, baseline_activity]
