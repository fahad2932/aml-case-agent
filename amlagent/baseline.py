"""Rule-based investigator. It needs no model and no API key.

It uses the same tools and hands in the same CaseReport as the model does, so it
serves two purposes: a reference score the model has to beat, and a way to run
the whole pipeline in tests.
"""
from __future__ import annotations

import datetime as dt

from . import tools
from .data import WATCHLIST_COUNTRIES
from .schema import CaseReport, Check, Claim


def _claim(text: str, txns: list[dict], op: str | None = None, extra: list[str] | None = None) -> Claim:
    ids = [t["id"] for t in txns] + (extra or [])
    check = None
    if op == "sum":
        check = Check(op="sum", value=round(sum(t["amount"] for t in txns), 2))
    elif op == "distinct_counterparties":
        check = Check(op=op, value=len({t["counterparty"] for t in txns}))
    return Claim(text=text, evidence=ids, check=check)


def _money(txns: list[dict]) -> str:
    return f"{sum(t['amount'] for t in txns):,.2f}"


def investigate(alert_id: str) -> CaseReport:
    alert = tools.get_alert(alert_id)
    acct, ws, we = alert["account_id"], alert["window_start"], alert["window_end"]
    prof = tools.get_customer_profile(acct)
    win = tools.list_transactions(acct, ws, we, limit=10**9)["transactions"]
    prev_start = (dt.date.fromisoformat(ws) - dt.timedelta(days=30)).isoformat()
    prev_end = (dt.date.fromisoformat(ws) - dt.timedelta(days=1)).isoformat()
    prev = tools.list_transactions(acct, prev_start, prev_end, limit=10**9)["transactions"]
    base = tools.baseline_activity(acct, ws)
    expected = prof["expected_countries"].split(",")
    who = f"{prof['name']} ({prof['occupation']}, declared monthly {'turnover' if prof['type'] == 'business' else 'income'} " \
          f"{prof['declared_monthly_income']:,.2f})"
    profile_claim = Claim(text=f"The account holder is {who}.", evidence=[prof["id"]])

    def pick(rows, **f):
        return [r for r in rows if all(r[k] == v for k, v in f.items())]

    cash_in, prev_cash = pick(win, direction="in", channel="cash"), pick(prev, direction="in", channel="cash")
    near = [t for t in cash_in if 8000 <= t["amount"] < 10000]
    ach_in, prev_ach = pick(win, direction="in", channel="ach"), pick(prev, direction="in", channel="ach")
    wires_out = pick(win, direction="out", channel="wire")
    watch = [t for t in wires_out if t["counterparty_country"] in WATCHLIST_COUNTRIES
             and t["counterparty_country"] not in expected]
    pairs = tools.find_linked_flows(acct, ws, we)["pairs"]
    senders = {t["counterparty"] for t in ach_in}

    def report(disposition, typology, summary, claims):
        return CaseReport(alert_id=alert_id, disposition=disposition, typology=typology,
                          summary=summary, claims=[profile_claim] + claims)

    if len(senders) >= 15 and len(ach_in) > 3 * max(len(prev_ach), 1) and wires_out:
        return report("escalate", "funnel_account",
                      "Many unrelated senders paid small amounts into the account and most of the money "
                      "was wired out within days. This does not fit the declared profile.",
                      [_claim(f"{len(ach_in)} incoming transfers from {len(senders)} different senders, "
                              f"totalling {_money(ach_in)}.", ach_in, "sum"),
                       _claim(f"The prior 30 days had {len(prev_ach)} incoming transfers.", prev_ach or ach_in[:1]),
                       _claim(f"{len(wires_out)} outgoing wires totalling {_money(wires_out)} followed.",
                              wires_out, "sum")])

    if len(near) >= 5 and len(near) >= 0.6 * len(cash_in) and len(prev_cash) < 0.3 * len(cash_in):
        claims = [_claim(f"{len(near)} cash deposits just under the 10,000 reporting threshold, "
                         f"totalling {_money(near)}.", near, "sum"),
                  _claim(f"The deposits were made at {len({t['counterparty'] for t in near})} different branches.",
                         near, "distinct_counterparties")]
        if wires_out:
            claims.append(_claim(f"Outgoing wires totalling {_money(wires_out)} followed.", wires_out, "sum"))
        return report("escalate", "structuring",
                      "Repeated cash deposits sit just under the reporting threshold and are far above "
                      "the declared income, with no cash history on the account.", claims)

    if len(watch) >= 2 and not set(t["counterparty_country"] for t in watch) & set(base["counterparty_countries"]):
        funding = [p["in"] for p in pairs]
        claims = [_claim(f"{len(watch)} wires totalling {_money(watch)} went to "
                         f"{', '.join(sorted({t['counterparty_country'] for t in watch}))}, which is not an "
                         f"expected country for this customer and has no history on the account.", watch, "sum")]
        if funding:
            claims.append(_claim(f"These wires were funded by deposits totalling {_money(funding)} made "
                                 f"within two days before.", funding, "sum"))
        return report("escalate", "high_risk_corridor",
                      "Wires to a watchlist country that the customer has no declared or historic link to, "
                      "funded by fresh third-party deposits.", claims)

    pair_in = [p["in"] for p in pairs]
    if len(pairs) >= 2 and sum(t["amount"] for t in pair_in) > 5 * prof["declared_monthly_income"] \
            and prof["type"] == "individual":
        pair_out = [p["out"] for p in pairs]
        return report("escalate", "pass_through",
                      "Large wires came in and left again within about a day, repeatedly, at amounts far "
                      "above the declared income. The account is acting as a pass-through.",
                      [_claim(f"{len(pair_in)} incoming wires totalling {_money(pair_in)}.", pair_in, "sum"),
                       _claim(f"Each was followed within 48 hours by an outgoing wire of a similar amount, "
                              f"totalling {_money(pair_out)}.", pair_out, "sum")])

    # Nothing suspicious found: explain why the alert fired and why it is consistent.
    claims = []
    if alert["rule"] == "cash_threshold_pattern" and cash_in:
        claims += [_claim(f"{len(cash_in)} cash deposits in the review window totalling {_money(cash_in)}.", cash_in, "sum"),
                   _claim(f"The prior 30 days show the same pattern: {len(prev_cash)} cash deposits "
                          f"totalling {_money(prev_cash)}.", prev_cash, "sum")] if prev_cash else []
    elif alert["rule"] == "rapid_movement" and pairs:
        p = pairs[0]
        claims += [_claim(f"One incoming wire of {p['in']['amount']:,.2f} described as "
                          f"'{p['in']['description']}' from {p['in']['counterparty']}.", [p["in"]]),
                   _claim(f"It was followed by one outgoing wire of {p['out']['amount']:,.2f} described as "
                          f"'{p['out']['description']}' to {p['out']['counterparty']}.", [p["out"]])]
    elif alert["rule"] == "many_to_one" and ach_in:
        claims += [_claim(f"{len(ach_in)} incoming transfers in the review window totalling {_money(ach_in)}.", ach_in, "sum")]
        if prev_ach:
            claims.append(_claim(f"The prior 30 days show the same pattern: {len(prev_ach)} incoming "
                                 f"transfers totalling {_money(prev_ach)}.", prev_ach, "sum"))
    elif alert["rule"] == "high_risk_country":
        w = [t for t in wires_out if t["counterparty_country"] in WATCHLIST_COUNTRIES]
        pw = [t for t in pick(prev, direction="out", channel="wire") if t["counterparty_country"] in WATCHLIST_COUNTRIES]
        if w:
            claims.append(_claim(f"{len(w)} wires totalling {_money(w)} to "
                                 f"{len({t['counterparty'] for t in w})} supplier in a declared trade country.", w, "sum"))
        if pw:
            claims.append(_claim(f"The same supplier was paid in the prior 30 days, totalling {_money(pw)}.", pw, "sum"))
    return report("close", "none",
                  "The activity that triggered the alert matches the customer's declared business and "
                  "the account's own history. No further action.", claims)
