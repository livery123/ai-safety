"""
AI 治理监测周报 / 简报生成（多篇综合四维，无单篇分析）。

功能：打包本周监测条目 + 可选历史脉络 → 固定 Prompt → Markdown。
输入：system_key、week_start/end、TrackEntry 列表。
输出：Markdown 字符串；无 DB 写入（由脚本层落库）。
上下游：core/weekly_report_data、core/mysql_weekly_reports、engine/prompts、core.llm_client。
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from core.config import API_KEY, LLM_MODEL
from core.llm_client import OpenAICompatibleBackend
from core.weekly_report_data import TrackEntry
from engine.prompts import (
    BRIEF_REPORT_SYSTEM,
    SYSTEM_LABELS,
    WEEKLY_REPORT_SYSTEM,
)

_MAX_SUMMARY_CHARS = 600
_MAX_TOPIC_CHARS = 400


def pack_entries_for_prompt(
    *,
    system_key: str,
    week_start: date,
    week_end: date,
    entries: List[TrackEntry],
    context_entries: Optional[List[TrackEntry]] = None,
) -> str:
    """
    功能：将条目列表格式化为 User Prompt 材料块。
    输入：系统 key、周界、本周条目、可选历史脉络条目。
    输出：纯文本材料包。
    """
    label = SYSTEM_LABELS.get(system_key, system_key)
    lines = [
        f"## 监测任务",
        f"- 子系统：{label}（{system_key}）",
        f"- 监测周期：{week_start.isoformat()} ～ {week_end.isoformat()}",
        f"- 本周纳入条目数：{len(entries)}",
        "",
    ]
    if not entries:
        lines.append("（本周监测库无新增条目；请按固定章节输出，说明「本周无相关新增」并做趋势性提示，勿编造。）")
        lines.append("")
    else:
        lines.append("## 本周监测条目")
        lines.append("")
        for i, e in enumerate(entries, 1):
            lines.extend(_format_entry_block(i, e))
    ctx = context_entries or []
    if ctx:
        lines.append("## 历史脉络参考（监测周期之前，仅供「与历史政策关系」章节对比，勿逐条复述）")
        lines.append("")
        for j, e in enumerate(ctx, 1):
            lines.extend(_format_entry_block(j, e, prefix="历史"))
    return "\n".join(lines)


def _format_entry_block(index: int, e: TrackEntry, *, prefix: str = "条目") -> List[str]:
    """单条条目 Markdown 块。"""
    label = f"{prefix} {index}"
    summary = (e.summary or e.main_topic or "")[: _MAX_SUMMARY_CHARS]
    topic = (e.main_topic or "")[: _MAX_TOPIC_CHARS]
    block = [
        f"### [{label}] {e.title or '（无标题）'}",
        f"- article_id：{e.article_id}",
        f"- 类型：{e.content_type or '—'}",
        f"- 主域：{e.risk_domain or '—'}",
        f"- 时间：{e.published_at or '—'}",
        f"- 来源：{e.source or '—'}",
    ]
    if e.url:
        block.append(f"- URL：{e.url}")
    if topic:
        block.append(f"- 核心议题：{topic}")
    if summary:
        block.append(f"- 摘要：{summary}")
    if e.entities:
        block.append(f"- 涉及主体：{e.entities}")
    if e.subdomains and e.subdomains != "未指定子域":
        block.append(f"- 子域：{e.subdomains}")
    if e.tags:
        block.append(f"- 标签：{e.tags}")
    block.append("")
    return block


def _fallback_markdown_no_llm(
    *,
    system_key: str,
    week_start: date,
    week_end: date,
    entries: List[TrackEntry],
    report_type: str,
) -> str:
    """无 API Key 或 dry-run 时的结构化占位报告（便于测试与验收入库）。"""
    label = SYSTEM_LABELS.get(system_key, system_key)
    kind = "监测周报" if report_type == "weekly" else "监测简报"
    lines = [
        f"# {label}{kind}（{week_start}～{week_end}）",
        "",
        "## 监测数据概况",
        f"- 监测周期：{week_start.isoformat()} ～ {week_end.isoformat()}",
        f"- 纳入条目：{len(entries)} 条",
        "",
        "## 政策意义",
        "（未调用大模型：请配置 DASHSCOPE_API_KEY 后由 cron 自动生成分析正文。）",
        "",
        "## 可能影响",
        "—",
        "",
        "## 与历史政策关系",
        "—",
        "",
        "## 落地性评估",
        "—",
        "",
        "## 本周重点条目",
    ]
    if not entries:
        lines.append("- 本周监测库无相关新增。")
    else:
        for i, e in enumerate(entries[:10], 1):
            lines.append(f"- [条目 {i}] {e.title}（{e.source}）")
    lines.extend(["", "## 参考文献", ""])
    for i, e in enumerate(entries[:10], 1):
        lines.append(f"- [条目 {i}] {e.title} — {e.source} — {e.url or '—'}")
    return "\n".join(lines)


def generate_weekly_report_markdown(
    *,
    system_key: str,
    week_start: date,
    week_end: date,
    entries: List[TrackEntry],
    context_entries: Optional[List[TrackEntry]] = None,
    report_type: str = "weekly",
    backend: Optional[OpenAICompatibleBackend] = None,
    model: Optional[str] = None,
    temperature: float = 0.25,
    timeout: float = 180.0,
    skip_llm: bool = False,
) -> str:
    """
    功能：生成监测周报或简报 Markdown。
    输入：系统、周界、条目、report_type=weekly|brief；skip_llm 时仅模板。
    输出：Markdown 正文。
    """
    packed = pack_entries_for_prompt(
        system_key=system_key,
        week_start=week_start,
        week_end=week_end,
        entries=entries,
        context_entries=context_entries,
    )
    if skip_llm:
        return _fallback_markdown_no_llm(
            system_key=system_key,
            week_start=week_start,
            week_end=week_end,
            entries=entries,
            report_type=report_type,
        )

    if not (API_KEY or "").strip():
        return _fallback_markdown_no_llm(
            system_key=system_key,
            week_start=week_start,
            week_end=week_end,
            entries=entries,
            report_type=report_type,
        )

    be = backend or OpenAICompatibleBackend()
    system_prompt = WEEKLY_REPORT_SYSTEM if report_type == "weekly" else BRIEF_REPORT_SYSTEM
    user_msg = (
        "请**直接输出完整 Markdown**（不要前言、不要用 ``` 围栏包裹全文）。\n"
        "材料中 [条目 n] / [历史 n] 编号须与正文引用角标一致。\n\n"
        + packed
    )
    raw = be.chat_completion(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        model=model or LLM_MODEL,
        temperature=temperature,
        timeout=timeout,
    ).strip()
    return raw


def build_report_title(
    system_key: str,
    week_start: date,
    week_end: date,
    report_type: str = "weekly",
) -> str:
    """生成报告标题（落库 title 字段）。"""
    label = SYSTEM_LABELS.get(system_key, system_key)
    kind = "周报" if report_type == "weekly" else "简报"
    return f"{label}{kind}（{week_start.isoformat()}～{week_end.isoformat()}）"
