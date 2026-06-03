"""
功能：政策/会议共用「全信源新闻同步包」，参数与 Streamlit 手动同步 defaults 对齐。

输入：NewsSyncConfig；dry_run 控制 policy/中文源是否写库（Guardian/NYT 无 dry_run，始终真实入库）。
输出：NewsSyncBundleResult；副作用：HTTP + LLM + MySQL。
上下游：scripts/sync_sources.py --source news；cron 定时任务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from crawler.orchestrator import (
    SyncResult,
    sync_guardian,
    sync_nyt,
    sync_policy,
    sync_sina_tech,
    sync_wechat_rss,
    sync_xinhua_tech,
)


@dataclass(frozen=True)
class NewsSyncConfig:
    """与 ui/pages/system.py 手动按钮默认参数一致。"""

    guardian_max_pages: int = 2
    guardian_page_size: int = 8
    nyt_max_pages: int = 2
    wechat_max_articles_per_feed: int = 5
    wechat_feed_names: Optional[List[str]] = None
    xinhua_max_articles: int = 10
    sina_max_articles: int = 10
    policy_max_articles_per_country: int = 10
    policy_countries: Optional[List[str]] = None
    nyt_begin_date: Optional[str] = None
    nyt_end_date: Optional[str] = None
    rag_enabled: bool = False
    llm_concurrency: int = 2


@dataclass
class NewsSyncBundleResult:
    """全信源同步汇总。"""

    merged: SyncResult = field(default_factory=SyncResult)
    by_source: Dict[str, SyncResult] = field(default_factory=dict)


def merge_sync_results(a: SyncResult, b: SyncResult) -> SyncResult:
    """合并两次 SyncResult。"""
    out = SyncResult()
    out.saved = a.saved + b.saved
    out.skipped_url_dup = a.skipped_url_dup + b.skipped_url_dup
    out.skipped_no_incident = a.skipped_no_incident + b.skipped_no_incident
    out.failed = a.failed + b.failed
    out.new_keywords = list(dict.fromkeys(list(a.new_keywords) + list(b.new_keywords)))
    out.new_subdomains = list(dict.fromkeys(list(a.new_subdomains) + list(b.new_subdomains)))
    out.debug_log = list(a.debug_log) + list(b.debug_log)
    return out


def _run_step(
    label: str,
    fn: Callable[[], SyncResult],
    *,
    bundle: NewsSyncBundleResult,
) -> None:
    bundle.merged.debug_log.append(f"\n{'=' * 48}\n▶ 开始：{label}\n{'=' * 48}")
    try:
        res = fn()
    except Exception as e:
        res = SyncResult()
        res.failed += 1
        res.debug_log.append(f"❌ {label} 异常: {type(e).__name__}: {e}")
    bundle.by_source[label] = res
    bundle.merged = merge_sync_results(bundle.merged, res)
    bundle.merged.debug_log.append(
        f"◀ {label} 小结：入库 {res.saved}，跳过已有 {res.skipped_url_dup}，"
        f"无关 {res.skipped_no_incident}，失败 {res.failed}"
    )


def sync_all_news_for_policy_meeting(
    cfg: Optional[NewsSyncConfig] = None,
    *,
    dry_run: bool = False,
) -> NewsSyncBundleResult:
    """
    功能：顺序跑完政策/会议相关全部信源；LLM 按 content_type 归入政策或会议。
    输入：NewsSyncConfig；dry_run 仅对支持 dry_run 的信源生效。
    输出：NewsSyncBundleResult。
    """
    c = cfg or NewsSyncConfig()
    bundle = NewsSyncBundleResult()
    rag = c.rag_enabled
    conc = max(1, min(int(c.llm_concurrency), 5))

    steps: List[tuple[str, Callable[[], SyncResult]]] = [
        (
            "policy_official",
            lambda: sync_policy(
                countries=c.policy_countries,
                max_articles_per_country=c.policy_max_articles_per_country,
                rag_enabled=rag,
                dry_run=dry_run,
                concurrency=conc,
            ),
        ),
        (
            "guardian",
            lambda: sync_guardian(
                max_pages=c.guardian_max_pages,
                page_size=c.guardian_page_size,
                rag_enabled=rag,
                concurrency=conc,
            ),
        ),
        (
            "nyt",
            lambda: sync_nyt(
                max_pages=c.nyt_max_pages,
                begin_date=c.nyt_begin_date,
                end_date=c.nyt_end_date,
                rag_enabled=rag,
                concurrency=conc,
            ),
        ),
        (
            "wechat_rss",
            lambda: sync_wechat_rss(
                feed_names=c.wechat_feed_names,
                max_articles_per_feed=c.wechat_max_articles_per_feed,
                rag_enabled=rag,
                dry_run=dry_run,
                concurrency=conc,
            ),
        ),
        (
            "xinhua_tech",
            lambda: sync_xinhua_tech(
                max_articles=c.xinhua_max_articles,
                rag_enabled=rag,
                dry_run=dry_run,
                concurrency=conc,
            ),
        ),
        (
            "sina_tech",
            lambda: sync_sina_tech(
                max_articles=c.sina_max_articles,
                rag_enabled=rag,
                dry_run=dry_run,
                concurrency=conc,
            ),
        ),
    ]

    for label, fn in steps:
        _run_step(label, fn, bundle=bundle)

    bundle.merged.debug_log.append(
        f"\n🏁 全信源新闻同步完成 | 合计入库 {bundle.merged.saved}，"
        f"跳过已有 {bundle.merged.skipped_url_dup}，"
        f"无关 {bundle.merged.skipped_no_incident}，失败 {bundle.merged.failed}"
    )
    return bundle
