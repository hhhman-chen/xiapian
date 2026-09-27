#!/usr/bin/env bash
# DGX Spark 上一键启动：ASR(GPU) + Web UI
# 浏览器访问：http://192.168.1.11:8000
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

VENV="${ROOT}/.venv"
ASR_PORT="${ASR_PORT:-8123}"
WEB_PORT="${WEB_PORT:-8000}"
ASR_HOST="${ASR_HOST:-127.0.0.1}"
WEB_HOST="${WEB_HOST:-0.0.0.0}"
ASR_LOG="${ROOT}/.asr.log"
WEB_LOG="${ROOT}/.web.log"

export ASR_MODEL="${ASR_MODEL:-$ROOT/models/Qwen3-ASR-1.7B}"
export ALIGNER_MODEL="${ALIGNER_MODEL:-$ROOT/models/Qwen3-ForcedAligner-0.6B}"
export ASR_URL="${ASR_URL:-http://127.0.0.1:${ASR_PORT}}"
export ANALYZE_BACKEND="${ANALYZE_BACKEND:-deepseek}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export PATH="/usr/bin:/usr/local/bin:${PATH}"

if [ ! -x "$VENV/bin/python" ]; then
  echo "❌ 找不到 $VENV ，先按 README 在 DGX 上建虚拟环境"
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

if [ "${ASR_BACKEND:-auto}" != "local" ]; then
  echo "✅ 使用线上百炼 ASR（不启动本地 8123）"
elif ! curl -sf "http://127.0.0.1:${ASR_PORT}/health" >/dev/null 2>&1; then
  echo "🚀 启动 Qwen3-ASR（GPU）port=${ASR_PORT}"
  nohup env ASR_PORT="$ASR_PORT" ASR_HOST="$ASR_HOST" ASR_MODEL="$ASR_MODEL" ALIGNER_MODEL="$ALIGNER_MODEL" \
    python "$ROOT/asr/qwen_asr_server.py" >>"$ASR_LOG" 2>&1 &
  echo $! > "$ROOT/.asr.pid"
  echo -n "⏳ 等待 ASR"
  ok=0
  for _ in $(seq 1 180); do
    if curl -sf "http://127.0.0.1:${ASR_PORT}/health" >/dev/null 2>&1; then
      echo " ✅"
      ok=1
      break
    fi
    echo -n "."
    sleep 1
  done
  if [ "$ok" != 1 ]; then
    echo ""
    echo "❌ ASR 启动失败，见 $ASR_LOG"
    tail -n 40 "$ASR_LOG" || true
    exit 1
  fi
else
  echo "✅ ASR 已在运行 :${ASR_PORT}"
fi

echo "🚀 启动 Web UI  ${WEB_HOST}:${WEB_PORT}"
echo "   公司局域网：http://192.168.1.11:${WEB_PORT}"
exec python -m uvicorn web.backend.app:app --host "$WEB_HOST" --port "$WEB_PORT" --log-level info
