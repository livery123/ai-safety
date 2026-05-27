#!/usr/bin/env python3
"""
信源同步入口（CLI）：按指定信源拉取 AI 治理/安全新闻并入库。

功能：根据 --source 参数调用对应信源的同步函数，打印摘要与 debug 日志；适合 cron 或手动触发。
      --dry-run 开关：完整走 LLM 抽取流程，但跳过 MySQL/Chroma 写入，用于测试与质量评估。
输入：命令行参数；各信源 API Key 从 .env 或环境变量读取。
输出：stdout 日志；exit 0/1；副作用（非 dry-run 时）：MySQL 写入。

用法:
  ./venv/bin/python scripts/sync_sources.py                                         # 默认卫报
  ./venv/bin/python scripts/sync_sources.py --source guardian --pages 3 --page-size 8
  ./venv/bin/python scripts/sync_sources.py --source nyt --pages 2
  ./venv/bin/python scripts/sync_sources.py --source xinhua_tech --max-articles 5 --dry-run
  ./venv/bin/python scripts/sync_sources.py --source xinhua_tech --max-articles 10 --no-rag
  ./venv/bin/python scripts/sync_sources.py --source sina_tech --max-articles 5 --dry-run
  ./venv/bin/python scripts/sync_sources.py --source sina_tech --max-articles 10 --no-rag
  ./venv/bin/python scripts/sync_sources.py --source wechat_rss --dry-run
  ./venv/bin/python scripts/sync_sources.py --source wechat_rss --feed-names 机器之心 --dry-run
  ./venv/bin/python scripts/sync_sources.py --source wechat_rss --no-rag
  ./venv/bin/python scripts/sync_sources.py --source policy --countries US EU --max-articles 5 --dry-run
  ./venv/bin/python scripts/sync_sources.py --source literature --literature-sources arxiv --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.db import init_db  # noqa: E402


def _print_result(r: object, dry_run: bool = False) -> None:
    """打印 SyncResult 到 stdout。"""
    for line in r.debug_log:  # type: ignore[attr-defined]
        print(line)
    print("\n--- 汇总 ---")
    label = "相关抽取（dry-run，未入库）" if dry_run else "入库"
    print(f"{label}: {r.saved}")  # type: ignore[attr-defined]
    if not dry_run:
        print(f"已有（跳过）: {r.skipped_url_dup}")  # type: ignore[attr-defined]
    print(f"无关（跳过）: {r.skipped_no_incident}")  # type: ignore[attr-defined]
    print(f"失败: {r.failed}")  # type: ignore[attr-defined]
    if getattr(r, "new_keywords", []):
        kws = r.new_keywords  # type: ignore[attr-defined]
        print(f"新关键词: {', '.join(kws[:10])}{'...' if len(kws) > 10 else ''}")
    if getattr(r, "new_subdomains", []):
        print(f"新子域: {', '.join(r.new_subdomains)}")  # type: ignore[attr-defined]


def _print_literature_result(r: object, dry_run: bool = False) -> None:
    """打印 LiteratureSyncResult。"""
    for line in r.debug_log:  # type: ignore[attr-defined]
        print(line)
    print("\n--- 汇总 ---")
    label = "可入库（dry-run）" if dry_run else "新入库"
    print(f"{label}: {r.saved}")  # type: ignore[attr-defined]
    print(f"已有（跳过）: {r.skipped_url_dup}")  # type: ignore[attr-defined]
    print(f"失败: {r.failed}")  # type: ignore[attr-defined]


def _run_guardian(args: argparse.Namespace) -> int:
    from crawler.orchestrator import sync_guardian

    r = sync_guardian(
        query=args.query.strip() or None,
        max_pages=args.pages,
        page_size=args.page_size,
        section=args.section.strip() or None,
        rag_enabled=False if args.no_rag else None,
    )
    _print_result(r)
    return 1 if (r.failed > 0 and r.saved == 0) else 0


def _run_nyt(args: argparse.Namespace) -> int:
    from crawler.orchestrator import sync_nyt

    r = sync_nyt(
        query=args.query.strip() or None,
        max_pages=args.pages,
        rag_enabled=False if args.no_rag else None,
    )
    _print_result(r)
    return 1 if (r.failed > 0 and r.saved == 0) else 0


def _run_xinhua_tech(args: argparse.Namespace) -> int:
    """
    功能：新华网科技频道同步 CLI 入口。
    输入：args.max_articles（每次抓取文章数）、args.dry_run（不入库）、args.no_rag。
    输出：打印日志与汇总；exit 0/1。
    上下游：调用 crawler.orchestrator.sync_xinhua_tech。
    """
    from crawler.orchestrator import sync_xinhua_tech

    r = sync_xinhua_tech(
        max_articles=args.max_articles,
        rag_enabled=False if args.no_rag else None,
        dry_run=args.dry_run,
    )
    _print_result(r, dry_run=args.dry_run)
    return 1 if (r.failed > 0 and r.saved == 0) else 0


def _run_sina_tech(args: argparse.Namespace) -> int:
    """
    功能：新浪科技同步 CLI 入口。
    输入：args.max_articles（每次抓取文章数）、args.dry_run（不入库）、args.no_rag。
    输出：打印日志与汇总；exit 0/1。
    上下游：调用 crawler.orchestrator.sync_sina_tech。
    """
    from crawler.orchestrator import sync_sina_tech

    r = sync_sina_tech(
        max_articles=args.max_articles,
        rag_enabled=False if args.no_rag else None,
        dry_run=args.dry_run,
    )
    _print_result(r, dry_run=args.dry_run)
    return 1 if (r.failed > 0 and r.saved == 0) else 0


def _run_wechat_rss(args: argparse.Namespace) -> int:
    """
    功能：微信公众号 RSS 同步 CLI 入口。
    输入：args.feed_names（逗号分隔的公众号名称，默认全池）、args.max_articles（每源文章数）、
         args.dry_run（不入库）、args.no_rag。
    输出：打印日志与汇总；exit 0/1。
    上下游：调用 crawler.orchestrator.sync_wechat_rss。
    """
    from crawler.orchestrator import sync_wechat_rss

    # --feed-names 支持多次传入或逗号分隔
    feed_names = None
    if args.feed_names:
        raw = []
        for item in args.feed_names:
            raw.extend(n.strip() for n in item.split(",") if n.strip())
        feed_names = raw or None

    r = sync_wechat_rss(
        feed_names=feed_names,
        max_articles_per_feed=args.max_articles,
        rag_enabled=False if args.no_rag else None,
        dry_run=args.dry_run,
    )
    _print_result(r, dry_run=args.dry_run)
    return 1 if (r.failed > 0 and r.saved == 0) else 0


def _run_policy(args: argparse.Namespace) -> int:
    from crawler.orchestrator import sync_policy

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
        dry_run=args.dry_run,
        skip_prefilter=args.skip_prefilter,
    )
    _print_result(r, dry_run=args.dry_run)
    return 1 if (r.failed > 0 and r.saved == 0) else 0


def _run_literature(args: argparse.Namespace) -> int:
    from crawler.orchestrator import sync_literature

    sources = None
    if args.literature_sources:
        sources = [s.strip().lower() for s in args.literature_sources if s.strip()]

    r = sync_literature(
        sources=sources or ["arxiv"],
        max_arxiv_per_category=args.max_articles,
        max_springer_per_domain=args.max_articles,
        scopus_max_results=args.max_articles,
        dry_run=args.dry_run,
    )
    _print_literature_result(r, dry_run=args.dry_run)
    return 1 if (r.failed > 0 and r.saved == 0) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="多信源 AI 安全新闻同步入库")
    parser.add_argument(
        "--source",
        choices=["guardian", "nyt", "xinhua_tech", "sina_tech", "wechat_rss", "policy", "literature"],
        default="guardian",
        help=(
            "信源：guardian | nyt | xinhua_tech | sina_tech | wechat_rss | "
            "policy（政策→articles）| literature（文献→literature_items）"
        ),
    )
    parser.add_argument("--pages", type=int, default=2, help="最大拉取页数（默认 2），guardian/nyt 有效")
    parser.add_argument("--page-size", type=int, default=10, help="每页条数，仅 guardian 有效（默认 10）")
    parser.add_argument(
        "--max-articles", type=int, default=10,
        help="抓取文章数上限：xinhua/sina 为总数，wechat_rss 为每公众号，policy 为每国家，literature 为每源上限（默认 10）",
    )
    parser.add_argument(
        "--countries", type=str, nargs="+", default=None,
        help="policy 专用：国家代码 US UK EU IN BR（可逗号分隔）",
    )
    parser.add_argument(
        "--literature-sources", type=str, nargs="+", default=None,
        choices=["arxiv", "scopus", "springer"],
        help="literature 专用：要同步的文献源",
    )
    parser.add_argument(
        "--skip-prefilter", action="store_true",
        help="policy 专用：跳过 AI 关键词预筛（调试）",
    )
    parser.add_argument("--section", type=str, default="", help="版块过滤，仅 guardian 有效")
    parser.add_argument("--query", type=str, default="", help="覆盖默认检索词，guardian/nyt 有效")
    parser.add_argument(
        "--feed-names", type=str, nargs="+", default=None,
        help="wechat_rss 专用：指定公众号名称（可多次传入或逗号分隔），默认全池同步",
    )
    parser.add_argument("--no-rag", action="store_true", help="禁用 RAG 精炼")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="干跑模式：完整走 LLM 抽取，但不写入 MySQL/Chroma，用于测试质量",
    )
    args = parser.parse_args()

    init_db()

    if args.source == "guardian":
        return _run_guardian(args)
    elif args.source == "nyt":
        return _run_nyt(args)
    elif args.source == "xinhua_tech":
        return _run_xinhua_tech(args)
    elif args.source == "sina_tech":
        return _run_sina_tech(args)
    elif args.source == "wechat_rss":
        return _run_wechat_rss(args)
    elif args.source == "policy":
        return _run_policy(args)
    elif args.source == "literature":
        return _run_literature(args)
    else:
        print(f"未知信源: {args.source}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
