"""The investigation agent as a LangGraph state machine.

    agent  <->  tools            the model gathers evidence with read-only tools
      |
    verify                       deterministic check of the submitted report
      |  \\
      |   back to agent          with the list of claims that failed, to fix and resubmit
    finalize                     claims that still fail are removed, never shipped
"""
from __future__ import annotations

import asyncio
import os
from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import ValidationError

from . import tools as bank_tools
from .schema import CaseReport, Verification
from .verify import verify_report

SYSTEM = """You investigate anti-money laundering alerts for a bank.

Start with case_overview. It gives you the alert, the customer, the review window against the
previous 30 days, and the transaction ids. Use the other tools only to look closer at something it
shows. You have a small budget of tool calls, so then call submit_report.

Rules for the report:
- disposition is 'escalate' or 'close'. A closed case has typology 'none'.
- typology is one of: structuring, pass_through, funnel_account, high_risk_corridor, none.
- Every claim must cite the record ids that prove it (transaction ids, the customer id or the alert id).
- If a claim states a total, count, max or min, put it in `check` and cite exactly the
  transactions it is computed over. Copy numbers from tool output. Do not round or estimate.
  Copy a group's `total` and `transaction_ids` together, as given.
- An alert firing is not proof. Many alerts are false positives that match the customer's
  declared business and history. Close those and say why.

Your report is checked automatically. Any claim whose evidence does not exist, belongs to another
account, or does not add up is sent back to you, and is removed if you cannot fix it."""


class State(TypedDict):
    alert_id: str
    messages: Annotated[list, add_messages]
    report: Optional[CaseReport]
    verification: Optional[Verification]
    attempts: int
    removed_claims: int


def _submit_report(**kwargs) -> str:  # never executed: the graph routes this call to `verify`
    return "received"


SUBMIT = StructuredTool.from_function(
    _submit_report, name="submit_report", args_schema=CaseReport,
    description="Hand in the final case report. Call this once the evidence is gathered.")


def default_tools() -> list:
    return [StructuredTool.from_function(f) for f in bank_tools.ALL_TOOLS]


def tool_rounds(messages: list) -> int:
    """How many times the model has asked for data tools so far."""
    return sum(1 for m in messages if isinstance(m, AIMessage) and m.tool_calls
               and not any(c["name"] == "submit_report" for c in m.tool_calls))


def build_graph(model: Any, tools: list | None = None, max_attempts: int = 3, max_tool_rounds: int = 6):
    tools = tools or default_tools()
    bound = model.bind_tools(tools + [SUBMIT])
    forced = model.bind_tools([SUBMIT], tool_choice="submit_report")

    async def agent(state: State):
        # Out of budget: the model may no longer ask for data. It must hand in what it has.
        if tool_rounds(state["messages"]) >= max_tool_rounds:
            note = HumanMessage("Tool budget used up. Call submit_report now with what you have found.")
            return {"messages": [note, await forced.ainvoke(state["messages"] + [note])]}
        return {"messages": [await bound.ainvoke(state["messages"])]}

    def route(state: State):
        calls = getattr(state["messages"][-1], "tool_calls", None) or []
        if any(c["name"] == "submit_report" for c in calls):
            return "verify"
        return "tools" if calls else "nudge"

    def nudge(state: State):
        return {"messages": [HumanMessage("Continue the investigation with the tools, or call submit_report.")]}

    def verify(state: State):
        calls = state["messages"][-1].tool_calls
        submit = next(c for c in calls if c["name"] == "submit_report")
        attempts = state["attempts"] + 1
        report, verification = state.get("report"), None
        try:
            report = CaseReport.model_validate({**submit["args"], "alert_id": state["alert_id"]})
            verification = verify_report(report)
            feedback = "accepted" if verification.passed else (
                "Rejected. Fix these and call submit_report again:\n" + verification.feedback())
        except ValidationError as e:
            feedback = f"Rejected. The report does not match the schema:\n{e}"
        msgs = [ToolMessage(feedback if c is submit else "ignored: submit_report was called in the same turn",
                            tool_call_id=c["id"]) for c in calls]
        return {"messages": msgs, "report": report, "verification": verification, "attempts": attempts}

    def after_verify(state: State):
        v = state.get("verification")
        if (v and v.passed) or state["attempts"] >= max_attempts:
            return "finalize"
        return "agent"

    def finalize(state: State):
        report, v = state.get("report"), state.get("verification")
        if not report or not v or v.passed:
            return {"removed_claims": 0}
        bad = {r.index for r in v.results if not r.ok}
        kept = report.model_copy(update={"claims": [c for i, c in enumerate(report.claims) if i not in bad]})
        return {"report": kept, "removed_claims": len(bad)}

    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(tools))
    g.add_node("nudge", nudge)
    g.add_node("verify", verify)
    g.add_node("finalize", finalize)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route, ["tools", "verify", "nudge"])
    g.add_edge("tools", "agent")
    g.add_edge("nudge", "agent")
    g.add_conditional_edges("verify", after_verify, ["agent", "finalize"])
    g.add_edge("finalize", END)
    return g.compile()


async def arun_alert(alert_id: str, model: Any, tools: list | None = None, max_attempts: int = 3,
                     recursion_limit: int = 60, max_tool_rounds: int = 6) -> dict:
    graph = build_graph(model, tools, max_attempts, max_tool_rounds)
    state = await graph.ainvoke(
        {"alert_id": alert_id, "attempts": 0, "removed_claims": 0, "report": None, "verification": None,
         "messages": [SystemMessage(SYSTEM), HumanMessage(f"Investigate alert {alert_id}.")]},
        {"recursion_limit": recursion_limit})
    return {k: state.get(k) for k in ("report", "verification", "attempts", "removed_claims")}


def run_alert(alert_id: str, model: Any, **kw) -> dict:
    return asyncio.run(arun_alert(alert_id, model, **kw))


def make_model():
    """Any OpenAI-compatible endpoint: OpenAI itself, or a self-hosted open-source model
    served on-prem (set OPENAI_BASE_URL to that server). Model name comes from AML_MODEL."""
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=os.environ.get("AML_MODEL", "gpt-4o-mini"), temperature=0)


async def mcp_tools() -> list:
    """Load the same tools through the MCP server instead of calling them in-process."""
    import sys

    from langchain_mcp_adapters.client import MultiServerMCPClient
    client = MultiServerMCPClient({"bank": {"command": sys.executable, "args": ["-m", "amlagent.mcp_server"],
                                            "transport": "stdio", "env": dict(os.environ)}})
    return await client.get_tools()
