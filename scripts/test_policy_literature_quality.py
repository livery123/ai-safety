#!/usr/bin/env python3
"""
政策源与文献源抓取质量诊断：只测拉取/字段完整性，默认不跑 LLM、不入库。

功能：分别测试 policy（RawArticle）、arxiv/scopus/springer（LiteratureItem）字段是否满足流水线要求。
输入：命令行 --policy-countries、--literature-sources、--output 等。
输出：UTF-8 报告写入 logs/；stdout 打印路径。
上下游：独立脚本；调用 crawler.sources.policy / literature。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _policy_section(countries: list[str], max_per: int) -> list[str]:
    from crawler.sources.policy import PolicySubscriber, PolicyConfig

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("【政策源 → articles 流水线】")
    lines.append(f"国家: {', '.join(countries)} | 每国上限: {max_per}")
    lines.append("-" * 72)

    cfg = PolicyConfig(max_articles_per_country=max(1, max_per))
    sub = PolicySubscriber(cfg)
    all_arts = []
    total_ok_url = total_ok_title = total_ok_body = total_ok_date = 0
    total_n = 0

    for country in countries:
        code = country.upper()
        lines.append(f"\n>> {code}")
        try:
            with sub._new_http_client() as client:
                page = sub.subscribe_country(
                    client=client,
                    country=code,
                    download_date=date.today(),
                )
            arts = page.articles
        except Exception as e:
            lines.append(f"  ❌ 拉取失败: {type(e).__name__}: {e}")
            continue

        lines.append(f"  ✓ 条数: {len(arts)} | 源页: {(page.page_url or '')[:80]}")
        ok_url = ok_title = ok_body = ok_date = 0
        preview = arts[:5]
        for i, a in enumerate(preview, 1):
            has_url = bool(a.web_url)
            has_title = bool(a.title)
            has_body = bool((a.body_text or a.trail_text or "").strip())
            has_date = bool(a.web_publication_date)
            ok_url += int(has_url)
            ok_title += int(has_title)
            ok_body += int(has_body)
            ok_date += int(has_date)
            lines.append(f"  --- [{i}] {a.title[:58]}")
            lines.append(f"      section : {a.section_name}")
            lines.append(f"      url     : {'OK' if has_url else '缺失'} | {(a.web_url or '')[:85]}")
            lines.append(f"      date    : {'OK' if has_date else '缺失'} | {a.web_publication_date}")
            lines.append(
                f"      body    : {'OK' if has_body else '缺失'} | len={len(a.body_text or a.trail_text or '')}"
            )
        n = len(preview)
        if n:
            lines.append(
                f"  小结(前{n}条): url {ok_url}/{n} | title {ok_title}/{n} | "
                f"正文/摘要 {ok_body}/{n} | 日期 {ok_date}/{n}"
            )
            total_ok_url += ok_url
            total_ok_title += ok_title
            total_ok_body += ok_body
            total_ok_date += ok_date
            total_n += n
        all_arts.extend(arts)

    lines.append("")
    lines.append(f"合计拉取: {len(all_arts)} 条")
    if total_n:
        lines.append(
            f"抽样质量(前{total_n}条): url {total_ok_url}/{total_n} | title {total_ok_title}/{total_n} | "
            f"正文/摘要 {total_ok_body}/{total_n} | 日期 {total_ok_date}/{total_n}"
        )
    return lines


def _literature_section(sources: list[str], max_arxiv: int, max_springer: int) -> list[str]:
    from crawler.sources.literature import (
        fetch_arxiv_literature,
        fetch_scopus_literature,
        fetch_springer_literature,
    )

    lines: list[str] = []
    lines.append("")
    lines.append("=" * 72)
    lines.append("【文献源 → literature_items 表】")
    lines.append(f"源: {', '.join(sources)}")
    lines.append("-" * 72)

    all_items = []
    if "arxiv" in sources:
        try:
            items = fetch_arxiv_literature(
                categories=["cs.AI", "cs.CL"],
                max_articles_per_category=max_arxiv,
            )
            lines.append(f"✓ arXiv: {len(items)} 条")
            all_items.extend(items)
        except Exception as e:
            lines.append(f"❌ arXiv: {type(e).__name__}: {e}")

    if "springer" in sources:
        try:
            items = fetch_springer_literature(
                domains=["Machine Learning"],
                max_articles_per_domain=max_springer,
            )
            lines.append(f"✓ Springer: {len(items)} 条")
            all_items.extend(items)
        except Exception as e:
            lines.append(f"❌ Springer: {type(e).__name__}: {e}")

    if "scopus" in sources:
        try:
            items = fetch_scopus_literature(max_results=5)
            lines.append(f"✓ Scopus: {len(items)} 条")
            all_items.extend(items)
        except Exception as e:
            lines.append(f"⚠ Scopus: {type(e).__name__}: {e}（需 SCOPUS_API_KEY）")

    ok_url = ok_title = ok_meta = ok_date = 0
    for i, it in enumerate(all_items[:25], 1):
        hu = bool(it.url)
        ht = bool(it.title)
        hm = bool(it.abstract or it.publication_name or it.doi)
        hd = bool(it.published_at)
        ok_url += int(hu)
        ok_title += int(ht)
        ok_meta += int(hm)
        ok_date += int(hd)
        lines.append(f"--- [{i}] [{it.source}] {it.title[:55]}")
        lines.append(f"  url   : {'OK' if hu else '缺失'}")
        lines.append(f"  doi   : {it.doi or '-'} | ext_id: {it.external_id or '-'}")
        lines.append(f"  pub   : {it.publication_name or '-'}")
        lines.append(f"  date  : {'OK' if hd else '缺失'} | {it.published_at}")
        lines.append(f"  abs   : {(it.abstract or '')[:100]}...")
    n = min(len(all_items), 25)
    if n:
        lines.append(
            f"小结(前{n}条): url {ok_url}/{n} | title {ok_title}/{n} | "
            f"摘要/元数据 {ok_meta}/{n} | 日期 {ok_date}/{n}"
        )
    elif not all_items:
        lines.append("（无文献条目）")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="政策+文献抓取质量报告")
    parser.add_argument("--policy-countries", nargs="+", default=["US", "UK", "EU", "IN", "BR"])
    parser.add_argument("--policy-max-per-country", type=int, default=5)
    parser.add_argument(
        "--literature-sources",
        nargs="+",
        default=["arxiv"],
        choices=["arxiv", "springer", "scopus"],
    )
    parser.add_argument("--max-arxiv", type=int, default=2)
    parser.add_argument("--max-springer", type=int, default=2)
    parser.add_argument("--output", default="")
    parser.add_argument("--skip-policy", action="store_true")
    parser.add_argument("--skip-literature", action="store_true")
    args = parser.parse_args()

    out_dir = _ROOT / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output.strip() else out_dir / f"policy_literature_quality_{ts}.txt"

    lines = [
        "政策源 + 文献源 抓取质量报告",
        f"生成时间: {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    if not args.skip_policy:
        lines.extend(_policy_section(args.policy_countries, args.policy_max_per_country))
    if not args.skip_literature:
        lines.extend(
            _literature_section(args.literature_sources, args.max_arxiv, args.max_springer)
        )

    text = "\n".join(lines) + "\n"
    out_path.write_text(text, encoding="utf-8")
    print(f"已写入: {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
