#!/usr/bin/env python3
"""
功能：对历史 policy/report 回填 article_extractions 发布地理四字段。
输入：--content-type、--limit、--dry-run、--force；读 articles + article_extractions。
输出：stdout 进度；调用 update_extraction_publish_fields 写 MySQL。
上下游：部署前执行 migrate_extraction_publish_fields.py；复用 crawler/extraction.extract_publish_geo_fields。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.llm_client import OpenAICompatibleBackend  # noqa: E402
from core.mysql_db import mysql_conn, update_extraction_publish_fields  # noqa: E402
from core.publish_actor import hints_from_raw_article  # noqa: E402
from crawler.extraction import extract_publish_geo_fields  # noqa: E402


def _parse_content_types(raw: str) -> List[str]:
    parts = [p.strip().lower() for p in (raw or "policy,report").split(",") if p.strip()]
    return parts or ["policy", "report"]


def _fetch_candidates(
    content_types: Sequence[str],
    limit: int,
    force: bool,
    *,
    before_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    功能：查询待回填的 extraction + article 行。
    输入：content_type 列表、limit、force；before_id 用于 --all 游标分页。
    输出：dict 列表，含 article_id、source、title、正文片段等。
    """
    placeholders = ", ".join(["%s"] * len(content_types))
    missing_clause = ""
    if not force:
        missing_clause = (
            " AND (e.publish_country IS NULL OR e.publish_country = '')"
            " AND (e.publish_region IS NULL OR e.publish_region = '')"
            " AND (e.publish_authority IS NULL OR e.publish_authority = '')"
        )
    cursor_clause = ""
    params: List[Any] = list(content_types)
    if before_id is not None:
        cursor_clause = " AND e.id < %s"
        params.append(before_id)
    params.append(max(1, int(limit)))
    sql = f"""
    SELECT
        e.article_id,
        e.id AS extraction_id,
        e.content_type,
        a.source,
        a.title_raw,
        a.summary_raw,
        a.content_raw,
        a.normalized_url,
        e.publish_country,
        e.publish_region,
        e.publish_authority
    FROM article_extractions e
    INNER JOIN articles a ON a.id = e.article_id
    WHERE e.content_type IN ({placeholders})
    {missing_clause}
    {cursor_clause}
    ORDER BY e.id DESC
    LIMIT %s
    """
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return list(cur.fetchall() or [])


def _build_body(row: Dict[str, Any]) -> str:
    """拼装 LLM 上下文：标题 + 摘要 + 正文。"""
    title = str(row.get("title_raw") or "").strip()
    summary = str(row.get("summary_raw") or "").strip()
    content = str(row.get("content_raw") or "").strip()
    parts = [p for p in (title, summary, content) if p]
    return "\n\n".join(parts)


def _count_candidates(content_types: Sequence[str], force: bool) -> int:
    """待回填总行数（--all 循环终止条件）。"""
    placeholders = ", ".join(["%s"] * len(content_types))
    missing_clause = ""
    if not force:
        missing_clause = (
            " AND (e.publish_country IS NULL OR e.publish_country = '')"
            " AND (e.publish_region IS NULL OR e.publish_region = '')"
            " AND (e.publish_authority IS NULL OR e.publish_authority = '')"
        )
    sql = f"""
    SELECT COUNT(*) AS n
    FROM article_extractions e
    WHERE e.content_type IN ({placeholders})
    {missing_clause}
    """
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(content_types))
            row = cur.fetchone()
    return int(row.get("n", 0) if row else 0)


def _process_one(
    row: Dict[str, Any],
    backend: Optional[OpenAICompatibleBackend],
    dry_run: bool,
    *,
    timeout: float = 180.0,
    max_retries: int = 3,
    retry_delay: float = 3.0,
) -> bool:
    """
    功能：对单行执行 geo 抽取并落库。
    输入：候选行、LLM backend、dry_run。
    输出：True 表示成功或 dry-run 预览。
    """
    article_id = int(row["article_id"])
    source = str(row.get("source") or "").strip()
    url = str(row.get("normalized_url") or "").strip()
    body = _build_body(row)
    if not body:
        print(f"  跳过 article_id={article_id}：无正文")
        return False

    hints = hints_from_raw_article(source=source, section_name="", body_text=body)
    geo, dbg = extract_publish_geo_fields(
        body,
        url,
        backend=backend,
        crawl_hints=hints,
        timeout=timeout,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
    for line in dbg:
        print(f"  {line}")

    if not geo:
        print(f"  失败 article_id={article_id}")
        return False

    pub_country = geo.get("publish_country") or ""
    pub_region = geo.get("publish_region") or ""
    pub_authority = geo.get("publish_authority") or ""
    intl = geo.get("international_orgs") or []

    print(
        f"  article_id={article_id} | "
        f"country={pub_country!r} region={pub_region!r} "
        f"authority={pub_authority!r} intl={intl}"
    )

    if dry_run:
        return True

    update_extraction_publish_fields(
        article_id,
        publish_country=pub_country,
        publish_region=pub_region,
        international_orgs=intl,
        publish_authority=pub_authority,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 policy/report 发布地理四字段")
    parser.add_argument(
        "--content-type",
        default="policy,report",
        help="逗号分隔 content_type，默认 policy,report",
    )
    parser.add_argument("--limit", type=int, default=50, help="最多处理条数")
    parser.add_argument("--dry-run", action="store_true", help="只预览不写库")
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使已有四字段也重新抽取",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="按 --limit 为批次循环，直到无候选行",
    )
    parser.add_argument(
        "--batch-delay",
        type=float,
        default=0.0,
        help="每条处理后的 sleep 秒数，避免 LLM 限流",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="单次 LLM 请求超时（秒），重试时会递增",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="LLM 超时/网络错误最大重试次数",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=3.0,
        help="重试前等待秒数",
    )
    args = parser.parse_args()

    content_types = _parse_content_types(args.content_type)
    batch_size = max(1, int(args.limit))
    total_pending = _count_candidates(content_types, args.force)
    print(
        f"候选共 {total_pending} 条（types={content_types}, "
        f"batch={batch_size}, dry_run={args.dry_run}, force={args.force}, all={args.all}）"
    )
    if total_pending == 0:
        print("无需处理。")
        return 0

    backend = OpenAICompatibleBackend()
    ok = 0
    fail = 0
    skip = 0
    processed = 0
    batch_no = 0
    before_id: Optional[int] = None

    while True:
        batch_no += 1
        rows = _fetch_candidates(content_types, batch_size, args.force, before_id=before_id)
        if not rows:
            break
        print(f"\n=== 批次 {batch_no}：{len(rows)} 条 ===")
        for i, row in enumerate(rows, 1):
            processed += 1
            print(f"[{processed}/{total_pending}] extraction_id={row.get('extraction_id')}")
            body = _build_body(row)
            if not body:
                skip += 1
                print(f"  跳过 article_id={row.get('article_id')}：无正文")
                before_id = int(row["extraction_id"])
                continue
            if _process_one(
                row,
                backend,
                args.dry_run,
                timeout=args.timeout,
                max_retries=args.retries,
                retry_delay=args.retry_delay,
            ):
                ok += 1
            else:
                fail += 1
            before_id = int(row["extraction_id"])
            if args.batch_delay > 0:
                time.sleep(args.batch_delay)
        if not args.all:
            break

    print(f"\n完成：成功 {ok}，失败 {fail}，跳过 {skip}，共处理 {processed}")
    return 0 if ok > 0 or args.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
