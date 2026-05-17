"""
功能：渲染「📊 监测看板」整页——指标、情报表、三元分类、环形图与关键词池。

输入：无参数；implicit 依赖 `@st.cache_data` 数据源（dashboard_service）。
输出：无；Streamlit 组件副作用。
上下游：`app.main` 路由；`services.dashboard_service`、`ui.components.charts`、`ui.components.chips`。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from models.schema import RISK_DOMAIN_CHOICES
from services import dashboard_service as dash
from ui.components.charts import fig_taxonomy_donut
from ui.components.chips import render_keyword_chips


def render_dashboard_page() -> None:
    """功能：监测看板单页入口。"""
    total_incidents, total_tags, taxonomy_kinds = dash.cached_stats()
    kw_df = dash.cached_keywords()
    kw_total = len(kw_df) if not kw_df.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("识别风险情报", total_incidents, help="已入库的 AI 治理/安全事件总数")
    with c2:
        st.metric("去重关键词总量", total_tags, help="从所有情报标签中提取的独立关键词数")
    with c3:
        st.metric("风险子域种数", taxonomy_kinds, help="动态演化的风险分类体系中不同子域数量")
    with c4:
        st.metric("自增长词库节点", kw_total, help="系统自动发现并持续追踪的领域术语数量")

    st.divider()

    left, right = st.columns([3, 2])

    with left:
        st.markdown("#### 📍 最新监测情报")
        df_latest = dash.cached_latest_incidents(20)
        if not df_latest.empty:
            if "主域" in df_latest.columns:
                df_latest["主域"] = (
                    df_latest["主域"].astype(str).str.replace(r"\s*\(.+$", "", regex=True).str.strip()
                )
            st.dataframe(
                df_latest.drop(columns=["来源"], errors="ignore"),
                use_container_width=True,
                hide_index=True,
                height=380,
            )
        else:
            st.dataframe(
                pd.DataFrame({"提示": ["暂无监测数据，请从演示操作区触发同步。"]}),
                use_container_width=True,
                hide_index=True,
                height=100,
            )

        st.markdown("#### 🌳 动态风险分类体系（三元主域 → 子域）")
        st.caption(
            "主域划分对齐 AI 安全与治理领域通行的「意图—来源」三类风险表述，便于与主流政策与学术话语对接；"
            "子域由抽取结果与语料统计动态演化。"
        )
        with st.expander("分类口径与依据（说明）", expanded=False):
            st.markdown(
                """
**三元主域**对应学界与产业常用的风险分层：**恶意滥用**（Malicious Use）、**意外失效**
（Accidental Failure / 可靠性）、**系统性与伦理风险**（Systemic & Ethical），与 NIST AI RMF、
OECD AI 原则、欧盟《人工智能法案》等国内外治理框架中的风险维度在**语义上可对齐**（非对某一条款的逐字映射）。

**子域**为在各主域下由模型标注、检索增强与词频统计共同沉淀的议题标签，会随监测语料扩充而**自动演化**。
                """.strip()
            )
        tax_df = dash.cached_taxonomy()
        dom_cols = st.columns(3)
        for i, domain_label in enumerate(RISK_DOMAIN_CHOICES):
            short = domain_label.split("(")[0].strip()
            sub_df = tax_df[tax_df["domain"] == domain_label].head(10) if not tax_df.empty else pd.DataFrame()
            with dom_cols[i]:
                st.markdown(f"**{short}**")
                if sub_df.empty:
                    st.caption("积累中…" if tax_df.empty else "—")
                else:
                    for _, row in sub_df.iterrows():
                        st.caption(f"· {row['subdomain']}（×{int(row['tax_count'])}）")

    with right:
        st.markdown("#### 📊 风险主域分布")
        tax_df_r = dash.cached_taxonomy()

        _txe = tax_df_r.empty
        if not tax_df_r.empty:
            domain_agg = tax_df_r.groupby("domain")["tax_count"].sum().reset_index()
            domain_agg["主域"] = domain_agg["domain"].str.replace(r"\s*\(.+$", "", regex=True).str.strip()
            domain_agg = domain_agg.rename(columns={"tax_count": "情报数"})
            d_lbl = domain_agg["主域"].tolist()
            d_val = pd.to_numeric(domain_agg["情报数"], errors="coerce").fillna(0).astype(int).tolist()
        else:
            d_lbl, d_val = [], []

        fig_domain = fig_taxonomy_donut(
            d_lbl,
            d_val,
            height=360,
            taxonomy_empty_banner="暂无分类统计数据。" if _txe else None,
        )
        st.plotly_chart(fig_domain, use_container_width=True, key="dash_risk_domain_donut")

        st.markdown("#### 🔥 高频风险子域 (Top 8 + 其他)")

        if not tax_df_r.empty:
            sub_sorted = tax_df_r.sort_values("tax_count", ascending=False).reset_index(drop=True)
            short_dom = sub_sorted["domain"].str.replace(r"\s*\(.+$", "", regex=True).str.strip()
            if len(sub_sorted) > 8:
                head = sub_sorted.head(8)
                short_h = short_dom.head(8)
                labels = (head["subdomain"] + " · " + short_h).tolist()
                vals = pd.to_numeric(head["tax_count"], errors="coerce").fillna(0).astype(int).tolist()
                other_count = int(pd.to_numeric(sub_sorted["tax_count"].iloc[8:], errors="coerce").fillna(0).sum())
                if other_count > 0:
                    labels.append("其他")
                    vals.append(other_count)
            else:
                labels = (sub_sorted["subdomain"] + " · " + short_dom).tolist()
                vals = pd.to_numeric(sub_sorted["tax_count"], errors="coerce").fillna(0).astype(int).tolist()
        else:
            labels, vals = [], []

        fig_sub = fig_taxonomy_donut(
            labels,
            vals,
            height=400,
            taxonomy_empty_banner="暂无子域数据。" if _txe else None,
        )
        st.plotly_chart(fig_sub, use_container_width=True, key="dash_subdomain_donut")

        st.markdown("#### 🧬 自增长关键词池")
        render_keyword_chips(kw_df, max_terms=40, n_cols=4)
