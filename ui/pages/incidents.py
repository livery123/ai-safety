"""
功能：「📋 情报详情」——筛选、分页表、导出与一键日报。

输入：Streamlit widget 状态（filter_domain 等）。
输出：无。
上下游：`services.dashboard_service`、`models.schema.RISK_DOMAIN_CHOICES`、`datetime`。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from models.schema import RISK_DOMAIN_CHOICES
from services import dashboard_service as dash


def render_incidents_page() -> None:
    """功能：情报库分页与日报生成区块。"""
    st.markdown("#### 📋 情报库（筛选 + MySQL 分页）")
    st.caption("列表按时间倒序；仅加载当前页，避免多人访问时一次性拉全表。")

    domains = ["全部"] + list(RISK_DOMAIN_CHOICES)
    fc1, fc2, fc3, fc4 = st.columns([1, 1, 2, 1])
    with fc1:
        sel_domain = st.selectbox("按主域筛选（三元模型）", domains, key="filter_domain")
    with fc2:
        lev_opts = ["全部"] + list(dash.cached_distinct_content_types())
        sel_level = st.selectbox("按资讯类别筛选", lev_opts, key="filter_level")
    with fc3:
        kw_search = st.text_input("关键词搜索（标题/摘要）", key="kw_search")
    with fc4:
        page_lim = int(
            st.select_slider("每页条数", options=[50, 100, 150, 200], value=50, key="inc_page_limit")
        )

    fdom = "" if sel_domain == "全部" else sel_domain
    flev = "" if sel_level == "全部" else sel_level
    fkw_s = (kw_search or "").strip()

    total_n = dash.cached_incidents_count(fdom, flev, fkw_s)
    pages = max(1, (total_n + page_lim - 1) // page_lim) if total_n > 0 else 1
    _pkey = "inc_page_no_val"
    if _pkey not in st.session_state:
        st.session_state[_pkey] = 1
    if int(st.session_state[_pkey]) > pages:
        st.session_state[_pkey] = pages
    pg_cur = int(st.number_input("页码", min_value=1, max_value=pages, step=1, key=_pkey))
    offset = (pg_cur - 1) * page_lim
    df_page = dash.cached_incidents_page(fdom, flev, fkw_s, offset, page_lim)

    if total_n == 0:
        _show_df = pd.DataFrame({"提示": ["暂无数据或当前筛选无结果；可先放宽筛选或从演示操作区触发同步。"]})
        _show_cap = ""
    else:
        _show_df = df_page.drop(columns=["id"], errors="ignore")
        _show_cap = f"符合条件 **{total_n}** 条 · 本页展示 **{len(df_page)}** 条 · 页 **{pg_cur}** / **{pages}**"
    if _show_cap:
        st.caption(_show_cap)
    st.dataframe(_show_df, use_container_width=True, hide_index=True, height=420)
    if total_n > 0 and not df_page.empty:
        csv_bytes = df_page.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 导出本页（CSV）",
            data=csv_bytes,
            file_name=f"AI_Governance_page{pg_cur}_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            key="dl_page_csv",
        )

    st.divider()

    st.markdown("#### 📄 自动化监测日报")
    if st.button("📥 一键生成 AI 治理监测日报", key="gen_report"):
        df_report = dash.cached_latest_incidents(10)
        kw_meta = dash.cached_keywords()
        kw_daily_total = len(kw_meta) if not kw_meta.empty else 0
        if not df_report.empty:
            report_md = f"## AI 治理动态监测内参（{datetime.now().strftime('%Y-%m-%d')}）\n\n"
            report_md += "### 一、最新情报摘要\n\n"
            for _, row in df_report.iterrows():
                ctype = str(row.get("资讯类别", "") or "").strip()
                dom = str(row.get("主域", "") or "").strip()
                sub = str(row.get("子域", "") or "").strip()
                entity = str(row.get("涉及主体", "") or "").strip()
                tri = f"{dom} / {sub}".strip(" /")
                report_md += f"- **[{ctype or '—'}]** {row['title']}（涉及主体：{entity or '—'}）"
                if tri:
                    report_md += f" — 分类：{tri}"
                report_md += "\n"

            report_md += "\n### 二、新兴术语感知\n\n"
            kw_top = kw_meta.head(10)
            if not kw_top.empty:
                report_md += "- 高频新词：" + "、".join(kw_top["keyword"].tolist()) + "\n"

            report_md += "\n### 三、系统统计\n\n"
            stats = dash.cached_stats()
            report_md += (
                f"- 已监测情报：{stats[0]} 条\n"
                f"- 风险子域种数：{stats[2]} 种\n"
                f"- 关键词库节点：{kw_daily_total} 个\n"
            )

            st.code(report_md, language="markdown")
            st.download_button(
                "下载 Markdown 日报",
                data=report_md.encode("utf-8"),
                file_name=f"AI_Governance_Daily_{datetime.now().strftime('%Y%m%d')}.md",
                mime="text/markdown",
                key="dl_report",
            )
        else:
            st.warning("数据库暂无数据，请先触发同步。")
