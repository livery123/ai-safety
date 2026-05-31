"""
功能：情报/新闻列表 API。

输入：分页与筛选 Query。
输出：PaginatedResponse[IncidentItem]。
上下游：api.services.portal_data。
"""

from __future__ import annotations

import math
from typing import Optional

from fastapi import APIRouter, Query

from api.schemas import IncidentItem, PaginatedResponse
from api.services import portal_data

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("/latest", response_model=list[IncidentItem])
def latest_incidents(limit: int = Query(12, ge=1, le=50)) -> list[IncidentItem]:
    """首页最新动态。"""
    return portal_data.get_latest_incidents(limit)


@router.get("", response_model=PaginatedResponse[IncidentItem])
def list_incidents(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    keyword: Optional[str] = Query(None),
    risk_domain: Optional[str] = Query(None),
    content_type: Optional[str] = Query(None),
) -> PaginatedResponse[IncidentItem]:
    """情报分页检索。"""
    items, total = portal_data.list_incidents(
        page=page,
        page_size=page_size,
        keyword=keyword,
        risk_domain=risk_domain,
        content_type=content_type,
    )
    pages = max(1, math.ceil(total / page_size)) if total else 1
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )
