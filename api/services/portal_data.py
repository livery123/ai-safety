"""
功能：门户只读数据聚合，复用 core.mysql_* 与 services.track_service，无 Streamlit 依赖。

输入：分页/筛选参数。
输出：Pydantic 模型或 plain dict；只读无副作用。
上下游：api.routers.* 调用；底层 core.mysql_dashboard、core.mysql_monitor_tracks。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, List, Optional

import pandas as pd

from api.schemas import (
    IncidentItem,
    KeywordItem,
    LiteratureItem,
    PolicyAnalyticsResponse,
    PolicyCountItem,
    PolicyCoverageStats,
    PolicyWordItem,
    SourceFilterOption,
    SourceFilterResponse,
    StatsResponse,
    SystemInfo,
    WeeklySummaryResponse,
)
from core.source_registry import TRACK_PANEL_TITLE, normalize_selected_keys
from core.mysql_dashboard import (
    count_dashboard_incidents,
    fetch_dashboard_incidents_page,
    fetch_dashboard_latest_rows,
    get_dashboard_keywords_df,
    get_dashboard_stats,
)
from core.mysql_monitor_tracks import (
    aggregate_policy_by_publish_country,
    aggregate_policy_by_publish_region,
    aggregate_policy_by_week,
    aggregate_policy_publish_coverage,
    aggregate_policy_wordcloud_tokens,
    fetch_literature_track_page,
    fetch_meeting_track_page,
    fetch_policy_track_page,
    list_track_source_options,
)
from services.track_service import (
    WEEK_DAYS,
    build_weekly_summary_literature,
    build_weekly_summary_meeting,
    build_weekly_summary_policy,
)
from core.mysql_monitor_tracks import (
    count_literature_track_rows,
    count_meeting_track_rows,
    count_policy_track_rows,
)


def _iso_dt(val: Any) -> Optional[str]:
    """datetime / str → ISO 字符串。"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    s = str(val).strip()
    return s or None


def _parse_tags(raw: Any) -> List[str]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except json.JSONDecodeError:
                pass
        return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def _short_domain(domain: str) -> str:
    return domain.split("(")[0].strip() if domain else ""


def get_stats() -> StatsResponse:
    """平台汇总指标。"""
    try:
        n_ext, n_tags, n_tax = get_dashboard_stats()
        kw_df = get_dashboard_keywords_df()
        kw_n = len(kw_df) if not kw_df.empty else 0
        return StatsResponse(
            total_incidents=n_ext,
            total_tags=n_tags,
            taxonomy_kinds=n_tax,
            keyword_nodes=kw_n,
        )
    except Exception:
        return StatsResponse(total_incidents=0, total_tags=0, taxonomy_kinds=0, keyword_nodes=0)


def get_keywords(limit: int = 20) -> List[KeywordItem]:
    """高频关键词 Top N。"""
    try:
        df = get_dashboard_keywords_df()
        if df.empty:
            return []
        out: List[KeywordItem] = []
        for _, row in df.head(max(1, min(limit, 60))).iterrows():
            out.append(
                KeywordItem(
                    keyword=str(row.get("keyword") or ""),
                    count=int(pd.to_numeric(row.get("count"), errors="coerce") or 0),
                )
            )
        return out
    except Exception:
        return []


def get_policy_analytics(
    *,
    country_limit: int = 12,
    word_limit: int = 40,
    word_field: str = "mixed",
    week_limit: int = 16,
) -> PolicyAnalyticsResponse:
    """
    功能：政策可视化分析（覆盖度、国家分布、周趋势、词云）。
    输入：各模块 limit 与 word_field。
    输出：PolicyAnalyticsResponse；只读。
    """
    try:
        cov_raw = aggregate_policy_publish_coverage()
        coverage = PolicyCoverageStats(
            sovereign_count=int(cov_raw.get("sovereign_count") or 0),
            sovereign_names=list(cov_raw.get("sovereign_names") or []),
            region_count=int(cov_raw.get("region_count") or 0),
            region_names=list(cov_raw.get("region_names") or []),
            intl_org_doc_count=int(cov_raw.get("intl_org_doc_count") or 0),
            missing_geo_count=int(cov_raw.get("missing_geo_count") or 0),
            meets_kpi=bool(cov_raw.get("meets_kpi")),
        )

        by_country: List[PolicyCountItem] = []
        country_df = aggregate_policy_by_publish_country(country_limit)
        for _, row in country_df.iterrows():
            by_country.append(
                PolicyCountItem(
                    label=str(row.get("label") or ""),
                    count=int(pd.to_numeric(row.get("cnt"), errors="coerce") or 0),
                    kind="sovereign",
                )
            )
        region_df = aggregate_policy_by_publish_region(country_limit)
        for _, row in region_df.iterrows():
            label = str(row.get("label") or "").strip()
            if not label:
                continue
            if any(x.label == label for x in by_country):
                continue
            by_country.append(
                PolicyCountItem(
                    label=label,
                    count=int(pd.to_numeric(row.get("cnt"), errors="coerce") or 0),
                    kind="region",
                )
            )
        by_country.sort(key=lambda x: x.count, reverse=True)
        by_country = by_country[: max(1, min(country_limit, 20))]

        week_df = aggregate_policy_by_week(week_limit)
        by_week: List[KeywordItem] = []
        if not week_df.empty:
            week_df = week_df.sort_values("sort_ts")
            for _, row in week_df.iterrows():
                by_week.append(
                    KeywordItem(
                        keyword=str(row.get("week_bucket") or ""),
                        count=int(pd.to_numeric(row.get("cnt"), errors="coerce") or 0),
                    )
                )

        wc_df = aggregate_policy_wordcloud_tokens(word_limit, word_field)
        wordcloud: List[PolicyWordItem] = []
        for _, row in wc_df.iterrows():
            wordcloud.append(
                PolicyWordItem(
                    text=str(row.get("text") or ""),
                    value=int(pd.to_numeric(row.get("value"), errors="coerce") or 0),
                    category=str(row.get("category") or "tag"),
                )
            )

        return PolicyAnalyticsResponse(
            coverage=coverage,
            by_country=by_country,
            by_week=by_week,
            wordcloud=wordcloud,
        )
    except Exception:
        return PolicyAnalyticsResponse(coverage=PolicyCoverageStats())


def _row_to_incident(row: pd.Series, *, with_id: bool = True) -> IncidentItem:
    """DataFrame 行 → IncidentItem；兼容中英文列名。"""
    title = str(row.get("标题") or row.get("title") or "").strip()
    url = str(row.get("URL") or row.get("url") or row.get("来源") or "").strip()
    if url and not url.startswith("http"):
        url = str(row.get("normalized_url") or "").strip()
    source = str(row.get("来源平台") or row.get("source") or "").strip()
    tags_raw = row.get("标签") or row.get("tags") or ""
    tags = _parse_tags(tags_raw)
    return IncidentItem(
        id=int(row["id"]) if with_id and "id" in row and pd.notna(row.get("id")) else None,
        title=title or "（无标题）",
        content_type=str(row.get("资讯类别") or row.get("content_type") or ""),
        risk_domain=_short_domain(str(row.get("主域") or row.get("risk_domain") or "")),
        subdomain=str(row.get("子域") or row.get("subdomain") or ""),
        entities=str(row.get("涉及主体") or row.get("entities") or ""),
        summary=str(row.get("摘要") or row.get("summary") or "")[:400],
        source=source,
        url=url if url.startswith("http") else "",
        tags=tags,
        published_at=_iso_dt(row.get("时间") or row.get("published_at")),
        publish_country=str(row.get("发布国家") or row.get("publish_country") or ""),
        publish_region=str(row.get("发布地区") or row.get("publish_region") or ""),
        publish_authority=str(row.get("发布主体") or row.get("publish_authority") or ""),
        international_orgs=str(row.get("国际组织") or row.get("international_orgs") or ""),
    )


