"""
功能：会议事件流 MySQL 读写（名录、事件、文章关联、专题分析）。
输入：catalog_key、event_id、article_id 等。
输出：dict 行或列表；副作用 INSERT/UPDATE。
上下游：meeting_event_linker、api/services/portal_data、engine/meeting_brief。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.mysql_db import mysql_conn, _ensure_json_array


def ensure_meeting_tables() -> None:
    """提示：请先执行 scripts/migrate_meeting_events.py。"""
    raise RuntimeError("请先执行 scripts/migrate_meeting_events.py 创建会议事件流表")


def upsert_catalog_row(
    *,
    catalog_key: str,
    series_name: str,
    category: str = "",
    aliases: List[str],
    topics: List[str],
    is_major: bool = True,
    official_urls: List[str],
    reference_url: str = "",
    sort_order: int = 0,
) -> int:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conference_catalog (
                    catalog_key, series_name, category, aliases_json, topics_json,
                    is_major, official_urls_json, reference_url, sort_order
                ) VALUES (%s, %s, %s, CAST(%s AS JSON), CAST(%s AS JSON), %s, CAST(%s AS JSON), %s, %s)
                ON DUPLICATE KEY UPDATE
                    series_name = VALUES(series_name),
                    category = VALUES(category),
                    aliases_json = VALUES(aliases_json),
                    topics_json = VALUES(topics_json),
                    is_major = VALUES(is_major),
                    official_urls_json = VALUES(official_urls_json),
                    reference_url = VALUES(reference_url),
                    sort_order = VALUES(sort_order),
                    id = LAST_INSERT_ID(id)
                """,
                (
                    catalog_key,
                    series_name,
                    category,
                    _ensure_json_array(aliases),
                    _ensure_json_array(topics),
                    1 if is_major else 0,
                    _ensure_json_array(official_urls),
                    reference_url,
                    sort_order,
                ),
            )
            return int(cur.lastrowid)


def get_or_create_event(
    *,
    catalog_key: str,
    edition_label: str,
    edition_year: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    location: str = "",
    host: str = "",
    countries: Optional[List[str]] = None,
    official_url: str = "",
    status: str = "scheduled",
    notes: str = "",
) -> int:
    """
    功能：按 catalog_key + edition_year 查找或创建 meeting_events。
    输出：event_id。
    """
    countries_json = _ensure_json_array(countries or [])
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            if edition_year is not None:
                cur.execute(
                    """
                    SELECT id FROM meeting_events
                    WHERE catalog_key = %s AND edition_year = %s
                    LIMIT 1
                    """,
                    (catalog_key, edition_year),
                )
            else:
                cur.execute(
                    """
                    SELECT id FROM meeting_events
                    WHERE catalog_key = %s AND edition_label = %s
                    LIMIT 1
                    """,
                    (catalog_key, edition_label[:256]),
                )
            row = cur.fetchone()
            if row:
                eid = int(row["id"])
                cur.execute(
                    """
                    UPDATE meeting_events SET
                        edition_label = %s,
                        start_date = %s,
                        end_date = %s,
                        location = %s,
                        host = %s,
                        countries_json = CAST(%s AS JSON),
                        official_url = %s,
                        status = %s,
                        notes = %s
                    WHERE id = %s
                    """,
                    (
                        edition_label[:256],
                        start_date or None,
                        end_date or None,
                        location[:256],
                        host[:256],
                        countries_json,
                        official_url[:1024],
                        status[:32],
                        notes or None,
                        eid,
                    ),
                )
                return eid

            cur.execute(
                """
                INSERT INTO meeting_events (
                    catalog_key, edition_label, edition_year,
                    start_date, end_date, location, host,
                    countries_json, official_url, status, notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON), %s, %s, %s)
                """,
                (
                    catalog_key,
                    edition_label[:256],
                    edition_year,
                    start_date or None,
                    end_date or None,
                    location[:256],
                    host[:256],
                    countries_json,
                    official_url[:1024],
                    status[:32],
                    notes or None,
                ),
            )
            return int(cur.lastrowid)


def link_article_to_event(
    *,
    event_id: int,
    article_id: int,
    phase: str = "unknown",
    link_score: float = 0.0,
    link_method: str = "rule",
) -> int:
    ph = phase if phase in ("pre", "during", "post", "unknown") else "unknown"
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO meeting_event_articles (
                    event_id, article_id, phase, link_score, link_method
                ) VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    phase = VALUES(phase),
                    link_score = GREATEST(link_score, VALUES(link_score)),
                    link_method = VALUES(link_method),
                    id = LAST_INSERT_ID(id)
                """,
                (event_id, article_id, ph, link_score, link_method[:32]),
            )
            return int(cur.lastrowid)


def update_extraction_meeting_fields(
    article_id: int,
    *,
    catalog_key: Optional[str] = None,
    phase: Optional[str] = None,
) -> None:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE article_extractions
                SET meeting_catalog_key = %s, meeting_phase = %s
                WHERE article_id = %s
                """,
                (
                    (catalog_key or "")[:64] or None,
                    (phase or "")[:16] or None,
                    article_id,
                ),
            )


