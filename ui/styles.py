"""
功能：注入全局 `<style>`（卡片、标签芯片等），保持汇报版深色主题一致。

输入：由 `layout.inject_global_styles()` 在每次 rerun 开始时调用。
输出：通过 `st.markdown(..., unsafe_allow_html=True)` 写入固定 CSS；无返回值。
上下游：仅 `ui.layout` 调用；与 `ui.components.chips` 中使用的 `.tag-chip` class 对应。

说明：此处保留 `unsafe_allow_html` 仅限**固定不变的样式块**，降低与业务动态 HTML 混用带来的 reconciler 风险。
"""

from __future__ import annotations

import streamlit as st


def inject_global_styles() -> None:
    """
    功能：挂载全局 CSS（metric-card / tag-chip / section-header）。
    输入：无。
    输出：无；副作用为 Streamlit 渲染一段固定 HTML/CSS。
    上下游：`ui.layout.render_app_shell` 在页面顶部调用。
    """
    st.markdown(
        """
    <style>
    .metric-card {
        background: linear-gradient(135deg, #1a1f35 0%, #242b4a 100%);
        border: 1px solid #2a3563;
        border-left: 4px solid #4f8ef7;
        border-radius: 10px;
        padding: 18px 22px;
        margin-bottom: 8px;
    }
    .metric-card .label { color: #8892b0; font-size: 13px; margin-bottom: 4px; }
    .metric-card .value { color: #e8eaf6; font-size: 32px; font-weight: 700; line-height: 1; }
    .metric-card .delta { color: #4ade80; font-size: 12px; margin-top: 4px; }
    .tag-chip {
        background: #1e2130; color: #7eb8f7; padding: 3px 10px;
        border-radius: 12px; margin: 2px; border: 1px solid #2a3563;
        display: inline-block; font-size: 12px;
    }
    .section-header {
        border-bottom: 2px solid #2a3563;
        padding-bottom: 6px;
        margin-bottom: 16px;
        color: #c7d0e8;
    }
    </style>
    """,
        unsafe_allow_html=True,
    )
