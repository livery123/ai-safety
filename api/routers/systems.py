"""
功能：三大子系统 Hub 与本周摘要 API。

输入：system key path。
输出：SystemInfo 列表 / WeeklySummaryResponse。
上下游：api.services.portal_data。
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from api.schemas import SystemInfo, WeeklySummaryResponse
from api.services import portal_data

router = APIRouter(prefix="/systems", tags=["systems"])

_VALID = frozenset({"policy", "meeting", "literature"})


@router.get("", response_model=List[SystemInfo])
def list_systems() -> List[SystemInfo]:
    """三大监测系统 Hub 卡片数据。"""
    return portal_data.get_systems_hub()


@router.get("/{system}/weekly", response_model=WeeklySummaryResponse)
def weekly_summary(system: str) -> WeeklySummaryResponse:
    """单系统本周监测摘要。"""
    key = system.strip().lower()
    if key not in _VALID:
        raise HTTPException(status_code=404, detail=f"未知系统: {system}")
    return portal_data.get_weekly_summary(key)
