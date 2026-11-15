from amlagent import baseline, tools
from amlagent.schema import Check, Claim
from amlagent.verify import verify_report


def _good():
    return baseline.investigate("AL0001")


def _with(claim):
    r = _good()
    return verify_report(r.model_copy(update={"claims": [claim]}))


def _window_txns():
    a = tools.get_alert("AL0001")
    return tools.list_transactions(a["account_id"], a["window_start"], a["window_end"])["transactions"]


def test_honest_report_passes():
    assert verify_report(_good()).passed


def test_invented_transaction_is_caught():
    v = _with(Claim(text="A wire was sent abroad.", evidence=["T9999999"]))
    assert not v.passed and "does not exist" in v.feedback()


def test_claim_without_evidence_is_caught():
    assert "no evidence" in _with(Claim(text="The customer is evasive.", evidence=[])).feedback()


def test_transaction_from_another_account_is_caught():
    a = tools.get_alert("AL0001")
    other = tools._rows("SELECT id FROM transactions WHERE account_id!=? LIMIT 1", (a["account_id"],))[0]["id"]
    assert "another account" in _with(Claim(text="See transfer.", evidence=[other])).feedback()


def test_wrong_total_is_caught():
    t = _window_txns()[:3]
    real = sum(x["amount"] for x in t)
    v = _with(Claim(text="Three payments.", evidence=[x["id"] for x in t], check=Check(op="sum", value=real * 2)))
    assert "report says" in v.feedback()
    assert _with(Claim(text="Three payments.", evidence=[x["id"] for x in t], check=Check(op="sum", value=real))).passed


def test_number_in_text_must_be_traceable():
    t = _window_txns()[:1]
    v = _with(Claim(text="A payment of 987,654.00 was made.", evidence=[t[0]["id"]]))
    assert "not traceable" in v.feedback()
    assert _with(Claim(text=f"A payment of {t[0]['amount']:,.2f} was made.", evidence=[t[0]["id"]])).passed


def test_escalation_needs_a_typology():
    r = _good().model_copy(update={"disposition": "escalate", "typology": "none"})
    assert "must name a typology" in verify_report(r).feedback()
