#!/usr/bin/env python3
"""
papers 旧库 → ai_governance（articles + article_extractions）高质量迁移。

功能：
  - 仅迁移 policy、articles（不含 arxiv/scorpus/文献表）。
  - SQL 预筛（similarity、AI 治理关键词、正文长度、脏数据剔除）+ LLM is_relevant 双门槛。
  - URL 已在目标库则跳过，不覆盖现网 Guardian/NYT 等数据。
输入：环境变量 PAPERS_MYSQL_*、MYSQL_*；命令行 --table/--limit/--offset 等。
输出：stdout 统计与质量报告；副作用：写入 MySQL（非 dry-run 时）。

用法：
  ./venv/bin/python scripts/migrate_papers_to_mysql.py --prefilter-only --table policy --limit 200
  ./venv/bin/python scripts/migrate_papers_to_mysql.py --table articles --limit 20 --no-index
  ./venv/bin/python scripts/migrate_papers_to_mysql.py --table policy --limit 5000 --offset 0
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pymysql
from pymysql.cursors import DictCursor

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.config import API_KEY, BASE_URL, LLM_MODEL  # noqa: E402
from core.llm_client import OpenAICompatibleBackend  # noqa: E402
from core.mysql_db import get_article_by_url, normalize_url  # noqa: E402
from crawler.extraction import (  # noqa: E402
    async_extract_article_from_text,
    article_dict_to_incident_like,
    merge_article_with_rag,
)
from core.mysql_db import save_article, save_extraction  # noqa: E402
from crawler.orchestrator import SyncResult, _persist_mysql_phase1  # noqa: E402
from crawler.sources.guardian import RawArticle  # noqa: E402


def _persist_row(
    c: CandidateRow,
    merged: Dict[str, Any],
    *,
    no_index: bool,
    llm_backend: OpenAICompatibleBackend,
) -> int:
    """
    写入 articles + article_extractions；no_index 时不建 Chroma。
    返回 1 表示成功写入 extraction，0 表示失败。
    """
    if no_index:
        try:
            article_id, is_new = save_article(
                url=c.url,
                title=c.title,
                summary=c.summary,
                content=c.content or c.summary,
                published_at=c.published_at,
                source=c.source_label,
            )
            if not is_new:
                return 0
            save_extraction(
                article_id,
                merged,
                model_name=(LLM_MODEL or "papers_import_v1").strip(),
            )
            return 1
        except Exception:
            return 0
    result = SyncResult()
    pub_str = c.published_at.strftime("%Y-%m-%d %H:%M:%S") if c.published_at else ""
    art = RawArticle(
        web_url=c.url,
        title=c.title,
        trail_text=c.summary,
        body_text=c.content or c.summary,
        web_publication_date=pub_str,
        section_name=c.source_table,
        api_url=None,
        guardian_id=None,
    )
    _persist_mysql_phase1(
        art,
        merged,
        result,
        llm_backend=llm_backend,
        source=c.source_label,
        force_reindex=False,
    )
    return result.saved

# ---------------------------------------------------------------------------
# 质量预筛：AI 治理/安全相关关键词（标题/摘要/正文任一命中即可进入候选）
# ---------------------------------------------------------------------------
_AI_GOV_PATTERN = re.compile(
    r"artificial intelligence|\bAI\b|machine learning|deep learning|neural network|"
    r"large language model|\bLLM\b|generative AI|AI governance|AI safety|AI regulation|"
    r"algorithmic bias|AI Act|executive order|alignment|red team|"
    r"人工智能|机器学习|大模型|生成式|对齐|算法歧视|AI监管|伦理治理",
    re.IGNORECASE,
)

# 旧库高价值 topic（加分，非必须）
_PREFERRED_TOPICS = frozenset(
    {
        "ethics_governance",
        "risk_assessment",
        "control_prevention",
        "theoretical_research",
    }
)

# 明显低价值 policy 标题（例行公告且无摘要）
_LOW_VALUE_POLICY_TITLE = re.compile(
    r"sunshine act meeting|public inspection|notice of filing",
    re.IGNORECASE,
)

_IP_LEAK_PATTERN = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


@dataclass
class CandidateRow:
    """单条通过预筛、待 LLM/入库的候选。"""

    source_table: str
    legacy_key: str
    url: str
    title: str
    summary: str
    content: str
    published_at: Optional[datetime]
    source_label: str
    similarity: Optional[float]
    topic: Optional[str]
    quality_score: float
    quality_notes: List[str] = field(default_factory=list)


@dataclass
class MigrateStats:
    """迁移运行统计。"""

    scanned: int = 0
    prefilter_pass: int = 0
    prefilter_reject: int = 0
    skipped_url_dup: int = 0
    llm_relevant: int = 0
    llm_irrelevant: int = 0
    llm_failed: int = 0
    saved: int = 0
    reject_reasons: Dict[str, int] = field(default_factory=dict)
    prefilter_reject_reasons: Dict[str, int] = field(default_factory=dict)
    samples_relevant: List[str] = field(default_factory=list)
    samples_rejected: List[str] = field(default_factory=list)


def _papers_mysql_config() -> Dict[str, Any]:
    """读取 papers 源库连接配置。"""
    return {
        "host": os.getenv("PAPERS_MYSQL_HOST", "").strip(),
        "port": int(os.getenv("PAPERS_MYSQL_PORT", "3306")),
        "user": os.getenv("PAPERS_MYSQL_USER", "").strip(),
        "password": os.getenv("PAPERS_MYSQL_PASSWORD", ""),
        "database": os.getenv("PAPERS_MYSQL_DATABASE", "papers").strip(),
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
    }


@contextmanager
def papers_conn():
    """papers 源库连接上下文。"""
    cfg = _papers_mysql_config()
    if not cfg["host"] or not cfg["user"]:
        raise RuntimeError("请配置 PAPERS_MYSQL_HOST / PAPERS_MYSQL_USER 等环境变量")
    conn = pymysql.connect(**cfg)
    try:
        yield conn
    finally:
        conn.close()


def _s(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _has_ip_leak(text: str) -> bool:
    return bool(_IP_LEAK_PATTERN.search(text or ""))


def _parse_published_at(raw: Any) -> Optional[datetime]:
    """将 date/datetime/str 转为 naive datetime；脏日期返回 None。"""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw.replace(tzinfo=None) if raw.tzinfo else raw
    elif isinstance(raw, date):
        dt = datetime.combine(raw, datetime.min.time())
    else:
        s = _s(raw)
        if not s:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s[:19], fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            return None
    if dt.year <= 1971:
        return None
    if dt.year > datetime.now().year + 1:
        return None
    return dt


def _pick_url_policy(row: Dict[str, Any]) -> str:
    u = _s(row.get("url"))
    if u:
        return u
    return f"papers://policy/{row.get('id')}"


def _pick_url_articles(row: Dict[str, Any]) -> str:
    link = _s(row.get("article_title_link"))
    if link:
        return link
    doi = _s(row.get("DOI"))
    if doi:
        return f"https://doi.org/{doi}"
    return ""


def _build_text_policy(row: Dict[str, Any]) -> Tuple[str, str, str]:
    title = _s(row.get("title"))
    summary = _s(row.get("summary"))
    content = summary
    return title, summary, content


def _build_text_articles(row: Dict[str, Any]) -> Tuple[str, str, str]:
    title = _s(row.get("article_title_text_cn")) or _s(row.get("article_title_text"))
    summary = (
        _s(row.get("article_introduction_whole_cn"))
        or _s(row.get("article_introduction_whole"))
        or _s(row.get("article_introduction_part_cn"))
        or _s(row.get("article_introduction_part"))
    )
    content = (
        _s(row.get("article_content_whole_cn"))
        or _s(row.get("article_content_whole"))
        or summary
    )
    return title, summary, content


def _score_candidate(
    *,
    title: str,
    summary: str,
    content: str,
    similarity: Optional[float],
    topic: Optional[str],
    min_similarity: float,
) -> Tuple[float, List[str], Optional[str]]:
    """
    质量打分与预筛判定。
    返回 (score, notes, reject_reason)；reject_reason 非空则拒绝。
    """
    notes: List[str] = []
    score = 0.0
    blob = f"{title}\n{summary}\n{content}"

    if not title:
        return 0.0, notes, "empty_title"

    if _has_ip_leak(blob):
        return 0.0, notes, "ip_leak_in_text"

    sim = similarity
    if sim is not None:
        if sim < min_similarity:
            return score, notes, f"low_similarity_{sim:.3f}"
        score += min(1.0, (sim - min_similarity) * 5)
        notes.append(f"similarity={sim:.3f}")

    text_len = len((content or summary or "").strip())
    if text_len < 80:
        return score, notes, "text_too_short"

    if text_len >= 500:
        score += 0.3
        notes.append("rich_text")
    elif text_len >= 200:
        score += 0.15

    if _AI_GOV_PATTERN.search(blob):
        score += 0.4
        notes.append("ai_gov_keyword")
    else:
        return score, notes, "no_ai_gov_keyword"

    tpc = _s(topic)
    if tpc in _PREFERRED_TOPICS:
        score += 0.15
        notes.append(f"topic={tpc}")

    return score, notes, None


def _score_policy_row(row: Dict[str, Any], min_similarity: float) -> Tuple[float, List[str], Optional[str]]:
    title, summary, content = _build_text_policy(row)
    sim = row.get("similarity")
    try:
        sim_f = float(sim) if sim is not None else None
    except (TypeError, ValueError):
        sim_f = None

    if _LOW_VALUE_POLICY_TITLE.search(title) and len(summary) < 50:
        return 0.0, [], "routine_meeting_no_summary"

    reason = _score_candidate(
        title=title,
        summary=summary,
        content=content,
        similarity=sim_f,
        topic=_s(row.get("topic")) or None,
        min_similarity=min_similarity,
    )
    return reason


def _score_articles_row(row: Dict[str, Any], min_similarity: float) -> Tuple[float, List[str], Optional[str]]:
    title, summary, content = _build_text_articles(row)
    sim = row.get("similarity")
    try:
        sim_f = float(sim) if sim is not None else None
    except (TypeError, ValueError):
        sim_f = None

    return _score_candidate(
        title=title,
        summary=summary,
        content=content,
        similarity=sim_f,
        topic=_s(row.get("topic")) or None,
        min_similarity=min_similarity,
    )


def _row_to_candidate(table: str, row: Dict[str, Any], min_similarity: float) -> Tuple[Optional[CandidateRow], Optional[str]]:
    if table == "policy":
        score, notes, reject = _score_policy_row(row, min_similarity)
        if reject:
            return None, reject
        title, summary, content = _build_text_policy(row)
        url = _pick_url_policy(row)
        legacy = str(row.get("id"))
        source_label = "papers_policy"
        pub = _parse_published_at(row.get("publishtime"))
        sim = row.get("similarity")
    elif table == "articles":
        score, notes, reject = _score_articles_row(row, min_similarity)
        if reject:
            return None, reject
        title, summary, content = _build_text_articles(row)
        url = _pick_url_articles(row)
        if not url:
            return None, "missing_url"
        legacy = _s(row.get("DOI")) or url
        source_label = "papers_doi"
        pub = _parse_published_at(row.get("article_date"))
        sim = row.get("similarity")
    else:
        return None, "unsupported_table"

    return (
        CandidateRow(
            source_table=table,
            legacy_key=legacy,
            url=url,
            title=title,
            summary=summary,
            content=content,
            published_at=pub,
            source_label=source_label,
            similarity=float(sim) if sim is not None else None,
            topic=_s(row.get("topic")) or None,
            quality_score=score,
            quality_notes=notes,
        ),
        None,
    )


def _count_source_rows(table: str, *, min_similarity: float) -> int:
    """源表总行数（可选 similarity 下限）。"""
    if table not in ("policy", "articles"):
        raise ValueError(f"unsupported table: {table}")
    sql = f"SELECT COUNT(*) AS c FROM `{table}` WHERE COALESCE(similarity, 0) >= %s"
    with papers_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (min_similarity,))
            row = cur.fetchone() or {}
    return int(row.get("c") or 0)


def _iter_source_rows(
    table: str,
    *,
    limit: int,
    offset: int,
    min_similarity: float,
    scan_multiplier: int = 8,
) -> Iterator[Dict[str, Any]]:
    """
    从源表按 similarity 降序扫描；预筛在 Python 层完成。
    scan_multiplier：每期望 1 条候选多扫若干行（因预筛会淘汰大部分）。
    """
    if table not in ("policy", "articles"):
        raise ValueError(f"unsupported table: {table}")

    batch = max(limit * scan_multiplier, limit, 100)
    sql = (
        f"SELECT * FROM `{table}` WHERE COALESCE(similarity, 0) >= %s "
        f"ORDER BY similarity DESC LIMIT %s OFFSET %s"
    )
    with papers_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (min_similarity, batch, offset))
            rows = cur.fetchall() or []
    for row in rows:
        yield row


# 全量分批默认参数（按旧库体量：policy 16 万但 sim>=0.76 仅 ~256；articles ~1.1 万）
_BATCH_PLAN: Dict[str, Dict[str, int]] = {
    "policy": {"batch_limit": 50, "scan_step": 600, "scan_multiplier": 12},
    "articles": {"batch_limit": 40, "scan_step": 900, "scan_multiplier": 15},
}


async def _extract_with_retry(
    c: CandidateRow,
    backend: OpenAICompatibleBackend,
    *,
    max_retries: int = 4,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """LLM 抽取，失败时指数退避重试（缓解 429/超时）。"""
    last_log: List[str] = []
    for attempt in range(max(1, max_retries)):
        ctx = _llm_context(c)
        article_dict, ext_log = await async_extract_article_from_text(
            ctx,
            source_url=c.url,
            backend=backend,
            timeout=180.0,
        )
        last_log = ext_log
        if article_dict is not None:
            return article_dict, ext_log
        if attempt < max_retries - 1:
            await asyncio.sleep(min(30, 2 ** attempt * 3))
    return None, last_log


def _llm_context(c: CandidateRow) -> str:
    parts = [f"标题: {c.title}"]
    if c.summary:
        parts.append(f"摘要: {c.summary}")
    if c.content and c.content != c.summary:
        parts.append(f"正文: {c.content}")
    if c.topic:
        parts.append(f"主题标签: {c.topic}")
    return "\n\n".join(parts)


def _bump_reason(d: Dict[str, int], key: str) -> None:
    d[key] = d.get(key, 0) + 1


async def _migrate_candidates(
    candidates: List[CandidateRow],
    *,
    dry_run: bool,
    no_index: bool,
    concurrency: int,
    rag_enabled: bool,
    llm_retries: int = 4,
) -> MigrateStats:
    stats = MigrateStats()
    stats.prefilter_pass = len(candidates)

    llm_backend = OpenAICompatibleBackend(api_key=API_KEY, base_url=BASE_URL)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _process_one(c: CandidateRow) -> None:
        nu = normalize_url(c.url)
        if nu and get_article_by_url(nu):
            stats.skipped_url_dup += 1
            return

        if dry_run:
            stats.llm_relevant += 1
            if len(stats.samples_relevant) < 8:
                stats.samples_relevant.append(f"[dry-run] {c.title[:80]} | score={c.quality_score:.2f}")
            return

        async with sem:
            article_dict, ext_log = await _extract_with_retry(
                c, llm_backend, max_retries=llm_retries
            )

        if not article_dict:
            stats.llm_failed += 1
            return

        if not article_dict.get("is_relevant"):
            stats.llm_irrelevant += 1
            rr = str(article_dict.get("reject_reason") or "not_relevant")
            _bump_reason(stats.reject_reasons, rr)
            if len(stats.samples_rejected) < 8:
                stats.samples_rejected.append(f"{c.title[:70]} | {rr}")
            return

        stats.llm_relevant += 1
        if len(stats.samples_relevant) < 8:
            ct = article_dict.get("content_type", "")
            stats.samples_relevant.append(
                f"{c.title[:70]} | type={ct} | score={c.quality_score:.2f}"
            )

        incident_like = article_dict_to_incident_like(article_dict)
        try:
            from engine.rag_ingestion import apply_rag_to_incidents as _rag

            incidents_rag, _ = _rag([incident_like], llm_backend=llm_backend, enabled=rag_enabled)
        except ImportError:
            incidents_rag = [incident_like]

        inc_rag = incidents_rag[0] if incidents_rag else incident_like
        merged = merge_article_with_rag(article_dict, inc_rag)

        stats.saved += _persist_row(
            c,
            merged,
            no_index=no_index,
            llm_backend=llm_backend,
        )

    await asyncio.gather(*[_process_one(c) for c in candidates])
    return stats


def run_prefilter_report(
    table: str,
    *,
    limit: int,
    offset: int,
    min_similarity: float,
) -> MigrateStats:
    """仅预筛，不调 LLM；用于评估候选质量与通过率。"""
    stats = MigrateStats()
    candidates: List[CandidateRow] = []

    for row in _iter_source_rows(table, limit=limit, offset=offset, min_similarity=min_similarity):
        stats.scanned += 1
        cand, reject = _row_to_candidate(table, row, min_similarity)
        if reject:
            stats.prefilter_reject += 1
            _bump_reason(stats.prefilter_reject_reasons, reject)
            if len(stats.samples_rejected) < 10:
                title = _s(row.get("title") or row.get("article_title_text") or row.get("article_title_text_cn"))
                stats.samples_rejected.append(f"{title[:70]} | {reject}")
            continue
        assert cand is not None
        stats.prefilter_pass += 1
        candidates.append(cand)
        if len(candidates) >= limit:
            break
        if len(stats.samples_relevant) < 10:
            stats.samples_relevant.append(
                f"score={cand.quality_score:.2f} | {cand.title[:75]} | notes={','.join(cand.quality_notes)}"
            )

    candidates.sort(key=lambda x: x.quality_score, reverse=True)
    stats.samples_relevant = [
        *(f"TOP: {c.title[:65]} (score={c.quality_score:.2f})" for c in candidates[:5]),
        *stats.samples_relevant,
    ][:12]
    return stats


async def run_migration(
    table: str,
    *,
    limit: int,
    offset: int,
    min_similarity: float,
    dry_run: bool,
    no_index: bool,
    concurrency: int,
    rag_enabled: bool,
    llm_retries: int = 4,
) -> MigrateStats:
    """预筛 + LLM + 入库。"""
    stats = MigrateStats()
    candidates: List[CandidateRow] = []

    for row in _iter_source_rows(
        table,
        limit=limit,
        offset=offset,
        min_similarity=min_similarity,
        scan_multiplier=12,
    ):
        stats.scanned += 1
        cand, reject = _row_to_candidate(table, row, min_similarity)
        if reject:
            stats.prefilter_reject += 1
            _bump_reason(stats.prefilter_reject_reasons, reject)
            continue
        assert cand is not None
        nu = normalize_url(cand.url)
        if nu and get_article_by_url(nu):
            stats.skipped_url_dup += 1
            continue
        if nu and any(normalize_url(x.url) == nu for x in candidates):
            stats.skipped_url_dup += 1
            continue
        candidates.append(cand)
        if len(candidates) >= limit:
            break

    stats.prefilter_pass = len(candidates)
    mig = await _migrate_candidates(
        candidates,
        dry_run=dry_run,
        no_index=no_index,
        concurrency=concurrency,
        rag_enabled=rag_enabled,
        llm_retries=llm_retries,
    )
    mig.scanned = stats.scanned
    mig.prefilter_reject = stats.prefilter_reject
    mig.prefilter_reject_reasons = stats.prefilter_reject_reasons
    return mig


async def run_all_batches(
    table: str,
    *,
    min_similarity: float,
    dry_run: bool,
    no_index: bool,
    concurrency: int,
    rag_enabled: bool,
    llm_retries: int = 4,
) -> MigrateStats:
    """
    按表体量自动分批跑完全部 similarity 达标的旧库行。
    输入：表名与迁移开关；输出：累计 MigrateStats。
    """
    plan = _BATCH_PLAN.get(table)
    if not plan:
        raise ValueError(f"no batch plan for {table}")

    total_rows = _count_source_rows(table, min_similarity=min_similarity)
    batch_limit = int(plan["batch_limit"])
    scan_step = int(plan["scan_step"])
    scan_mult = int(plan["scan_multiplier"])

    agg = MigrateStats()
    offset = 0
    batch_no = 0
    empty_streak = 0

    print(f"\n>>> 全量分批 · {table} | 源库 sim>={min_similarity} 共 {total_rows:,} 行")
    print(f"    每批候选上限 {batch_limit}，扫描步长 {scan_step}")

    while offset < total_rows:
        batch_no += 1
        stats = MigrateStats()
        candidates: List[CandidateRow] = []

        for row in _iter_source_rows(
            table,
            limit=batch_limit,
            offset=offset,
            min_similarity=min_similarity,
            scan_multiplier=scan_mult,
        ):
            stats.scanned += 1
            cand, reject = _row_to_candidate(table, row, min_similarity)
            if reject:
                stats.prefilter_reject += 1
                _bump_reason(stats.prefilter_reject_reasons, reject)
                continue
            assert cand is not None
            nu = normalize_url(cand.url)
            if nu and get_article_by_url(nu):
                stats.skipped_url_dup += 1
                continue
            if nu and any(normalize_url(x.url) == nu for x in candidates):
                stats.skipped_url_dup += 1
                continue
            candidates.append(cand)
            if len(candidates) >= batch_limit:
                break

        stats.prefilter_pass = len(candidates)
        mig = await _migrate_candidates(
            candidates,
            dry_run=dry_run,
            no_index=no_index,
            concurrency=concurrency,
            rag_enabled=rag_enabled,
            llm_retries=llm_retries,
        )
        mig.scanned = stats.scanned
        mig.prefilter_reject = stats.prefilter_reject
        mig.prefilter_reject_reasons = stats.prefilter_reject_reasons
        mig.skipped_url_dup += stats.skipped_url_dup

        _print_report(f"批次 {batch_no} · {table} @ offset={offset}", mig)

        agg.scanned += mig.scanned
        agg.prefilter_pass += mig.prefilter_pass
        agg.prefilter_reject += mig.prefilter_reject
        agg.skipped_url_dup += mig.skipped_url_dup
        agg.llm_relevant += mig.llm_relevant
        agg.llm_irrelevant += mig.llm_irrelevant
        agg.llm_failed += mig.llm_failed
        agg.saved += mig.saved
        for k, v in mig.prefilter_reject_reasons.items():
            agg.prefilter_reject_reasons[k] = agg.prefilter_reject_reasons.get(k, 0) + v
        for k, v in mig.reject_reasons.items():
            agg.reject_reasons[k] = agg.reject_reasons.get(k, 0) + v

        if mig.scanned == 0:
            break
        if mig.saved == 0 and mig.prefilter_pass == 0:
            empty_streak += 1
        else:
            empty_streak = 0
        if empty_streak >= 3:
            print(f"    连续 {empty_streak} 批无候选/入库，提前结束。")
            break

        offset += scan_step

    _print_report(f"全量累计 · {table}", agg)
    return agg


def _print_report(label: str, stats: MigrateStats) -> None:
    print(f"\n{'=' * 60}", flush=True)
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(f"  扫描行数:        {stats.scanned}")
    print(f"  预筛通过:        {stats.prefilter_pass}")
    print(f"  预筛拒绝:        {stats.prefilter_reject}")
    if stats.skipped_url_dup:
        print(f"  URL 已存在跳过:  {stats.skipped_url_dup}")
    if stats.llm_relevant or stats.llm_irrelevant or stats.llm_failed:
        print(f"  LLM 相关:        {stats.llm_relevant}")
        print(f"  LLM 不相关:      {stats.llm_irrelevant}")
        print(f"  LLM 失败:        {stats.llm_failed}")
        print(f"  入库成功:        {stats.saved}")
    if stats.prefilter_pass and stats.scanned:
        print(f"  预筛通过率:      {100.0 * stats.prefilter_pass / stats.scanned:.1f}%")
    if stats.llm_relevant and stats.prefilter_pass:
        print(f"  LLM 相关率(占预筛通过): {100.0 * stats.llm_relevant / stats.prefilter_pass:.1f}%")

    if stats.prefilter_reject_reasons:
        print("\n  预筛拒绝原因 Top:")
        for k, v in sorted(stats.prefilter_reject_reasons.items(), key=lambda x: -x[1])[:8]:
            print(f"    - {k}: {v}")
    if stats.reject_reasons:
        print("\n  LLM 拒绝原因 Top:")
        for k, v in sorted(stats.reject_reasons.items(), key=lambda x: -x[1])[:8]:
            print(f"    - {k}: {v}")
    if stats.samples_relevant:
        print("\n  相关/高分样本:")
        for s in stats.samples_relevant[:8]:
            print(f"    ✓ {s}")
    if stats.samples_rejected:
        print("\n  拒绝样本:")
        for s in stats.samples_rejected[:6]:
            print(f"    ✗ {s}")


def main() -> int:
    parser = argparse.ArgumentParser(description="papers → ai_governance 高质量迁移")
    parser.add_argument("--table", choices=("policy", "articles", "both"), default="both")
    parser.add_argument("--limit", type=int, default=20, help="每表最多处理条数（预筛通过后）")
    parser.add_argument("--offset", type=int, default=0, help="源表扫描 OFFSET（按 similarity 降序）")
    parser.add_argument("--min-similarity", type=float, default=0.76, help="similarity 下限")
    parser.add_argument("--prefilter-only", action="store_true", help="仅预筛质量报告，不调 LLM")
    parser.add_argument("--dry-run", action="store_true", help="预筛后跳过 LLM 与写库")
    parser.add_argument("--no-index", action="store_true", help="跳过 Chroma 向量索引（试跑更快）")
    parser.add_argument("--no-rag", action="store_true", help="跳过 RAG 精炼")
    parser.add_argument("--concurrency", type=int, default=3, help="LLM 并发上限")
    parser.add_argument("--llm-retries", type=int, default=4, help="LLM 失败重试次数")
    parser.add_argument(
        "--run-all",
        action="store_true",
        help="按旧库体量自动分批跑完（policy/articles 分别使用内置步长）",
    )
    args = parser.parse_args()

    if not API_KEY and not args.prefilter_only and not args.dry_run:
        print("ERROR: DASHSCOPE_API_KEY 未配置，无法 LLM 抽取", file=sys.stderr)
        return 1

    tables = ["policy", "articles"] if args.table == "both" else [args.table]

    for tbl in tables:
        if args.prefilter_only:
            stats = run_prefilter_report(
                tbl,
                limit=args.limit,
                offset=args.offset,
                min_similarity=args.min_similarity,
            )
            _print_report(f"预筛质量报告 · {tbl}", stats)
        elif args.run_all:
            asyncio.run(
                run_all_batches(
                    tbl,
                    min_similarity=args.min_similarity,
                    dry_run=args.dry_run,
                    no_index=args.no_index,
                    concurrency=args.concurrency,
                    rag_enabled=not args.no_rag,
                    llm_retries=args.llm_retries,
                )
            )
        else:
            stats = asyncio.run(
                run_migration(
                    tbl,
                    limit=args.limit,
                    offset=args.offset,
                    min_similarity=args.min_similarity,
                    dry_run=args.dry_run,
                    no_index=args.no_index,
                    concurrency=args.concurrency,
                    rag_enabled=not args.no_rag,
                    llm_retries=args.llm_retries,
                )
            )
            _print_report(f"迁移结果 · {tbl}", stats)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
