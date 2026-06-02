#!/usr/bin/env bash
# 功能：重新构建并重启公众门户 Next.js（nginx → 127.0.0.1:3001）。
# 用法：bash scripts/rebuild_portal_web.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB="$ROOT/web"
NPM="${NPM:-$HOME/.local/node-extract/bin/npm}"
PORT="${PORTAL_WEB_PORT:-3001}"
LOG="/tmp/ai-safety-web.log"

if [[ ! -x "$NPM" ]]; then
  echo "未找到 npm: $NPM"
  exit 1
fi

echo "=== 1/3 next build ==="
cd "$WEB"
"$NPM" run build

echo "=== 2/3 停止旧 next-server (port $PORT) ==="
pkill -f "next start.*-p $PORT" 2>/dev/null || true
pkill -f "next-server.*$PORT" 2>/dev/null || true
sleep 1

echo "=== 3/3 启动 next start -p $PORT ==="
nohup "$NPM" run start -- -p "$PORT" -H 127.0.0.1 >> "$LOG" 2>&1 &
sleep 2

if curl -sf "http://127.0.0.1:$PORT/" >/dev/null; then
  echo "✅ 门户已更新: http://127.0.0.1:$PORT"
  echo "   导航应含「监测周报」；政策页应含「AI 监测简报」"
  echo "   日志: $LOG"
else
  echo "❌ 启动失败，查看: tail -50 $LOG"
  exit 1
fi

# systemd 若已安装则同步重启
if systemctl is-enabled ai-safety-portal-web &>/dev/null; then
  echo "提示: 也可 sudo systemctl restart ai-safety-portal-web"
fi
