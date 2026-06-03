"""
功能：平台统计与关键词接口。

输入：Query limit。
输出：StatsResponse / KeywordItem 列表。
上下游：api.services.portal_data。
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Query

from api.schemas import KeywordItem, PolicyAnalyticsResponse, StatsResponse, SystemInfo, WeeklySummaryResponse
from api.services import portal_data

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=StatsResponse)
def get_stats() -> StatsResponse:
    """平台汇总指标。"""
    return portal_data.get_stats()


@router.get("/keywords", response_model=List[KeywordItem])
def get_keywords(limit: int = Query(20, ge=1, le=60)) -> List[KeywordItem]:
    """高频关键词。"""
    return portal_data.get_keywords(limit)


@router.get("/policy/analytics", response_model=PolicyAnalyticsResponse)
def get_policy_analytics(
    country_limit: int = Query(12, ge=3, le=30),
    word_limit: int = Query(40, ge=10, le=120),
    word_field: str = Query("mixed", description="authority|tags|intl|mixed"),
    week_limit: int = Query(16, ge=4, le=52),
) -> PolicyAnalyticsResponse:
    """政策可视化：覆盖度、国家分布、周趋势、词云。"""
    return portal_data.get_policy_analytics(
        country_limit=country_limit,
        word_limit=word_limit,
        word_field=word_field,
        week_limit=week_limit,
    )


@router.get("/systems", response_model=List[SystemInfo])
def get_systems() -> List[SystemInfo]:
    """三大子系统 Hub 信息。"""
    return portal_data.get_systems_hub()


@router.get("/weekly/{system}", response_model=WeeklySummaryResponse)
def get_weekly_summary(system: str) -> WeeklySummaryResponse:
    """单系统本周摘要；system=policy|meeting|literature。"""
    return portal_data.get_weekly_summary(system)
