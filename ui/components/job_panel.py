"""
功能：后台任务（SQLite `ui_background_jobs`）状态卡片——壳与按 job_type 分派的结果渲染。

输入：slot_key（session_state 中存放 job id 的键）、展示标题。
输出：无；可能触发 `st.cache_data.clear()`、`st.rerun()`。
上下游：`core.ui_jobs.get_job`；各页面按需调用 `render_background_job_panel`。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

import streamlit as st

from core.ui_jobs import get_job


def _render_feed_sync_success(res: Dict[str, Any], label: str) -> None:
    """功能：信源批量同步任务完成摘要（卫报/NYT/RSS 等）。"""
    st.success(
        f"✅ {label}同步完成：入库 **{res.get('saved', 0)}**，"
        f"跳过已有 {res.get('skipped_url_dup', 0)}，"
        f"无关 {res.get('skipped_no_incident', 0)}，失败 {res.get('failed', 0)}"
    )
    nkw = res.get("new_keywords") or []
    if nkw:
        st.info("新增关键词：" + ", ".join(str(x) for x in nkw[:8]))
    dlog = res.get("debug_log") or []
    if dlog:
        with st.expander("详细日志"):
            for line in dlog:
                st.caption(str(line))


def _render_agent_scout_completed(res: Dict[str, Any]) -> None:
    """功能：Agent URL 侦察完成摘要。"""
    st.success(f"✅ Agent 完成：提取 **{res.get('extracted', 0)}** 条，入库 **{res.get('saved', 0)}** 条")
    nkw = res.get("new_keywords") or []
    if nkw:
        st.info("新增关键词：" + ", ".join(str(x) for x in nkw[:6]))
    dbg = res.get("debug_info") or []
    if dbg:
        with st.expander("调试日志"):
            for line in dbg:
                st.caption(str(line))


def _render_deep_research_completed(res: Dict[str, Any], slot_key: str) -> None:
    """功能：深度调研完成：证据预览 +（可选）报告正文与下载。"""
    prev_only = bool(res.get("preview_only"))
    if prev_only:
        st.info("已选择「仅检索」：跳过 LLM。")
    hits_n = int(res.get("hits_count") or 0)
    st.success(f"深度调研已完成：检索 **{hits_n}** 条证据。")
    evs = res.get("evidence_previews") or []
    if evs:
        with st.expander("证据摘要", expanded=False):
            for i, it in enumerate(evs, 1):
                st.caption(
                    f"**{i}.** article_id={it.get('article_id')} rrf={it.get('rrf')} — "
                    f"{it.get('snippet', '')}…"
                )
    if not prev_only:
        report_md = str(res.get("report_markdown") or "")
        if report_md.strip():
            st.markdown(report_md)
            rid = res.get("saved_report_id")
            if rid:
                st.caption(f"已保存至 MySQL，`research_reports.id` = **{rid}**")
            fn = f"DeepResearch_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
            st.download_button(
                "下载 Markdown 报告",
                data=report_md.encode("utf-8"),
                file_name=fn,
                mime="text/markdown",
                key=f"dr_dl_{slot_key}",
            )
        else:
            st.warning("报告正文为空，请检查模型与 API。")


def _maybe_clear_cache_after_complete(slot_key: str) -> None:
    """功能：任务首次进入 completed 时清一次 Streamlit data cache。"""
    if not st.session_state.get(f"_{slot_key}_cleared_cache"):
        st.cache_data.clear()
        st.session_state[f"_{slot_key}_cleared_cache"] = True


def render_completed_job_body(row: Dict[str, Any], slot_key: str) -> None:
    """
    功能：根据 job_type 渲染已完成任务的主体内容（不含标题与按钮）。
    输入：任务行字典、slot_key（用于下载按钮 key 前缀）。
    输出：无。
    """
    res: Dict[str, Any] = row.get("result") or {}
    jt = str(row.get("job_type") or "")

    sync_labels = {
        "guardian_sync": "卫报",
        "nyt_sync": "NYT",
        "wechat_rss_sync": "微信 RSS",
        "xinhua_tech_sync": "新华网科技",
        "sina_tech_sync": "新浪科技",
        "policy_sync": "政策/法规",
        "literature_sync": "文献库",
    }
    if jt in sync_labels:
        if jt == "literature_sync":
            st.success(
                f"✅ {sync_labels[jt]}同步完成：新入库 **{res.get('saved', 0)}**，"
                f"已有跳过 {res.get('skipped_url_dup', 0)}，失败 {res.get('failed', 0)}"
            )
        else:
            _render_feed_sync_success(res, sync_labels[jt])
    elif jt == "agent_scout":
        _render_agent_scout_completed(res)
    elif jt == "deep_research":
        _render_deep_research_completed(res, slot_key)
    else:
        st.info(f"任务类型 `{jt}`：无专用结果展示模板。")


def render_background_job_panel(slot_key: str, title: str) -> None:
    """
    功能： bordered 占位容器内展示单条后台任务生命周期与结果。
    输入：slot_key（如 bg_guardian_job）、title（卡片标题）。
    输出：无。
    """
    jid = st.session_state.get(slot_key)
    row = get_job(str(jid)) if jid else None

    with st.container(border=True):
        if not jid:
            st.caption(f"**{title}**：当前无进行中任务；如需查看进度请先提交后台任务。")
            return

        if row is None:
            st.warning(f"{title}：任务记录不存在。")
            if st.button("关闭", key=f"dismiss_{slot_key}"):
                st.session_state.pop(slot_key, None)
                st.rerun()
            return

        stat = str(row.get("status") or "")
        st.markdown(f"**{title}** · `{str(jid)[:8]}…` · 状态：**{stat}**")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔄 刷新状态", key=f"refresh_{slot_key}"):
                st.rerun()
        with c2:
            if stat in ("completed", "failed") and st.button("收起", key=f"close_{slot_key}"):
                st.session_state.pop(slot_key, None)
                st.session_state.pop(f"_{slot_key}_cleared_cache", None)
                st.rerun()

        err_msg = (row.get("error_text") or "").strip()

        if stat == "failed":
            st.error(err_msg or "任务失败")
            return

        if stat != "completed":
            st.caption("任务在后台执行中，稍后点「刷新状态」或与本页任一控件交互以重跑脚本。")
            return

        _maybe_clear_cache_after_complete(slot_key)
        render_completed_job_body(row, slot_key)
