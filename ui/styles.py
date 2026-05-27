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
    /* 专项监测 · 三系统 Hub 卡片 */
    .track-hub-card {
        border-radius: 12px;
        padding: 22px 20px 18px;
        margin-bottom: 12px;
        min-height: 220px;
        border: 1px solid #2a3563;
    }
    .track-hub-card-policy {
        background: linear-gradient(145deg, #1a2744 0%, #1e3a5f55 100%);
        border-left: 5px solid #2563eb;
    }
    .track-hub-card-meeting {
        background: linear-gradient(145deg, #251a44 0%, #3b1f6e55 100%);
        border-left: 5px solid #7c3aed;
    }
    .track-hub-card-literature {
        background: linear-gradient(145deg, #142a24 0%, #064e3b55 100%);
        border-left: 5px solid #059669;
    }
    .track-hub-card .sys-no { color: #8892b0; font-size: 12px; letter-spacing: 0.05em; }
    .track-hub-card .sys-icon { font-size: 28px; margin: 8px 0 4px; }
    .track-hub-card .sys-name { color: #e8eaf6; font-size: 17px; font-weight: 700; margin-bottom: 6px; }
    .track-hub-card .sys-tagline { color: #a0aec0; font-size: 13px; line-height: 1.45; min-height: 38px; }
    .track-hub-card .sys-stat { color: #c7d0e8; font-size: 13px; margin-top: 14px; }
    .track-hub-card .sys-stat strong { color: #fff; font-size: 22px; }
    /* 专项监测 · 子系统顶栏 */
    .track-system-banner {
        border-radius: 10px;
        padding: 16px 20px;
        margin-bottom: 16px;
        border: 1px solid #2a3563;
    }
    .track-banner-policy {
        background: linear-gradient(90deg, #1e3a5f44 0%, transparent 70%);
        border-left: 5px solid #2563eb;
    }
    .track-banner-meeting {
        background: linear-gradient(90deg, #3b1f6e44 0%, transparent 70%);
        border-left: 5px solid #7c3aed;
    }
    .track-banner-literature {
        background: linear-gradient(90deg, #064e3b44 0%, transparent 70%);
        border-left: 5px solid #059669;
    }
    .track-system-banner .banner-no { color: #8892b0; font-size: 12px; }
    .track-system-banner .banner-title { color: #e8eaf6; font-size: 20px; font-weight: 700; margin: 4px 0; }
    .track-system-banner .banner-tagline { color: #a0aec0; font-size: 13px; }
    /* 专项监测 · 左侧系统切换 */
    .track-switch-item {
        border-radius: 8px;
        padding: 10px 12px;
        margin-bottom: 6px;
        border: 1px solid transparent;
        cursor: default;
    }
    .track-switch-item.active-policy {
        background: #2563eb22;
        border-color: #2563eb;
    }
    .track-switch-item.active-meeting {
        background: #7c3aed22;
        border-color: #7c3aed;
    }
    .track-switch-item.active-literature {
        background: #05966922;
        border-color: #059669;
    }
    .track-switch-item.inactive { background: #1a1f2e; border-color: #2a3563; opacity: 0.75; }
    .track-switch-item .sw-no { font-size: 11px; color: #8892b0; }
    .track-switch-item .sw-name { font-size: 14px; color: #e8eaf6; font-weight: 600; }
    .track-summary-box {
        background: #1a1f35;
        border: 1px solid #2a3563;
        border-radius: 10px;
        padding: 14px 18px;
        margin-bottom: 12px;
    }
    .track-summary-box ul { margin: 8px 0 0; padding-left: 18px; color: #c7d0e8; font-size: 13px; }
    .track-summary-box li { margin-bottom: 4px; }
    </style>
    """,
        unsafe_allow_html=True,
    )
