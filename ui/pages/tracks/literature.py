"""
功能：系统三 · 文献情报监测系统子页（本周摘要 + 来源筛选表格）。

输入：Streamlit 控件状态。
输出：无。
上下游：`services.track_service`、`core.mysql_monitor_tracks`、`ui.components.track_shell`。
"""

from __future__ import annotations

import streamlit as st

from core.mysql_monitor_tracks import literature_monitor_status
from services import track_service as trk
from ui.components.tables import empty_dataframe
from ui.components.track_shell import render_system_layout, render_weekly_summary_block
from ui.components.track_themes import get_theme

_THEME = get_theme("literature")
_TABLE_COLS = ["标题", "来源", "作者", "期刊/会议", "类型", "DOI", "时间", "链接"]
_PAGE_LIMIT = 50


def _render_body() -> None:
    summary = trk.cached_literature_weekly_summary()
    render_weekly_summary_block(_THEME, summary)

    lit = literature_monitor_status()
    if lit.get("message"):
        st.caption(str(lit.get("message")))

    st.markdown("##### 📊 文献情报数据表")
    fc1, fc2 = st.columns([2, 1])
    with fc1:
        kw_in = st.text_input("关键词（标题/摘要）", key="track_lit_kw")
    with fc2:
        src_sel = st.selectbox(
            "来源",
            ["全部", "arxiv", "scopus", "springer"],
            key="track_lit_source",
        )
    kw = (kw_in or "").strip()
    src_filter = "" if src_sel == "全部" else src_sel
    total = trk.cached_literature_track_count(kw, src_filter)
    recent7 = trk.cached_literature_track_recent7(kw, src_filter)
    pages = max(1, (total + _PAGE_LIMIT - 1) // _PAGE_LIMIT) if total > 0 else 1
    pkey = "track_lit_page"
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
    df = trk.cached_literature_track_page_rows(kw, offset, _PAGE_LIMIT, src_filter)
    if total <= 0 or df.empty:
        show = empty_dataframe(_TABLE_COLS)
    else:
        show = df
    st.dataframe(show, use_container_width=True, hide_index=True, height=400)


def render_literature_system_page() -> None:
    """系统三入口。"""
    render_system_layout(_THEME, _render_body)