def infer_phase_from_dates(
    published_at: Optional[datetime],
    start_date: Optional[date],
    end_date: Optional[date],
    *,
    pre_days: int = 7,
    post_days: int = 7,
) -> str:
    """根据发布时间推断会前/会中/会后。"""
    if not published_at or not start_date:
        return "unknown"
    pub = published_at.date() if isinstance(published_at, datetime) else published_at
    pre_start = start_date - timedelta(days=pre_days)
    end = end_date or start_date
    post_end = end + timedelta(days=post_days)
    if pub < pre_start:
        return "unknown"
    if pub < start_date:
        return "pre"
    if pub <= end:
        return "during"
    if pub <= post_end:
        return "post"
    return "post"


def list_catalog_from_db() -> List[Dict[str, Any]]:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT catalog_key, series_name, category, aliases_json, topics_json,
                       is_major, official_urls_json, reference_url, sort_order
                FROM conference_catalog
                ORDER BY sort_order ASC, catalog_key ASC
                """
            )
            rows = cur.fetchall() or []
    for r in rows:
        for k in ("aliases_json", "topics_json", "official_urls_json"):
            if isinstance(r.get(k), str):
                try:
                    r[k] = json.loads(r[k])
                except json.JSONDecodeError:
                    r[k] = []
    return rows


def count_events(
    *,
    catalog_key: Optional[str] = None,
    major_only: bool = False,
) -> int:
    clauses = ["1=1"]
    binds: List[Any] = []
    if catalog_key:
        clauses.append("e.catalog_key = %s")
        binds.append(catalog_key)
    if major_only:
        clauses.append("c.is_major = 1")
    where = " AND ".join(clauses)
    sql = f"""
        SELECT COUNT(*) AS c FROM meeting_events e
        LEFT JOIN conference_catalog c ON c.catalog_key = e.catalog_key
        WHERE {where}
    """
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, binds)
            row = cur.fetchone()
            return int((row or {}).get("c") or 0)


def list_events_for_news_sync(
    *,
    catalog_key: Optional[str] = None,
    event_id: Optional[int] = None,
    recent_only: bool = False,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """
    功能：列出待按届检索新闻的 meeting_events（含 series_name）。
    输入：catalog_key/event_id 筛选；recent_only 仅近/近期会期。
    输出：行 dict 列表。
    """
    clauses = ["1=1"]
    binds: List[Any] = []
    if event_id:
        clauses.append("e.id = %s")
        binds.append(event_id)
    if catalog_key:
        clauses.append("e.catalog_key = %s")
        binds.append(catalog_key)
    if recent_only:
        clauses.append(
            """(
                e.status = 'scheduled'
                OR (e.start_date IS NOT NULL AND e.start_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY))
                OR (e.end_date IS NOT NULL AND e.end_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY))
                OR (e.start_date IS NOT NULL AND e.start_date <= DATE_ADD(CURDATE(), INTERVAL %s DAY))
            )"""
        )
        from core.config import (
            MEETING_NEWS_RECENT_FUTURE_DAYS,
            MEETING_NEWS_RECENT_PAST_DAYS,
        )

        binds.extend(
            [
                MEETING_NEWS_RECENT_PAST_DAYS,
                MEETING_NEWS_RECENT_PAST_DAYS,
                MEETING_NEWS_RECENT_FUTURE_DAYS,
            ]
        )
    where = " AND ".join(clauses)
    sql = f"""
        SELECT e.*, c.series_name, c.aliases_json
        FROM meeting_events e
        LEFT JOIN conference_catalog c ON c.catalog_key = e.catalog_key
        WHERE {where}
        ORDER BY e.start_date DESC, e.id DESC
        LIMIT %s
    """
    binds.append(limit)
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, binds)
            rows = list(cur.fetchall() or [])
    for r in rows:
        if isinstance(r.get("aliases_json"), str):
            try:
                r["aliases_json"] = json.loads(r["aliases_json"])
            except json.JSONDecodeError:
                r["aliases_json"] = []
    return rows


def list_events(
    *,
    catalog_key: Optional[str] = None,
    major_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    clauses = ["1=1"]
    binds: List[Any] = []
    if catalog_key:
        clauses.append("e.catalog_key = %s")
        binds.append(catalog_key)
    if major_only:
        clauses.append("c.is_major = 1")
    where = " AND ".join(clauses)
    sql = f"""
        SELECT e.*, c.series_name, c.category, c.is_major,
               (SELECT COUNT(*) FROM meeting_event_articles m WHERE m.event_id = e.id) AS article_count
        FROM meeting_events e
        LEFT JOIN conference_catalog c ON c.catalog_key = e.catalog_key
        WHERE {where}
        ORDER BY e.start_date DESC, e.id DESC
        LIMIT %s OFFSET %s
    """
    binds.extend([limit, offset])
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, binds)
            return list(cur.fetchall() or [])


def get_event_by_id(event_id: int) -> Optional[Dict[str, Any]]:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.*, c.series_name, c.category, c.is_major, c.aliases_json, c.topics_json
                FROM meeting_events e
                LEFT JOIN conference_catalog c ON c.catalog_key = e.catalog_key
                WHERE e.id = %s LIMIT 1
                """,
                (event_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    for k in ("countries_json", "aliases_json", "topics_json"):
        if isinstance(row.get(k), str):
            try:
                row[k] = json.loads(row[k])
            except json.JSONDecodeError:
                row[k] = []
    return row


def get_latest_analysis(event_id: int) -> Optional[Dict[str, Any]]:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, event_id, analysis_markdown, structured_json, model_name, generated_at
                FROM meeting_event_analyses
                WHERE event_id = %s
                ORDER BY generated_at DESC
                LIMIT 1
                """,
                (event_id,),
            )
            return cur.fetchone()


def save_event_analysis(
    event_id: int,
    markdown: str,
    *,
    structured: Optional[Dict[str, Any]] = None,
    model_name: str = "",
) -> int:
    sj = json.dumps(structured or {}, ensure_ascii=False)
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO meeting_event_analyses (
                    event_id, analysis_markdown, structured_json, model_name
                ) VALUES (%s, %s, CAST(%s AS JSON), %s)
                """,
                (event_id, markdown, sj, (model_name or "")[:128]),
            )
            return int(cur.lastrowid)


