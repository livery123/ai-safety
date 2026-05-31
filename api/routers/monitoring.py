"""
功能：运行监控中心 API。

输入：无（只读聚合）。
输出：MonitoringOverviewResponse。
上下游：web/ 首页 MonitoringPanel；api.services.monitoring_data。
"""

from __future__ import annotations

from fastapi import APIRouter

from api.schemas import MonitoringOverviewResponse
from api.services import monitoring_data

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/overview", response_model=MonitoringOverviewResponse)
def monitoring_overview() -> MonitoringOverviewResponse:
    """平台运行监控中心：全局状态 + 三子系统卡片 + 最近运行时间线。"""
    data = monitoring_data.get_monitoring_overview()
    return MonitoringOverviewResponse(**data)
