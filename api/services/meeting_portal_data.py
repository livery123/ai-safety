"""
功能：会议事件流门户数据组装。
输入：event_id、筛选参数。
输出：Pydantic 友好 dict。
上下游：api/routers/meetings.py。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from core.meeting_catalog import load_catalog_series
from core.mysql_meeting_events import (
    count_events,
    fetch_event_timeline,
    get_event_by_id,
    get_latest_analysis,
    list_events,
)


def _str_date(v: Any) -> Optional[str]:
    if v is None:
        return None
    return str(v)[:10]


def get_meeting_catalog() -> List[Dict[str, Any]]:
    """名录 + 各系列下事件摘要。"""
    series_list = load_catalog_series()
    ev_rows = list_events(limit=200, major_only=False)
    by_key: Dict[str, List[Dict[str, Any]]] = {}
    for ev in ev_rows:
        ck = str(ev.get("catalog_key") or "")
        by_key.setdefault(ck, []).append(ev)

    out: List[Dict[str, Any]] = []
    for s in series_list:
        events = []
        for ev in by_key.get(s.catalog_key, []):
            aid = int(ev.get("id") or 0)
            events.append(
                {
                    "id": aid,
                    "catalog_key": s.catalog_key,
                    "series_name": s.series_name,
                    "edition_label": str(ev.get("edition_label") or ""),
                    "edition_year": ev.get("edition_year"),
                    "start_date": _str_date(ev.get("start_date")),
                    "end_date": _str_date(ev.get("end_date")),
                    "location": str(ev.get("location") or ""),
                    "host": str(ev.get("host") or ""),
                    "status": str(ev.get("status") or ""),
                    "article_count": int(ev.get("article_count") or 0),
                    "has_analysis": get_latest_analysis(aid) is not None,
                }
            )
        if not events:
            for seed in s.events:
                events.append(
                    {
                        "id": 0,
                        "catalog_key": s.catalog_key,
                        "series_name": s.series_name,
                        "edition_label": seed.edition_label,
                        "edition_year": seed.edition_year,
                        "start_date": seed.start_date,
                        "end_date": seed.end_date,
                        "location": seed.location,
                        "host": seed.host,
                        "status": seed.status,
                        "article_count": 0,
                        "has_analysis": False,
                    }
                )
        out.append(
            {
                "catalog_key": s.catalog_key,
                "series_name": s.series_name,
                "category": s.category,
                "is_major": s.is_major,
                "aliases": s.aliases,
                "topics": s.topics,
                "official_urls": s.official_urls,
                "events": events,
            }
        )
    return out


def list_meeting_events(
    *,
    catalog_key: Optional[str] = None,
    major_only: bool = True,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[Dict[str, Any]], int]:
    offset = (page - 1) * page_size
    total = count_events(catalog_key=catalog_key, major_only=major_only)
    rows = list_events(
        catalog_key=catalog_key,
        major_only=major_only,
        limit=page_size,
        offset=offset,
    )
    items = []
    for ev in rows:
        eid = int(ev.get("id") or 0)
        items.append(
            {
                "id": eid,
                "catalog_key": str(ev.get("catalog_key") or ""),
                "series_name": str(ev.get("series_name") or ""),
                "edition_label": str(ev.get("edition_label") or ""),
                "edition_year": ev.get("edition_year"),
                "start_date": _str_date(ev.get("start_date")),
                "end_date": _str_date(ev.get("end_date")),
                "location": str(ev.get("location") or ""),
                "host": str(ev.get("host") or ""),
                "status": str(ev.get("status") or ""),
                "article_count": int(ev.get("article_count") or 0),
                "has_analysis": get_latest_analysis(eid) is not None,
            }
        )
    return items, total


def get_meeting_event_detail(event_id: int) -> Optional[Dict[str, Any]]:
    ev = get_event_by_id(event_id)
    if not ev:
        return None
    countries = ev.get("countries_json") or []
    if isinstance(countries, str):
        try:
            countries = json.loads(countries)
        except json.JSONDecodeError:
            countries = []
    analysis = get_latest_analysis(event_id)
    timeline = fetch_event_timeline(event_id)
    article_count = sum(len(v) for v in timeline.values())
    summary = {
        "id": int(ev.get("id") or 0),
        "catalog_key": str(ev.get("catalog_key") or ""),
        "series_name": str(ev.get("series_name") or ""),
        "edition_label": str(ev.get("edition_label") or ""),
        "edition_year": ev.get("edition_year"),
        "start_date": _str_date(ev.get("start_date")),
        "end_date": _str_date(ev.get("end_date")),
        "location": str(ev.get("location") or ""),
        "host": str(ev.get("host") or ""),
        "status": str(ev.get("status") or ""),
        "article_count": article_count,
        "has_analysis": analysis is not None,
    }
    return {
        "event": summary,
        "countries": countries if isinstance(countries, list) else [],
        "official_url": str(ev.get("official_url") or ""),
        "notes": str(ev.get("notes") or ""),
        "analysis_markdown": str((analysis or {}).get("analysis_markdown") or ""),
        "analysis_generated_at": str((analysis or {}).get("generated_at") or "") or None,
    }


def get_meeting_timeline(event_id: int) -> Dict[str, Any]:
    buckets = fetch_event_timeline(event_id)

    def _map_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for r in rows:
            out.append(
                {
                    "article_id": int(r.get("article_id") or 0),
                    "title": str(r.get("title_raw") or ""),
                    "summary": str(r.get("summary_structured") or r.get("summary_raw") or ""),
                    "source": str(r.get("source") or ""),
                    "url": str(r.get("normalized_url") or ""),
                    "published_at": str(r.get("published_at") or "") or None,
                    "phase": str(r.get("phase") or "unknown"),
                }
            )
        return out

    return {
        "event_id": event_id,
        "pre": _map_rows(buckets.get("pre") or []),
        "during": _map_rows(buckets.get("during") or []),
        "post": _map_rows(buckets.get("post") or []),
        "unknown": _map_rows(buckets.get("unknown") or []),
    }
