"""
功能：专项监测三系统（政策 / 会议 / 文献）缓存查询与本周摘要聚合。

输入：关键词、分页参数、系统 key。
输出：计数、DataFrame 或 WeeklySummary 结构体。
上下游：`core.mysql_monitor_tracks`；`ui.pages.tracks.*`、`ui.components.track_shell`。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd
import streamlit as st

from core.mysql_monitor_tracks import (
    aggregate_literature_by_source,
    aggregate_meeting_by_source,
    aggregate_policy_by_source,
    count_literature_recent_days,
    count_literature_track_rows,
    count_meeting_recent_days,
    count_meeting_track_rows,
    count_policy_recent_days,
    count_policy_track_rows,
    fetch_literature_recent_rows,
    fetch_literature_track_page,
    fetch_meeting_recent_rows,
    fetch_meeting_track_page,
    fetch_policy_recent_rows,
    fetch_policy_track_page,
)

WEEK_DAYS = 7


@dataclass
class WeeklySummary:
    """
    功能：单系统「本周监测摘要」展示载荷。
    输入：由 build_weekly_summary_* 从 MySQL 聚合。
    输出：供 track_shell 渲染指标卡与 bullet 要点。
    """

    range_start: str
    range_end: str
    week_new: int
    total: int
    top_source: str
    top_subdomain: str
    highlights: List[str] = field(default_factory=list)
    bullets: List[str] = field(default_factory=list)


def _week_date_range() -> tuple[str, str]:
    """近 7 日窗口起止（含今天）。"""
    end = datetime.now().date()
    start = end - timedelta(days=WEEK_DAYS - 1)
    return start.isoformat(), end.isoformat()


def _top_from_counter(counter: Counter[str], fallback: str = "—") -> str:
    if not counter:
        return fallback
    name, _ = counter.most_common(1)[0]
    return name or fallback


def _top_source_label(df: pd.DataFrame) -> str:
    if df.empty or "source" not in df.columns or "cnt" not in df.columns:
        return "—"
    row = df.iloc[0]
    src = str(row.get("source") or "").strip()
    cnt = int(pd.to_numeric(row.get("cnt"), errors="coerce") or 0)
    return f"{src}（{cnt}）" if src else "—"


def _subdomain_counter(df: pd.DataFrame) -> Counter[str]:
    counter: Counter[str] = Counter()
    if df.empty or "子域" not in df.columns:
        return counter
    for val in df["子域"].astype(str):
        v = val.strip()
        if v and v != "未指定子域":
            counter[v] += 1
    return counter


def _format_highlights(df: pd.DataFrame, limit: int = 5) -> List[str]:
    if df.empty or "标题" not in df.columns:
        return []
    out: List[str] = []
    for _, row in df.head(limit).iterrows():
        title = str(row.get("标题") or "").strip()
        if not title:
            continue
        ts = row.get("时间")
        if ts is not None and not (isinstance(ts, float) and pd.isna(ts)):
            if hasattr(ts, "strftime"):
                date_s = ts.strftime("%m-%d")
            else:
                date_s = str(ts)[:10]
                if len(date_s) >= 10:
                    date_s = date_s[5:10]
            out.append(f"{title}（{date_s}）")
        else:
            out.append(title)
    return out


def _build_bullets(
    week_new: int,
    top_source: str,
    top_subdomain: str,
    highlights: List[str],
    empty_hint: str,
) -> List[str]:
    bullets: List[str] = []
    if week_new <= 0:
        bullets.append(empty_hint)
        return bullets
    bullets.append(f"近 {WEEK_DAYS} 日新增 {week_new} 条。")
    if top_subdomain != "—":
        bullets.append(f"活跃子域以「{top_subdomain}」为主。")
    if top_source != "—":
        bullets.append(f"主要来源：{top_source}。")
    for h in highlights[:3]:
        bullets.append(f"• {h}")
    return bullets


def build_weekly_summary_policy() -> WeeklySummary:
    """政策法规系统本周摘要。"""
    start, end = _week_date_range()
    week_new = count_policy_recent_days(WEEK_DAYS)
    total = count_policy_track_rows()
    src_df = aggregate_policy_by_source(5)
    recent_df = fetch_policy_recent_rows(WEEK_DAYS, 80)
    sub_top = _top_from_counter(_subdomain_counter(recent_df))
    top_src = _top_source_label(src_df)
    highlights = _format_highlights(recent_df)
    bullets = _build_bullets(
        week_new,
        top_src,
        sub_top,
        highlights,
        "本周暂无新增政策法规条目，可稍后刷新或触发政策源同步。",
    )
    return WeeklySummary(start, end, week_new, total, top_src, sub_top, highlights, bullets)


def build_weekly_summary_meeting() -> WeeklySummary:
    """国际会议系统本周摘要。"""
    start, end = _week_date_range()
    week_new = count_meeting_recent_days(WEEK_DAYS)
    total = count_meeting_track_rows()
    src_df = aggregate_meeting_by_source(5)
    recent_df = fetch_meeting_recent_rows(WEEK_DAYS, 80)
    sub_top = _top_from_counter(_subdomain_counter(recent_df))
    top_src = _top_source_label(src_df)
    highlights = _format_highlights(recent_df)
    bullets = _build_bullets(
        week_new,
        top_src,
        sub_top,
        highlights,
        "本周暂无新增国际会议条目，可稍后刷新或触发信源同步。",
    )
    return WeeklySummary(start, end, week_new, total, top_src, sub_top, highlights, bullets)


def build_weekly_summary_literature() -> WeeklySummary:
    """文献监测系统本周摘要。"""
    start, end = _week_date_range()
    week_new = count_literature_recent_days(WEEK_DAYS)
    total = count_literature_track_rows()
    src_df = aggregate_literature_by_source(5)
    recent_df = fetch_literature_recent_rows(WEEK_DAYS, 80)
    top_src = _top_source_label(src_df)
    highlights = _format_highlights(recent_df)
    bullets = _build_bullets(
        week_new,
        top_src,
        "—",
        highlights,
        "本周暂无新增文献条目，可稍后刷新或触发文献同步。",
    )
    return WeeklySummary(start, end, week_new, total, top_src, "—", highlights, bullets)


@st.cache_data(ttl=45)
def cached_policy_weekly_summary() -> WeeklySummary:
    try:
        return build_weekly_summary_policy()
    except Exception:
        s, e = _week_date_range()
        return WeeklySummary(s, e, 0, 0, "—", "—", [], ["数据暂不可用，请检查 MySQL 连接。"])


@st.cache_data(ttl=45)
def cached_meeting_weekly_summary() -> WeeklySummary:
    try:
        return build_weekly_summary_meeting()
    except Exception:
        s, e = _week_date_range()
        return WeeklySummary(s, e, 0, 0, "—", "—", [], ["数据暂不可用，请检查 MySQL 连接。"])


@st.cache_data(ttl=45)
def cached_literature_weekly_summary() -> WeeklySummary:
    try:
        return build_weekly_summary_literature()
    except Exception:
        s, e = _week_date_range()
        return WeeklySummary(s, e, 0, 0, "—", "—", [], ["数据暂不可用，请检查 MySQL 连接。"])


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
        return count_policy_recent_days(WEEK_DAYS, keyword=(kw or "").strip() or None)
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
def cached_meeting_track_recent7(kw: str) -> int:
    """国际会议近 7 日条数。"""
    try:
        return count_meeting_recent_days(WEEK_DAYS, keyword=(kw or "").strip() or None)
    except Exception:
        return 0


@st.cache_data(ttl=45)
def cached_meeting_track_recent30(kw: str) -> int:
    """国际会议近 30 日条数（兼容旧口径）。"""
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


@st.cache_data(ttl=45)
def cached_literature_track_count(kw: str, source: str = "") -> int:
    """文献库总行数。"""
    try:
        return count_literature_track_rows(
            keyword=(kw or "").strip() or None,
            source=(source or "").strip() or None,
        )
    except Exception:
        return 0


@st.cache_data(ttl=45)
def cached_literature_track_recent7(kw: str, source: str = "") -> int:
    """文献库近 7 日条数。"""
    try:
        return count_literature_recent_days(
            WEEK_DAYS,
            keyword=(kw or "").strip() or None,
            source=(source or "").strip() or None,
        )
    except Exception:
        return 0


@st.cache_data(ttl=45)
def cached_literature_track_page_rows(
    kw: str, offset: int, limit: int, source: str = ""
) -> pd.DataFrame:
    """文献库分页明细。"""
    try:
        return fetch_literature_track_page(
            offset,
            limit,
            keyword=(kw or "").strip() or None,
            source=(source or "").strip() or None,
        )
    except Exception:
        return pd.DataFrame()
