"""
功能：专项监测三系统的视觉与文案主题配置（编号、名称、颜色、数据口径）。

输入：系统 key（policy / meeting / literature）。
输出：TrackTheme 实例与 TRACK_SYSTEMS 注册表。
上下游：`ui.components.track_shell`、`ui.pages.tracks.*` 读取；不含 Streamlit 副作用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal

TrackSystemKey = Literal["policy", "meeting", "literature"]


@dataclass(frozen=True)
class TrackTheme:
    """
    功能：单套子系统的 UI 身份包。
    输入：构造时传入各字段。
    输出：只读属性供 shell 渲染 banner / 卡片 / 切换器。
    """

    key: TrackSystemKey
    system_no: str
    icon: str
    short_name: str
    full_name: str
    tagline: str
    color: str
    color_dim: str
    banner_class: str
    hub_card_class: str
    data_scope: str


TRACK_SYSTEMS: Dict[TrackSystemKey, TrackTheme] = {
    "policy": TrackTheme(
        key="policy",
        system_no="系统一",
        icon="📋",
        short_name="政策监管",
        full_name="政策监管监测系统",
        tagline="追踪全球 AI 立法、监管文件与科技政策动态",
        color="#2563eb",
        color_dim="#1e3a5f",
        banner_class="track-banner-policy",
        hub_card_class="track-hub-card-policy",
        data_scope="数据来源：article_extractions 中 policy + report 类型资讯",
    ),
    "meeting": TrackTheme(
        key="meeting",
        system_no="系统二",
        icon="🌐",
        short_name="会议追踪",
        full_name="国际会议追踪系统",
        tagline="监测重大国际 AI 治理会议、论坛与多边磋商",
        color="#7c3aed",
        color_dim="#3b1f6e",
        banner_class="track-banner-meeting",
        hub_card_class="track-hub-card-meeting",
        data_scope="数据来源：article_extractions 中 meeting 类型资讯",
    ),
    "literature": TrackTheme(
        key="literature",
        system_no="系统三",
        icon="📖",
        short_name="文献情报",
        full_name="文献情报监测系统",
        tagline="汇聚 arXiv / Scopus / Springer 等 AI 安全学术文献",
        color="#059669",
        color_dim="#064e3b",
        banner_class="track-banner-literature",
        hub_card_class="track-hub-card-literature",
        data_scope="数据来源：literature_items 文献库（arxiv / scopus / springer）",
    ),
}

TRACK_SYSTEM_ORDER: tuple[TrackSystemKey, ...] = ("policy", "meeting", "literature")


def get_theme(key: TrackSystemKey) -> TrackTheme:
    """按 key 取主题；key 非法时抛 KeyError。"""
    return TRACK_SYSTEMS[key]
