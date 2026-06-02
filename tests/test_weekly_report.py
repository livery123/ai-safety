"""
监测周报单元测试（不依赖 LLM / 可选 MySQL）。

功能：验证 Prompt 打包、周界计算、模板 fallback、Prompt 契约。
"""

from __future__ import annotations

from datetime import date

import pytest

from core.mysql_weekly_reports import compute_week_range
from core.weekly_report_data import TrackEntry
from engine.prompts import BRIEF_REPORT_SYSTEM, WEEKLY_REPORT_SYSTEM
from engine.weekly_report import (
    build_report_title,
    generate_weekly_report_markdown,
    pack_entries_for_prompt,
)


def test_compute_week_range_previous_monday_sunday():
    """anchor=2026-06-01（周一）→ 上一周 2026-05-25～2026-05-31。"""
    ws, we = compute_week_range(anchor=date(2026, 6, 1))
    assert ws == date(2026, 5, 25)
    assert we == date(2026, 5, 31)


def test_compute_week_range_explicit_start():
    ws, we = compute_week_range(week_start=date(2026, 1, 5))
    assert ws == date(2026, 1, 5)
    assert we == date(2026, 1, 11)


def test_pack_entries_includes_four_dimension_context():
    entries = [
        TrackEntry(
            article_id=101,
            title="EU AI Act enforcement update",
            content_type="policy",
            risk_domain="Systemic & Ethical Risk (系统性与伦理风险)",
            summary="欧盟 AI 法案进入执法准备阶段",
            source="guardian",
            url="https://example.com/1",
            published_at="2026-05-28",
        )
    ]
    packed = pack_entries_for_prompt(
        system_key="policy",
        week_start=date(2026, 5, 26),
        week_end=date(2026, 6, 1),
        entries=entries,
        context_entries=[],
    )
    assert "条目 1" in packed
    assert "EU AI Act" in packed
    assert "article_id" in packed
    assert "2026-05-26" in packed


def test_pack_entries_empty_week():
    packed = pack_entries_for_prompt(
        system_key="policy",
        week_start=date(2026, 5, 26),
        week_end=date(2026, 6, 1),
        entries=[],
    )
    assert "无新增条目" in packed or "0" in packed


def test_skip_llm_fallback_has_four_sections():
    md = generate_weekly_report_markdown(
        system_key="policy",
        week_start=date(2026, 5, 26),
        week_end=date(2026, 6, 1),
        entries=[],
        skip_llm=True,
    )
    for heading in ("政策意义", "可能影响", "与历史政策关系", "落地性评估"):
        assert heading in md


def test_weekly_system_prompt_requires_four_dimensions():
    for key in ("政策意义", "可能影响", "与历史政策关系", "落地性评估"):
        assert key in WEEKLY_REPORT_SYSTEM
        assert key in BRIEF_REPORT_SYSTEM


def test_build_report_title():
    t = build_report_title("policy", date(2026, 5, 26), date(2026, 6, 1), "weekly")
    assert "政策" in t
    assert "2026-05-26" in t
