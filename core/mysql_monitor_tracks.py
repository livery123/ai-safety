"""
专项监测赛道：政策法规 / 国际会议 —— MySQL 只读聚合。
文献赛道接口预留：`literature_monitor_*` 返回空占位，待独立文献表或多平台 ingest 接入。

功能：
- 政策法规与科技政策：以 `article_extractions.content_type IN ('policy','report')` 为主口径；
  可选标题/摘要关键词 AND 收窄（不传则只看类型）。
- 重大国际会议：`content_type = 'meeting'`。
- 文献：仅占位数据结构，不产生 SQL。

输入：`articles` + `article_extractions` JOIN；分页参数 offset/limit；可选 keyword LIKE。
输出：DataFrame / 计数 int；无副作用。
上下游：仅 Streamlit 专项监测页调用；可与后续 `literature_records` 表并排演进。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pymysql.cursors

from core.db import coerce_risk_domain
from core.mysql_db import mysql_conn
from core.source_registry import (
    build_literature_sources_filter_sql,
    build_sources_filter_sql,
    is_db_source_allowed,
    scope_exclude_sql_articles,
)


def _format_literature_display_time(value: Any) -> Optional[str]:
    """文献列表「时间」列：优先展示发表日，格式化为 YYYY-MM-DD。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def _read_sql_dataframe(sql: str, params: Optional[Tuple[Any, ...]] = None) -> pd.DataFrame:
    """pandas.read_sql 与 PyMySQL DictCursor 组合误解析时使用 tuple 游标。"""
    with mysql_conn() as conn:
        cur = conn.cursor(pymysql.cursors.Cursor)
        cur.execute(sql, params or ())
        desc = cur.description or []
        cols = [d[0] for d in desc]
        rows = cur.fetchall()
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(list(rows), columns=cols)


def _parse_json_list_safe(val: Any) -> List[Any]:
    """从 JSON / list / str 取列表，失败则 []。"""
    import json

    if val is None or (isinstance(val, float) and pd.isna(val)):
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return []
    return []


def _finalize_row_df(df: pd.DataFrame) -> pd.DataFrame:
    """补齐子域、主体、标签、主域归一。"""
    if df.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "标题",
                "资讯类别",
                "主域",
                "main_topic",
                "子域",
                "涉及主体",
                "标签",
                "摘要",
                "来源平台",
                "URL",
                "时间",
            ]
        )
    subs = df["_subs"].apply(_parse_json_list_safe)
    df["子域"] = subs.apply(lambda L: str(L[0]).strip() if L else "未指定子域")
    ents = df["_ents"].apply(_parse_json_list_safe)
    df["涉及主体"] = ents.apply(
        lambda L: "、".join(str(x).strip() for x in L[:8] if str(x).strip()) if L else ""
    )
    tags = df["_tags"].apply(_parse_json_list_safe)
    df["标签"] = tags.apply(lambda L: ",".join(str(x).strip() for x in L if str(x).strip()))
    if "主域" in df.columns:
        df["主域"] = df["主域"].map(lambda x: coerce_risk_domain(str(x)))
    return df.drop(columns=["_subs", "_ents", "_tags"], errors="ignore")


# ---------------------------------------------------------------------------
# 政策法规（含科技政策口径：policy + report）
# ---------------------------------------------------------------------------