def get_latest_incidents(limit: int = 12) -> List[IncidentItem]:
    """首页最新动态卡片。"""
    try:
        df = fetch_dashboard_latest_rows(max(1, min(limit, 50)))
        if df.empty:
            return []
        items: List[IncidentItem] = []
        for _, row in df.iterrows():
            item = _row_to_incident(row, with_id=False)
            if not item.source and item.url:
                item.source = "链接"
            items.append(item)
        return items
    except Exception:
        return []


def list_incidents(
    *,
    page: int = 1,
    page_size: int = 12,
    keyword: Optional[str] = None,
    risk_domain: Optional[str] = None,
    content_type: Optional[str] = None,
) -> tuple[List[IncidentItem], int]:
    """情报分页列表。"""
    pg = max(1, page)
    ps = max(1, min(page_size, 50))
    offset = (pg - 1) * ps
    try:
        total = count_dashboard_incidents(
            risk_domain=(risk_domain or "").strip() or None,
            content_type=(content_type or "").strip() or None,
            keyword=(keyword or "").strip() or None,
        )
        df = fetch_dashboard_incidents_page(
            offset,
            max(ps, 50),
            risk_domain=(risk_domain or "").strip() or None,
            content_type=(content_type or "").strip() or None,
            keyword=(keyword or "").strip() or None,
        )
        if df.empty:
            return [], total
        df = df.head(ps)
        return [_row_to_incident(row) for _, row in df.iterrows()], total
    except Exception:
        return [], 0


def _track_rows_to_incidents(df: pd.DataFrame) -> List[IncidentItem]:
    if df.empty:
        return []
    out: List[IncidentItem] = []
    for _, row in df.iterrows():
        tags = _parse_tags(row.get("标签"))
        out.append(
            IncidentItem(
                id=int(row["id"]) if "id" in row and pd.notna(row.get("id")) else None,
                title=str(row.get("标题") or "").strip() or "（无标题）",
                content_type=str(row.get("资讯类别") or ""),
                risk_domain=_short_domain(str(row.get("主域") or "")),
                subdomain=str(row.get("子域") or ""),
                entities=str(row.get("涉及主体") or ""),
                summary=str(row.get("摘要") or "")[:400],
                source=str(row.get("来源平台") or ""),
                url=str(row.get("URL") or "").strip(),
                tags=tags,
                published_at=_iso_dt(row.get("时间")),
            )
        )
    return out


def list_policy_tracks(
    *,
    page: int = 1,
    page_size: int = 12,
    keyword: Optional[str] = None,
    sources: Optional[List[str]] = None,
) -> tuple[List[IncidentItem], int]:
    pg, ps = max(1, page), max(1, min(page_size, 50))
    offset = (pg - 1) * ps
    kw = (keyword or "").strip() or None
    srcs = normalize_selected_keys("policy", sources) or None
    try:
        total = count_policy_track_rows(keyword=kw, sources=srcs)
        df = fetch_policy_track_page(offset, max(ps, 25), keyword=kw, sources=srcs)
        return _track_rows_to_incidents(df.head(ps)), total
    except Exception:
        return [], 0


def list_meeting_tracks(
    *,
    page: int = 1,
    page_size: int = 12,
    keyword: Optional[str] = None,
    sources: Optional[List[str]] = None,
) -> tuple[List[IncidentItem], int]:
    pg, ps = max(1, page), max(1, min(page_size, 50))
    offset = (pg - 1) * ps
    kw = (keyword or "").strip() or None
    srcs = normalize_selected_keys("meeting", sources) or None
    try:
        total = count_meeting_track_rows(keyword=kw, sources=srcs)
        df = fetch_meeting_track_page(offset, max(ps, 25), keyword=kw, sources=srcs)
        return _track_rows_to_incidents(df.head(ps)), total
    except Exception:
        return [], 0


