"""
功能：监测看板与情报分页相关的缓存查询。

输入：筛选条件、分页 offset/limit（由页面控件推导）。
输出：统计元组或 DataFrame；异常时降级为空。
上下游：`core.mysql_dashboard`；被 `ui.pages.dashboard`、`ui.pages.incidents`、`ui.pages.system` 使用。
"""

from __future__ import annotations

from typing import List, Tuple

import pandas as pd
import streamlit as st

from core.mysql_dashboard import (
    count_dashboard_incidents,
    fetch_dashboard_incidents_page,
    fetch_dashboard_latest_rows,
    fetch_distinct_content_types,
    get_dashboard_keywords_df,
    get_dashboard_stats,
    get_dashboard_taxonomy_df,
)


@st.cache_data(ttl=120)
def cached_stats() -> Tuple[int, int, int]:
    """功能：缓存版 MySQL 汇总；(extractions 数, 标签去重数, 主域×子域组合种数)。"""
    try:
        return get_dashboard_stats()
    except Exception:
        return 0, 0, 0


@st.cache_data(ttl=120)
def cached_taxonomy() -> pd.DataFrame:
    """功能：缓存版主域×子域频次（MySQL JSON 展开聚合）。"""
    try:
        return get_dashboard_taxonomy_df()
    except Exception:
        return pd.DataFrame(columns=["domain", "subdomain", "tax_count", "first_seen"])


@st.cache_data(ttl=120)
def cached_keywords() -> pd.DataFrame:
    """功能：缓存版 tags_raw 聚合高频词（Top 60）。"""
    try:
        return get_dashboard_keywords_df()
    except Exception:
        return pd.DataFrame(columns=["keyword", "count"])


@st.cache_data(ttl=60)
def cached_latest_incidents(limit: int = 20) -> pd.DataFrame:
    """功能：缓存最新情报列表；输入 limit。"""
    try:
        return fetch_dashboard_latest_rows(limit)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=45)
def cached_distinct_content_types() -> List[str]:
    """功能：资讯类别下拉 DISTINCT。"""
    try:
        return fetch_distinct_content_types()
    except Exception:
        return []


@st.cache_data(ttl=30)
def cached_incidents_count(fdom: str, flevel: str, fkw: str) -> int:
    """功能：情报分页总条数；空串表示不按该维度筛选。"""
    try:
        return count_dashboard_incidents(
            risk_domain=fdom.strip() or None,
            content_type=flevel.strip() or None,
            keyword=fkw.strip() or None,
        )
    except Exception:
        return 0


@st.cache_data(ttl=30)
def cached_incidents_page(fdom: str, flevel: str, fkw: str, offset: int, limit: int) -> pd.DataFrame:
    """功能：情报详情分页行。"""
    try:
        return fetch_dashboard_incidents_page(
            offset,
            limit,
            risk_domain=fdom.strip() or None,
            content_type=flevel.strip() or None,
            keyword=fkw.strip() or None,
        )
    except Exception:
        return pd.DataFrame()
