"""
功能：专项监测（政策法规 / 国际会议）缓存查询与文献占位状态。

输入：关键词、分页参数。
输出：计数或 DataFrame。
上下游：`core.mysql_monitor_tracks`；`ui.pages.tracks`。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.mysql_monitor_tracks import (
    count_meeting_recent_days,
    count_meeting_track_rows,
    count_policy_recent_days,
    count_policy_track_rows,
    fetch_meeting_track_page,
    fetch_policy_track_page,
)


@st.cache_data(ttl=45)
def cached_policy_track_count(kw: str) -> int:
    """政策法规赛道总行数。"""
    try:
        return count_policy_track_rows(keyword=(kw or "").strip() or None)
    except Exception:
        return 0


@st.cache_data(ttl=45)
def cached_policy_track_recent7(kw: str) -> int:
    """政策法规近 7 日条数。"""
    try:
        return count_policy_recent_days(7, keyword=(kw or "").strip() or None)
    except Exception:
        return 0


@st.cache_data(ttl=45)
def cached_policy_track_page_rows(kw: str, offset: int, limit: int) -> pd.DataFrame:
    """政策法规分页明细。"""
    try:
        return fetch_policy_track_page(offset, limit, keyword=(kw or "").strip() or None)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=45)
def cached_meeting_track_count(kw: str) -> int:
    """国际会议赛道总行数。"""
    try:
        return count_meeting_track_rows(keyword=(kw or "").strip() or None)
    except Exception:
        return 0


@st.cache_data(ttl=45)
def cached_meeting_track_recent30(kw: str) -> int:
    """国际会议近 30 日条数。"""
    try:
        return count_meeting_recent_days(30, keyword=(kw or "").strip() or None)
    except Exception:
        return 0


@st.cache_data(ttl=45)
def cached_meeting_track_page_rows(kw: str, offset: int, limit: int) -> pd.DataFrame:
    """国际会议分页明细。"""
    try:
        return fetch_meeting_track_page(offset, limit, keyword=(kw or "").strip() or None)
    except Exception:
        return pd.DataFrame()
