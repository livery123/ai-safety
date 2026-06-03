#!/usr/bin/env python3
"""
五国政策源流水线诊断：分国统计 raw → 预筛 →（可选）LLM 漏斗。

功能：不改库，量化各国抓取量与 orchestrator 预筛命中率，定位入库少根因。
输入：--countries、--max-per、--llm-sample（可选 dry-run LLM 条数）。
输出：UTF-8 报告至 logs/policy_pipeline_diagnose_*.txt；stdout 打印路径。
上下游：调用 crawler.sources.policy、orchestrator 预筛逻辑。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _build_config(max_per: int):
    from crawler.sources.policy import PolicyConfig, policy_config_from_env

    cfg = policy_config_from_env()
    return PolicyConfig(
        timeout_sec=cfg.timeout_sec,
        retry_count=cfg.retry_count,
        retry_delay_sec=cfg.retry_delay_sec,
        page_delay_sec=cfg.page_delay_sec,
        eu_days_back=cfg.eu_days_back,
        brazil_max_offsets_per_day=cfg.brazil_max_offsets_per_day,
        brazil_lookback_days=cfg.brazil_lookback_days,
        max_articles_per_country=max(1, max_per),
        user_agent=cfg.user_agent,
        fetch_eu_full_text=cfg.fetch_eu_full_text,
        us_use_api=cfg.us_use_api,
        us_api_days_back=cfg.us_api_days_back,
        eu_use_search=cfg.eu_use_search,
    )


def _run_diagnose(
    countries: list[str],
    max_per: int,
    llm_sample: int,
) -> list[str]:
    from crawler.orchestrator import _policy_prefilter_relevant, _policy_source_tag
    from crawler.sources.policy import PolicySubscriber

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("【五国政策流水线诊断】")
    lines.append(f"时间: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"国家: {', '.join(countries)} | 每国 raw 上限: {max_per}")
    lines.append("-" * 72)

    cfg = _build_config(max_per)
    sub = PolicySubscriber(cfg)
    today = date.today()

    totals = {"raw": 0, "prefilter_ok": 0}

    for country in countries:
        code = country.upper()
        lines.append(f"\n>> {code}")
        try:
            with sub._new_http_client() as client:
                page = sub.subscribe_country(
                    client=client,
                    country=code,
                    download_date=today,
                )
            arts = page.articles
        except Exception as e:
            lines.append(f"  ❌ 抓取失败: {type(e).__name__}: {e}")
            continue

        raw_n = len(arts)
        pf_ok = [a for a in arts if _policy_prefilter_relevant(a)]
        pf_n = len(pf_ok)
        totals["raw"] += raw_n
        totals["prefilter_ok"] += pf_n

        lines.append(f"  ✓ raw: {raw_n} | 过预筛: {pf_n} | 源: {(page.page_url or '')[:100]}")
        if raw_n:
            pct = 100.0 * pf_n / raw_n
            lines.append(f"  预筛通过率: {pct:.1f}%")
        else:
            lines.append("  ⚠ raw=0 → 瓶颈在 policy.py 抓取层")

        for i, a in enumerate(arts[:5], 1):
            tag = _policy_source_tag(a)
            passed = _policy_prefilter_relevant(a)
            lines.append(
                f"  [{i}] {'✓' if passed else '✗'} {a.title[:60]} | {tag}"
            )

        if llm_sample > 0 and pf_ok:
            lines.append(f"  --- LLM dry-run 抽样（最多 {llm_sample} 条）---")
            sample = pf_ok[:llm_sample]
            try:
                from crawler.orchestrator import async_sync_policy

                result = asyncio.run(
                    async_sync_policy(
                        countries=[code],
                        max_articles_per_country=len(sample),
                        dry_run=True,
                        skip_prefilter=True,
                        concurrency=2,
                    )
                )
                for log_line in result.debug_log[-min(20, len(result.debug_log)) :]:
                    lines.append(f"    {log_line}")
                lines.append(f"  LLM 抽样 saved(dry): {result.saved} | skipped: {result.skipped_no_incident}")
            except Exception as e:
                lines.append(f"  ⚠ LLM 抽样失败: {type(e).__name__}: {e}")

    lines.append("\n" + "=" * 72)
    lines.append(
        f"合计 raw={totals['raw']} | 过预筛={totals['prefilter_ok']}"
    )
    if totals["raw"]:
        lines.append(
            f"总预筛通过率: {100.0 * totals['prefilter_ok'] / totals['raw']:.1f}%"
        )
    lines.append("=" * 72)
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="五国政策源 raw→预筛→LLM 漏斗诊断")
    parser.add_argument(
        "--countries",
        default="US,UK,EU,IN,BR",
        help="逗号分隔国家代码",
    )
    parser.add_argument("--max-per", type=int, default=30, help="每国 raw 上限")
    parser.add_argument(
        "--llm-sample",
        type=int,
        default=0,
        help="每国对过预筛条目做 dry-run LLM 抽样条数（0=跳过）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="报告路径；默认 logs/policy_pipeline_diagnose_<ts>.txt",
    )
    args = parser.parse_args()

    countries = [c.strip() for c in args.countries.split(",") if c.strip()]
    lines = _run_diagnose(countries, args.max_per, args.llm_sample)

    out_dir = _ROOT / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = _ROOT / out_path
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"policy_pipeline_diagnose_{ts}.txt"

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n报告已写入: {out_path}")


if __name__ == "__main__":
    main()
