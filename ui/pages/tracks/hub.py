"""
功能：专项监测三系统 Hub 大厅——并列展示三个子系统入口卡片。

输入：无；读 track_service 本周/累计统计。
输出：无；点击按钮写入 session_state 并 rerun。
上下游：`ui.components.track_shell`、`services.track_service`、`ui.pages.tracks` 路由。
"""

from __future__ import annotations

import streamlit as st

from services import track_service as trk
from ui.components.track_shell import render_hub_card
from ui.components.track_themes import TRACK_SYSTEM_ORDER, get_theme


def render_tracks_hub() -> None:
    """三系统 Hub 入口页。"""
    st.markdown("#### 📌 专项监测 · 三大子系统")
    st.caption("政策监管 · 国际会议 · 文献情报 三套监测系统并行运行，请选择进入对应系统。")

    summaries = {
        "policy": trk.cached_policy_weekly_summary(),
        "meeting": trk.cached_meeting_weekly_summary(),
        "literature": trk.cached_literature_weekly_summary(),
    }

    cols = st.columns(3)
    for col, key in zip(cols, TRACK_SYSTEM_ORDER):
        theme = get_theme(key)
        sm = summaries[key]
        with col:
            render_hub_card(theme, sm.week_new, sm.total)

    st.divider()
    st.caption(
        "各系统独立维护数据口径与本周摘要；进入后可切换子系统或返回本页。"
    )
