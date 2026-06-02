"""
功能：监测周报 / 简报只读 API。

输入：system、report_type、report id。
输出：WeeklyReportItem 列表 / WeeklyReportDetail。
上下游：web/lib/api.ts；api/services/analysis_data.py。
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from api.schemas import ReportContinuityResponse, WeeklyReportDetail, WeeklyReportItem
from api.services import analysis_data

router = APIRouter(prefix="/analysis", tags=["analysis"])

_VALID_SYSTEMS = frozenset({"policy", "meeting", "literature", "platform", ""})


@router.get("/reports/weekly", response_model=List[WeeklyReportItem])
def list_weekly_reports(
    system: Optional[str] = Query(None, description="policy|meeting|literature|platform"),
    report_type: str = Query("weekly", description="weekly|brief"),
    limit: int = Query(52, ge=1, le=200),
) -> List[WeeklyReportItem]:
    """监测报告历史列表（按周倒序）。"""
    if system and system.strip().lower() not in _VALID_SYSTEMS - {""}:
        raise HTTPException(status_code=400, detail=f"未知系统: {system}")
    key = system.strip().lower() if system else None
    return analysis_data.get_reports_list(system=key, report_type=report_type, limit=limit)


@router.get("/reports/weekly/{report_id}", response_model=WeeklyReportDetail)
def get_weekly_report(report_id: int) -> WeeklyReportDetail:
    """单条监测报告全文。"""
    detail = analysis_data.get_report_detail(report_id)
    if not detail:
        raise HTTPException(status_code=404, detail="报告不存在")
    return detail


@router.get("/reports/continuity", response_model=ReportContinuityResponse)
def report_continuity(
    system: str = Query("policy"),
    report_type: str = Query("weekly"),
    weeks: int = Query(12, ge=1, le=52),
) -> ReportContinuityResponse:
    """验收辅助：最近 N 周是否均有 success 报告。"""
    return analysis_data.get_continuity(system=system, report_type=report_type, weeks=weeks)
