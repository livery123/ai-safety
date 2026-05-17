"""
功能：`app.main` 的页面壳：配置、全局样式、顶部标题区与主导航。

输入：`st.set_page_config` 参数等（写死在函数内）。
输出：当前选中主导航文案（与 `ui.state.NAV_MAIN` 对齐）。
上下游：`core.db.init_db`；被根 `app.py` 调用。
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from core.db import init_db

from ui.state import NAV_MAIN, SessionKeys
from ui.styles import inject_global_styles


def configure_page() -> None:
    """功能：`set_page_config` + 初始化 Agent SQLite。"""
    st.set_page_config(
        page_title="全球 AI 治理监测系统",
        layout="wide",
        page_icon="🛡️",
        initial_sidebar_state="collapsed",
    )
    init_db()


def render_header_refresh_row() -> None:
    """功能：渲染标题栏与刷新数据按钮（清理数据缓存并重跑脚本）。"""
    col_title, col_ts = st.columns([4, 1])
    with col_title:
        st.markdown("## 🛡️ 国际动态监测平台")
        st.caption("基于大语言模型的 AI 安全动态智能感知平台 · 实时追踪监管政策、技术风险与治理事件")
    with col_ts:
        st.caption(f"数据更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        if st.button("🔄 刷新数据", use_container_width=True):
            st.cache_data.clear()
            st.rerun()


def render_main_navigation() -> str:
    """
    功能：水平主导航 radio。
    输入：session_state[`nav_main_radio`] 或由控件默认。
    输出：当前选中的页面文案（应为 `NAV_MAIN` 中之一）。
    """
    page = st.radio(
        "主导航",
        NAV_MAIN,
        horizontal=True,
        label_visibility="collapsed",
        key=SessionKeys.NAV_MAIN_RADIO,
    )
    return str(page)


def render_app_shell() -> str:
    """
    功能：组合「全局样式 → 标题区 → 分隔线 → 主导航」，返回所选页面 key。
    输入：无。
    输出：导航当前选项字符串。
    """
    inject_global_styles()
    render_header_refresh_row()
    st.divider()
    return render_main_navigation()


def render_sidebar_about() -> None:
    """功能：左侧栏静态简介（不参与业务操作）。"""
    with st.sidebar:
        st.markdown("### 🛡️ 系统简介")
        st.markdown("""
**全球 AI 治理监测与自增长 Agent 系统**

自动感知全球 AI 安全动态，基于三元意图风险模型结构化分类，持续演化知识体系。

**核心能力**
- 多信源同步（卫报 / NYT / 新华网 / 新浪科技 / 微信 RSS，后台线程 + SQLite 任务状态）
- 任意 URL 深度 Agent 侦察
- 问答式深度调研（混合检索 + 报告留痕）
- LLM 并发抽取（5 路并发）
- RAG 增强风险子域精炼
- 自增长关键词与子域体系

**技术栈**
Python · Streamlit · MySQL  
Crawl4AI · ChromaDB · httpx
        """)
        st.divider()
        st.caption(f"© {datetime.now().year} AI Safety Research")
