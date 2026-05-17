"""
功能：「📌 专项监测」——政策/会议两张表与文献占位状态。

输入：关键词过滤控件。
输出：无。
上下游：`services.track_service`、`core.mysql_monitor_tracks.literature_monitor_status`、`ui.components.tables`。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.mysql_monitor_tracks import literature_monitor_status
from services import track_service as trk
from ui.components.tables import empty_dataframe


def render_tracks_page() -> None:
    """功能：专项监测单列纵向布局。"""
    _TRACK_PREVIEW = 80
    _POL_COLS = ["标题", "资讯类别", "主域", "子域", "摘要", "来源平台", "时间"]
    _MTG_COLS = ["标题", "资讯类别", "主域", "议题(main_topic)", "子域", "摘要", "来源平台", "时间"]

    st.markdown("#### 📌 专项监测")
    st.caption(
        "自上而下三块：**政策法规/科技政策**（policy+report）、**重大国际会议**（meeting）、"
        "**文献占位**。各块仅展示关键词筛选下最新若干条；主导航仍为水平 radio。"
    )

    st.markdown("### 📋 政策法规 / 科技政策")
    kw_p_in = st.text_input(
        "关键词（可选，标题/摘要，与 policy+report 类型 AND）",
        key="track_policy_kw",
    )
    kw_ps = (kw_p_in or "").strip()
    total_p = trk.cached_policy_track_count(kw_ps)
    recent7_p = trk.cached_policy_track_recent7(kw_ps)
    st.caption(
        f"符合条件 **{total_p:,}** 条 · 近 7 日 **{recent7_p:,}** 条 · "
        f"下表最多 **{_TRACK_PREVIEW}** 条（倒序）"
    )
    df_pol = trk.cached_policy_track_page_rows(kw_ps, 0, _TRACK_PREVIEW)
    if total_p <= 0 or df_pol.empty:
        show_pol = empty_dataframe(_POL_COLS)
    else:
        show_pol = df_pol.drop(columns=["id", "main_topic"], errors="ignore")
    st.dataframe(show_pol, use_container_width=True, hide_index=True, height=340)

    st.divider()

    st.markdown("### 🌐 重大国际会议")
    kw_m_in = st.text_input(
        "关键词（可选，标题/摘要/main_topic AND meeting 类型）",
        key="track_meeting_kw",
    )
    kw_ms = (kw_m_in or "").strip()
    total_m = trk.cached_meeting_track_count(kw_ms)
    recent30_m = trk.cached_meeting_track_recent30(kw_ms)
    st.caption(
        f"符合条件 **{total_m:,}** 条 · 近 30 日 **{recent30_m:,}** 条 · "
        f"下表最多 **{_TRACK_PREVIEW}** 条（倒序）"
    )
    df_mtg = trk.cached_meeting_track_page_rows(kw_ms, 0, _TRACK_PREVIEW)
    if total_m <= 0 or df_mtg.empty:
        show_mtg = empty_dataframe(_MTG_COLS)
    else:
        show_mtg = df_mtg.drop(columns=["id"], errors="ignore").copy()
        if "main_topic" in show_mtg.columns:
            show_mtg.rename(columns={"main_topic": "议题(main_topic)"}, inplace=True)
    st.dataframe(show_mtg, use_container_width=True, hide_index=True, height=340)

    st.divider()

    st.markdown("### 📖 国内外文献（预留）")
    lit = literature_monitor_status()
    planned_join = ", ".join(lit.get("planned_tables") or []) or "—"
    df_lit = pd.DataFrame(
        [
            {
                "已实现": bool(lit.get("implemented")),
                "规划表": planned_join,
                "说明": str(lit.get("message") or "文献监测尚未实现。"),
            }
        ]
    )
    st.dataframe(df_lit, use_container_width=True, hide_index=True, height=90)
