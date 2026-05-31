#!/usr/bin/env python3
"""
信源同步 CLI：全信源新闻包（政策+会议 LLM 分类）与文献库独立同步。

功能：--source news 跑齐 Guardian/NYT/微信/新华/新浪/政策官方源（参数对齐 Streamlit 手动默认）；
      --source literature 跑 arXiv/Scopus/Springer；每次运行写入 MySQL system_tasks 供门户展示。
输入：命令行；API Key 从 .env 读取。
输出：stdout 日志；exit 0/1。
上下游：crawler.news_sync_bundle、crawler.orchestrator、core.system_tasks；cron 定时调用。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.db import init_db  # noqa: E402
from core.system_tasks import (  # noqa: E402
    _build_message,
    begin_task,
    finish_task,
    record_news_bundle_tasks,
    run_tracked_sync,
)
from crawler.news_sync_bundle import NewsSyncConfig, sync_all_news_for_policy_meeting  # noqa: E402
from crawler.orchestrator import (  # noqa: E402
    SyncResult,
    sync_guardian,
    sync_literature,
    sync_nyt,
    sync_policy,
    sync_sina_tech,
    sync_wechat_rss,
    sync_xinhua_tech,
)


def _print_result(r: SyncResult, *, dry_run: bool = False) -> None:
    for line in r.debug_log:
        print(line)
    print("\n--- 汇总 ---")
    label = "相关抽取（dry-run，未入库）" if dry_run else "入库"
    print(f"{label}: {r.saved}")
    if not dry_run:
        print(f"已有（跳过）: {r.skipped_url_dup}")
    print(f"无关（跳过）: {r.skipped_no_incident}")
    print(f"失败/缺字段: {r.failed}")


def _print_literature_result(r: object, dry_run: bool = False) -> None:
    for line in r.debug_log:  # type: ignore[attr-defined]
        print(line)
    print("\n--- 汇总 ---")
    label = "可入库（dry-run）" if dry_run else "新入库"
    print(f"{label}: {r.saved}")  # type: ignore[attr-defined]
    print(f"已有（跳过）: {r.skipped_url_dup}")  # type: ignore[attr-defined]
    print(f"失败/缺字段: {r.failed}")  # type: ignore[attr-defined]


def _run_news(args: argparse.Namespace) -> int:
    cfg = NewsSyncConfig(
        guardian_max_pages=args.pages,
        guardian_page_size=args.page_size,
        nyt_max_pages=args.pages,
        wechat_max_articles_per_feed=args.max_articles,
        xinhua_max_articles=args.max_articles,
        sina_max_articles=args.max_articles,
        policy_max_articles_per_country=args.max_articles,
        rag_enabled=False if args.no_rag else False,
    )

    def _sync():
        return sync_all_news_for_policy_meeting(cfg, dry_run=args.dry_run)

    try:
        bundle = _sync()
        if not args.dry_run:
            record_news_bundle_tasks(bundle, trigger_source="cron")
    except Exception as e:
        if not args.dry_run:
            for system_key, label in (
                ("policy", "政策/新闻采集"),
                ("meeting", "会议/新闻采集"),
            ):
                tid = begin_task(system_key, f"crawl_{system_key}", trigger_source="cron")
                finish_task(
                    tid,
                    status="failed",
                    data_count=0,
                    message=_build_message(summary=f"{label}异常: {e}"),
                )
        raise

    for line in bundle.merged.debug_log:
        print(line)
    print("\n--- 全信源汇总 ---")
    print(f"入库: {bundle.merged.saved}")
    print(f"跳过已有: {bundle.merged.skipped_url_dup}")
    print(f"无关: {bundle.merged.skipped_no_incident}")
    print(f"失败/缺字段: {bundle.merged.failed}")
    for name, res in bundle.by_source.items():
        print(f"  · {name}: saved={res.saved} dup={res.skipped_url_dup} fail={res.failed}")
    r = bundle.merged
    return 1 if (r.failed > 0 and r.saved == 0) else 0


def _run_literature(args: argparse.Namespace) -> int:
    sources = None
    if args.literature_sources:
        sources = [s.strip().lower() for s in args.literature_sources if s.strip()]

    def _sync():
        return sync_literature(
            sources=sources or ["arxiv", "scopus", "springer"],
            max_arxiv_per_category=args.max_articles,
            max_springer_per_domain=args.max_articles,
            scopus_max_results=max(5, args.max_articles * 5),
            scopus_days_back=7,
            dry_run=args.dry_run,
        )

    if args.dry_run:
        r = _sync()
    else:
        r = run_tracked_sync(
            "literature",
            "crawl_literature",
            _sync,
            trigger_source="cron",
            action_label="完成文献更新",
        )
    _print_literature_result(r, dry_run=args.dry_run)
    return 1 if (r.failed > 0 and r.saved == 0) else 0


def _run_single(args: argparse.Namespace) -> int:
    dry = args.dry_run
    if args.source == "guardian":
        r = sync_guardian(
            max_pages=args.pages,
            page_size=args.page_size,
            rag_enabled=False if args.no_rag else None,
        )
    elif args.source == "nyt":
        r = sync_nyt(max_pages=args.pages, rag_enabled=False if args.no_rag else None)
    elif args.source == "wechat_rss":
        r = sync_wechat_rss(
            max_articles_per_feed=args.max_articles,
            rag_enabled=False if args.no_rag else None,
            dry_run=dry,
        )
    elif args.source == "xinhua_tech":
        r = sync_xinhua_tech(
            max_articles=args.max_articles,
            rag_enabled=False if args.no_rag else None,
            dry_run=dry,
        )
    elif args.source == "sina_tech":
        r = sync_sina_tech(
            max_articles=args.max_articles,
            rag_enabled=False if args.no_rag else None,
            dry_run=dry,
        )
    elif args.source == "policy":
        countries = None
        if args.countries:
            raw = []
            for item in args.countries:
                raw.extend(c.strip().upper() for c in item.split(",") if c.strip())
            countries = raw or None
        r = sync_policy(
            countries=countries,
            max_articles_per_country=args.max_articles,
            rag_enabled=False if args.no_rag else None,
            dry_run=dry,
        )
    else:
        print(f"未知信源: {args.source}", file=sys.stderr)
        return 1
    _print_result(r, dry_run=dry)
    return 1 if (r.failed > 0 and r.saved == 0) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 治理监测 — 多信源同步")
    parser.add_argument(
        "--source",
        choices=[
            "news",
            "literature",
            "guardian",
            "nyt",
            "wechat_rss",
            "xinhua_tech",
            "sina_tech",
            "policy",
        ],
        default="news",
        help="news=全信源新闻包；literature=文献三源；其余=单信源调试",
    )
    parser.add_argument("--pages", type=int, default=2, help="Guardian/NYT 页数（默认 2）")
    parser.add_argument("--page-size", type=int, default=8, help="Guardian 每页条数（默认 8）")
    parser.add_argument(
        "--max-articles",
        type=int,
        default=10,
        help="微信/新华/新浪/政策每源或每国上限（默认 10）；文献为每源上限",
    )
    parser.add_argument("--countries", type=str, nargs="+", default=None, help="policy 专用国家代码")
    parser.add_argument(
        "--literature-sources",
        type=str,
        nargs="+",
        default=None,
        choices=["arxiv", "scopus", "springer"],
    )
    parser.add_argument("--no-rag", action="store_true", help="禁用 RAG 子域精炼（与手动同步一致）")
    parser.add_argument("--dry-run", action="store_true", help="完整 LLM 抽取但不写库（部分信源支持）")
    args = parser.parse_args()

    init_db()

    if args.source == "news":
        return _run_news(args)
    if args.source == "literature":
        return _run_literature(args)
    return _run_single(args)


if __name__ == "__main__":
    raise SystemExit(main())
