"""Score an investigator against the planted ground truth, and gate a release on it.

    python -m amlagent.evals --investigator baseline
    python -m amlagent.evals --investigator llm --limit 10
    python -m amlagent.evals --investigator llm --mcp

Exit code is 1 when a gate fails, so this can block a deploy in CI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import baseline, tools
from .verify import verify_report


async def _run_one(alert_id: str, investigator: str, model, tool_list) -> dict:
    if investigator == "baseline":
        report = baseline.investigate(alert_id)
        v = verify_report(report)
        return {"report": report, "first_pass_grounded": v.grounded_rate, "attempts": 1, "removed_claims": 0}
    from .graph import arun_alert
    out = await arun_alert(alert_id, model, tool_list)
    v = out["verification"]
    return {"report": out["report"], "first_pass_grounded": v.grounded_rate if v else 0.0,
            "attempts": out["attempts"], "removed_claims": out["removed_claims"]}


async def evaluate(investigator: str = "baseline", labels_path: str = "data/labels.json",
                   limit: int | None = None, model=None, use_mcp: bool = False) -> dict:
    labels = json.loads(Path(labels_path).read_text())
    ids = sorted(labels)[:limit]
    tool_list = None
    if investigator == "llm":
        from .graph import make_model, mcp_tools
        model = model or make_model()
        tool_list = await mcp_tools() if use_mcp else None
    rows = []
    for aid in ids:
        try:
            r = await _run_one(aid, investigator, model, tool_list)
        except Exception as e:  # a crashed case counts as a wrong one, it is not skipped
            r = {"report": None, "first_pass_grounded": 0.0, "attempts": 0, "removed_claims": 0, "error": repr(e)}
        rep, truth = r["report"], labels[aid]
        rows.append({"alert": aid, "truth": truth["typology"],
                     "predicted": rep.typology if rep else None,
                     "disposition_ok": bool(rep) and rep.disposition == truth["disposition"],
                     "typology_ok": bool(rep) and rep.typology == truth["typology"],
                     "claims": len(rep.claims) if rep else 0,
                     "first_pass_grounded": r["first_pass_grounded"], "attempts": r["attempts"],
                     "removed_claims": r["removed_claims"], "error": r.get("error")})
    n = len(rows)
    true_cases = [r for r in rows if r["truth"] != "none"]
    false_alerts = [r for r in rows if r["truth"] == "none"]
    metrics = {
        "alerts": n,
        "typology_accuracy": sum(r["typology_ok"] for r in rows) / n,
        "disposition_accuracy": sum(r["disposition_ok"] for r in rows) / n,
        "missed_true_cases": sum(not r["disposition_ok"] for r in true_cases),
        "false_alerts_correctly_closed": sum(r["disposition_ok"] for r in false_alerts) / max(len(false_alerts), 1),
        "first_pass_grounded_rate": sum(r["first_pass_grounded"] for r in rows) / n,
        "claims_removed_as_unsupported": sum(r["removed_claims"] for r in rows),
        "mean_attempts": sum(r["attempts"] for r in rows) / n,
    }
    return {"investigator": investigator, "metrics": metrics, "rows": rows}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--investigator", choices=["baseline", "llm"], default="baseline")
    p.add_argument("--labels", default="data/labels.json")
    p.add_argument("--limit", type=int)
    p.add_argument("--mcp", action="store_true", help="load tools through the MCP server")
    p.add_argument("--gate-typology", type=float, default=0.8)
    p.add_argument("--gate-missed", type=int, default=0, help="max true cases wrongly closed")
    p.add_argument("--out", default="eval_results.json")
    a = p.parse_args(argv)
    if not Path(a.labels).exists():
        from .data import generate
        generate(tools.DB_PATH, a.labels)
    res = asyncio.run(evaluate(a.investigator, a.labels, a.limit, use_mcp=a.mcp))
    Path(a.out).write_text(json.dumps(res, indent=1))
    m = res["metrics"]
    for k, v in m.items():
        print(f"{k:32} {v:.3f}" if isinstance(v, float) else f"{k:32} {v}")
    failed = []
    if m["typology_accuracy"] < a.gate_typology:
        failed.append(f"typology accuracy {m['typology_accuracy']:.2f} is below {a.gate_typology}")
    if m["missed_true_cases"] > a.gate_missed:
        failed.append(f"{m['missed_true_cases']} true cases were closed, allowed {a.gate_missed}")
    for f in failed:
        print("GATE FAILED:", f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
