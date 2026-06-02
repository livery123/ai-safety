"""
监测周报数据拉取：按日期窗口从三系统赛道聚合入库条目。

功能：将 MySQL 监测行转为 TrackEntry 列表，供 engine/weekly_report 打包 Prompt。
输入：system_key、week_start/week_end。
输出：TrackEntry 列表；无副作用。
上下游：engine/weekly_report.py、scripts/generate_weekly_report.py。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, List, Optional

import pandas as pd

from core.mysql_monitor_tracks import (
    _finalize_row_df,
    _literature_where_clause,
    _meeting_where_clause,
    _policy_where_clause,
    _read_sql_dataframe,
)


@dataclass
class TrackEntry:
    """
    功能：单条监测材料（周报引用单元）。
    输入：SQL 行或 literature 行 dict。
    输出：供 pack_entries_for_prompt 序列化。
    """

    article_id: int
    title: str
    content_type: str = ""
    risk_domain: str = ""
    main_topic: str = ""
    summary: str = ""
    source: str = ""
    url: str = ""
    entities: str = ""
    tags: str = ""
    published_at: str = ""
    subdomains: str = ""


def _row_to_entry(row: Any, idx: int) -> TrackEntry:
    """DataFrame 行 → TrackEntry。"""
    aid = int(row.get("article_id") or row.get("id") or idx)
    ts = row.get("时间")
    if ts is not None and hasattr(ts, "strftime"):
        pub = ts.strftime("%Y-%m-%d")
    else:
        pub = str(ts or "")[:10]
    return TrackEntry(
        article_id=aid,
        title=str(row.get("标题") or "").strip(),
        content_type=str(row.get("资讯类别") or "").strip(),
        risk_domain=str(row.get("主域") or "").strip(),
        main_topic=str(row.get("main_topic") or "").strip(),
        summary=str(row.get("摘要") or "").strip(),
        source=str(row.get("来源平台") or row.get("source") or "").strip(),
        url=str(row.get("URL") or row.get("landing_url") or "").strip(),
        entities=str(row.get("涉及主体") or "").strip(),
        tags=str(row.get("标签") or "").strip(),
        published_at=pub,
        subdomains=str(row.get("子域") or "").strip(),
    )


def _fetch_extractions_in_range(
    where_fn,
    week_start: date,
    week_end: date,
    limit: int = 80,
) -> pd.DataFrame:
    """article_extractions 赛道：按日期闭区间查询。"""
    where_sql, binds = where_fn(None, None)
    sql = f"""
    SELECT
        a.id AS article_id,
        e.id AS id,
        a.title_raw AS `标题`,
        e.content_type AS `资讯类别`,
        e.risk_domain AS `主域`,
        e.main_topic AS main_topic,
        e.risk_subdomains_json AS _subs,
        e.entities_json AS _ents,
        a.summary_raw AS `摘要`,
        a.source AS `来源平台`,
        a.normalized_url AS URL,
        e.tags_raw AS _tags,
        COALESCE(a.published_at, e.created_at) AS `时间`
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    WHERE {where_sql}
      AND DATE(COALESCE(a.published_at, e.created_at)) >= %s
      AND DATE(COALESCE(a.published_at, e.created_at)) <= %s
    ORDER BY COALESCE(a.published_at, e.created_at) DESC
    LIMIT %s
    """
    df = _read_sql_dataframe(
        sql,
        tuple(list(binds) + [week_start.isoformat(), week_end.isoformat(), limit]),
    )
    return _finalize_row_df(df)


def _fetch_literature_in_range(
    week_start: date,
    week_end: date,
    limit: int = 80,
) -> List[TrackEntry]:
    """literature_items 表按发表/入库日落库。"""
    where_sql, params = _literature_where_clause(None, source=None, sources=None)
    sql = f"""
    SELECT id AS article_id, source, title, abstract, authors_json,
           publication_name, document_type, landing_url, published_at, created_at
    FROM literature_items
    WHERE {where_sql}
      AND DATE(COALESCE(published_at, created_at)) >= %s
      AND DATE(COALESCE(published_at, created_at)) <= %s
    ORDER BY COALESCE(published_at, created_at) DESC
    LIMIT %s
    """
    lim = max(5, min(int(limit), 200))
    df = _read_sql_dataframe(
        sql,
        tuple(list(params) + [week_start.isoformat(), week_end.isoformat(), lim]),
    )
    entries: List[TrackEntry] = []
    for _, row in df.iterrows():
        authors = row.get("authors_json")
        ent_str = ""
        if authors:
            try:
                al = json.loads(authors) if isinstance(authors, str) else authors
                if isinstance(al, list):
                    ent_str = "、".join(str(x) for x in al[:5])
            except (json.JSONDecodeError, TypeError):
                pass
        ts = row.get("published_at") or row.get("created_at")
        pub = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts or "")[:10]
        entries.append(
            TrackEntry(
                article_id=int(row.get("article_id") or 0),
                title=str(row.get("title") or "").strip(),
                content_type="literature",
                summary=str(row.get("abstract") or "")[:800],
                source=str(row.get("source") or "").strip(),
                url=str(row.get("landing_url") or "").strip(),
                entities=ent_str,
                published_at=pub,
            )
        )
    return entries


def fetch_context_entries(
    system_key: str,
    week_start: date,
    week_end: date,
    *,
    limit: int = 80,
    context_days: int = 30,
) -> List[TrackEntry]:
    """
    功能：拉取「历史脉络」补充材料（week 之前 context_days 天内少量条目）。
    输入：system_key、周界、context_days。
    输出：TrackEntry 列表（不含本周，最多 15 条）。
    """
    ctx_start = week_start - timedelta(days=max(7, context_days))
    ctx_end = week_start - timedelta(days=1)
    if ctx_end < ctx_start:
        return []
    lim = 15
    key = system_key.strip().lower()
    if key == "literature":
        all_e = _fetch_literature_in_range(ctx_start, ctx_end, limit=lim)
        return all_e
    where_fn = {
        "policy": _policy_where_clause,
        "meeting": _meeting_where_clause,
    }.get(key)
    if not where_fn:
        return []
    df = _fetch_extractions_in_range(where_fn, ctx_start, ctx_end, limit=lim)
    return [_row_to_entry(r, i) for i, r in enumerate(df.to_dict("records"), 1)]


def fetch_entries_for_week(
    system_key: str,
    week_start: date,
    week_end: date,
    *,
    limit: int = 80,
) -> List[TrackEntry]:
    """
    功能：拉取指定系统、指定周内的监测条目。
    输入：system_key=policy|meeting|literature|platform；week_start/week_end。
    输出：TrackEntry 列表；platform 合并三系统。
    """
    key = system_key.strip().lower()
    lim = max(5, min(int(limit), 120))

    if key == "platform":
        out: List[TrackEntry] = []
        for sk in ("policy", "meeting", "literature"):
            out.extend(fetch_entries_for_week(sk, week_start, week_end, limit=lim // 3 + 10))
        out.sort(key=lambda e: e.published_at or "", reverse=True)
        return out[:lim]

    if key == "literature":
        return _fetch_literature_in_range(week_start, week_end, limit=lim)

    where_fn = {
        "policy": _policy_where_clause,
        "meeting": _meeting_where_clause,
    }.get(key)
    if not where_fn:
        return []

    df = _fetch_extractions_in_range(where_fn, week_start, week_end, limit=lim)
    if df.empty:
        return []
    return [_row_to_entry(r, i) for i, r in enumerate(df.to_dict("records"), 1)]
