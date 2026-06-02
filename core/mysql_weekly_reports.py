"""
监测周报 / 简报持久化（MySQL monitoring_weekly_reports）。

功能：建表、按周幂等写入、列表与详情查询、连续性检查。
输入：报告元数据与 Markdown 正文。
输出：report id；查询 dict 列表。
上下游：engine/weekly_report.py、scripts/generate_weekly_report.py、api/services/analysis_data.py。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.mysql_db import mysql_conn

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS monitoring_weekly_reports (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  system_key VARCHAR(32) NOT NULL,
  report_type VARCHAR(16) NOT NULL DEFAULT 'weekly',
  week_start DATE NOT NULL,
  week_end DATE NOT NULL,
  title VARCHAR(512) NOT NULL DEFAULT '',
  report_markdown MEDIUMTEXT NOT NULL,
  source_article_ids JSON NULL,
  article_count INT NOT NULL DEFAULT 0,
  model_name VARCHAR(64) NOT NULL DEFAULT '',
  task_id BIGINT UNSIGNED NULL,
  trigger_source VARCHAR(32) NOT NULL DEFAULT 'cron',
  status ENUM('success', 'failed', 'pending') NOT NULL DEFAULT 'success',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_system_report_week (system_key, report_type, week_start),
  KEY idx_week_start (week_start),
  KEY idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def ensure_monitoring_weekly_reports_table() -> None:
    """幂等建表。"""
    try:
        with mysql_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE_TABLE_SQL)
    except Exception:
        pass


def compute_week_range(
    *,
    week_start: Optional[date] = None,
    anchor: Optional[date] = None,
) -> Tuple[date, date]:
    """
    功能：计算监测周起止（周一～周日，含首尾）。
    输入：week_start 指定则以其为周一；否则取 anchor 所在周的**上一完整自然周**。
    输出：(week_start, week_end)。
    """
    today = anchor or date.today()
    if week_start is not None:
        ws = week_start
    else:
        # 上一完整自然周：本周一减 7 天为上周一
        this_monday = today - timedelta(days=today.weekday())
        ws = this_monday - timedelta(days=7)
    we = ws + timedelta(days=6)
    return ws, we


def get_report_by_week(
    system_key: str,
    report_type: str,
    week_start: date,
) -> Optional[Dict[str, Any]]:
    """按系统+类型+周起始查是否已有报告（幂等）。"""
    ensure_monitoring_weekly_reports_table()
    try:
        with mysql_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, system_key, report_type, week_start, week_end, title,
                           report_markdown, source_article_ids, article_count,
                           model_name, task_id, trigger_source, status, created_at
                    FROM monitoring_weekly_reports
                    WHERE system_key = %s AND report_type = %s AND week_start = %s
                    LIMIT 1
                    """,
                    (system_key, report_type, week_start.isoformat()),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception:
        return None