def fetch_event_timeline(event_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """按 phase 分组返回关联文章。"""
    sql = """
        SELECT m.phase, m.link_score, m.link_method,
               a.id AS article_id, a.title_raw, a.summary_raw, a.normalized_url,
               a.source, a.published_at,
               e.summary_structured, e.main_topic, e.meeting_phase
        FROM meeting_event_articles m
        JOIN articles a ON a.id = m.article_id
        LEFT JOIN article_extractions e ON e.article_id = a.id
        WHERE m.event_id = %s
        ORDER BY a.published_at ASC, m.id ASC
    """
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (event_id,))
            rows = cur.fetchall() or []
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "pre": [],
        "during": [],
        "post": [],
        "unknown": [],
    }
    for r in rows:
        ph = str(r.get("phase") or "unknown")
        if ph not in buckets:
            ph = "unknown"
        buckets[ph].append(r)
    return buckets


def fetch_meeting_articles_for_linking(
    *,
    limit: int = 500,
    offset: int = 0,
    only_unlinked: bool = False,
) -> List[Dict[str, Any]]:
    """拉取 meeting 类型文章供批量 link。"""
    extra = ""
    if only_unlinked:
        extra = """
            AND NOT EXISTS (
                SELECT 1 FROM meeting_event_articles m WHERE m.article_id = a.id
            )
        """
    sql = f"""
        SELECT a.id AS article_id, a.title_raw, a.summary_raw, a.published_at,
               e.main_topic, e.tags_raw, e.entities_json, e.meeting_catalog_key,
               e.meeting_phase, e.content_type
        FROM articles a
        JOIN article_extractions e ON e.article_id = a.id
        WHERE e.content_type = 'meeting' {extra}
        ORDER BY a.published_at DESC
        LIMIT %s OFFSET %s
    """
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit, offset))
            rows = cur.fetchall() or []
    for r in rows:
        for k in ("tags_raw", "entities_json"):
            if isinstance(r.get(k), str):
                try:
                    r[k] = json.loads(r[k])
                except json.JSONDecodeError:
                    r[k] = []
            elif r.get(k) is None:
                r[k] = []
    return rows


def count_meeting_linked_since(since: datetime) -> int:
    """统计自某时刻以来新关联的 meeting 文章数（用于任务 data_count）。"""
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(DISTINCT m.article_id) AS c
                FROM meeting_event_articles m
                WHERE m.created_at >= %s
                """,
                (since,),
            )
            row = cur.fetchone()
            return int((row or {}).get("c") or 0)


def count_meeting_extractions_since(since: datetime) -> int:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS c FROM article_extractions
                WHERE content_type = 'meeting' AND created_at >= %s
                """,
                (since,),
            )
            row = cur.fetchone()
            return int((row or {}).get("c") or 0)


def events_needing_brief(
    *,
    min_articles: int = 2,
    days_after_end: int = 7,
) -> List[Dict[str, Any]]:
    """返回应生成专题分析的事件（有足够文章或已结束）。"""
    sql = """
        SELECT e.id, e.catalog_key, e.edition_label, e.end_date,
               COUNT(m.id) AS article_count
        FROM meeting_events e
        LEFT JOIN meeting_event_articles m ON m.event_id = e.id
        LEFT JOIN meeting_event_analyses a ON a.event_id = e.id
        GROUP BY e.id
        HAVING article_count >= %s
           OR (e.end_date IS NOT NULL AND e.end_date <= DATE_SUB(CURDATE(), INTERVAL %s DAY))
    """
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (min_articles, days_after_end))
            return list(cur.fetchall() or [])


def prior_events_same_catalog(catalog_key: str, before_event_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.edition_label, e.edition_year, e.start_date, e.end_date
                FROM meeting_events e
                WHERE e.catalog_key = %s AND e.id < %s
                ORDER BY e.start_date DESC
                LIMIT %s
                """,
                (catalog_key, before_event_id, limit),
            )
            return list(cur.fetchall() or [])
