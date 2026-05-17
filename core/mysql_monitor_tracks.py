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

from typing import Any, List, Optional, Tuple

import pandas as pd
import pymysql.cursors

from core.db import coerce_risk_domain
from core.mysql_db import mysql_conn


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


def _policy_where_clause(keyword: Optional[str]) -> Tuple[str, List[Any]]:
    """
    WHERE 片段（不含 WHERE 关键字）。
    keyword 非空时对 title_raw / summary_raw 追加 AND (LIKE OR LIKE)。
    """
    base = "e.content_type IN ('policy','report')"
    binds: List[Any] = []
    kw = (keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        base += " AND (a.title_raw LIKE %s OR a.summary_raw LIKE %s)"
        binds.extend([like, like])
    return base, binds


def count_policy_track_rows(*, keyword: Optional[str] = None) -> int:
    """政策法规赛道符合条件的行数。"""
    where_sql, binds = _policy_where_clause(keyword)
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
) -> pd.DataFrame:
    """分页明细；limit 夹在 25～300。"""
    lim = max(25, min(int(limit), 300))
    off = max(0, int(offset))
    where_sql, binds = _policy_where_clause(keyword)
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
    where_sql, binds = _policy_where_clause(keyword)
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


# ---------------------------------------------------------------------------
# 国际会议
# ---------------------------------------------------------------------------


def _meeting_where_clause(keyword: Optional[str]) -> Tuple[str, List[Any]]:
    base = "e.content_type = 'meeting'"
    binds: List[Any] = []
    kw = (keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        base += " AND (a.title_raw LIKE %s OR a.summary_raw LIKE %s OR e.main_topic LIKE %s)"
        binds.extend([like, like, like])
    return base, binds


def count_meeting_track_rows(*, keyword: Optional[str] = None) -> int:
    """会议赛道条目数。"""
    where_sql, binds = _meeting_where_clause(keyword)
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
) -> pd.DataFrame:
    lim = max(25, min(int(limit), 300))
    off = max(0, int(offset))
    where_sql, binds = _meeting_where_clause(keyword)
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
    where_sql, binds = _meeting_where_clause(keyword)
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


# ---------------------------------------------------------------------------
# 文献监测 —— 预留接口（不向 MySQL 发查询）
# ---------------------------------------------------------------------------


def literature_monitor_status() -> dict:
    """返回文献模块是否已实现及说明字符串，供前端展示。"""
    return {
        "implemented": False,
        "planned_tables": ["literature_records", "literature_authors"],
        "message": (
            "待接入论文平台（如 arXiv / OpenAlex / Semantic Scholar）与 "
            "`literature_records` 等业务表后将在此展示作者、机构与平台聚合。"
        ),
    }


def count_literature_track_rows(**_kwargs: Any) -> int:
    """占位：恒为 0。"""
    return 0


def fetch_literature_track_page(offset: int, limit: int, **_kwargs: Any) -> pd.DataFrame:
    """占位：返回带列结构的空 DataFrame。"""
    _ = offset, limit
    return _finalize_row_df(pd.DataFrame())


def aggregate_literature_by_week(**_kwargs: Any) -> pd.DataFrame:
    """占位：空周趋势表。"""
    return pd.DataFrame(columns=["week_bucket", "sort_ts", "cnt"])


def aggregate_literature_by_source(**_kwargs: Any) -> pd.DataFrame:
    """占位：空来源分布表。"""
    return pd.DataFrame(columns=["source", "cnt"])
