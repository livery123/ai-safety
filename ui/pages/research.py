"""
功能：「📚 深度调研」——表单、后台任务卡片、报告历史与**固定挂载**的报告正文区。

输入：API_KEY、BASE_URL、LLM_MODEL（core.config）；session_state 选中报告 id。
输出：无；写入 session_state.bg_deep_job 等。
上下游：`core.ui_jobs.start_job_thread`、`services.research_service`、`ui.components.job_panel`、`ui.state.SessionKeys`。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.config import API_KEY, BASE_URL, LLM_MODEL
from core.ui_jobs import start_job_thread
from models.schema import RISK_DOMAIN_CHOICES
from services import research_service as rs
from ui.components.job_panel import render_background_job_panel
from ui.state import SessionKeys


def render_research_page() -> None:
    """功能：深度调研单页入口。"""
    st.markdown("### 📚 问答式深度调研")
    st.caption(
        "基于 Chroma 向量 + MySQL 全文（若已迁移）混合检索证据，由大模型生成带引用的 Markdown 报告；"
        "可选择写入 `research_reports` 便于留痕。"
    )
    rq = st.text_area(
        "研究问题",
        height=88,
        placeholder="例如：欧盟 AI 法案执法近期有哪些公开讨论？",
        key="deep_research_question",
    )
    dr1, dr2, dr3 = st.columns(3)
    with dr1:
        dom_opts = ["（不筛选）"] + list(RISK_DOMAIN_CHOICES)
        dr_domain_sel = st.selectbox("主域筛选（可选）", dom_opts, key="dr_domain")
        dr_risk_domain = None if dr_domain_sel.startswith("（") else dr_domain_sel
    with dr2:
        dr_source = st.text_input("信源 source 精确匹配（可选）", "", key="dr_source")
    with dr3:
        dr_top_k = st.slider("纳入证据条数", 6, 32, 16, key="dr_top_k")

    dr_save = st.checkbox("生成后写入 MySQL（research_reports + 引用行）", value=True, key="dr_save")
    dr_preview = st.checkbox("仅检索证据、暂不调用 LLM（调试用）", value=False, key="dr_preview")

    st.caption(
        "检索与报告生成在**后台线程**执行，不阻塞其他访客；提交后在下方卡片点「刷新状态」查看进度。"
    )

    if st.button("🔎 后台提交：检索并生成报告", type="primary", use_container_width=True, key="dr_run"):
        if not (rq or "").strip():
            st.warning("请先填写研究问题。")
        elif not dr_preview and not (API_KEY or "").strip():
            st.error("未配置 DASHSCOPE_API_KEY，无法调用大模型生成报告（可勾选「仅检索」跳过 LLM）。")
        else:
            payload = {
                "question": (rq or "").strip(),
                "preview_only": bool(dr_preview),
                "save_report": bool(dr_save),
                "top_k": int(dr_top_k),
                "risk_domain": dr_risk_domain,
                "source": (dr_source or "").strip(),
                "llm_model": LLM_MODEL,
                "api_key": (API_KEY or "").strip(),
                "base_url": (BASE_URL or "").strip(),
            }
            jid = start_job_thread("deep_research", payload)
            st.session_state["bg_deep_job"] = jid
            st.session_state.pop("_bg_deep_job_cleared_cache", None)
            st.rerun()

    render_background_job_panel("bg_deep_job", "深度调研")

    st.divider()
    st.markdown("**近期已保存报告**")
    hist = rs.cached_research_report_list(30)
    records = hist.to_dict("records") if not hist.empty else []
    n_hist = len(records)

    st.caption(
        "暂无保存的报告；成功后约 30s 内缓存可见。"
        if n_hist <= 0
        else f"共 **{n_hist}** 条缓存记录可供选择。"
    )
    pick_opts = list(range(max(1, n_hist)))

    def _dr_hist_fmt(i: int) -> str:
        if n_hist <= 0:
            return "（暂无保存的报告）"
        rr = records[i]
        return f"#{int(rr['id'])} — " f"{str(rr.get('question') or '')[:60]}"

    pick_ix = int(
        st.selectbox(
            "选择一条查看",
            pick_opts,
            format_func=_dr_hist_fmt,
            disabled=bool(n_hist <= 0),
            key="dr_hist_pick",
        )
    )

    sid = SessionKeys.SELECTED_RESEARCH_REPORT_ID
    if st.button("载入所选报告", key="dr_hist_load", disabled=bool(n_hist <= 0)):
        hid = int(records[pick_ix]["id"])
        st.session_state[sid] = hid

    # 固定容器：按钮只写入 session_state，正文区每轮 rerun 一致存在，减轻 insertBefore/reconcile 风险
    with st.container(border=True):
        rid = st.session_state.get(sid)
        if not rid:
            st.caption("请选择一条记录后点击「载入所选报告」查看正文与引用。")
        else:
            row = rs.fetch_research_report(int(rid))
            if row and row.get("report_markdown"):
                st.markdown(str(row["report_markdown"]))
                if row.get("sources"):
                    with st.expander("引用行（research_report_sources）"):
                        st.dataframe(
                            pd.DataFrame(row["sources"]),
                            use_container_width=True,
                            hide_index=True,
                        )
            elif row:
                st.warning("该记录尚无 report_markdown 正文。")
            else:
                st.warning("未找到该报告（可能已删除）；可重新载入或清空缓存后重试。")
