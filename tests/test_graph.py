"""Runs the full LangGraph loop with a scripted model, so no API key is needed."""
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from amlagent import baseline
from amlagent.graph import run_alert


class Scripted(BaseChatModel):
    script: list = []
    seen: list = []
    modes: list = []
    mode: str = "all"

    @property
    def _llm_type(self):
        return "scripted"

    def bind_tools(self, tools, **kw: Any):
        return self.model_copy(update={"mode": "forced" if kw.get("tool_choice") else "all"})

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        self.seen.append(messages[-1])
        self.modes.append(self.mode)
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


def call(name, args, i):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}"}])


def reports(alert="AL0001"):
    good = baseline.investigate(alert).model_dump()
    bad = baseline.investigate(alert).model_dump()
    bad["claims"].append({"text": "A further wire of 500,000.00 was sent abroad.", "evidence": ["T9999999"], "check": None})
    return good, bad


def test_tool_use_then_rejected_report_is_fixed_on_resubmit():
    good, bad = reports()
    m = Scripted(script=[call("get_alert", {"alert_id": "AL0001"}, 1), call("submit_report", bad, 2),
                         call("submit_report", good, 3)], seen=[], modes=[])
    out = run_alert("AL0001", m)
    assert out["attempts"] == 2 and out["verification"].passed and out["removed_claims"] == 0
    assert "AL0001" in m.seen[1].content                 # the tool result reached the model
    assert "T9999999 does not exist" in m.seen[2].content  # the verifier's feedback reached the model


def test_claim_that_is_never_fixed_is_removed_not_shipped():
    good, bad = reports()
    m = Scripted(script=[call("submit_report", bad, i) for i in range(3)], seen=[], modes=[])
    out = run_alert("AL0001", m)
    assert out["attempts"] == 3 and out["removed_claims"] == 1
    assert all("T9999999" not in c.evidence for c in out["report"].claims)
    assert len(out["report"].claims) == len(good["claims"])


def test_model_that_stops_without_reporting_is_nudged():
    good, _ = reports()
    m = Scripted(script=[AIMessage(content="I think it is fine."), call("submit_report", good, 1)], seen=[], modes=[])
    assert run_alert("AL0001", m)["verification"].passed


def test_a_model_that_keeps_exploring_is_forced_to_submit():
    good, _ = reports()
    m = Scripted(script=[call("get_alert", {"alert_id": "AL0001"}, 1), call("get_alert", {"alert_id": "AL0001"}, 2),
                         call("submit_report", good, 3)], seen=[], modes=[])
    out = run_alert("AL0001", m, max_tool_rounds=2)
    assert m.modes == ["all", "all", "forced"] and out["verification"].passed


def test_case_overview_gives_citable_ids_and_the_verifier_accepts_them():
    from amlagent import tools
    from amlagent.schema import Check, Claim
    from amlagent.verify import verify_report
    ov = tools.case_overview("AL0001")
    g = next(x for x in ov["review_window"]["by_direction_and_channel"] if not x.get("ids_truncated"))
    claim = Claim(text=f"{g['count']} {g['direction']} {g['channel']} transactions totalling {g['total']:,.2f}.",
                  evidence=g["transaction_ids"], check=Check(op="sum", value=g["total"]))
    rep = baseline.investigate("AL0001").model_copy(update={"claims": [claim]})
    assert verify_report(rep).passed
    assert len(str(ov)) < 24000
