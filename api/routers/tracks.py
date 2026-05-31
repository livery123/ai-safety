"""
功能：专项监测三系统列表 API。

输入：分页、关键词、来源多选。
输出：PaginatedResponse / SourceFilterResponse。
上下游：api.services.portal_data。
"""

from __future__ import annotations

import math
from typing import List, Optional

from fastapi import APIRouter, Query

from api.schemas import IncidentItem, LiteratureItem, PaginatedResponse, SourceFilterResponse
from api.services import portal_data

router = APIRouter(prefix="/tracks", tags=["tracks"])


def _parse_sources(sources: Optional[List[str]] = None) -> Optional[List[str]]:
    """合并逗号分隔的 sources 查询参数。"""
    if not sources:
        return None
    out: List[str] = []
    for item in sources:
        for part in str(item).split(","):
            p = part.strip()
            if p:
                out.append(p)
    return out or None


@router.get("/policy/sources", response_model=SourceFilterResponse)
def policy_source_filters() -> SourceFilterResponse:
    """政策系统左栏来源筛选项。"""
    return portal_data.get_track_source_filters("policy")


@router.get("/meetings/sources", response_model=SourceFilterResponse)
def meeting_source_filters() -> SourceFilterResponse:
    """会议系统左栏来源筛选项。"""
    return portal_data.get_track_source_filters("meeting")


@router.get("/literature/sources", response_model=SourceFilterResponse)
def literature_source_filters() -> SourceFilterResponse:
    """文献系统左栏来源筛选项。"""
    return portal_data.get_track_source_filters("literature")


@router.get("/policy", response_model=PaginatedResponse[IncidentItem])
def policy_tracks(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    keyword: Optional[str] = Query(None),
    sources: Optional[List[str]] = Query(None),
) -> PaginatedResponse[IncidentItem]:
    """政策监管系统列表。"""
    srcs = _parse_sources(sources)
    items, total = portal_data.list_policy_tracks(
        page=page, page_size=page_size, keyword=keyword, sources=srcs
    )
    pages = max(1, math.ceil(total / page_size)) if total else 1
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size, pages=pages)


@router.get("/meetings", response_model=PaginatedResponse[IncidentItem])
def meeting_tracks(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    keyword: Optional[str] = Query(None),
    sources: Optional[List[str]] = Query(None),
) -> PaginatedResponse[IncidentItem]:
    """国际会议系统列表。"""
    srcs = _parse_sources(sources)
    items, total = portal_data.list_meeting_tracks(
        page=page, page_size=page_size, keyword=keyword, sources=srcs
    )
    pages = max(1, math.ceil(total / page_size)) if total else 1
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size, pages=pages)


@router.get("/literature", response_model=PaginatedResponse[LiteratureItem])
def literature_tracks(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    keyword: Optional[str] = Query(None),
    source: Optional[str] = Query(None, description="兼容旧单选参数"),
    sources: Optional[List[str]] = Query(None),
) -> PaginatedResponse[LiteratureItem]:
    """文献情报系统列表。"""
    srcs = _parse_sources(sources)
    items, total = portal_data.list_literature_tracks(
        page=page, page_size=page_size, keyword=keyword, source=source, sources=srcs
    )
    pages = max(1, math.ceil(total / page_size)) if total else 1
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size, pages=pages)