def _policy_where_clause(
    keyword: Optional[str],
    sources: Optional[List[str]] = None,
) -> Tuple[str, List[Any]]:
    """
    WHERE 片段（不含 WHERE 关键字）。
    keyword 非空时对 title_raw / summary_raw 追加 LIKE；sources 为多选来源 key。
    """
    base = "e.content_type IN ('policy','report')"
    binds: List[Any] = []
    scope_sql, scope_binds = scope_exclude_sql_articles("policy")
    base += scope_sql
    binds.extend(scope_binds)
    src_sql, src_binds = build_sources_filter_sql(sources)
    base += src_sql
    binds.extend(src_binds)
    kw = (keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        base += " AND (a.title_raw LIKE %s OR a.summary_raw LIKE %s)"
        binds.extend([like, like])
    return base, binds


def count_policy_track_rows(
    *,
    keyword: Optional[str] = None,
    sources: Optional[List[str]] = None,
) -> int:
    """政策法规赛道符合条件的行数。"""
    where_sql, binds = _policy_where_clause(keyword, sources)
    sql = f"""
    SELECT COUNT(*) AS n
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    WHERE {where_sql}
    """
    df = _read_sql_dataframe(sql, tuple(binds))
    if df.empty:
        return 0
    return int(pd.to_numeric(df.iloc[0].get("n", 0), errors="coerce") or 0)


def fetch_policy_track_page(
    offset: int,
    limit: int,
    *,
    keyword: Optional[str] = None,
    sources: Optional[List[str]] = None,
) -> pd.DataFrame:
    """分页明细；limit 夹在 25～300。"""
    lim = max(25, min(int(limit), 300))
    off = max(0, int(offset))
    where_sql, binds = _policy_where_clause(keyword, sources)
    sql = f"""
    SELECT
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
    ORDER BY COALESCE(a.published_at, e.created_at) DESC
    LIMIT %s OFFSET %s
    """
    df = _read_sql_dataframe(sql, tuple(list(binds) + [lim, off]))
    return _finalize_row_df(df)


def aggregate_policy_by_source(limit: int = 20) -> pd.DataFrame:
    """来源平台分布 Top N。"""
    lm = max(5, min(int(limit), 50))
    where_sql, binds = _policy_where_clause(None)
    sql = f"""
    SELECT a.source AS source, COUNT(*) AS cnt
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    WHERE {where_sql}
    GROUP BY a.source
    ORDER BY cnt DESC
    LIMIT %s
    """
    return _read_sql_dataframe(sql, tuple(list(binds) + [lm]))


def aggregate_policy_by_week(limit_weeks: int = 16) -> pd.DataFrame:
    """按 ISO 周年份-周序号聚合条目数。"""
    lw = max(4, min(int(limit_weeks), 104))
    where_sql, binds = _policy_where_clause(None)
    sql = f"""
    SELECT
      DATE_FORMAT(COALESCE(a.published_at, e.created_at), '%X-W%V') AS week_bucket,
      MIN(COALESCE(a.published_at, e.created_at)) AS sort_ts,
      COUNT(*) AS cnt
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    WHERE {where_sql}
      AND COALESCE(a.published_at, e.created_at) IS NOT NULL
    GROUP BY week_bucket
    ORDER BY sort_ts DESC
    LIMIT %s
    """
    return _read_sql_dataframe(sql, tuple(list(binds) + [lw]))


def count_policy_recent_days(days: int = 7, *, keyword: Optional[str] = None) -> int:
    """近 N 天内新增条目数。"""
    d = max(1, min(int(days), 366))
    where_sql, binds = _policy_where_clause(keyword, None)
    sql = f"""
    SELECT COUNT(*) AS n
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    WHERE {where_sql}
      AND COALESCE(a.published_at, e.created_at) >= DATE_SUB(NOW(), INTERVAL %s DAY)
    """
    df = _read_sql_dataframe(sql, tuple(list(binds) + [d]))
    if df.empty:
        return 0
    return int(pd.to_numeric(df.iloc[0].get("n", 0), errors="coerce") or 0)


def fetch_policy_recent_rows(
    days: int = 7,
    limit: int = 80,
    *,
    keyword: Optional[str] = None,
) -> pd.DataFrame:
    """
    功能：近 N 日政策法规明细（供本周摘要与子域统计）。
    输入：days 窗口、limit 上限、可选 keyword。
    输出：与 fetch_policy_track_page 同结构的 DataFrame。
    """
    d = max(1, min(int(days), 366))
    lim = max(5, min(int(limit), 300))
    where_sql, binds = _policy_where_clause(keyword, None)
    sql = f"""
    SELECT
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
      AND COALESCE(a.published_at, e.created_at) >= DATE_SUB(NOW(), INTERVAL %s DAY)
    ORDER BY COALESCE(a.published_at, e.created_at) DESC
    LIMIT %s
    """
    df = _read_sql_dataframe(sql, tuple(list(binds) + [d, lim]))
    return _finalize_row_df(df)


# ---------------------------------------------------------------------------
# 国际会议
# ---------------------------------------------------------------------------


def _meeting_where_clause(
    keyword: Optional[str],
    sources: Optional[List[str]] = None,
) -> Tuple[str, List[Any]]:
    base = "e.content_type = 'meeting'"
    binds: List[Any] = []
    scope_sql, scope_binds = scope_exclude_sql_articles("meeting")
    base += scope_sql
    binds.extend(scope_binds)
    src_sql, src_binds = build_sources_filter_sql(sources)
    base += src_sql
    binds.extend(src_binds)
    kw = (keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        base += " AND (a.title_raw LIKE %s OR a.summary_raw LIKE %s OR e.main_topic LIKE %s)"
        binds.extend([like, like, like])
    return base, binds


def count_meeting_track_rows(
    *,
    keyword: Optional[str] = None,
    sources: Optional[List[str]] = None,
) -> int:
    """会议赛道条目数。"""
    where_sql, binds = _meeting_where_clause(keyword, sources)
    sql = f"""
    SELECT COUNT(*) AS n
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    WHERE {where_sql}
    """
    df = _read_sql_dataframe(sql, tuple(binds))
    if df.empty:
        return 0
    return int(pd.to_numeric(df.iloc[0].get("n", 0), errors="coerce") or 0)


def fetch_meeting_track_page(
    offset: int,
    limit: int,
    *,
    keyword: Optional[str] = None,
    sources: Optional[List[str]] = None,
) -> pd.DataFrame:
    lim = max(25, min(int(limit), 300))
    off = max(0, int(offset))
    where_sql, binds = _meeting_where_clause(keyword, sources)
    sql = f"""
    SELECT
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
    ORDER BY COALESCE(a.published_at, e.created_at) DESC
    LIMIT %s OFFSET %s
    """
    df = _read_sql_dataframe(sql, tuple(list(binds) + [lim, off]))
    return _finalize_row_df(df)


def aggregate_meeting_by_source(limit: int = 20) -> pd.DataFrame:
    lm = max(5, min(int(limit), 50))
    where_sql, binds = _meeting_where_clause(None)
    sql = f"""
    SELECT a.source AS source, COUNT(*) AS cnt
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    WHERE {where_sql}
    GROUP BY a.source
    ORDER BY cnt DESC
    LIMIT %s
    """
    return _read_sql_dataframe(sql, tuple(list(binds) + [lm]))


def aggregate_meeting_by_week(limit_weeks: int = 16) -> pd.DataFrame:
    lw = max(4, min(int(limit_weeks), 104))
    where_sql, binds = _meeting_where_clause(None)
    sql = f"""
    SELECT
      DATE_FORMAT(COALESCE(a.published_at, e.created_at), '%X-W%V') AS week_bucket,
      MIN(COALESCE(a.published_at, e.created_at)) AS sort_ts,
      COUNT(*) AS cnt
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    WHERE {where_sql}
      AND COALESCE(a.published_at, e.created_at) IS NOT NULL
    GROUP BY week_bucket
    ORDER BY sort_ts DESC
    LIMIT %s
    """
    return _read_sql_dataframe(sql, tuple(list(binds) + [lw]))


def count_meeting_recent_days(days: int = 30, *, keyword: Optional[str] = None) -> int:
    d = max(1, min(int(days), 366))
    where_sql, binds = _meeting_where_clause(keyword, None)
    sql = f"""
    SELECT COUNT(*) AS n
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    WHERE {where_sql}
      AND COALESCE(a.published_at, e.created_at) >= DATE_SUB(NOW(), INTERVAL %s DAY)
    """
    df = _read_sql_dataframe(sql, tuple(list(binds) + [d]))
    if df.empty:
        return 0
    return int(pd.to_numeric(df.iloc[0].get("n", 0), errors="coerce") or 0)


def fetch_meeting_recent_rows(
    days: int = 7,
    limit: int = 80,
    *,
    keyword: Optional[str] = None,
) -> pd.DataFrame:
    """
    功能：近 N 日国际会议明细（供本周摘要与子域统计）。
    输入：days 窗口、limit 上限、可选 keyword。
    输出：与 fetch_meeting_track_page 同结构的 DataFrame。
    """
    d = max(1, min(int(days), 366))
    lim = max(5, min(int(limit), 300))
    where_sql, binds = _meeting_where_clause(keyword, None)
    sql = f"""
    SELECT
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
      AND COALESCE(a.published_at, e.created_at) >= DATE_SUB(NOW(), INTERVAL %s DAY)
    ORDER BY COALESCE(a.published_at, e.created_at) DESC
    LIMIT %s
    """
    df = _read_sql_dataframe(sql, tuple(list(binds) + [d, lim]))
    return _finalize_row_df(df)


# ---------------------------------------------------------------------------
# 文献监测 —— 预留接口（不向 MySQL 发查询）
# ---------------------------------------------------------------------------


def literature_monitor_status() -> dict:
    """返回文献模块是否已实现及说明字符串，供前端展示。"""
    try:
        from core.mysql_db import count_literature_items

        total = count_literature_items()
        return {
            "implemented": True,
            "planned_tables": ["literature_items"],
            "total_rows": total,
            "message": f"已接入 arXiv / Scopus / Springer → literature_items，当前 {total} 条。",
        }
    except Exception as e:
        return {
            "implemented": False,
            "planned_tables": ["literature_items"],
            "total_rows": 0,
            "message": f"literature_items 表不可用: {type(e).__name__}: {e}",
        }


def count_literature_recent_days(
    days: int = 7,
    *,
    keyword: Optional[str] = None,
    source: Optional[str] = None,
) -> int:
    """文献库近 N 日新增条数。"""
    d = max(1, min(int(days), 366))
    try:
        wheres = ["COALESCE(published_at, created_at) >= DATE_SUB(NOW(), INTERVAL %s DAY)"]
        params: List[Any] = [d]
        src = (source or "").strip()
        if src:
            wheres.append("source = %s")
            params.append(src)
        kw = (keyword or "").strip()
        if kw:
            wheres.append("(title LIKE %s OR abstract LIKE %s)")
            like = f"%{kw}%"
            params.extend([like, like])
        sql = f"SELECT COUNT(*) AS n FROM literature_items WHERE {' AND '.join(wheres)}"
        df = _read_sql_dataframe(sql, tuple(params))
        if df.empty:
            return 0
        return int(pd.to_numeric(df.iloc[0].get("n", 0), errors="coerce") or 0)
    except Exception:
        return 0


def _literature_where_clause(
    keyword: Optional[str] = None,
    *,
    source: Optional[str] = None,
    sources: Optional[List[str]] = None,
) -> Tuple[str, List[Any]]:
    """文献库 WHERE 片段；限定 arxiv/scopus/springer。"""
    wheres = ["source IN ('arxiv', 'scopus', 'springer')"]
    binds: List[Any] = []
    if sources:
        src_sql, src_binds = build_literature_sources_filter_sql(sources)
        if src_sql:
            wheres.append(src_sql.lstrip(" AND "))
            binds.extend(src_binds)
    elif source and str(source).strip():
        wheres.append("source = %s")
        binds.append(str(source).strip())
    kw = (keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        wheres.append("(title LIKE %s OR abstract LIKE %s)")
        binds.extend([like, like])
    return " AND ".join(wheres), binds


def count_literature_track_rows(
    *,
    keyword: Optional[str] = None,
    source: Optional[str] = None,
    sources: Optional[List[str]] = None,
) -> int:
    """文献库条目数；可选 keyword / source / sources 过滤。"""
    try:
        where_sql, params = _literature_where_clause(keyword, source=source, sources=sources)
        sql = f"SELECT COUNT(*) AS n FROM literature_items WHERE {where_sql}"
        df = _read_sql_dataframe(sql, tuple(params))
        if df.empty:
            return 0
        return int(pd.to_numeric(df.iloc[0].get("n", 0), errors="coerce") or 0)
    except Exception:
        return 0


def fetch_literature_recent_rows(
    days: int = 7,
    limit: int = 80,
    *,
    keyword: Optional[str] = None,
    source: Optional[str] = None,
) -> pd.DataFrame:
    """近 N 日文献明细（供本周摘要要点）。"""
    try:
        from core.mysql_db import fetch_literature_page
        import json

        d = max(1, min(int(days), 366))
        lim = max(5, min(int(limit), 300))
        rows = fetch_literature_page(0, lim * 3, source=source, keyword=keyword)
        if not rows:
            return pd.DataFrame(columns=["标题", "时间"])
        out = []
        cutoff = datetime.now() - timedelta(days=d)
        for r in rows:
            ts = r.get("published_at") or r.get("created_at")
            if ts is None:
                continue
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00").split("+")[0])
                except ValueError:
                    continue
            if hasattr(ts, "replace") and ts.tzinfo:
                ts = ts.replace(tzinfo=None)
            if ts < cutoff:
                continue
            out.append({"标题": r.get("title"), "时间": ts})
            if len(out) >= lim:
                break
        return pd.DataFrame(out)
    except Exception:
        return pd.DataFrame(columns=["标题", "时间"])


def fetch_literature_track_page(
    offset: int,
    limit: int,
    *,
    keyword: Optional[str] = None,
    source: Optional[str] = None,
    sources: Optional[List[str]] = None,
) -> pd.DataFrame:
    """文献库分页明细（展示用）。"""
    try:
        import json

        lim = max(1, min(int(limit), 200))
        off = max(0, int(offset))
        where_sql, params = _literature_where_clause(keyword, source=source, sources=sources)
        sql = f"""
        SELECT id, source, title, abstract, authors_json, publication_name,
               document_type, subject_area, doi, external_id, published_at,
               landing_url, pdf_url, created_at
        FROM literature_items
        WHERE {where_sql}
        ORDER BY COALESCE(published_at, created_at) DESC
        LIMIT %s OFFSET %s
        """
        with mysql_conn() as conn:
            cur = conn.cursor(pymysql.cursors.DictCursor)
            cur.execute(sql, tuple(list(params) + [lim, off]))
            rows = list(cur.fetchall() or [])
        if not rows:
            return pd.DataFrame(
                columns=["标题", "来源", "作者", "期刊/会议", "类型", "DOI", "时间", "链接"]
            )
        out = []
        for r in rows:
            authors_raw = r.get("authors_json")
            authors: List[str] = []
            if authors_raw:
                try:
                    parsed = json.loads(authors_raw) if isinstance(authors_raw, str) else authors_raw
                    if isinstance(parsed, list):
                        authors = [str(x) for x in parsed[:5]]
                except (json.JSONDecodeError, TypeError):
                    pass
            out.append(
                {
                    "标题": r.get("title"),
                    "来源": r.get("source"),
                    "作者": "、".join(authors),
                    "期刊/会议": r.get("publication_name"),
                    "类型": r.get("document_type"),
                    "DOI": r.get("doi") or r.get("external_id"),
                    "时间": _format_literature_display_time(
                        r.get("published_at") or r.get("created_at")
                    ),
                    "链接": r.get("landing_url"),
                }
            )
        return pd.DataFrame(out)
    except Exception:
        return pd.DataFrame(
            columns=["标题", "来源", "作者", "期刊/会议", "类型", "DOI", "时间", "链接"]
        )


def aggregate_literature_by_week(limit_weeks: int = 16) -> pd.DataFrame:
    """按周聚合 literature_items。"""
    lw = max(4, min(int(limit_weeks), 104))
    sql = """
    SELECT
      DATE_FORMAT(COALESCE(published_at, created_at), '%X-W%V') AS week_bucket,
      MIN(COALESCE(published_at, created_at)) AS sort_ts,
      COUNT(*) AS cnt
    FROM literature_items
    WHERE COALESCE(published_at, created_at) IS NOT NULL
    GROUP BY week_bucket
    ORDER BY sort_ts DESC
    LIMIT %s
    """
    try:
        return _read_sql_dataframe(sql, (lw,))
    except Exception:
        return pd.DataFrame(columns=["week_bucket", "sort_ts", "cnt"])


def aggregate_literature_by_source(limit: int = 20) -> pd.DataFrame:
    lm = max(5, min(int(limit), 50))
    sql = """
    SELECT source AS source, COUNT(*) AS cnt
    FROM literature_items
    GROUP BY source
    ORDER BY cnt DESC
    LIMIT %s
    """
    try:
        return _read_sql_dataframe(sql, (lm,))
    except Exception:
        return pd.DataFrame(columns=["source", "cnt"])


def list_track_source_options(track: str) -> List[Dict[str, Any]]:
    """
    功能：返回某 track 左栏来源筛选项（含 count）。
    输入：policy | meeting | literature。
    输出：SourceFilterOption 形状 dict 列表。
    """
    from core.source_registry import build_source_options, is_db_source_allowed

    track_key = track.strip().lower()
    if track_key in ("meetings", "meeting"):
        track_key = "meeting"
    elif track_key not in ("policy", "meeting", "literature"):
        track_key = "policy"

    if track_key == "literature":
        df = aggregate_literature_by_source(limit=50)
    elif track_key == "meeting":
        df = aggregate_meeting_by_source(limit=50)
    else:
        df = aggregate_policy_by_source(limit=50)

    counts: Dict[str, int] = {}
    if not df.empty:
        for _, row in df.iterrows():
            src = str(row.get("source") or "").strip()
            if not src:
                continue
            if track_key == "literature":
                if src not in {"arxiv", "scopus", "springer"}:
                    continue
            elif not is_db_source_allowed(track_key, src):
                continue
            counts[src] = int(pd.to_numeric(row.get("cnt"), errors="coerce") or 0)

    return build_source_options(track_key, counts)
