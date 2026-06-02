"""
功能：门户 API 的 Pydantic 响应/请求模型。

输入：路由层组装的数据 dict 或 ORM 行。
输出：JSON 序列化契约；便于 web/ TypeScript 类型对齐。
上下游：api.routers.* 返回；web/lib/types.ts 应保持一致。
"""

from __future__ import annotations

from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class StatsResponse(BaseModel):
    """平台汇总指标。"""

    total_incidents: int = Field(description="已入库情报总数")
    total_tags: int = Field(description="去重标签数")
    taxonomy_kinds: int = Field(description="主域×子域组合种数")
    keyword_nodes: int = Field(description="高频关键词节点数")


class KeywordItem(BaseModel):
    keyword: str
    count: int


class IncidentItem(BaseModel):
    """单条情报/新闻卡片。"""

    id: Optional[int] = None
    title: str
    content_type: str = ""
    risk_domain: str = ""
    subdomain: str = ""
    entities: str = ""
    summary: str = ""
    source: str = ""
    url: str = ""
    tags: List[str] = Field(default_factory=list)
    published_at: Optional[str] = None


class LiteratureItem(BaseModel):
    """文献卡片。"""

    title: str
    source: str = ""
    authors: str = ""
    publication: str = ""
    document_type: str = ""
    doi: str = ""
    published_at: Optional[str] = None
    url: str = ""


class WeeklySummaryResponse(BaseModel):
    """单系统本周监测摘要。"""

    range_start: str
    range_end: str
    week_new: int
    total: int
    top_source: str
    top_subdomain: str
    highlights: List[str] = Field(default_factory=list)
    bullets: List[str] = Field(default_factory=list)


class SystemInfo(BaseModel):
    """三大子系统元信息（Hub 卡片）。"""

    key: str
    system_no: str
    name: str
    tagline: str
    color: str
    week_new: int
    total: int


class PaginatedResponse(BaseModel, Generic[T]):
    """通用分页包装。"""

    items: List[T]
    total: int
    page: int
    page_size: int
    pages: int


class PlatformStatus(BaseModel):
    """全局运行状态（首页运行监控中心顶部）。"""

    status: str
    status_label: str
    online_subsystems: int
    total_subsystems: int
    today_run_count: int
    today_new_data: int
    last_run_at: Optional[str] = None
    last_run_ago: str = "—"
    next_scheduled_at: Optional[str] = None
    next_scheduled_ago: str = "—"


class SubsystemStatus(BaseModel):
    """单个子系统运行卡片。"""

    key: str
    name: str
    status: str
    status_label: str
    last_run_at: Optional[str] = None
    last_run_ago: str = "—"
    today_new: int = 0
    total: int = 0
    source_count: int = 0
    source_label: str = "数据源"
    detail_href: str = "/"
    highlight_count: Optional[int] = None
    highlight_label: Optional[str] = None


class TimelineItem(BaseModel):
    """最近运行时间线条目。"""

    at: Optional[str] = None
    system_key: str
    system_label: str
    summary: str
    status: str
    data_count: int = 0


class SourceFilterOption(BaseModel):
    """左栏来源筛选项。"""

    key: str
    label: str
    group: str
    group_label: str
    count: int = 0
    hint: str = ""


class SourceFilterResponse(BaseModel):
    """来源筛选面板数据。"""

    track: str
    panel_title: str
    options: List[SourceFilterOption]
    total_count: int = 0


class MonitoringOverviewResponse(BaseModel):
    """运行监控中心聚合响应。"""

    platform: PlatformStatus
    subsystems: List[SubsystemStatus]
    timeline: List[TimelineItem]


class WeeklyReportItem(BaseModel):
    """监测周报/简报列表项（不含全文）。"""

    id: int
    system_key: str
    report_type: str = "weekly"
    week_start: str
    week_end: str
    title: str = ""
    excerpt: str = ""
    article_count: int = 0
    task_id: Optional[int] = None
    trigger_source: str = "cron"
    created_at: str = ""


class WeeklyReportDetail(BaseModel):
    """监测报告详情（含 Markdown 全文）。"""

    id: int
    system_key: str
    report_type: str = "weekly"
    week_start: str
    week_end: str
    title: str = ""
    report_markdown: str = ""
    article_count: int = 0
    model_name: str = ""
    task_id: Optional[int] = None
    trigger_source: str = "cron"
    created_at: str = ""
    source_article_ids: List[int] = Field(default_factory=list)


class ReportContinuityResponse(BaseModel):
    """周报连续性验收响应。"""

    system_key: str
    report_type: str
    expected_weeks: int
    present_weeks: int
    missing: List[str] = Field(default_factory=list)
    last_report_id: Optional[int] = None
    last_week_start: Optional[str] = None
    last_generated_at: Optional[str] = None