def list_literature_tracks(
    *,
    page: int = 1,
    page_size: int = 12,
    keyword: Optional[str] = None,
    source: Optional[str] = None,
    sources: Optional[List[str]] = None,
) -> tuple[List[LiteratureItem], int]:
    pg, ps = max(1, page), max(1, min(page_size, 50))
    offset = (pg - 1) * ps
    kw = (keyword or "").strip() or None
    srcs = normalize_selected_keys("literature", sources) or None
    legacy = (source or "").strip() or None
    try:
        total = count_literature_track_rows(keyword=kw, source=legacy if not srcs else None, sources=srcs)
        df = fetch_literature_track_page(
            offset,
            max(ps, 25),
            keyword=kw,
            source=legacy if not srcs else None,
            sources=srcs,
        )
        if df.empty:
            return [], total
        items: List[LiteratureItem] = []
        for _, row in df.head(ps).iterrows():
            items.append(
                LiteratureItem(
                    title=str(row.get("标题") or "").strip() or "（无标题）",
                    source=str(row.get("来源") or ""),
                    authors=str(row.get("作者") or ""),
                    publication=str(row.get("期刊/会议") or ""),
                    document_type=str(row.get("类型") or ""),
                    doi=str(row.get("DOI") or ""),
                    published_at=_iso_dt(row.get("时间")),
                    url=str(row.get("链接") or "").strip(),
                )
            )
        return items, total
    except Exception:
        return [], 0


def get_track_source_filters(track: str) -> SourceFilterResponse:
    """左栏来源筛选项（含条数）。"""
    track_key = track.strip().lower()
    if track_key == "meetings":
        track_key = "meeting"
    opts = list_track_source_options(track_key)
    total = sum(o.get("count", 0) for o in opts) if track_key == "literature" else 0
    return SourceFilterResponse(
        track=track_key,
        panel_title=TRACK_PANEL_TITLE.get(track_key, "来源筛选"),
        options=[SourceFilterOption(**o) for o in opts],
        total_count=total,
    )


def get_weekly_summary(system: str) -> WeeklySummaryResponse:
    """三系统本周摘要。"""
    builders = {
        "policy": build_weekly_summary_policy,
        "meeting": build_weekly_summary_meeting,
        "literature": build_weekly_summary_literature,
    }
    fn = builders.get(system, build_weekly_summary_policy)
    try:
        s = fn()
        return WeeklySummaryResponse(
            range_start=s.range_start,
            range_end=s.range_end,
            week_new=s.week_new,
            total=s.total,
            top_source=s.top_source,
            top_subdomain=s.top_subdomain,
            highlights=s.highlights,
            bullets=s.bullets,
        )
    except Exception:
        return WeeklySummaryResponse(
            range_start="",
            range_end="",
            week_new=0,
            total=0,
            top_source="—",
            top_subdomain="—",
        )


def get_systems_hub() -> List[SystemInfo]:
    """三大子系统 Hub 卡片数据。"""
    meta = [
        ("policy", "系统一", "政策监管监测系统", "追踪全球 AI 立法、监管文件与科技政策", "#2563eb"),
        ("meeting", "系统二", "国际会议追踪系统", "监测重大国际 AI 治理会议与论坛", "#7c3aed"),
        ("literature", "系统三", "文献情报监测系统", "汇聚 arXiv / Scopus / Springer 学术文献", "#059669"),
    ]
    out: List[SystemInfo] = []
    summaries = {
        "policy": build_weekly_summary_policy(),
        "meeting": build_weekly_summary_meeting(),
        "literature": build_weekly_summary_literature(),
    }
    for key, no, name, tagline, color in meta:
        sm = summaries[key]
        out.append(
            SystemInfo(
                key=key,
                system_no=no,
                name=name,
                tagline=tagline,
                color=color,
                week_new=sm.week_new,
                total=sm.total,
            )
        )
    return out
