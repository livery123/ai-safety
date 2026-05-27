"""
功能：专项监测三系统共用 UI 壳（Hub 卡片、顶栏、切换器、本周摘要区）。

输入：TrackTheme、WeeklySummary、回调与表格区由子页注入。
输出：无；Streamlit 渲染副作用。
上下游：`ui.components.track_themes`、`services.track_service.WeeklySummary`、`ui.pages.tracks.*`。
"""

from __future__ import annotations

import html

import streamlit as st

from services.track_service import WeeklySummary
from ui.components.track_themes import (
    TRACK_SYSTEM_ORDER,
    TrackSystemKey,
    TrackTheme,
    get_theme,
)
from ui.state import SessionKeys


def go_to_track_hub() -> None:
    """返回三系统 Hub 大厅。"""
    st.session_state.pop(SessionKeys.TRACK_SYSTEM, None)
    st.rerun()


def go_to_track_system(key: TrackSystemKey) -> None:
    """进入指定子系统全屏页。"""
    st.session_state[SessionKeys.TRACK_SYSTEM] = key
    st.rerun()


def render_hub_card(theme: TrackTheme, week_new: int, total: int) -> None:
    """
    功能：Hub 页单个系统入口卡片（含统计与进入按钮）。
    输入：主题、本周新增、累计总量。
    输出：无。
    """
    st.markdown(
        f"""
<div class="track-hub-card {theme.hub_card_class}">
  <div class="sys-no">{html.escape(theme.system_no)}</div>
  <div class="sys-icon">{theme.icon}</div>
  <div class="sys-name">{html.escape(theme.full_name)}</div>
  <div class="sys-tagline">{html.escape(theme.tagline)}</div>
  <div class="sys-stat">本周新增 <strong>{week_new:,}</strong> · 累计 <strong>{total:,}</strong></div>
</div>
        """.strip(),
        unsafe_allow_html=True,
    )
    if st.button(
        f"进入 {theme.short_name} →",
        key=f"hub_enter_{theme.key}",
        use_container_width=True,
        type="primary",
    ):
        go_to_track_system(theme.key)


def render_system_banner(theme: TrackTheme) -> None:
    """子系统页顶栏：编号 + 全称 + 定位语。"""
    st.markdown(
        f"""
<div class="track-system-banner {theme.banner_class}">
  <div class="banner-no">{html.escape(theme.system_no)} · {html.escape(theme.short_name)}</div>
  <div class="banner-title">{theme.icon} {html.escape(theme.full_name)}</div>
  <div class="banner-tagline">{html.escape(theme.tagline)}</div>
</div>
        """.strip(),
        unsafe_allow_html=True,
    )


def render_system_switcher(current: TrackSystemKey) -> None:
    """
    功能：左侧竖排三系统切换器；当前项高亮，其余可点击跳转。
    输入：当前系统 key。
    输出：无。
    """
    st.markdown("##### 子系统")
    for key in TRACK_SYSTEM_ORDER:
        theme = get_theme(key)
        is_active = key == current
        active_cls = f"active-{key}" if is_active else "inactive"
        st.markdown(
            f"""
<div class="track-switch-item {active_cls}">
  <div class="sw-no">{html.escape(theme.system_no)}</div>
  <div class="sw-name">{theme.icon} {html.escape(theme.short_name)}</div>
</div>
            """.strip(),
            unsafe_allow_html=True,
        )
        if not is_active:
            if st.button(
                f"切换至{theme.short_name}",
                key=f"sw_{current}_to_{key}",
                use_container_width=True,
            ):
                go_to_track_system(key)


def render_weekly_summary_block(theme: TrackTheme, summary: WeeklySummary) -> None:
    """
    功能：本周监测摘要区（四指标 + bullet 要点）。
    输入：主题色用于 metric 帮助文案；summary 为聚合结果。
    输出：无。
    """
    st.markdown(
        f"#### 📅 本周监测摘要 · {theme.system_no} · {theme.full_name}  "
        f"（{summary.range_start} ~ {summary.range_end}）"
    )
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("本周新增", f"{summary.week_new:,}", help=f"近 7 日窗口内新增条目")
    with m2:
        st.metric("系统累计", f"{summary.total:,}", help="当前系统口径下全库总量")
    with m3:
        st.metric("活跃子域", summary.top_subdomain, help="近 7 日条目中最常出现的子域")
    with m4:
        st.metric("主要来源", summary.top_source, help="全库来源平台 Top1")

    bullets_html = "".join(f"<li>{html.escape(b)}</li>" for b in summary.bullets)
    st.markdown(
        f'<div class="track-summary-box"><strong style="color:#c7d0e8;">监测要点</strong>'
        f"<ul>{bullets_html}</ul></div>",
        unsafe_allow_html=True,
    )


def render_system_page_header(theme: TrackTheme) -> None:
    """顶栏 + 返回按钮行。"""
    top_left, top_right = st.columns([5, 1])
    with top_left:
        render_system_banner(theme)
    with top_right:
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        if st.button("← 返回三系统", key=f"back_hub_{theme.key}", use_container_width=True):
            go_to_track_hub()


def render_system_layout(theme: TrackTheme, body_fn) -> None:
    """
    功能：子系统页整体布局（左切换器 + 右内容区）。
    输入：theme；body_fn 在无参调用时渲染表格与筛选。
    输出：无。
    """
    render_system_page_header(theme)
    st.caption(theme.data_scope)
    st.divider()
    nav_col, main_col = st.columns([1, 4])
    with nav_col:
        render_system_switcher(theme.key)
    with main_col:
        body_fn()
