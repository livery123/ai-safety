"""
功能：将 meeting 类文章归并到 meeting_events，标注会前/会中/会后阶段。
输入：article_id、标题/摘要/抽取字段、发布时间。
输出：event_id 或 None；副作用写 meeting_event_articles 与 extraction 会议列。
上下游：crawler/orchestrator._persist_mysql_phase1、scripts/link_meeting_articles.py。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from core.config import MEETING_DISCOVERY_MIN_SCORE
from core.meeting_catalog import (
    CatalogMatch,
    find_best_catalog_match,
    find_seed_event,
    get_series_by_key,
    match_catalog_key,
)
from core.mysql_meeting_events import insert_discovery_candidate
from core.mysql_meeting_events import (
    get_event_by_id,
    get_or_create_event,
    infer_phase_from_dates,
    link_article_to_event,
    update_extraction_meeting_fields,
)


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def link_article_to_meeting_event(
    article_id: int,
    *,
    title: str = "",
    summary: str = "",
    published_at: Optional[datetime] = None,
    extraction: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """
    功能：匹配名录并关联到 meeting_events。
    输入：article_id 与文章/抽取字段。
    输出：event_id；无法匹配 major 会议时返回 None。
    """
    ext = extraction or {}
    if str(ext.get("content_type") or "").strip() != "meeting":
        return None

    tags = ext.get("tags") if isinstance(ext.get("tags"), list) else []
    entities = ext.get("entities") if isinstance(ext.get("entities"), list) else []

    match: Optional[CatalogMatch] = match_catalog_key(
        title=title,
        summary=summary,
        main_topic=str(ext.get("main_topic") or ""),
        tags=tags,
        entities=entities,
        llm_catalog_key=str(ext.get("meeting_catalog_key") or ""),
        edition_hint=str(ext.get("meeting_edition_hint") or ""),
    )
    if not match:
        proposed = str(ext.get("proposed_series_name") or "").strip()
        best = find_best_catalog_match(
            title=title,
            summary=summary,
            main_topic=str(ext.get("main_topic") or ""),
            tags=tags,
            entities=entities,
            llm_catalog_key=str(ext.get("meeting_catalog_key") or ""),
            edition_hint=proposed or str(ext.get("meeting_edition_hint") or ""),
        )
        score = float(best.score) if best else 0.0
        if proposed or score >= MEETING_DISCOVERY_MIN_SCORE:
            try:
                insert_discovery_candidate(
                    article_id=article_id,
                    title=title[:512],
                    proposed_series_name=proposed,
                    meeting_catalog_key=str(best.catalog_key if best else ext.get("meeting_catalog_key") or ""),
                    match_score=score,
                    reason="linker_no_event_match",
                )
            except Exception:
                pass
        return None

    series = get_series_by_key(match.catalog_key)
    if not series:
        return None

    seed = find_seed_event(series, match.edition_year)
    edition_label = seed.edition_label if seed else series.series_name
    edition_year = match.edition_year if match.edition_year is not None else (seed.edition_year if seed else None)

    event_id = get_or_create_event(
        catalog_key=match.catalog_key,
        edition_label=edition_label,
        edition_year=edition_year,
        start_date=seed.start_date if seed else None,
        end_date=seed.end_date if seed else None,
        location=seed.location if seed else "",
        host=seed.host if seed else "",
        official_url=seed.official_url if seed else "",
        status=seed.status if seed else "unknown",
        notes=seed.notes if seed else "",
    )

    ev_row = get_event_by_id(event_id) or {}
    phase = str(ext.get("meeting_phase") or "").strip().lower()
    if phase not in ("pre", "during", "post", "unknown"):
        phase = infer_phase_from_dates(
            published_at,
            _parse_date(str(ev_row.get("start_date") or "")),
            _parse_date(str(ev_row.get("end_date") or "")),
        )

    method = "llm" if str(ext.get("meeting_catalog_key") or "").strip() else "rule"
    link_article_to_event(
        event_id=event_id,
        article_id=article_id,
        phase=phase,
        link_score=min(1.0, float(match.score)),
        link_method=method,
    )
    update_extraction_meeting_fields(article_id, catalog_key=match.catalog_key, phase=phase)
    return event_id


def batch_link_meeting_articles(
    *,
    limit: int = 500,
    offset: int = 0,
    only_unlinked: bool = True,
) -> Dict[str, int]:
    """
    功能：批量关联存量 meeting 文章。
    输出：{"linked": n, "skipped": m}。
    """
    from core.mysql_meeting_events import fetch_meeting_articles_for_linking

    rows = fetch_meeting_articles_for_linking(
        limit=limit, offset=offset, only_unlinked=only_unlinked
    )
    linked = 0
    skipped = 0
    for r in rows:
        ext = {
            "content_type": "meeting",
            "main_topic": r.get("main_topic"),
            "tags": r.get("tags_raw") or [],
            "entities": r.get("entities_json") or [],
            "meeting_catalog_key": r.get("meeting_catalog_key"),
            "meeting_edition_hint": "",
            "meeting_phase": r.get("meeting_phase"),
        }
        eid = link_article_to_meeting_event(
            int(r["article_id"]),
            title=str(r.get("title_raw") or ""),
            summary=str(r.get("summary_raw") or ""),
            published_at=r.get("published_at"),
            extraction=ext,
        )
        if eid:
            linked += 1
        else:
            skipped += 1
    return {"linked": linked, "skipped": skipped}
