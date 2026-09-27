#!/usr/bin/env bash
# 一键启动智能剪辑 Web UI：先启动 ASR 服务，再启动后端

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ASR_PORT="${ASR_PORT:-8123}"
ASR_VENV="$HOME/.openclaw/workspace/venv-qwen-asr"
ASR_SCRIPT="$HOME/.openclaw/workspace/video-editor-desktop/backend/services/qwen_asr_server.py"
ASR_LOG="$SCRIPT_DIR/.asr.log"

# 检查 ASR 服务是否已经在运行
if ! curl -s "http://127.0.0.1:$ASR_PORT/health" >/dev/null 2>&1; then
  if [ ! -d "$ASR_VENV" ]; then
    echo "❌ ASR venv not found: $ASR_VENV"
    echo "   请确认 Qwen3-ASR 服务已正确安装"
    exit 1
  fi
  if [ ! -f "$ASR_SCRIPT" ]; then
    echo "❌ ASR server script not found: $ASR_SCRIPT"
    exit 1
  fi

  # 清理旧日志
  rm -f "$ASR_LOG"

  echo "🚀 正在后台启动 Qwen3-ASR 服务（端口 $ASR_PORT）..."
  (
    source "$ASR_VENV/bin/activate"
    export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
    export ASR_PORT="$ASR_PORT"
    python3 "$ASR_SCRIPT" >>"$ASR_LOG" 2>&1
  ) &

  # 等待 ASR 服务健康
  echo -n "⏳ 等待 ASR 服务就绪"
  for i in $(seq 1 120); do
    if curl -s "http://127.0.0.1:$ASR_PORT/health" >/dev/null 2>&1; then
      echo " ✅"
      break
    fi
    echo -n "."
    sleep 1
  done

  if ! curl -s "http://127.0.0.1:$ASR_PORT/health" >/dev/null 2>&1; then
    echo ""
    echo "❌ ASR 服务启动失败，日志："
    tail -n 30 "$ASR_LOG" 2>/dev/null || true
    exit 1
  fi
else
  echo "✅ ASR 服务已在运行（端口 $ASR_PORT）"
fi

source .venv/bin/activate

echo ""
echo "启动智能剪辑 Web UI 后端..."
echo "打开浏览器访问: http://127.0.0.1:8000"
echo ""
echo "ASR 日志: $ASR_LOG"

python3 -m web.backend.app
