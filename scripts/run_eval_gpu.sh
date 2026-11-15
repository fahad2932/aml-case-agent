#!/usr/bin/env bash
# Start the vLLM server, wait until it is ready, then run the labelled eval against it.
#   bash scripts/run_eval_gpu.sh        all 40 alerts
#   bash scripts/run_eval_gpu.sh 4      first 4 alerts
set -uo pipefail
cd "$(dirname "$0")/.."
export MODEL="${MODEL:-Qwen/Qwen2.5-7B-Instruct-AWQ}" PORT="${PORT:-8000}"
LIMIT="${1:-}"
if ! curl -s "localhost:$PORT/v1/models" >/dev/null; then
  nohup bash scripts/serve_vllm.sh > vllm.log 2>&1 &
  SERVER=$!
  until curl -s "localhost:$PORT/v1/models" >/dev/null; do
    kill -0 "$SERVER" 2>/dev/null || { echo "vLLM failed to start:"; tail -n 5 vllm.log; exit 1; }
    sleep 5
  done
fi
[ -f data/labels.json ] || python -m amlagent.data
export OPENAI_BASE_URL="http://localhost:$PORT/v1" OPENAI_API_KEY="${OPENAI_API_KEY:-local}" AML_MODEL="$MODEL"
python -m amlagent.evals --investigator llm ${LIMIT:+--limit "$LIMIT"} --gate-typology 0 --gate-missed 99
