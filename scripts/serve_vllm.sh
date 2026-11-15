#!/usr/bin/env bash
# Serve an open-source model with vLLM behind an OpenAI-compatible API, with tool calling enabled.
# Needs one NVIDIA GPU. Defaults fit a 16 GB card (T4). On a larger card, set a bigger model:
#   MODEL=Qwen/Qwen2.5-14B-Instruct-AWQ MAX_LEN=32768 bash scripts/serve_vllm.sh
set -euo pipefail
MODEL="${MODEL:-Qwen/Qwen2.5-7B-Instruct-AWQ}"
MAX_LEN="${MAX_LEN:-32768}"
PORT="${PORT:-8000}"
python -c "import vllm" 2>/dev/null || pip install -q vllm
exec python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" --max-model-len "$MAX_LEN" --gpu-memory-utilization "${GPU_UTIL:-0.92}" \
  --enable-auto-tool-choice --tool-call-parser hermes --port "$PORT"