def save_weekly_report(
    *,
    system_key: str,
    report_type: str,
    week_start: date,
    week_end: date,
    title: str,
    report_markdown: str,
    source_article_ids: Optional[List[int]] = None,
    article_count: int = 0,
    model_name: str = "",
    task_id: Optional[int] = None,
    trigger_source: str = "cron",
    status: str = "success",
) -> int:
    """
    功能：插入或更新监测报告（同 system+type+week_start 唯一）。
    输出：report id。
    """
    ensure_monitoring_weekly_reports_table()
    ids_json = json.dumps(source_article_ids or [], ensure_ascii=False)
    st = status if status in ("success", "failed", "pending") else "success"
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monitoring_weekly_reports (
                    system_key, report_type, week_start, week_end, title,
                    report_markdown, source_article_ids, article_count,
                    model_name, task_id, trigger_source, status
                ) VALUES (%s, %s, %s, %s, %s, %s, CAST(%s AS JSON), %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    week_end = VALUES(week_end),
                    title = VALUES(title),
                    report_markdown = VALUES(report_markdown),
                    source_article_ids = VALUES(source_article_ids),
                    article_count = VALUES(article_count),
                    model_name = VALUES(model_name),
                    task_id = VALUES(task_id),
                    trigger_source = VALUES(trigger_source),
                    status = VALUES(status)
                """,
                (
                    system_key,
                    report_type,
                    week_start.isoformat(),
                    week_end.isoformat(),
                    (title or "")[:512],
                    (report_markdown or "").strip(),
                    ids_json,
                    int(article_count),
                    (model_name or "")[:64],
                    task_id,
                    trigger_source,
                    st,
                ),
            )
            cur.execute(
                """
                SELECT id FROM monitoring_weekly_reports
                WHERE system_key = %s AND report_type = %s AND week_start = %s
                LIMIT 1
                """,
                (system_key, report_type, week_start.isoformat()),
            )
            row = cur.fetchone()
            return int(row["id"]) if row else int(cur.lastrowid)


def list_weekly_reports(
    *,
    system_key: Optional[str] = None,
    report_type: str = "weekly",
    limit: int = 52,
) -> List[Dict[str, Any]]:
    """列表：按 week_start 倒序，不含全文（excerpt）。"""
    ensure_monitoring_weekly_reports_table()
    lim = max(1, min(int(limit), 200))
    wheres = ["report_type = %s", "status = 'success'"]
    params: List[Any] = [report_type]
    if system_key:
        wheres.append("system_key = %s")
        params.append(system_key)
    sql = f"""
        SELECT id, system_key, report_type, week_start, week_end, title,
               article_count, model_name, task_id, trigger_source, created_at,
               LEFT(report_markdown, 320) AS excerpt
        FROM monitoring_weekly_reports
        WHERE {" AND ".join(wheres)}
        ORDER BY week_start DESC, system_key ASC
        LIMIT %s
    """
    params.append(lim)
    try:
        with mysql_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return [dict(r) for r in (cur.fetchall() or [])]
    except Exception:
        return []


def get_weekly_report_by_id(report_id: int) -> Optional[Dict[str, Any]]:
    """单条详情（含全文）。"""
    ensure_monitoring_weekly_reports_table()
    rid = int(report_id)
    if rid <= 0:
        return None
    try:
        with mysql_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, system_key, report_type, week_start, week_end, title,
                           report_markdown, source_article_ids, article_count,
                           model_name, task_id, trigger_source, status, created_at
                    FROM monitoring_weekly_reports WHERE id = %s LIMIT 1
                    """,
                    (rid,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception:
        return None


def check_report_continuity(
    *,
    system_key: str = "policy",
    report_type: str = "weekly",
    weeks: int = 12,
) -> Dict[str, Any]:
    """
    功能：检查最近 N 个自然周是否均有 success 报告（验收辅助）。
    输出：expected_weeks、present_weeks、missing 列表。
    """
    ensure_monitoring_weekly_reports_table()
    n = max(1, min(int(weeks), 52))
    expected: List[str] = []
    ws, _ = compute_week_range()
    cur = ws
    for _ in range(n):
        expected.append(cur.isoformat())
        cur = cur - timedelta(days=7)
    expected.reverse()

    try:
        with mysql_conn() as conn:
            with conn.cursor() as cur_db:
                cur_db.execute(
                    """
                    SELECT week_start FROM monitoring_weekly_reports
                    WHERE system_key = %s AND report_type = %s AND status = 'success'
                      AND week_start >= %s
                    """,
                    (system_key, report_type, expected[0]),
                )
                present = {
                    str(r["week_start"])[:10]
                    for r in (cur_db.fetchall() or [])
                }
    except Exception:
        present = set()

    missing = [w for w in expected if w not in present]
    last_row = list_weekly_reports(system_key=system_key, report_type=report_type, limit=1)
    last = last_row[0] if last_row else None
    return {
        "system_key": system_key,
        "report_type": report_type,
        "expected_weeks": n,
        "present_weeks": n - len(missing),
        "missing": missing,
        "last_report_id": last.get("id") if last else None,
        "last_week_start": str(last.get("week_start"))[:10] if last else None,
        "last_generated_at": str(last.get("created_at")) if last else None,
    }
