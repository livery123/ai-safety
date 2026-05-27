"""
功能：系统一 · 政策监管监测系统子页（本周摘要 + 筛选表格）。

输入：Streamlit 控件状态。
输出：无。
上下游：`services.track_service`、`ui.components.track_shell`。
"""

from __future__ import annotations

import streamlit as st

from services import track_service as trk
from ui.components.tables import empty_dataframe
from ui.components.track_shell import render_system_layout, render_weekly_summary_block
from ui.components.track_themes import get_theme

_THEME = get_theme("policy")
_TABLE_COLS = ["标题", "资讯类别", "主域", "子域", "摘要", "来源平台", "时间"]
_PAGE_LIMIT = 50


def _render_body() -> None:
    summary = trk.cached_policy_weekly_summary()
    render_weekly_summary_block(_THEME, summary)

    st.markdown("##### 📊 政策监管数据表")
    kw_in = st.text_input(
        "关键词（标题/摘要，与 policy+report 类型 AND）",
        key="track_policy_kw",
    )
    kw = (kw_in or "").strip()
    total = trk.cached_policy_track_count(kw)
    recent7 = trk.cached_policy_track_recent7(kw)
    pages = max(1, (total + _PAGE_LIMIT - 1) // _PAGE_LIMIT) if total > 0 else 1
    pkey = "track_policy_page"
    if pkey not in st.session_state:
        st.session_state[pkey] = 1
    if int(st.session_state[pkey]) > pages:
        st.session_state[pkey] = pages
    pg = int(st.number_input("页码", min_value=1, max_value=pages, step=1, key=pkey))
    offset = (pg - 1) * _PAGE_LIMIT
    st.caption(
        f"符合条件 **{total:,}** 条 · 近 7 日 **{recent7:,}** 条 · "
        f"页 **{pg}** / **{pages}**"
    )
    df = trk.cached_policy_track_page_rows(kw, offset, _PAGE_LIMIT)
    if total <= 0 or df.empty:
        show = empty_dataframe(_TABLE_COLS)
    else:
        show = df.drop(columns=["id", "main_topic"], errors="ignore")
    st.dataframe(show, use_container_width=True, hide_index=True, height=400)


def render_policy_system_page() -> None:
    """系统一入口。"""
    render_system_layout(_THEME, _render_body)
