"""
功能：为一届 meeting_event 生成 Markdown 专题分析（含趋势三节）。
输入：event_id、关联报道材料、可选往届分析摘要。
输出：Markdown 正文；无材料时不调用 LLM。
上下游：scripts/generate_meeting_briefs.py、api 重生成端点。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.config import LLM_MODEL
from core.llm_client import OpenAICompatibleBackend
from core.mysql_meeting_events import (
    fetch_event_timeline,
    get_event_by_id,
    prior_events_same_catalog,
)
from engine.prompts import MEETING_BRIEF_SYSTEM, MEETING_TREND_USER_HINT


def _pack_event_materials(
    event: Dict[str, Any],
    timeline: Dict[str, List[Dict[str, Any]]],
    prior_summaries: List[str],
) -> str:
    lines: List[str] = [
        "## 会议实例",
        f"- 系列：{event.get('series_name') or event.get('catalog_key')}",
        f"- 届次：{event.get('edition_label')}",
        f"- 时间：{event.get('start_date')} — {event.get('end_date')}",
        f"- 地点：{event.get('location')}",
        f"- 主办：{event.get('host')}",
        f"- 状态：{event.get('status')}",
        "",
    ]
    if prior_summaries:
        lines.append("## 同系列往届分析摘要（供延续性参考）")
        for i, s in enumerate(prior_summaries, 1):
            lines.append(f"### 往届 {i}")
            lines.append(s[:2500])
            lines.append("")

    idx = 0
    for phase in ("pre", "during", "post", "unknown"):
        items = timeline.get(phase) or []
        if not items:
            continue
        lines.append(f"## 阶段：{phase}")
        for row in items:
            idx += 1
            lines.append(f"### 条目 {idx}")
            lines.append(f"- 标题：{row.get('title_raw')}")
            lines.append(f"- 来源：{row.get('source')}")
            lines.append(f"- 时间：{row.get('published_at')}")
            lines.append(f"- URL：{row.get('normalized_url')}")
            lines.append(f"- 摘要：{row.get('summary_structured') or row.get('summary_raw')}")
            body = (row.get("summary_raw") or "")[:3500]
            if body:
                lines.append(body)
            lines.append("")
    return "\n".join(lines)


def generate_meeting_brief_markdown(
    event_id: int,
    *,
    backend: Optional[OpenAICompatibleBackend] = None,
    model: Optional[str] = None,
    temperature: float = 0.4,
    timeout: float = 240.0,
) -> str:
    """
    功能：生成会议专题 Markdown。
    输入：meeting_events.id。
    输出：报告正文；无关联文章时返回说明段。
    """
    event = get_event_by_id(event_id)
    if not event:
        return "（未找到该会议事件。）"
    timeline = fetch_event_timeline(event_id)
    total = sum(len(v) for v in timeline.values())
    if total == 0:
        return (
            f"# {event.get('edition_label') or '会议专题'}\n\n"
            "**监测库中尚无关联报道。** 请先运行采集与 `scripts/link_meeting_articles.py`。\n"
        )

    prior_ctx: List[str] = []
    for pe in prior_events_same_catalog(str(event.get("catalog_key") or ""), event_id, limit=3):
        from core.mysql_meeting_events import get_latest_analysis

        pa = get_latest_analysis(int(pe["id"]))
        if pa and pa.get("analysis_markdown"):
            prior_ctx.append(str(pa["analysis_markdown"])[:3000])

    packed = _pack_event_materials(event, timeline, prior_ctx)
    be = backend or OpenAICompatibleBackend()
    m = model or LLM_MODEL
    user_msg = (
        "请直接输出完整 Markdown 专题报告（不要代码围栏、不要对话）。\n"
        "材料中「条目 n」对应 ### 条目 n；引用使用 [条目 n]。\n\n"
        + MEETING_TREND_USER_HINT
        + "\n\n"
        + packed
    )
    md = be.chat_completion(
        [
            {"role": "system", "content": MEETING_BRIEF_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        model=m,
        temperature=temperature,
        timeout=timeout,
    )
    return (md or "").strip() or "（LLM 返回为空。）"
