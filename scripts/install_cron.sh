#!/usr/bin/env bash
# 功能：安装/检查 ai-safety 定时同步 cron（幂等，不重复追加）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXAMPLE="$ROOT/deploy/cron-ai-safety-sync.example"
MARKER="# ai-safety-sync-cron"
PY="$ROOT/venv/bin/python"

if [[ ! -f "$EXAMPLE" ]]; then
  echo "缺少: $EXAMPLE" >&2
  exit 1
fi
if [[ ! -x "$PY" ]]; then
  echo "未找到: $PY" >&2
  exit 1
fi

CURRENT="$(crontab -l 2>/dev/null || true)"
if echo "$CURRENT" | grep -qF "$MARKER"; then
  echo "cron 已存在（$MARKER），同步更新文献调度为示例配置…"
  BLOCK="$(sed "s|^ROOT=.*|ROOT=$ROOT|; s|^PY=.*|PY=$PY|" "$EXAMPLE")"
  # 保留 MARKER 之前的内容，用最新示例块替换 ai-safety 块
  BEFORE="$(echo "$CURRENT" | sed "/$MARKER/,\$d" | sed '/^$/d')"
  {
    [[ -n "$BEFORE" ]] && printf '%s\n\n' "$BEFORE"
    printf '%s\n' "$MARKER"
    printf '%s\n' "$BLOCK"
  } | crontab -
  echo "cron 已更新。"
else
  BLOCK="$(sed "s|^ROOT=.*|ROOT=$ROOT|; s|^PY=.*|PY=$PY|" "$EXAMPLE")"
  {
    [[ -n "$CURRENT" ]] && printf '%s\n\n' "$CURRENT"
    printf '%s\n' "$MARKER"
    printf '%s\n' "$BLOCK"
  } | crontab -
  echo "cron 安装完成。"
fi

echo ""
echo "=== crontab 中的 ai-safety 条目 ==="
crontab -l | grep -E 'ai-safety|sync_sources|SHELL=/bin/bash' || true
echo ""
echo "运行日志: /tmp/ai-safety-sync.log"

# 检查 cron 守护进程是否在跑（仅有 crontab 条目不够，服务 dead 时任务不会触发）
CRON_ACTIVE="unknown"
if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet cron 2>/dev/null || systemctl is-active --quiet crond 2>/dev/null; then
    CRON_ACTIVE="yes"
  else
    CRON_ACTIVE="no"
  fi
elif pgrep -x cron >/dev/null 2>&1 || pgrep -x crond >/dev/null 2>&1; then
  CRON_ACTIVE="yes"
else
  CRON_ACTIVE="no"
fi

echo ""
if [[ "$CRON_ACTIVE" == "yes" ]]; then
  echo "✅ cron 守护进程运行中，定时任务会按计划执行。"
else
  echo "⚠️  cron 守护进程未运行！crontab 已配置但任务不会自动触发。"
  echo "   请用 root 执行：sudo systemctl start cron && sudo systemctl enable cron"
  echo "   验证：systemctl is-active cron"
fi
