#!/usr/bin/env python3
"""
微信 RSS 抓取质量诊断：抽样拉取配置池中的公众号 RSS，统计 RawArticle 各字段完整性并写入日志。

功能：评估 wechat2rss 条目是否满足编排器去重、入库时间与 LLM 抽取上下文所需字段；
     对照 `raw_article_to_llm_context` 所需 Title / Lead / Body。
输入：命令行 --max-articles（每公众号抽样篇数）、--output（日志路径，可选）、--feed-delay；
      依赖网络访问 wechat2rss。
输出：UTF-8 文本报告；同时在 stdout 打印日志路径。
上下游：独立脚本；调用 crawler.sources.wechat2rss.fetch_wechat_feed / WECHAT_RSS_POOL。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# 字段说明（与 orchestrator / guardian.RawArticle / LLM 上下文对齐）
# ---------------------------------------------------------------------------
_REQUIRED_FIELDS_DOC = """
【编排与抽取关心的字段】
- web_url（必填）：MySQL 去重与溯源；为空则条目丢弃。
- title（必填）：展示与 LLM 上下文标题行。
- web_publication_date（强烈建议）：入库 articles.published_at；缺失则入库解析可能失败或退化。
- trail_text（摘要/导语）：LLM 上下文 Lead；可与正文互补。
- body_text（正文）：LLM 上下文 Body；RSS 常仅有摘要则正文偏短。
- section_name：微信侧为 \"WeChat / {公众号名}\"，用于展示来源。
- api_url：此处填 RSS URL，便于调试。
- guardian_id：微信 RSS 不适用，恒为 None。
"""


def _preview(text: Optional[str], max_len: int = 160) -> str:
    """截取单行预览，避免日志过长。"""
    if not text:
        return ""
    one_line = " ".join(text.split())
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 3] + "..."


def _bool_tag(ok: bool) -> str:
    return "OK" if ok else "缺失"


def _analyze_article_fields(art: Any) -> dict[str, Any]:
    """从 RawArticle 汇总布尔与长度指标。"""
    trail = art.trail_text or ""
    body = art.body_text or ""
    return {
        "has_url": bool((art.web_url or "").strip()),
        "has_title": bool((art.title or "").strip()),
        "has_date": bool(art.web_publication_date),
        "trail_len": len(trail),
        "body_len": len(body),
        "has_lead_or_body": bool(trail.strip() or body.strip()),
        "body_substantial": len(body.strip()) >= 80,
        "section_ok": bool(art.section_name and "WeChat" in art.section_name),
    }


def _feed_diagnostic_lines(
    feed_name: str,
    rss_url: str,
    articles: List[Any],
    status_code: int,
    entry_count: int,
) -> List[str]:
    """生成单个公众号小节的多行文本。"""
    lines: List[str] = []
    lines.append("")
    lines.append("=" * 72)
    lines.append(f"公众号: {feed_name}")
    lines.append(f"RSS URL: {rss_url}")
    lines.append(f"HTTP status: {status_code} | feed 总条目数（解析后）: {entry_count} | 本次抽样: {len(articles)} 篇")
    lines.append("-" * 72)

    if not articles:
        lines.append("（无可用 RawArticle：可能 RSS 错误或条目均无 link）")
        return lines

    stats = {"has_date": 0, "has_body80": 0, "has_lead_body": 0}
    for i, art in enumerate(articles, 1):
        m = _analyze_article_fields(art)
        stats["has_date"] += int(m["has_date"])
        stats["has_body80"] += int(m["body_substantial"])
        stats["has_lead_body"] += int(m["has_lead_or_body"])

        lines.append(f"--- 第 {i} 篇 ---")
        lines.append(f"  title       : {_preview(art.title, 200)}")
        lines.append(f"  web_url     : {(art.web_url or '')[:120]}{'...' if len(art.web_url or '') > 120 else ''}")
        lines.append(f"  pub_date    : {_bool_tag(m['has_date'])} | {art.web_publication_date or '(null)'}")
        lines.append(
            f"  trail_text  : len={m['trail_len']} | {_bool_tag(m['trail_len'] > 0)}"
            + (f" | {_preview(art.trail_text)}" if art.trail_text else "")
        )
        lines.append(
            f"  body_text   : len={m['body_len']} | >=80字:{_bool_tag(m['body_substantial'])}"
            + (f" | {_preview(art.body_text)}" if art.body_text else "")
        )
        lines.append(f"  section_name: {art.section_name}")
        api_u = art.api_url or ""
        lines.append(f"  api_url     : {api_u[:120]}{'...' if len(api_u) > 120 else ''}")
        lines.append(f"  guardian_id : {art.guardian_id}")
        ctx_hint = "LLM上下文"
        if m["has_lead_or_body"]:
            ctx_hint += ": 有 Lead 或 Body"
        else:
            ctx_hint += ": ⚠ 仅有标题（抽取质量差）"
        lines.append(f"  → {ctx_hint}")

    n = len(articles)
    lines.append("-" * 72)
    lines.append(
        f"小结（{feed_name}）: 有发布时间 {stats['has_date']}/{n} | "
        f"正文≥80字 {stats['has_body80']}/{n} | "
        f"有摘要或正文 {stats['has_lead_body']}/{n}"
    )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="诊断微信 RSS → RawArticle 字段完整性")
    parser.add_argument(
        "--max-articles",
        type=int,
        default=5,
        help="每个公众号最多抽样篇数（默认 5）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="日志输出路径（默认 logs/wechat_rss_quality_<时间戳>.txt）",
    )
    parser.add_argument(
        "--feed-delay",
        type=float,
        default=0.5,
        help="公众号之间的请求间隔秒数（默认 0.5，减轻对方压力）",
    )
    args = parser.parse_args()

    from crawler.sources.wechat2rss import WECHAT_RSS_POOL, WechatRSSError, fetch_wechat_feed

    import time as time_mod

    out_dir = _ROOT / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output.strip() else out_dir / f"wechat_rss_quality_{ts}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = []
    lines.append("微信 RSS 抓取质量报告（RawArticle 字段）")
    lines.append(f"生成时间: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"每公众号抽样上限: {args.max_articles}")
    lines.append(_REQUIRED_FIELDS_DOC.strip())

    pool_errors = 0
    grand = {"feeds": 0, "articles": 0, "has_date": 0, "body80": 0, "lead_or_body": 0}

    for idx, (name, url) in enumerate(WECHAT_RSS_POOL.items()):
        if idx > 0:
            time_mod.sleep(max(0.0, float(args.feed_delay)))
        try:
            page = fetch_wechat_feed(name, url, max_articles=args.max_articles)
        except WechatRSSError as e:
            pool_errors += 1
            lines.append("")
            lines.append("=" * 72)
            lines.append(f"公众号: {name} — RSS 失败")
            lines.append(f"  {e}")
            continue
        except Exception as e:
            pool_errors += 1
            lines.append("")
            lines.append("=" * 72)
            lines.append(f"公众号: {name} — 异常 {type(e).__name__}: {e}")
            continue

        grand["feeds"] += 1
        arts = page.articles
        grand["articles"] += len(arts)
        for a in arts:
            m = _analyze_article_fields(a)
            grand["has_date"] += int(m["has_date"])
            grand["body80"] += int(m["body_substantial"])
            grand["lead_or_body"] += int(m["has_lead_or_body"])

        lines.extend(_feed_diagnostic_lines(name, url, arts, page.status_code, page.entry_count))

    lines.append("")
    lines.append("=" * 72)
    lines.append("全局汇总")
    lines.append(
        f"成功拉取的公众号数: {grand['feeds']} / {len(WECHAT_RSS_POOL)} | "
        f"RSS 失败或异常公众号: {pool_errors}"
    )
    if grand["articles"]:
        n = grand["articles"]
        lines.append(
            f"总抽样文章: {n} | 含发布时间: {grand['has_date']}/{n} "
            f"({100.0 * grand['has_date'] / n:.1f}%)"
        )
        lines.append(
            f"正文≥80字: {grand['body80']}/{n} ({100.0 * grand['body80'] / n:.1f}%) | "
            f"有摘要或正文: {grand['lead_or_body']}/{n} ({100.0 * grand['lead_or_body'] / n:.1f}%)"
        )
    else:
        lines.append("总抽样文章: 0（请检查网络或 RSS 可用性）")

    text = "\n".join(lines) + "\n"
    out_path.write_text(text, encoding="utf-8")
    print(f"已写入: {out_path.resolve()}")
    return 0 if pool_errors == 0 or grand["articles"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
