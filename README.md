# aml-case-agent

An agent that investigates anti-money laundering alerts and writes a case report in which every
statement is checked against the bank's records before an analyst sees it.

Built with LangGraph. The data tools are also served over MCP. All data is synthetic.

## Why

Analysts stop using an AI summary the first time one sentence cannot be traced to a transaction.
So the hard part of an investigation agent is not the writing. It is proving that what it wrote is true.

This repo does that with a verifier that uses no model. A report is a list of claims. Each claim
cites record ids, and the verifier checks that

1. every cited record exists and belongs to the alerted account,
2. any total, count, max or min the claim states matches the cited transactions,
3. every large number in the sentence can be traced to those records.

A claim that fails goes back to the model with the reason. If the model cannot fix it, the claim is
removed. It never reaches the analyst.

```
agent <-> tools      gather evidence with read-only tools
  |
verify               deterministic check of the submitted report
  |  \
  |   back to agent  with the claims that failed
finalize             unfixed claims are removed
```

## Run it

```bash
pip install -r requirements.txt
python -m amlagent.data                          # builds data/bank.db and data/labels.json
python -m amlagent.evals --investigator baseline # no model or API key needed
pytest -q
```

With a model, through any OpenAI-compatible endpoint. That includes an open-source model you host
yourself, by pointing `OPENAI_BASE_URL` at your own server.

```bash
export OPENAI_API_KEY=...            # and OPENAI_BASE_URL=http://your-server/v1 for a self-hosted model
export AML_MODEL=gpt-4o-mini
python -m amlagent.evals --investigator llm --limit 10
python -m amlagent.evals --investigator llm --mcp     # same run, tools loaded through the MCP server
```

The eval exits with code 1 when typology accuracy falls below the gate or a true case is closed, so
it can block a release in CI.

## The data

`amlagent/data.py` generates 40 customers, about 9,700 transactions over nine months, and 40 alerts.
Half the alerts are real cases of four typologies: structuring, pass-through, funnel account and a
high-risk corridor. The other half are false positives that fire the same rules: a cash-heavy
restaurant, a property sale, an online seller, an importer paying a known supplier. The rule name
never gives the answer away. Labels live in a separate file that the tools cannot read.

## Results

| Investigator | Typology accuracy | True cases missed | Claims grounded on first submission |
|---|---|---|---|
| Rule-based baseline | 40 / 40 | 0 | 100% |
| Model | not run yet | | |

Read the baseline number with care. The baseline's rules were written by the same person who wrote
the generator, so a perfect score is expected. It is there as a reference and to test the pipeline
end to end.

## Limits

- Synthetic data is cleaner than a real bank's. Real cases mix typologies and have missing fields.
- The verifier proves a claim is backed by records. It does not prove the conclusion drawn from them is right.
  That is what the labelled eval is for.
- Four typologies, one account per alert, no network view across customers.

## Layout

```
amlagent/data.py        synthetic bank and planted cases
amlagent/tools.py       read-only investigation tools
amlagent/mcp_server.py  the same tools over MCP
amlagent/schema.py      CaseReport, Claim, Check
amlagent/verify.py      the deterministic verifier
amlagent/graph.py       the LangGraph agent
amlagent/baseline.py    rule-based investigator
amlagent/evals.py       scoring and release gate
tests/                  12 tests, including the full agent loop with a scripted model
```
