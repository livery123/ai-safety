"""
功能：统一子系统任务运行记录（MySQL system_tasks 表读写）。

输入：system_key、task_name、SyncResult 或异常信息。
输出：task id；finish 时更新 status / data_count / message。
上下游：scripts/sync_sources.py、core/ui_jobs 写入；api/services/monitoring_data 读取。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TypeVar

from core.mysql_db import mysql_conn

T = TypeVar("T")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS system_tasks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  system_key VARCHAR(32) NOT NULL,
  task_name VARCHAR(64) NOT NULL,
  status ENUM('running', 'success', 'failed') NOT NULL DEFAULT 'running',
  start_time DATETIME NOT NULL,
  end_time DATETIME NULL,
  data_count INT NOT NULL DEFAULT 0,
  message TEXT NULL,
  trigger_source VARCHAR(32) NOT NULL DEFAULT 'cron',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_system_tasks_system_time (system_key, start_time DESC),
  KEY idx_system_tasks_status_time (status, start_time DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def ensure_system_tasks_table() -> None:
    """建表（幂等）；MySQL 不可用时静默跳过，由调用方处理空数据。"""
    try:
        with mysql_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE_TABLE_SQL)
    except Exception:
        pass


def _now_local() -> datetime:
    return datetime.now().replace(microsecond=0)


def _build_message(
    *,
    summary: str,
    log_tail: Optional[List[str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    payload: Dict[str, Any] = {"summary": summary}
    if log_tail:
        payload["log_tail"] = [str(x) for x in log_tail[-8:]]
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def begin_task(
    system_key: str,
    task_name: str,
    *,
    trigger_source: str = "cron",
) -> Optional[int]:
    """
    功能：记录任务开始，返回 task id。
    输入：子系统 key、任务名、触发来源 cron|manual。
    输出：插入 id；MySQL 失败时 None。
    """
    ensure_system_tasks_table()
    now = _now_local()
    try:
        with mysql_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO system_tasks
                      (system_key, task_name, status, start_time, trigger_source)
                    VALUES (%s, %s, 'running', %s, %s)
                    """,
                    (system_key, task_name, now, trigger_source),
                )
                return int(cur.lastrowid)
    except Exception:
        return None


def finish_task(
    task_id: Optional[int],
    *,
    status: str,
    data_count: int = 0,
    message: str = "",
) -> None:
    """
    功能：更新任务结束状态。
    输入：begin_task 返回的 id、success|failed、新增数量、message JSON 或纯文本。
    输出：无；写 MySQL。
    """
    if not task_id:
        return
    now = _now_local()
    st = status if status in ("success", "failed", "running") else "failed"
    try:
        with mysql_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE system_tasks
                    SET status = %s, end_time = %s, data_count = %s, message = %s
                    WHERE id = %s
                    """,
                    (st, now, int(data_count), message[:65000] if message else None, task_id),
                )
    except Exception:
        pass


def summary_from_sync_result(r: Any, *, action: str) -> str:
    """从 SyncResult / LiteratureSyncResult 生成 timeline 摘要。"""
    saved = int(getattr(r, "saved", 0) or 0)
    failed = int(getattr(r, "failed", 0) or 0)
    if failed > 0 and saved == 0:
        return f"{action}失败（失败 {failed} 条）"
    return f"{action}（新增 {saved} 条）"


def record_news_bundle_tasks(
    bundle: Any,
    *,
    trigger_source: str = "cron",
) -> None:
    """
    功能：全信源新闻包跑完后，为政策/会议两子系统各写一条任务记录。
    输入：NewsSyncBundleResult；trigger_source cron|manual。
    输出：无；写 MySQL system_tasks。
    """
    merged = getattr(bundle, "merged", bundle)
    saved = int(getattr(merged, "saved", 0) or 0)
    failed = int(getattr(merged, "failed", 0) or 0)
    log_tail = list(getattr(merged, "debug_log", []) or [])
    status = "failed" if failed > 0 and saved == 0 else "success"
    by_source = {
        k: int(getattr(v, "saved", 0) or 0)
        for k, v in (getattr(bundle, "by_source", {}) or {}).items()
    }
    for system_key, label in (
        ("policy", "完成政策/新闻采集"),
        ("meeting", "完成会议/新闻采集"),
    ):
        tid = begin_task(system_key, f"crawl_{system_key}", trigger_source=trigger_source)
        finish_task(
            tid,
            status=status,
            data_count=saved if system_key == "policy" else 0,
            message=_build_message(
                summary=f"{label}（包内新增 {saved} 条）",
                log_tail=log_tail,
                extra={"by_source": by_source},
            ),
        )


def run_tracked_sync(
    system_key: str,
    task_name: str,
    fn: Callable[[], T],
    *,
    trigger_source: str = "cron",
    action_label: str = "完成同步",
    get_data_count: Optional[Callable[[T], int]] = None,
    get_log_tail: Optional[Callable[[T], List[str]]] = None,
) -> T:
    """
    功能：包装同步函数，自动 begin/finish 写 system_tasks。
    输入：子系统标识、可调用 sync 函数、可选计数与 log 提取器。
    输出：sync 函数返回值；异常时 mark failed 后重新抛出。
    """
    task_id = begin_task(system_key, task_name, trigger_source=trigger_source)
    try:
        result = fn()
        count = get_data_count(result) if get_data_count else int(getattr(result, "saved", 0) or 0)
        log_tail = get_log_tail(result) if get_log_tail else list(getattr(result, "debug_log", []) or [])
        summary = summary_from_sync_result(result, action=action_label)
        finish_task(
            task_id,
            status="success",
            data_count=count,
            message=_build_message(summary=summary, log_tail=log_tail),
        )
        return result
    except Exception as e:
        finish_task(
            task_id,
            status="failed",
            data_count=0,
            message=_build_message(summary=f"{action_label}异常: {e}", log_tail=[str(e)]),
        )
        raise


def fetch_recent_tasks(limit: int = 20) -> List[Dict[str, Any]]:
    """最近运行记录，按 start_time 倒序。"""
    ensure_system_tasks_table()
    lim = max(1, min(int(limit), 100))
    try:
        with mysql_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, system_key, task_name, status, start_time, end_time,
                           data_count, message, trigger_source, created_at
                    FROM system_tasks
                    ORDER BY start_time DESC
                    LIMIT %s
                    """,
                    (lim,),
                )
                return list(cur.fetchall() or [])
    except Exception:
        return []


def fetch_last_success_by_system() -> Dict[str, Dict[str, Any]]:
    """各子系统最近一次 success 任务。"""
    ensure_system_tasks_table()
    out: Dict[str, Dict[str, Any]] = {}
    try:
        with mysql_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.system_key, t.start_time, t.end_time, t.data_count, t.message
                    FROM system_tasks t
                    INNER JOIN (
                      SELECT system_key, MAX(end_time) AS max_end
                      FROM system_tasks
                      WHERE status = 'success' AND end_time IS NOT NULL
                      GROUP BY system_key
                    ) latest ON t.system_key = latest.system_key AND t.end_time = latest.max_end
                    """
                )
                for row in cur.fetchall() or []:
                    out[str(row["system_key"])] = dict(row)
    except Exception:
        pass
    return out


def count_today_runs() -> int:
    """今日任务运行次数。"""
    ensure_system_tasks_table()
    try:
        with mysql_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS n FROM system_tasks
                    WHERE DATE(start_time) = CURDATE()
                    """
                )
                row = cur.fetchone()
                return int(row["n"] if row else 0)
    except Exception:
        return 0
