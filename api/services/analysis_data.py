"""
功能：监测周报 API 数据层。

输入：system_key、report id、分页 limit。
输出：Pydantic 模型或 plain dict。
上下游：api/routers/analysis.py；core/mysql_weekly_reports.py。
"""

from __future__ import annotations

from typing import List, Optional

from api.schemas import ReportContinuityResponse, WeeklyReportItem, WeeklyReportDetail
from core.mysql_weekly_reports import (
    check_report_continuity,
    get_weekly_report_by_id,
    list_weekly_reports,
)


def _iso(val) -> Optional[str]:
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def get_reports_list(
    *,
    system: Optional[str] = None,
    report_type: str = "weekly",
    limit: int = 52,
) -> List[WeeklyReportItem]:
    """监测报告列表（不含全文）。"""
    rows = list_weekly_reports(system_key=system, report_type=report_type, limit=limit)
    out: List[WeeklyReportItem] = []
    for r in rows:
        out.append(
            WeeklyReportItem(
                id=int(r["id"]),
                system_key=str(r.get("system_key") or ""),
                report_type=str(r.get("report_type") or "weekly"),
                week_start=_iso(r.get("week_start")) or "",
                week_end=_iso(r.get("week_end")) or "",
                title=str(r.get("title") or ""),
                excerpt=str(r.get("excerpt") or "").strip(),
                article_count=int(r.get("article_count") or 0),
                task_id=int(r["task_id"]) if r.get("task_id") else None,
                trigger_source=str(r.get("trigger_source") or "cron"),
                created_at=_iso(r.get("created_at")) or "",
            )
        )
    return out


def get_report_detail(report_id: int) -> Optional[WeeklyReportDetail]:
    """单条报告详情。"""
    row = get_weekly_report_by_id(report_id)
    if not row:
        return None
    ids = row.get("source_article_ids")
    if isinstance(ids, str):
        import json

        try:
            ids = json.loads(ids)
        except json.JSONDecodeError:
            ids = []
    return WeeklyReportDetail(
        id=int(row["id"]),
        system_key=str(row.get("system_key") or ""),
        report_type=str(row.get("report_type") or "weekly"),
        week_start=_iso(row.get("week_start")) or "",
        week_end=_iso(row.get("week_end")) or "",
        title=str(row.get("title") or ""),
        report_markdown=str(row.get("report_markdown") or ""),
        article_count=int(row.get("article_count") or 0),
        model_name=str(row.get("model_name") or ""),
        task_id=int(row["task_id"]) if row.get("task_id") else None,
        trigger_source=str(row.get("trigger_source") or "cron"),
        created_at=_iso(row.get("created_at")) or "",
        source_article_ids=list(ids) if isinstance(ids, list) else [],
    )


def get_continuity(system: str = "policy", report_type: str = "weekly", weeks: int = 12) -> ReportContinuityResponse:
    """验收用：最近 N 周报告连续性。"""
    d = check_report_continuity(system_key=system, report_type=report_type, weeks=weeks)
    return ReportContinuityResponse(
        system_key=d.get("system_key") or system,
        report_type=d.get("report_type") or report_type,
        expected_weeks=int(d.get("expected_weeks") or weeks),
        present_weeks=int(d.get("present_weeks") or 0),
        missing=list(d.get("missing") or []),
        last_report_id=int(d["last_report_id"]) if d.get("last_report_id") else None,
        last_week_start=d.get("last_week_start"),
        last_generated_at=d.get("last_generated_at"),
    )
