"""
功能：会议事件流 API（名录、事件、时间线、专题分析重生成）。
输入：路径/查询参数。
输出：Meeting* 响应模型。
上下游：api.services.meeting_portal_data、engine.meeting_brief。
"""

from __future__ import annotations

import math
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from api.schemas import (
    MeetingBriefRegenerateResponse,
    MeetingCatalogItem,
    MeetingEventDetailResponse,
    MeetingEventSummary,
    MeetingTimelineResponse,
    PaginatedResponse,
)
from api.services import meeting_portal_data
from core.config import LLM_MODEL
from core.mysql_meeting_events import save_event_analysis
from engine.meeting_brief import generate_meeting_brief_markdown

router = APIRouter(prefix="/meetings", tags=["meetings"])


@router.get("/catalog", response_model=List[MeetingCatalogItem])
def meeting_catalog() -> List[MeetingCatalogItem]:
    """重大国际会议名录（含各届事件摘要）。"""
    return [MeetingCatalogItem.model_validate(x) for x in meeting_portal_data.get_meeting_catalog()]


@router.get("/events", response_model=PaginatedResponse[MeetingEventSummary])
def meeting_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    catalog_key: Optional[str] = Query(None),
    major_only: bool = Query(True),
) -> PaginatedResponse[MeetingEventSummary]:
    """会议事件列表。"""
    items, total = meeting_portal_data.list_meeting_events(
        catalog_key=catalog_key,
        major_only=major_only,
        page=page,
        page_size=page_size,
    )
    pages = max(1, math.ceil(total / page_size)) if total else 1
    return PaginatedResponse(
        items=[MeetingEventSummary.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get("/events/{event_id}", response_model=MeetingEventDetailResponse)
def meeting_event_detail(event_id: int) -> MeetingEventDetailResponse:
    """会议事件详情与最新专题分析。"""
    detail = meeting_portal_data.get_meeting_event_detail(event_id)
    if not detail:
        raise HTTPException(status_code=404, detail="event not found")
    return MeetingEventDetailResponse.model_validate(detail)


@router.get("/events/{event_id}/timeline", response_model=MeetingTimelineResponse)
def meeting_event_timeline(event_id: int) -> MeetingTimelineResponse:
    """会前/会中/会后事件流。"""
    if not meeting_portal_data.get_meeting_event_detail(event_id):
        raise HTTPException(status_code=404, detail="event not found")
    return MeetingTimelineResponse.model_validate(
        meeting_portal_data.get_meeting_timeline(event_id)
    )


@router.post(
    "/events/{event_id}/regenerate-analysis",
    response_model=MeetingBriefRegenerateResponse,
)
def regenerate_meeting_analysis(event_id: int) -> MeetingBriefRegenerateResponse:
    """手动重生成会议专题分析。"""
    if not meeting_portal_data.get_meeting_event_detail(event_id):
        raise HTTPException(status_code=404, detail="event not found")
    md = generate_meeting_brief_markdown(event_id)
    aid = save_event_analysis(event_id, md, model_name=LLM_MODEL)
    return MeetingBriefRegenerateResponse(
        event_id=event_id, analysis_id=aid, message="analysis regenerated"
    )
