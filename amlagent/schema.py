"""The case report an investigator (model or rule-based) must hand in."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Typology = Literal["structuring", "pass_through", "funnel_account", "high_risk_corridor", "none"]


class Check(BaseModel):
    """A number the claim states, in a form the verifier can recompute from the cited transactions."""
    op: Literal["sum", "count", "max", "min", "distinct_counterparties"]
    value: float


class Claim(BaseModel):
    text: str = Field(description="One factual statement an analyst could check.")
    evidence: list[str] = Field(
        description="Record ids that support the statement: transaction ids (T...), "
                    "the customer id (C...) or the alert id (AL...).")
    check: Optional[Check] = Field(
        default=None, description="If the statement gives a total, count, max or min over the cited "
                                  "transactions, state it here so it can be recomputed.")


class CaseReport(BaseModel):
    alert_id: str
    disposition: Literal["escalate", "close"]
    typology: Typology
    summary: str = Field(description="Two or three plain sentences for the reviewing analyst.")
    claims: list[Claim]


class ClaimResult(BaseModel):
    index: int
    ok: bool
    problems: list[str] = []


class Verification(BaseModel):
    results: list[ClaimResult]
    report_problems: list[str] = []

    @property
    def passed(self) -> bool:
        return not self.report_problems and all(r.ok for r in self.results)

    @property
    def grounded_rate(self) -> float:
        return sum(r.ok for r in self.results) / len(self.results) if self.results else 0.0

    def feedback(self) -> str:
        lines = list(self.report_problems)
        lines += [f"claim {r.index}: {'; '.join(r.problems)}" for r in self.results if not r.ok]
        return "\n".join(lines)
