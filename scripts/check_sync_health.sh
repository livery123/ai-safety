#!/usr/bin/env bash
# 功能：排查 ai-safety 定时同步与门户监控状态是否一致。
# 输入：无（读 crontab、system_tasks、可选 API）。
# 输出：终端诊断报告；exit 1 表示 cron 守护进程未运行。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/venv/bin/python"
LOG="/tmp/ai-safety-sync.log"

echo "=== ai-safety 同步健康检查 ==="
echo "时间: $(date)"
echo ""

# 1. cron 守护进程
CRON_OK=0
if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet cron 2>/dev/null || systemctl is-active --quiet crond 2>/dev/null; then
    echo "[OK] cron 守护进程运行中"
    CRON_OK=1
  else
    echo "[FAIL] cron 守护进程未运行（crontab 有条目也不会触发）"
    echo "       修复: sudo systemctl start cron && sudo systemctl enable cron"
  fi
else
  echo "[WARN] 无法检测 systemctl，请手动确认 cron 进程"
fi

# 2. crontab 条目
echo ""
if crontab -l 2>/dev/null | grep -qF "ai-safety-sync-cron"; then
  echo "[OK] crontab 已配置 ai-safety 条目"
  crontab -l 2>/dev/null | grep sync_sources || true
else
  echo "[FAIL] crontab 未安装，执行: bash scripts/install_cron.sh"
fi

# 3. 同步日志
echo ""
if [[ -f "$LOG" ]]; then
  echo "[OK] 日志文件存在: $LOG ($(wc -l < "$LOG") 行)"
  echo "--- 最近 5 行 ---"
  tail -5 "$LOG"
else
  echo "[WARN] 日志不存在: $LOG（cron 可能从未成功触发过）"
fi

# 4. system_tasks 数据库
echo ""
if [[ -x "$PY" ]]; then
  (cd "$ROOT" && "$PY" - <<'PY') || echo "[FAIL] 无法读取 system_tasks（MySQL？）"
from core.system_tasks import count_today_runs, fetch_last_success_by_system, fetch_recent_tasks
from datetime import datetime

today = count_today_runs()
print(f"[INFO] 今日任务记录数: {today}")
last = fetch_last_success_by_system()
now = datetime.now()
for key in ("policy", "meeting", "literature"):
    row = last.get(key)
    if not row:
        print(f"  {key}: 无成功记录")
        continue
    end = row["end_time"]
    if hasattr(end, "strftime"):
        hours = (now - end).total_seconds() / 3600
        print(f"  {key}: 上次成功 {end} ({hours:.1f}h 前) 新增={row.get('data_count')}")
print("--- 最近 3 条 ---")
for r in fetch_recent_tasks(3):
    print(f"  {r.get('start_time')} {r.get('system_key')} {r.get('status')} +{r.get('data_count')}")
PY
else
  echo "[FAIL] 未找到 $PY"
fi

# 5. API（可选）
echo ""
if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  curl -s http://127.0.0.1:8000/api/monitoring/overview | "$PY" -c "
import sys, json
d = json.load(sys.stdin)
p = d['platform']
print(f\"[API] {p['status_label']} | 在线 {p['online_subsystems']}/{p['total_subsystems']} | 今日运行 {p['today_run_count']} | {p['last_run_ago']}\")
" 2>/dev/null || echo "[WARN] monitoring API 解析失败"
else
  echo "[WARN] FastAPI 未启动 (127.0.0.1:8000)"
fi

echo ""
if [[ "$CRON_OK" -eq 0 ]]; then
  exit 1
fi
