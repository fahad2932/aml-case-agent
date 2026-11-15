import asyncio, json

from amlagent.evals import evaluate
from amlagent.graph import mcp_tools


def test_baseline_scores_and_every_claim_is_grounded(bank):
    m = asyncio.run(evaluate("baseline", bank))["metrics"]
    assert m["alerts"] == 40 and m["missed_true_cases"] == 0
    assert m["typology_accuracy"] >= 0.9 and m["first_pass_grounded_rate"] == 1.0


def test_tools_work_through_the_mcp_server():
    async def go():
        tools = {t.name: t for t in await mcp_tools()}
        assert {"get_alert", "list_transactions", "find_linked_flows"} <= set(tools)
        return await tools["get_alert"].ainvoke({"alert_id": "AL0001"})
    out = asyncio.run(go())
    assert "AL0001" in json.dumps(out, default=str)
