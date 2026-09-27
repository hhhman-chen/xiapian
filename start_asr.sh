#!/usr/bin/env bash
# 启动 Qwen3-ASR 服务（用于 v6 engine 声音通道）

set -e

PORT="${1:-8123}"
VENV="$HOME/.openclaw/workspace/venv-qwen-asr"
SCRIPT="$HOME/.openclaw/workspace/video-editor-desktop/backend/services/qwen_asr_server.py"

if [ ! -d "$VENV" ]; then
  echo "❌ ASR venv not found: $VENV"
  exit 1
fi

if [ ! -f "$SCRIPT" ]; then
  echo "❌ ASR server script not found: $SCRIPT"
  exit 1
fi

source "$VENV/bin/activate"

# 检查端口是否被占用
PID=$(lsof -ti tcp:$PORT 2>/dev/null || true)
if [ -n "$PID" ]; then
  echo "⚠️  Port $PORT 已被占用（PID=$PID），先 kill..."
  kill $PID 2>/dev/null || true
  sleep 1
fi

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export ASR_PORT="$PORT"

echo "🚀 启动 Qwen3-ASR 服务（port=$PORT）"
python3 "$SCRIPT"
