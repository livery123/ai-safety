"""
Streamlit 看板只读查询：从 MySQL articles + article_extractions 聚合，
DataFrame 列名与旧版 SQLite incidents / watched_keywords / risk_taxonomy 消费方一致。
"""

from __future__ import annotations

import json
from typing import Any, Callable, List, Optional, Tuple

import pandas as pd
import pymysql.cursors

from core.db import coerce_risk_domain
from core.mysql_db import mysql_conn


def _read_sql_dataframe(sql: str, params: Optional[Tuple[Any, ...]] = None) -> pd.DataFrame:
    """
    pandas.read_sql 与 PyMySQL DictCursor 组合会误解析行；这里用 tuple 游标拉取后构造 DataFrame。
    """
    with mysql_conn() as conn:
        cur = conn.cursor(pymysql.cursors.Cursor)
        cur.execute(sql, params or ())
        desc = cur.description or []
        cols = [d[0] for d in desc]
        rows = cur.fetchall()
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(list(rows), columns=cols)


def _parse_json_list(val: Any) -> List[Any]:
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


def get_dashboard_stats() -> Tuple[int, int, int]:
    """(article_extractions 行数, 全库去重标签数, 主域×子域组合种数)。"""
    with mysql_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM article_extractions")
        n_ext = int((cur.fetchone() or {}).get("n") or 0)

        cur.execute(
            """
            SELECT COUNT(DISTINCT jt.tag) AS n
            FROM article_extractions e
            JOIN JSON_TABLE(
                COALESCE(e.tags_raw, JSON_ARRAY()),
                '$[*]' COLUMNS (tag VARCHAR(191) PATH '$')
            ) jt
            WHERE jt.tag IS NOT NULL AND CHAR_LENGTH(TRIM(jt.tag)) > 0
            """
        )
        n_tags = int((cur.fetchone() or {}).get("n") or 0)

    tax_df = get_dashboard_taxonomy_df()
    n_tax = int(len(tax_df)) if not tax_df.empty else 0

    return n_ext, n_tags, n_tax


def get_dashboard_taxonomy_df() -> pd.DataFrame:
    """列：domain, subdomain, tax_count, first_seen（避免列名 count 与 MySQL 保留字/驱动交互导致 pandas 读出字符串）。"""
    sql = """
    SELECT
        t.domain AS domain,
        t.subdomain AS subdomain,
        t.cnt AS tax_count,
        t.first_seen AS first_seen
    FROM (
        SELECT
            e.risk_domain AS domain,
            TRIM(COALESCE(NULLIF(jt.subdomain, ''), '未指定子域')) AS subdomain,
            COUNT(*) AS cnt,
            MIN(e.created_at) AS first_seen
        FROM article_extractions e
        JOIN JSON_TABLE(
            IF(
                JSON_LENGTH(COALESCE(e.risk_subdomains_json, JSON_ARRAY())) > 0,
                e.risk_subdomains_json,
                JSON_ARRAY('未指定子域')
            ),
            '$[*]' COLUMNS (subdomain VARCHAR(191) PATH '$')
        ) jt
        WHERE e.risk_domain IS NOT NULL AND CHAR_LENGTH(TRIM(e.risk_domain)) > 0
        GROUP BY e.risk_domain, TRIM(COALESCE(NULLIF(jt.subdomain, ''), '未指定子域'))
    ) t
    ORDER BY t.domain, t.cnt DESC
    """
    df = _read_sql_dataframe(sql)
    if df.empty:
        return pd.DataFrame(columns=["domain", "subdomain", "tax_count", "first_seen"])
    df["domain"] = df["domain"].map(lambda x: coerce_risk_domain(str(x)))
    df = (
        df.groupby(["domain", "subdomain"], as_index=False)
        .agg(tax_count=("tax_count", "sum"), first_seen=("first_seen", "min"))
        .sort_values(["domain", "tax_count"], ascending=[True, False])
    )
    return df


def get_dashboard_keywords_df() -> pd.DataFrame:
    """列：keyword, count；由 tags_raw 聚合，Top 60。"""
    sql = """
    SELECT jt.tag AS keyword, COUNT(*) AS count
    FROM article_extractions e
    JOIN JSON_TABLE(
        COALESCE(e.tags_raw, JSON_ARRAY()),
        '$[*]' COLUMNS (tag VARCHAR(191) PATH '$')
    ) jt
    WHERE jt.tag IS NOT NULL AND CHAR_LENGTH(TRIM(jt.tag)) > 0
    GROUP BY jt.tag
    ORDER BY count DESC
    LIMIT 60
    """
    df = _read_sql_dataframe(sql)
    if df.empty:
        return pd.DataFrame(columns=["keyword", "count"])
    return df


def fetch_dashboard_latest_rows(limit: int = 20) -> pd.DataFrame:
    """title, 资讯类别(content_type), 主域, 子域, 涉及主体, 来源, 时间。"""
    lim = max(1, min(int(limit), 500))
    sql = """
    SELECT
        a.title_raw AS title,
        e.content_type AS `资讯类别`,
        e.risk_domain AS `主域`,
        e.risk_subdomains_json AS _subs,
        e.entities_json AS _ents,
        a.normalized_url AS `来源`,
        COALESCE(a.published_at, e.created_at) AS `时间`
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    ORDER BY COALESCE(a.published_at, e.created_at) DESC
    LIMIT %s
    """
    df = _read_sql_dataframe(sql, (lim,))
    if df.empty:
        return df
    subs = df["_subs"].apply(_parse_json_list)
    df["子域"] = subs.apply(lambda L: str(L[0]).strip() if L else "未指定子域")
    ents = df["_ents"].apply(_parse_json_list)
    df["涉及主体"] = ents.apply(
        lambda L: "、".join(str(x).strip() for x in L[:5] if str(x).strip()) if L else ""
    )
    if "主域" in df.columns:
        df["主域"] = df["主域"].map(lambda x: coerce_risk_domain(str(x)))
    return df.drop(columns=["_subs", "_ents"], errors="ignore")


