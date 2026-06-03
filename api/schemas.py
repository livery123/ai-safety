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


class PolicyCountItem(BaseModel):
    """政策按国家/区域计数项。"""

    label: str
    count: int
    kind: str = Field(description="sovereign | region")


class PolicyWordItem(BaseModel):
    """词云词条。"""

    text: str
    value: int
    category: str = Field(description="authority | tag | intl_org")


class PolicyCoverageStats(BaseModel):
    """政策发布地理覆盖度 KPI。"""

    sovereign_count: int = 0
    sovereign_names: List[str] = Field(default_factory=list)
    region_count: int = 0
    region_names: List[str] = Field(default_factory=list)
    intl_org_doc_count: int = 0
    missing_geo_count: int = 0
    meets_kpi: bool = False


class PolicyAnalyticsResponse(BaseModel):
    """政策可视化分析聚合响应。"""

    coverage: PolicyCoverageStats
    by_country: List[PolicyCountItem] = Field(default_factory=list)
    by_week: List[KeywordItem] = Field(default_factory=list)
    wordcloud: List[PolicyWordItem] = Field(default_factory=list)


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
    publish_country: str = ""
    publish_region: str = ""
    publish_authority: str = ""
    international_orgs: str = ""


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


class MeetingCatalogItem(BaseModel):
    """重大会议名录项。"""

    catalog_key: str
    series_name: str
    category: str = ""
    is_major: bool = True
    aliases: List[str] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)
    official_urls: List[str] = Field(default_factory=list)
    events: List["MeetingEventSummary"] = Field(default_factory=list)


class MeetingEventSummary(BaseModel):
    """会议事件列表项。"""

    id: int
    catalog_key: str
    series_name: str = ""
    edition_label: str = ""
    edition_year: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    location: str = ""
    host: str = ""
    status: str = ""
    article_count: int = 0
    has_analysis: bool = False


class MeetingTimelineArticle(BaseModel):
    """事件流中单篇报道。"""

    article_id: int
    title: str
    summary: str = ""
    source: str = ""
    url: str = ""
    published_at: Optional[str] = None
    phase: str = "unknown"


class MeetingTimelineResponse(BaseModel):
    """会前/会中/会后时间线。"""

    event_id: int
    pre: List[MeetingTimelineArticle] = Field(default_factory=list)
    during: List[MeetingTimelineArticle] = Field(default_factory=list)
    post: List[MeetingTimelineArticle] = Field(default_factory=list)
    unknown: List[MeetingTimelineArticle] = Field(default_factory=list)


class MeetingEventDetailResponse(BaseModel):
    """会议事件详情 + 专题分析。"""

    event: MeetingEventSummary
    countries: List[str] = Field(default_factory=list)
    official_url: str = ""
    notes: str = ""
    analysis_markdown: str = ""
    analysis_generated_at: Optional[str] = None


class MeetingBriefRegenerateResponse(BaseModel):
    """重生成专题分析结果。"""

    event_id: int
    analysis_id: int
    message: str = "ok"


MeetingCatalogItem.model_rebuild()
