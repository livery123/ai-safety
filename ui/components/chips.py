"""
功能：自增长关键词池展示——仅使用原生 Streamlit（`st.caption` + 列布局），不使用 `unsafe_allow_html`。

输入：高频词 DataFrame（含 keyword / count）。
输出：无；写入组件树。
上下游：`ui.pages.dashboard`；视觉上为栅格 caption，与原 HTML chip 略有差异但更利于前端 reconciler 稳定。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def render_keyword_chips(kw_df: pd.DataFrame, *, max_terms: int = 40, n_cols: int = 4) -> None:
    """
    功能：横向折行排列关键词条目（每项一行 caption）。
    输入：keywords DataFrame；max_terms 截取前 N 条。
    输出：无。
    """
    if kw_df.empty:
        st.caption("🌱 词库为空，触发一次同步后自动填充。")
        return

    top_kw = kw_df.head(max_terms)
    cols = st.columns(n_cols)
    for i, (_, row) in enumerate(top_kw.iterrows()):
        with cols[i % n_cols]:
            st.caption(f"🏷️ {row['keyword']} ×{row['count']}")