def _finalize_incidents_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """功能：为情报列表补齐子域 / 实体 / 标签列并归一主域；输入输出均为 DataFrame。"""
    if df.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "标题",
                "资讯类别",
                "主域",
                "子域",
                "涉及主体",
                "摘要",
                "来源",
                "标签",
                "时间",
            ]
        )
    subs = df["_subs"].apply(_parse_json_list)
    df["子域"] = subs.apply(lambda L: str(L[0]).strip() if L else "未指定子域")
    ents = df["_ents"].apply(_parse_json_list)
    df["涉及主体"] = ents.apply(
        lambda L: "、".join(str(x).strip() for x in L[:5] if str(x).strip()) if L else ""
    )
    tags = df["_tags"].apply(_parse_json_list)
    df["标签"] = tags.apply(lambda L: ",".join(str(x).strip() for x in L if str(x).strip()))
    if "主域" in df.columns:
        df["主域"] = df["主域"].map(lambda x: coerce_risk_domain(str(x)))
    return df.drop(columns=["_subs", "_ents", "_tags"], errors="ignore")


def fetch_distinct_content_types() -> List[str]:
    """article_extractions 中已出现的资讯类别，供分页筛选下拉框。"""
    sql = """
    SELECT DISTINCT e.content_type AS ct
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    WHERE e.content_type IS NOT NULL AND CHAR_LENGTH(TRIM(e.content_type)) > 0
    ORDER BY ct
    """
    df = _read_sql_dataframe(sql)
    if df.empty or "ct" not in df.columns:
        return []
    return sorted(str(x).strip() for x in df["ct"].dropna().unique().tolist() if str(x).strip())


def _build_incidents_where(
    *,
    risk_domain: Optional[str] = None,
    content_type: Optional[str] = None,
    keyword: Optional[str] = None,
) -> Tuple[str, List[Any]]:
    """生成 WHERE 子句片段（无前导 WHERE）与绑定参数列表。"""
    parts: List[str] = []
    params: List[Any] = []
    dom = str(risk_domain).strip() if risk_domain else ""
    if dom:
        parts.append("e.risk_domain = %s")
        params.append(dom)
    ctype = str(content_type).strip() if content_type else ""
    if ctype:
        parts.append("e.content_type = %s")
        params.append(ctype)
    kw = str(keyword).strip() if keyword else ""
    if kw:
        like = f"%{kw}%"
        parts.append("(a.title_raw LIKE %s OR a.summary_raw LIKE %s)")
        params.extend([like, like])
    if not parts:
        return "1=1", []
    return " AND ".join(parts), params


def count_dashboard_incidents(
    *,
    risk_domain: Optional[str] = None,
    content_type: Optional[str] = None,
    keyword: Optional[str] = None,
) -> int:
    """分页总条数；筛选与 fetch_dashboard_incidents_page 一致。"""
    where_sql, binds = _build_incidents_where(
        risk_domain=risk_domain, content_type=content_type, keyword=keyword
    )
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


def fetch_dashboard_incidents_page(
    offset: int,
    limit: int,
    *,
    risk_domain: Optional[str] = None,
    content_type: Optional[str] = None,
    keyword: Optional[str] = None,
) -> pd.DataFrame:
    """情报详情分页；按时间倒序；limit 裁剪在 50～500。"""
    lim = max(50, min(int(limit), 500))
    off = max(0, int(offset))
    where_sql, binds = _build_incidents_where(
        risk_domain=risk_domain, content_type=content_type, keyword=keyword
    )
    sql = f"""
    SELECT
        e.id AS id,
        a.title_raw AS `标题`,
        e.content_type AS `资讯类别`,
        e.risk_domain AS `主域`,
        e.risk_subdomains_json AS _subs,
        e.entities_json AS _ents,
        a.summary_raw AS `摘要`,
        a.normalized_url AS `来源`,
        e.tags_raw AS _tags,
        COALESCE(a.published_at, e.created_at) AS `时间`
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    WHERE {where_sql}
    ORDER BY COALESCE(a.published_at, e.created_at) DESC
    LIMIT %s OFFSET %s
    """
    params = list(binds) + [lim, off]
    df = _read_sql_dataframe(sql, tuple(params))
    return _finalize_incidents_dataframe(df)


def fetch_dashboard_all_rows() -> pd.DataFrame:
    """情报全量导出/脚本用时；大批量慎用，前台「情报详情」已改分页接口。"""
    sql = """
    SELECT
        e.id AS id,
        a.title_raw AS `标题`,
        e.content_type AS `资讯类别`,
        e.risk_domain AS `主域`,
        e.risk_subdomains_json AS _subs,
        e.entities_json AS _ents,
        a.summary_raw AS `摘要`,
        a.normalized_url AS `来源`,
        e.tags_raw AS _tags,
        COALESCE(a.published_at, e.created_at) AS `时间`
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    ORDER BY COALESCE(a.published_at, e.created_at) DESC
    """
    df = _read_sql_dataframe(sql)
    return _finalize_incidents_dataframe(df)
