"""Deterministic check that every statement in a report is backed by records.

No model is involved. A claim passes only if
  1. it cites at least one record,
  2. every cited record exists and belongs to the alerted account or its holder,
  3. a stated total, count, max or min matches what the cited transactions give,
  4. every large number written in the text can be traced to the cited records.
"""
from __future__ import annotations

import re

from . import tools
from .schema import CaseReport, ClaimResult, Verification

REL_TOL = 0.005
REFERENCE_VALUES = [10000.0]  # the cash reporting threshold may be named without a citation
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?")
_ID = re.compile(r"\b(?:T|C|AL|A|CP|P2P|SUP|EMP|BUY)\d{3,}\b|BRANCH-\d+")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(REL_TOL * max(abs(a), abs(b)), 0.01)


def _numbers_in(text: str) -> list[float]:
    text = _ID.sub(" ", _DATE.sub(" ", text))
    out = []
    for m in _NUM.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def verify_report(report: CaseReport) -> Verification:
    alert = tools.get_alert(report.alert_id)
    if "error" in alert:
        return Verification(results=[], report_problems=[alert["error"]])
    profile = tools.get_customer_profile(alert["account_id"])
    problems = []
    if not report.claims:
        problems.append("report has no claims")
    if report.disposition == "escalate" and report.typology == "none":
        problems.append("an escalated case must name a typology")
    if report.disposition == "close" and report.typology != "none":
        problems.append("a closed case must have typology 'none'")

    results = []
    for i, claim in enumerate(report.claims):
        p: list[str] = []
        txns, traceable = [], list(REFERENCE_VALUES)
        if not claim.evidence:
            p.append("no evidence cited")
        for ref in claim.evidence:
            if ref.startswith("T"):
                rows = tools._rows("SELECT * FROM transactions WHERE id=?", (ref,))
                if not rows:
                    p.append(f"{ref} does not exist")
                elif rows[0]["account_id"] != alert["account_id"]:
                    p.append(f"{ref} belongs to another account")
                else:
                    txns.append(rows[0])
            elif ref == profile["id"]:
                traceable.append(profile["declared_monthly_income"])
            elif ref == alert["id"]:
                pass
            else:
                p.append(f"{ref} is not a record of this case")

        amounts = [t["amount"] for t in txns]
        if claim.check:
            if not txns:
                p.append("check given but no transactions cited")
            else:
                actual = {"sum": sum(amounts), "count": len(amounts), "max": max(amounts),
                          "min": min(amounts),
                          "distinct_counterparties": len({t["counterparty"] for t in txns})}[claim.check.op]
                if not _close(actual, claim.check.value):
                    p.append(f"{claim.check.op} of cited transactions is {actual:,.2f}, "
                             f"report says {claim.check.value:,.2f}")
                traceable.append(claim.check.value)
        if amounts:
            traceable += amounts + [sum(amounts)]
        for n in _numbers_in(claim.text):
            if n >= 1000 and not any(_close(n, t) for t in traceable):
                p.append(f"number {n:,.2f} in the text is not traceable to the cited records")
        results.append(ClaimResult(index=i, ok=not p, problems=p))
    return Verification(results=results, report_problems=problems)
