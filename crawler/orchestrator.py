"""
信源编排器：拉取文章 → 并发 LLM 抽取 → 可选 RAG 精炼 → 入库（支持多信源）。

功能：
  - async_sync_guardian / sync_guardian：卫报 Content API 信源同步流水线。
  - async_sync_nyt / sync_nyt：NYT Article Search API 信源同步流水线。
  - async_sync_xinhua_tech / sync_xinhua_tech：新华网科技频道同步流水线。
  - async_sync_sina_tech / sync_sina_tech：新浪科技频道同步流水线。
  - async_sync_wechat_rss / sync_wechat_rss：微信公众号 RSS（wechat2rss）同步流水线。
  - async_sync_policy / sync_policy：多国政策/法规源同步流水线（进 articles + LLM 抽取）。
  - sync_literature：arXiv / Scopus / Springer 文献入库（literature_items，不跑 LLM）。
  各信源共用去重、并发 LLM 抽取、RAG 精炼、MySQL/Chroma 写入逻辑；
  _persist_mysql_phase1 接受 source 参数，正确标注数据来源。
  所有 async_sync_* 均支持 dry_run=True，可完整走抽取流程但跳过 MySQL/Chroma 写入，用于测试。
  LLM/预筛未命中写入 unmatched_articles（dry_run 时不写）。
输入：各 sync_* 函数的 query/max_pages 等参数；api_key/base_url 可覆盖 .env；rag_enabled 控制 RAG。
输出：SyncResult（MySQL 入库篇数、跳过数、debug 日志）；副作用：HTTP 拉取 + 并发 LLM + MySQL/Chroma。
上下游：app.py、scripts/sync_sources；下游 core.mysql_db。
扩展点：新增信源只需实现对应 async_sync_* 函数，共用 _persist_mysql_phase1 与 _run_sync_pipeline。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from core.config import API_KEY, BASE_URL, LLM_MODEL
from core.llm_client import OpenAICompatibleBackend
from crawler.extraction import (
    async_extract_raw_article,
    article_dict_to_incident_like,
    merge_article_with_rag,
)
from crawler.sources.guardian import (
    DEFAULT_AI_GOVERNANCE_QUERY,
    GuardianAPIError,
    RawArticle,
    search_articles_multipage,
)
from crawler.sources.nyt import (
    DEFAULT_NYT_AI_GOVERNANCE_QUERY,
    NYTAPIError,
    search_nyt_articles_multipage,
)
from crawler.sources.xinhua_net import (
    XinhuaNetError,
    search_xinhua_tech_articles_multipage,
)
from crawler.sources.sina_tech import (
    SinaTechError,
    search_sina_tech_articles_multipage,
)
from crawler.sources.wechat2rss import fetch_wechat_pool
from crawler.sources.policy import fetch_policy_articles, build_backfill_policy_config, PolicyConfig
from crawler.sources.literature import (
    LiteratureItem,
    fetch_arxiv_literature,
    fetch_scopus_literature,
    fetch_springer_literature,
    parse_literature_published_at,
)
# engine.rag_ingestion 依赖 chromadb（可选安装），延迟到运行时导入，
# 避免仅 import orchestrator 就要求 chromadb 存在。

# 并发 LLM 请求上限：防止向厂商发起过多并发被 429 限流。
from core.config import SYNC_EXTRACT_CONCURRENCY

EXTRACT_CONCURRENCY = SYNC_EXTRACT_CONCURRENCY


def _persist_mysql_phase1(
    art: RawArticle,
    merged_extraction: Dict[str, Any],
    result: SyncResult,
    llm_backend: Optional[OpenAICompatibleBackend] = None,
    *,
    source: str = "guardian",
    force_reindex: bool = False,
) -> None:
    """
    功能：将文章与抽取结果写入 MySQL + Chroma（best-effort，成功时递增 result.saved）。
    输入：art 原始文章、merged_extraction LLM 抽取结果、source 信源标识（如 guardian/nyt）。
    输出：无返回值；副作用：MySQL articles + article_extractions 写入 + Chroma 向量索引。
    上下游：被各 async_sync_* 调用；下游 core.mysql_db、engine.article_index。
    """
    if not merged_extraction:
        return
    try:
        from core.mysql_db import save_article, save_extraction
    except Exception as e:
        result.debug_log.append(f"⚠️ MySQL Phase1 未启用: {type(e).__name__}: {e}")
        return

    summary = art.trail_text or (art.body_text or "")[:512]
    content = art.body_text or art.trail_text or ""

    published_at = _parse_raw_article_published_at(art)

    if not (art.title or "").strip():
        result.debug_log.append("⏭ 缺少标题，跳过入库")
        result.failed += 1
        return

    if published_at is None:
        result.debug_log.append(f"⏭ 缺少有效发布时间，跳过入库: {(art.title or '')[:60]}")
        result.skipped_no_incident += 1
        return

    try:
        article_id, is_new = save_article(
            url=art.web_url,
            title=art.title,
            summary=summary,
            content=content,
            published_at=published_at,
            source=source,
        )
    except Exception as e:
        result.debug_log.append(f"⚠️ MySQL article 写入失败: {type(e).__name__}: {e}")
        return

    # 写入全文向量索引（best-effort，不中断主流程）
    if is_new or force_reindex:
        try:
            from engine.article_index.indexer import index_article
            # 取首条 incident 的 risk_domain 作为 chunk metadata（多条时取第一条）
            first_domain = str(merged_extraction.get("risk_domain", "")).strip()
            pub_at_str = published_at.strftime("%Y-%m-%d") if published_at else ""
            n_chunks = index_article(
                article_id=article_id,
                title=art.title,
                summary=summary,
                content=content,
                source=source,
                risk_domain=first_domain,
                published_at=pub_at_str,
                url=art.web_url,
                backend=llm_backend,
                extraction_ctx=merged_extraction,
            )
            result.debug_log.append(f"🔢 向量索引写入 {n_chunks} chunks (article_id={article_id})")
        except Exception as e:
            result.debug_log.append(f"⚠️ 向量索引写入失败: {type(e).__name__}: {e}")

    try:
        extraction_id = save_extraction(
            article_id=article_id,
            extraction_dict=merged_extraction,
            model_name=(LLM_MODEL or "").strip(),
        )
        result.saved += 1
        if is_new:
            result.debug_log.append(f"💾 MySQL 写入 article_id={article_id}, extraction_id={extraction_id}")
        elif force_reindex:
            result.debug_log.append(
                f"💾 MySQL 重索引后更新 extraction article_id={article_id}, extraction_id={extraction_id}"
            )
        if str(merged_extraction.get("content_type") or "") == "meeting":
            try:
                from services.meeting_event_linker import link_article_to_meeting_event

                ev_id = link_article_to_meeting_event(
                    article_id,
                    title=art.title or "",
                    summary=summary,
                    published_at=published_at,
                    extraction=merged_extraction,
                )
                if ev_id:
                    result.debug_log.append(f"🔗 已关联会议事件 event_id={ev_id}")
            except Exception as link_err:
                result.debug_log.append(f"⚠️ 会议事件关联失败: {type(link_err).__name__}: {link_err}")
    except Exception as e:
        result.debug_log.append(f"⚠️ MySQL extraction 写入失败: {type(e).__name__}: {e}")


@dataclass
class SyncResult:
    """
    sync_guardian 的运行结果摘要。

    功能：便于 Streamlit UI 和脚本统一展示；saved = 成功 upsert MySQL article_extractions 的篇数。
    """

    saved: int = 0
    skipped_url_dup: int = 0
    skipped_no_incident: int = 0
    failed: int = 0
    new_keywords: List[str] = field(default_factory=list)
    new_subdomains: List[str] = field(default_factory=list)
    debug_log: List[str] = field(default_factory=list)


def _url_already_in_mysql(web_url: str) -> bool:
    """
    按规范化 URL 查 MySQL articles 是否已存在（与 save_article 去重一致）。
    MySQL 不可用时返回 False（避免整批被跳过），错误仅能被后续写入阶段发现。
    """
    if not web_url:
        return False
    try:
        from core.mysql_db import get_article_by_url, normalize_url

        nu = normalize_url(web_url)
        if not nu:
            return False
        return get_article_by_url(nu) is not None
    except Exception:
        return False


def _parse_raw_article_published_at(art: RawArticle) -> Optional[datetime]:
    """
    功能：解析 RawArticle.web_publication_date 为 naive datetime（与 save_article 一致）。
    输入：RawArticle；无法解析时返回 None。
    输出：datetime | None。
    """
    if not art.web_publication_date:
        return None
    raw_date = art.web_publication_date.strip()
    published_at: Optional[datetime] = None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            slice_len = 19 if "H" in fmt else 10
            published_at = datetime.strptime(raw_date[:slice_len], fmt)
            break
        except ValueError:
            continue
    if published_at is None:
        try:
            from datetime import timezone

            normalized = raw_date.replace("Z", "+00:00")
            dt_aware = datetime.fromisoformat(normalized)
            published_at = dt_aware.replace(tzinfo=None)
        except (ValueError, AttributeError):
            pass
    return published_at


def _persist_unmatched(
    art: RawArticle,
    *,
    source: str,
    reject_stage: str,
    reject_reason: str,
    result: SyncResult,
) -> None:
    """
    功能：将未命中条目写入 unmatched_articles（best-effort，失败只记日志）。
    输入：RawArticle、信源标签、reject_stage/reject_reason。
    输出：无；副作用：MySQL unmatched_articles upsert。
    """
    if not (art.web_url or "").strip():
        return
    try:
        from core.mysql_db import build_unmatched_content_preview, save_unmatched_article
    except Exception as e:
        result.debug_log.append(f"⚠️ unmatched 审计未启用: {type(e).__name__}: {e}")
        return

    summary = art.trail_text or ""
    content = art.body_text or ""
    preview = build_unmatched_content_preview(summary, content)
    try:
        save_unmatched_article(
            url=art.web_url,
            source=source,
            title=art.title or "",
            summary=summary,
            content_preview=preview,
            published_at=_parse_raw_article_published_at(art),
            section_name=str(art.section_name or ""),
            reject_stage=reject_stage,
            reject_reason=reject_reason,
        )
    except Exception as e:
        result.debug_log.append(f"⚠️ unmatched 写入失败: {type(e).__name__}: {e}")


def _handle_llm_reject(
    art: RawArticle,
    article_dict: Optional[Dict[str, Any]],
    *,
    source: str,
    dry_run: bool,
    result: SyncResult,
) -> None:
    """
    功能：LLM 阶段未命中（is_relevant=false 或解析失败）— 计数、dry_run 日志、写 unmatched。
    输入：抽取结果 article_dict；dry_run 时不写库。
    """
    result.skipped_no_incident += 1
    if dry_run:
        if article_dict:
            result.debug_log.append(
                f"  ⛔ 无关: {art.title[:50]} | reason={article_dict.get('reject_reason', '')}"
            )
        elif art.title:
            result.debug_log.append(
                f"  ⛔ LLM 未产出: {art.title[:50]} | reason=llm_parse_failed"
            )
        return
    reason = (
        str(article_dict.get("reject_reason") or "no_ai_governance_content")[:255]
        if article_dict
        else "llm_parse_failed"
    )
    _persist_unmatched(
        art,
        source=source,
        reject_stage="llm",
        reject_reason=reason,
        result=result,
    )


def _handle_prefilter_reject(
    art: RawArticle,
    *,
    source: str,
    dry_run: bool,
    result: SyncResult,
    log: List[str],
) -> None:
    """
    功能：政策源关键词预筛未过（未调 LLM）— 计数、日志、写 unmatched（reject_stage=fetch）。
    """
    result.skipped_no_incident += 1
    log.append(f"⏭ 预筛无关: {art.title[:55]}")
    if dry_run:
        return
    _persist_unmatched(
        art,
        source=source,
        reject_stage="fetch",
        reject_reason="prefilter_keyword",
        result=result,
    )


def _build_llm_backend(api_key: Optional[str], base_url: Optional[str]) -> OpenAICompatibleBackend:
    k = (api_key or "").strip() or API_KEY
    b = (base_url or "").strip() or BASE_URL
    return OpenAICompatibleBackend(api_key=k, base_url=b)


async def async_sync_guardian(
    *,
    query: Optional[str] = None,
    max_pages: int = 2,
    page_size: int = 10,
    section: Optional[str] = None,
    show_fields: str = "trailText,bodyText",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    guardian_api_key: Optional[str] = None,
    rag_enabled: Optional[bool] = None,
    concurrency: int = EXTRACT_CONCURRENCY,
    force_reindex: bool = False,
) -> SyncResult:
    """
    功能：同 sync_guardian，但 LLM 抽取步骤并发执行（asyncio.gather + Semaphore）。
    输入：concurrency 控制最大并发 LLM 请求数（默认 5）；其余参数同 sync_guardian。
    输出：SyncResult；副作用：Guardian HTTP（同步，在线程池）+ 并发 LLM + MySQL 顺序写入。
    上下游：sync_guardian 通过 asyncio.run 调用；也可在已有事件循环中 await。
    """
    result = SyncResult()
    log = result.debug_log
    llm_backend = _build_llm_backend(api_key, base_url)

    # 1. 从卫报 API 拉文章列表（同步 httpx，放到线程池执行，不阻塞事件循环）
    try:
        log.append(f"📡 拉取 Guardian（query={query or DEFAULT_AI_GOVERNANCE_QUERY[:40]}... pages≤{max_pages}）")
        articles: List[RawArticle] = await asyncio.to_thread(
            search_articles_multipage,
            query=query,
            max_pages=max_pages,
            page_size=page_size,
            section=section,
            show_fields=show_fields,
            api_key=guardian_api_key,
        )
        log.append(f"✓ 拉取到 {len(articles)} 条（含可能重复）")
    except GuardianAPIError as e:
        log.append(f"❌ Guardian API 失败: {e}")
        result.failed += 1
        return result
    except Exception as e:
        log.append(f"❌ 拉取异常: {type(e).__name__}: {e}")
        result.failed += 1
        return result

    # 2. URL 去重（本次批次内也去重）
    seen_urls: set[str] = set()
    deduped: List[RawArticle] = []
    for art in articles:
        if not art.web_url:
            continue
        if art.web_url in seen_urls:
            continue
        seen_urls.add(art.web_url)
        if _url_already_in_mysql(art.web_url):
            result.skipped_url_dup += 1
            log.append(f"⏭ 已存在，跳过: {art.title[:60]}")
            continue
        deduped.append(art)
    log.append(f"✓ 去重后待处理 {len(deduped)} 篇")

    if not deduped:
        log.append("💡 本次无新文章需要处理")
        return result

    # 3. 并发 LLM 抽取（Semaphore 限制最大并发数，防止 429）
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _extract_one(art: RawArticle) -> Tuple[RawArticle, Optional[Dict[str, Any]], List[str]]:
        async with sem:
            return await async_extract_raw_article(art, "guardian", backend=llm_backend)

    log.append(f"🚀 并发抽取 {len(deduped)} 篇（并发上限 {concurrency}）...")
    extract_results = await asyncio.gather(*[_extract_one(a) for a in deduped], return_exceptions=False)

    # 4. 顺序处理抽取结果（RAG + MySQL/Chroma 顺序写入）
    for art, article_dict, ext_log in extract_results:
        log.extend(ext_log)

        if not article_dict or not article_dict.get("is_relevant"):
            _handle_llm_reject(art, article_dict, source="guardian", dry_run=False, result=result)
            continue

        incident_like = article_dict_to_incident_like(article_dict)
        # RAG 精炼（可选）；延迟导入以支持未安装 chromadb 的轻量环境。
        try:
            from engine.rag_ingestion import apply_rag_to_incidents as _rag
            incidents_rag, rag_log = _rag(
                [incident_like],
                llm_backend=llm_backend,
                enabled=rag_enabled,
            )
        except ImportError:
            incidents_rag = [incident_like]
            rag_log = ["⚠️ chromadb 未安装，RAG 步骤跳过"]
        log.extend(rag_log)

        inc_rag = incidents_rag[0] if incidents_rag else incident_like
        merged = merge_article_with_rag(article_dict, inc_rag)
        _persist_mysql_phase1(
            art, merged, result, llm_backend=llm_backend,
            source="guardian", force_reindex=force_reindex,
        )

    log.append(
        f"✅ 完成 | 入库 {result.saved} 条，跳过已有 {result.skipped_url_dup}，"
        f"无关 {result.skipped_no_incident}，失败 {result.failed}"
    )
    return result


def sync_guardian(
    *,
    query: Optional[str] = None,
    max_pages: int = 2,
    page_size: int = 10,
    section: Optional[str] = None,
    show_fields: str = "trailText,bodyText",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    guardian_api_key: Optional[str] = None,
    rag_enabled: Optional[bool] = None,
    concurrency: int = EXTRACT_CONCURRENCY,
    force_reindex: bool = False,
) -> SyncResult:
    """
    功能：sync_guardian 是 async_sync_guardian 的同步包装，供 CLI 脚本与 Streamlit 直接调用。
    输入：同 async_sync_guardian。
    输出：SyncResult；副作用同 async_sync_guardian。
    上下游：scripts/sync_sources.py、app.py；不应在已有事件循环中调用（用 await async_sync_guardian 代替）。
    """
    return asyncio.run(
        async_sync_guardian(
            query=query,
            max_pages=max_pages,
            page_size=page_size,
            section=section,
            show_fields=show_fields,
            api_key=api_key,
            base_url=base_url,
            guardian_api_key=guardian_api_key,
            rag_enabled=rag_enabled,
            concurrency=concurrency,
            force_reindex=force_reindex,
        )
    )


# ---------------------------------------------------------------------------
# NYT 信源同步流水线
# ---------------------------------------------------------------------------

async def async_sync_nyt(
    *,
    query: Optional[str] = None,
    max_pages: int = 2,
    begin_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort: str = "newest",
    nyt_api_key: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    rag_enabled: Optional[bool] = None,
    concurrency: int = EXTRACT_CONCURRENCY,
    force_reindex: bool = False,
) -> SyncResult:
    """
    功能：NYT Article Search API 同步流水线（并发 LLM 抽取）。
         拉取 → URL 去重（MySQL）→ 并发 LLM 抽取 → 写 MySQL + Chroma。
    输入：query 默认 DEFAULT_NYT_AI_GOVERNANCE_QUERY；max_pages NYT page 从 0 开始每页 10 条；
         begin_date/end_date 可选按日期过滤（格式 YYYYMMDD）；nyt_api_key 覆盖 .env NYT_API_KEY。
    输出：SyncResult；副作用：NYT HTTP + 并发 LLM + MySQL/Chroma 写入，source 标记为 "nyt"。
    上下游：sync_nyt（同步包装）、ui_jobs、scripts/sync_sources.py。
    """
    result = SyncResult()
    log = result.debug_log
    llm_backend = _build_llm_backend(api_key, base_url)

    # 1. 从 NYT API 拉文章列表（同步 httpx，放到线程池执行）
    try:
        log.append(
            f"📡 拉取 NYT（query={query or DEFAULT_NYT_AI_GOVERNANCE_QUERY[:40]}... pages≤{max_pages}）"
        )
        articles: List[RawArticle] = await asyncio.to_thread(
            search_nyt_articles_multipage,
            query=query,
            max_pages=max_pages,
            begin_date=begin_date,
            end_date=end_date,
            sort=sort,
            api_key=nyt_api_key,
        )
        log.append(f"✓ 拉取到 {len(articles)} 条（含可能重复）")
    except NYTAPIError as e:
        log.append(f"❌ NYT API 失败: {e}")
        result.failed += 1
        return result
    except Exception as e:
        log.append(f"❌ 拉取异常: {type(e).__name__}: {e}")
        result.failed += 1
        return result

    # 2. URL 去重
    seen_urls: set[str] = set()
    deduped: List[RawArticle] = []
    for art in articles:
        if not art.web_url:
            continue
        if art.web_url in seen_urls:
            continue
        seen_urls.add(art.web_url)
        if _url_already_in_mysql(art.web_url):
            result.skipped_url_dup += 1
            log.append(f"⏭ 已存在，跳过: {art.title[:60]}")
            continue
        deduped.append(art)
    log.append(f"✓ 去重后待处理 {len(deduped)} 篇")

    if not deduped:
        log.append("💡 本次无新文章需要处理")
        return result

    # 3. 并发 LLM 抽取
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _extract_one(art: RawArticle) -> Tuple[RawArticle, Optional[Dict[str, Any]], List[str]]:
        async with sem:
            return await async_extract_raw_article(art, "nyt", backend=llm_backend)

    log.append(f"🚀 并发抽取 {len(deduped)} 篇（并发上限 {concurrency}）...")
    extract_results = await asyncio.gather(*[_extract_one(a) for a in deduped], return_exceptions=False)

    # 4. 顺序处理抽取结果（RAG + 入库）
    for art, article_dict, ext_log in extract_results:
        log.extend(ext_log)

        if not article_dict or not article_dict.get("is_relevant"):
            _handle_llm_reject(art, article_dict, source="nyt", dry_run=False, result=result)
            continue

        incident_like = article_dict_to_incident_like(article_dict)
        try:
            from engine.rag_ingestion import apply_rag_to_incidents as _rag
            incidents_rag, rag_log = _rag(
                [incident_like],
                llm_backend=llm_backend,
                enabled=rag_enabled,
            )
        except ImportError:
            incidents_rag = [incident_like]
            rag_log = ["⚠️ chromadb 未安装，RAG 步骤跳过"]
        log.extend(rag_log)

        inc_rag = incidents_rag[0] if incidents_rag else incident_like
        merged = merge_article_with_rag(article_dict, inc_rag)
        _persist_mysql_phase1(
            art, merged, result, llm_backend=llm_backend,
            source="nyt", force_reindex=force_reindex,
        )

    log.append(
        f"✅ 完成 | 入库 {result.saved} 条，跳过已有 {result.skipped_url_dup}，"
        f"无关 {result.skipped_no_incident}，失败 {result.failed}"
    )
    return result


def sync_nyt(
    *,
    query: Optional[str] = None,
    max_pages: int = 2,
    begin_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort: str = "newest",
    nyt_api_key: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    rag_enabled: Optional[bool] = None,
    concurrency: int = EXTRACT_CONCURRENCY,
    force_reindex: bool = False,
) -> SyncResult:
    """
    功能：sync_nyt 是 async_sync_nyt 的同步包装，供 CLI 脚本与 Streamlit 直接调用。
    输入：同 async_sync_nyt。
    输出：SyncResult；副作用同 async_sync_nyt。
    上下游：scripts/sync_sources.py、app.py；不应在已有事件循环中调用（用 await async_sync_nyt 代替）。
    """
    return asyncio.run(
        async_sync_nyt(
            query=query,
            max_pages=max_pages,
            begin_date=begin_date,
            end_date=end_date,
            sort=sort,
            nyt_api_key=nyt_api_key,
            api_key=api_key,
            base_url=base_url,
            rag_enabled=rag_enabled,
            concurrency=concurrency,
            force_reindex=force_reindex,
        )
    )


# ---------------------------------------------------------------------------
# 新华网科技频道同步流水线
# ---------------------------------------------------------------------------

async def async_sync_xinhua_tech(
    *,
    max_articles: int = 10,
    page_urls: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    rag_enabled: Optional[bool] = None,
    concurrency: int = EXTRACT_CONCURRENCY,
    force_reindex: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """
    功能：新华网科技频道同步流水线（并发 LLM 抽取）。
         列表页抓取 → URL 去重（MySQL）→ 并发 LLM 抽取 → 写 MySQL + Chroma。
    输入：max_articles 每次抓取文章数上限；page_urls 覆盖默认频道 URL（可传多个分页）；
         dry_run=True 时完整走抽取流程但跳过 MySQL/Chroma 写入，用于测试与质量评估。
    输出：SyncResult；副作用（dry_run=False 时）：HTTP + 并发 LLM + MySQL/Chroma 写入，source="xinhua_tech"。
    上下游：sync_xinhua_tech（同步包装）、ui_jobs、scripts/sync_sources.py。
    """
    result = SyncResult()
    log = result.debug_log
    llm_backend = _build_llm_backend(api_key, base_url)

    dry_tag = " [DRY-RUN，不入库]" if dry_run else ""

    # 1. 抓取新华网文章列表（同步 httpx，放到线程池执行）
    try:
        log.append(f"📡 抓取新华网科技频道（max_articles={max_articles}）{dry_tag}")
        articles: List[RawArticle] = await asyncio.to_thread(
            search_xinhua_tech_articles_multipage,
            page_urls=page_urls,
            max_articles_per_page=max_articles,
        )
        log.append(f"✓ 抓取到 {len(articles)} 条（含可能重复）")
    except XinhuaNetError as e:
        log.append(f"❌ 新华网抓取失败: {e}")
        result.failed += 1
        return result
    except Exception as e:
        log.append(f"❌ 抓取异常: {type(e).__name__}: {e}")
        result.failed += 1
        return result

    # 2. URL 去重（dry_run 时跳过 MySQL 查询，全量处理）
    seen_urls: set[str] = set()
    deduped: List[RawArticle] = []
    for art in articles:
        if not art.web_url:
            continue
        if art.web_url in seen_urls:
            continue
        seen_urls.add(art.web_url)
        if not dry_run and _url_already_in_mysql(art.web_url):
            result.skipped_url_dup += 1
            log.append(f"⏭ 已存在，跳过: {art.title[:60]}")
            continue
        deduped.append(art)
    log.append(f"✓ 去重后待处理 {len(deduped)} 篇")

    if not deduped:
        log.append("💡 本次无新文章需要处理")
        return result

    # 3. 并发 LLM 抽取
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _extract_one(art: RawArticle) -> Tuple[RawArticle, Optional[Dict[str, Any]], List[str]]:
        async with sem:
            return await async_extract_raw_article(art, "xinhua_tech", backend=llm_backend)

    log.append(f"🚀 并发抽取 {len(deduped)} 篇（并发上限 {concurrency}）{dry_tag}...")
    extract_results = await asyncio.gather(*[_extract_one(a) for a in deduped], return_exceptions=False)

    # 4. 顺序处理结果（dry_run 时只打印，不入库）
    for art, article_dict, ext_log in extract_results:
        log.extend(ext_log)

        if not article_dict or not article_dict.get("is_relevant"):
            _handle_llm_reject(art, article_dict, source="xinhua_tech", dry_run=dry_run, result=result)
            continue

        if dry_run:
            # dry_run 模式：打印抽取摘要，不入库
            log.append(f"  ✅ [DRY] {art.title[:50]}")
            log.append(f"     content_type  : {article_dict.get('content_type')}")
            log.append(f"     risk_domain   : {article_dict.get('risk_domain','')[:60]}")
            log.append(f"     risk_subdomains: {article_dict.get('risk_subdomains')}")
            log.append(f"     entities      : {article_dict.get('entities')}")
            log.append(f"     main_topic    : {str(article_dict.get('main_topic',''))[:80]}")
            log.append(f"     summary       : {str(article_dict.get('summary_structured',''))[:100]}")
            log.append(f"     tags          : {article_dict.get('tags')}")
            result.saved += 1  # dry_run 时 saved 表示"相关且抽取成功"篇数
            continue

        incident_like = article_dict_to_incident_like(article_dict)
        try:
            from engine.rag_ingestion import apply_rag_to_incidents as _rag
            incidents_rag, rag_log = _rag(
                [incident_like],
                llm_backend=llm_backend,
                enabled=rag_enabled,
            )
        except ImportError:
            incidents_rag = [incident_like]
            rag_log = ["⚠️ chromadb 未安装，RAG 步骤跳过"]
        log.extend(rag_log)

        inc_rag = incidents_rag[0] if incidents_rag else incident_like
        merged = merge_article_with_rag(article_dict, inc_rag)
        _persist_mysql_phase1(
            art, merged, result, llm_backend=llm_backend,
            source="xinhua_tech", force_reindex=force_reindex,
        )

    if dry_run:
        log.append(
            f"✅ DRY-RUN 完成 | 相关抽取 {result.saved} 条，"
            f"无关 {result.skipped_no_incident}，失败 {result.failed}（均未入库）"
        )
    else:
        log.append(
            f"✅ 完成 | 入库 {result.saved} 条，跳过已有 {result.skipped_url_dup}，"
            f"无关 {result.skipped_no_incident}，失败 {result.failed}"
        )
    return result


def sync_xinhua_tech(
    *,
    max_articles: int = 10,
    page_urls: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    rag_enabled: Optional[bool] = None,
    concurrency: int = EXTRACT_CONCURRENCY,
    force_reindex: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """
    功能：sync_xinhua_tech 是 async_sync_xinhua_tech 的同步包装，供 CLI 脚本与 Streamlit 调用。
    输入：同 async_sync_xinhua_tech；dry_run=True 时不写库，用于质量评估。
    输出：SyncResult；副作用同 async_sync_xinhua_tech。
    上下游：scripts/sync_sources.py、app.py；不应在已有事件循环中调用。
    """
    return asyncio.run(
        async_sync_xinhua_tech(
            max_articles=max_articles,
            page_urls=page_urls,
            api_key=api_key,
            base_url=base_url,
            rag_enabled=rag_enabled,
            concurrency=concurrency,
            force_reindex=force_reindex,
            dry_run=dry_run,
        )
    )


# ---------------------------------------------------------------------------
# 新浪科技同步流水线
# ---------------------------------------------------------------------------

async def async_sync_sina_tech(
    *,
    max_articles: int = 10,
    page_urls: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    rag_enabled: Optional[bool] = None,
    concurrency: int = EXTRACT_CONCURRENCY,
    force_reindex: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """
    功能：新浪科技同步流水线（并发 LLM 抽取）。
         列表页抓取 → URL 去重（MySQL）→ 并发 LLM 抽取 → 写 MySQL + Chroma。
    输入：max_articles 每次抓取文章数上限；page_urls 覆盖默认频道 URL；
         dry_run=True 时完整走抽取流程但跳过 MySQL/Chroma 写入，用于测试与质量评估。
    输出：SyncResult；副作用（dry_run=False 时）：HTTP + 并发 LLM + MySQL/Chroma 写入，source="sina_tech"。
    上下游：sync_sina_tech（同步包装）、ui_jobs、scripts/sync_sources.py。
    """
    result = SyncResult()
    log = result.debug_log
    llm_backend = _build_llm_backend(api_key, base_url)

    dry_tag = " [DRY-RUN，不入库]" if dry_run else ""

    # 1. 抓取新浪科技文章列表（同步 httpx，放到线程池执行）
    try:
        log.append(f"📡 抓取新浪科技频道（max_articles={max_articles}）{dry_tag}")
        articles: List[RawArticle] = await asyncio.to_thread(
            search_sina_tech_articles_multipage,
            page_urls=page_urls,
            max_articles_per_page=max_articles,
        )
        log.append(f"✓ 抓取到 {len(articles)} 条（含可能重复）")
    except SinaTechError as e:
        log.append(f"❌ 新浪科技抓取失败: {e}")
        result.failed += 1
        return result
    except Exception as e:
        log.append(f"❌ 抓取异常: {type(e).__name__}: {e}")
        result.failed += 1
        return result

    # 2. URL 去重（dry_run 时跳过 MySQL 查询，全量处理）
    seen_urls: set[str] = set()
    deduped: List[RawArticle] = []
    for art in articles:
        if not art.web_url:
            continue
        if art.web_url in seen_urls:
            continue
        seen_urls.add(art.web_url)
        if not dry_run and _url_already_in_mysql(art.web_url):
            result.skipped_url_dup += 1
            log.append(f"⏭ 已存在，跳过: {art.title[:60]}")
            continue
        deduped.append(art)
    log.append(f"✓ 去重后待处理 {len(deduped)} 篇")

    if not deduped:
        log.append("💡 本次无新文章需要处理")
        return result

    # 3. 并发 LLM 抽取
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _extract_one(art: RawArticle) -> Tuple[RawArticle, Optional[Dict[str, Any]], List[str]]:
        async with sem:
            return await async_extract_raw_article(art, "sina_tech", backend=llm_backend)

    log.append(f"🚀 并发抽取 {len(deduped)} 篇（并发上限 {concurrency}）{dry_tag}...")
    extract_results = await asyncio.gather(*[_extract_one(a) for a in deduped], return_exceptions=False)

    # 4. 顺序处理结果（dry_run 时只打印，不入库）
    for art, article_dict, ext_log in extract_results:
        log.extend(ext_log)

        if not article_dict or not article_dict.get("is_relevant"):
            _handle_llm_reject(art, article_dict, source="sina_tech", dry_run=dry_run, result=result)
            continue

        if dry_run:
            log.append(f"  ✅ [DRY] {art.title[:50]}")
            log.append(f"     content_type  : {article_dict.get('content_type')}")
            log.append(f"     risk_domain   : {article_dict.get('risk_domain','')[:60]}")
            log.append(f"     risk_subdomains: {article_dict.get('risk_subdomains')}")
            log.append(f"     entities      : {article_dict.get('entities')}")
            log.append(f"     main_topic    : {str(article_dict.get('main_topic',''))[:80]}")
            log.append(f"     summary       : {str(article_dict.get('summary_structured',''))[:100]}")
            log.append(f"     tags          : {article_dict.get('tags')}")
            result.saved += 1
            continue

        incident_like = article_dict_to_incident_like(article_dict)
        try:
            from engine.rag_ingestion import apply_rag_to_incidents as _rag
            incidents_rag, rag_log = _rag(
                [incident_like],
                llm_backend=llm_backend,
                enabled=rag_enabled,
            )
        except ImportError:
            incidents_rag = [incident_like]
            rag_log = ["⚠️ chromadb 未安装，RAG 步骤跳过"]
        log.extend(rag_log)

        inc_rag = incidents_rag[0] if incidents_rag else incident_like
        merged = merge_article_with_rag(article_dict, inc_rag)
        _persist_mysql_phase1(
            art, merged, result, llm_backend=llm_backend,
            source="sina_tech", force_reindex=force_reindex,
        )

    if dry_run:
        log.append(
            f"✅ DRY-RUN 完成 | 相关抽取 {result.saved} 条，"
            f"无关 {result.skipped_no_incident}，失败 {result.failed}（均未入库）"
        )
    else:
        log.append(
            f"✅ 完成 | 入库 {result.saved} 条，跳过已有 {result.skipped_url_dup}，"
            f"无关 {result.skipped_no_incident}，失败 {result.failed}"
        )
    return result


def sync_sina_tech(
    *,
    max_articles: int = 10,
    page_urls: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    rag_enabled: Optional[bool] = None,
    concurrency: int = EXTRACT_CONCURRENCY,
    force_reindex: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """
    功能：sync_sina_tech 是 async_sync_sina_tech 的同步包装，供 CLI 脚本与 Streamlit 调用。
    输入：同 async_sync_sina_tech；dry_run=True 时不写库，用于质量评估。
    输出：SyncResult；副作用同 async_sync_sina_tech。
    上下游：scripts/sync_sources.py、app.py；不应在已有事件循环中调用。
    """
    return asyncio.run(
        async_sync_sina_tech(
            max_articles=max_articles,
            page_urls=page_urls,
            api_key=api_key,
            base_url=base_url,
            rag_enabled=rag_enabled,
            concurrency=concurrency,
            force_reindex=force_reindex,
            dry_run=dry_run,
        )
    )


# ---------------------------------------------------------------------------
# 微信公众号 RSS 同步流水线
# ---------------------------------------------------------------------------

async def async_sync_wechat_rss(
    *,
    feed_names: Optional[List[str]] = None,
    max_articles_per_feed: int = 20,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    rag_enabled: Optional[bool] = None,
    concurrency: int = EXTRACT_CONCURRENCY,
    force_reindex: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """
    功能：微信公众号 RSS 同步流水线（并发 LLM 抽取）。
         RSS 拉取 → URL 去重（MySQL）→ 并发 LLM 抽取 → 写 MySQL + Chroma。
    输入：feed_names 可选只同步指定公众号（默认全池 WECHAT_RSS_POOL）；
         max_articles_per_feed 每个公众号最多取文章数；
         dry_run=True 时完整走抽取但跳过写入，用于测试。
    输出：SyncResult；副作用（dry_run=False 时）：RSS HTTP + 并发 LLM + MySQL/Chroma。
         source 标记为 "wechat_rss:<公众号名>"，便于后续按公众号过滤。
    上下游：sync_wechat_rss（同步包装）、ui_jobs、scripts/sync_sources.py。
    """
    result = SyncResult()
    log = result.debug_log
    llm_backend = _build_llm_backend(api_key, base_url)

    dry_tag = " [DRY-RUN，不入库]" if dry_run else ""
    feeds_desc = "、".join(feed_names) if feed_names else "全池"
    log.append(f"📡 拉取微信 RSS（公众号：{feeds_desc}，每源最多 {max_articles_per_feed} 篇）{dry_tag}")

    # 1. 拉取所有 RSS（同步，放到线程池）
    try:
        articles: List[RawArticle] = await asyncio.to_thread(
            fetch_wechat_pool,
            None,
            max_articles_per_feed=max_articles_per_feed,
            feed_names=feed_names,
        )
        log.append(f"✓ 拉取到 {len(articles)} 条（含可能重复）")
    except Exception as e:
        log.append(f"❌ RSS 拉取异常: {type(e).__name__}: {e}")
        result.failed += 1
        return result

    # 2. URL 去重（dry_run 跳过 MySQL 查询，全量处理）
    seen_urls: set[str] = set()
    deduped: List[RawArticle] = []
    for art in articles:
        if not art.web_url:
            continue
        if art.web_url in seen_urls:
            continue
        seen_urls.add(art.web_url)
        if not dry_run and _url_already_in_mysql(art.web_url):
            result.skipped_url_dup += 1
            log.append(f"⏭ 已存在，跳过: {art.title[:55]}")
            continue
        deduped.append(art)
    log.append(f"✓ 去重后待处理 {len(deduped)} 篇")

    if not deduped:
        log.append("💡 本次无新文章需要处理")
        return result

    # 3. 并发 LLM 抽取
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _extract_one(art: RawArticle) -> Tuple[RawArticle, Optional[Dict[str, Any]], List[str]]:
        async with sem:
            feed_name = (art.section_name or "").replace("WeChat / ", "").strip()
            source_tag = f"wechat_rss:{feed_name}" if feed_name else "wechat_rss"
            return await async_extract_raw_article(art, source_tag, backend=llm_backend)

    log.append(f"🚀 并发抽取 {len(deduped)} 篇（并发上限 {concurrency}）{dry_tag}...")
    extract_results = await asyncio.gather(*[_extract_one(a) for a in deduped], return_exceptions=False)

    # 4. 顺序处理结果（dry_run 时只打印，不入库）
    for art, article_dict, ext_log in extract_results:
        log.extend(ext_log)

        feed_name = (art.section_name or "").replace("WeChat / ", "").strip()
        wechat_source = f"wechat_rss:{feed_name}" if feed_name else "wechat_rss"

        if not article_dict or not article_dict.get("is_relevant"):
            _handle_llm_reject(art, article_dict, source=wechat_source, dry_run=dry_run, result=result)
            continue

        if dry_run:
            log.append(f"  ✅ [DRY] {art.title[:50]}")
            log.append(f"     section       : {art.section_name}")
            log.append(f"     date          : {art.web_publication_date}")
            log.append(f"     content_type  : {article_dict.get('content_type')}")
            log.append(f"     risk_domain   : {article_dict.get('risk_domain','')[:60]}")
            log.append(f"     risk_subdomains: {article_dict.get('risk_subdomains')}")
            log.append(f"     entities      : {article_dict.get('entities')}")
            log.append(f"     main_topic    : {str(article_dict.get('main_topic',''))[:80]}")
            log.append(f"     summary       : {str(article_dict.get('summary_structured',''))[:100]}")
            log.append(f"     tags          : {article_dict.get('tags')}")
            result.saved += 1
            continue

        # source 按公众号细分，便于后续按源过滤
        source_tag = wechat_source

        incident_like = article_dict_to_incident_like(article_dict)
        try:
            from engine.rag_ingestion import apply_rag_to_incidents as _rag
            incidents_rag, rag_log = _rag(
                [incident_like],
                llm_backend=llm_backend,
                enabled=rag_enabled,
            )
        except ImportError:
            incidents_rag = [incident_like]
            rag_log = ["⚠️ chromadb 未安装，RAG 步骤跳过"]
        log.extend(rag_log)

        inc_rag = incidents_rag[0] if incidents_rag else incident_like
        merged = merge_article_with_rag(article_dict, inc_rag)
        _persist_mysql_phase1(
            art, merged, result, llm_backend=llm_backend,
            source=source_tag, force_reindex=force_reindex,
        )

    if dry_run:
        log.append(
            f"✅ DRY-RUN 完成 | 相关抽取 {result.saved} 条，"
            f"无关 {result.skipped_no_incident}，失败 {result.failed}（均未入库）"
        )
    else:
        log.append(
            f"✅ 完成 | 入库 {result.saved} 条，跳过已有 {result.skipped_url_dup}，"
            f"无关 {result.skipped_no_incident}，失败 {result.failed}"
        )
    return result


def sync_wechat_rss(
    *,
    feed_names: Optional[List[str]] = None,
    max_articles_per_feed: int = 20,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    rag_enabled: Optional[bool] = None,
    concurrency: int = EXTRACT_CONCURRENCY,
    force_reindex: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """
    功能：sync_wechat_rss 是 async_sync_wechat_rss 的同步包装，供 CLI 脚本与 Streamlit 调用。
    输入：feed_names 可选只同步指定公众号；其余同 async_sync_wechat_rss。
    输出：SyncResult；副作用同 async_sync_wechat_rss。
    上下游：scripts/sync_sources.py、app.py；不应在已有事件循环中调用。
    """
    return asyncio.run(
        async_sync_wechat_rss(
            feed_names=feed_names,
            max_articles_per_feed=max_articles_per_feed,
            api_key=api_key,
            base_url=base_url,
            rag_enabled=rag_enabled,
            concurrency=concurrency,
            force_reindex=force_reindex,
            dry_run=dry_run,
        )
    )


# ---------------------------------------------------------------------------
# 政策/法规源同步（进 articles + LLM 抽取）
# ---------------------------------------------------------------------------

POLICY_AI_KEYWORDS: Tuple[str, ...] = (
    "artificial intelligence",
    " ai ",
    "algorithm",
    "automated decision",
    "machine learning",
    "generative",
    "large language model",
    " llm",
    "data protection",
    "cyber",
    "cybersecurity",
    "deepfake",
    "facial recognition",
    "automation",
    "robot",
    "neural network",
    "model risk",
    "chatbot",
    "foundation model",
    # 葡语（巴西 LexML 等）
    "inteligência artificial",
    "aprendizado de máquina",
    "aprendizagem de máquina",
)

# 抓取层已定向或标题语言非英文：跳过关键词预筛，仍走 LLM is_relevant
POLICY_PREFILTER_SKIP_TAGS: frozenset[str] = frozenset(
    {"policy:IN", "policy:BR", "policy:EU"}
)

_POLICY_COUNTRY_CODE: dict[str, str] = {
    "US": "US",
    "USA": "US",
    "UK": "UK",
    "GB": "UK",
    "EU": "EU",
    "EC": "EU",
    "IN": "IN",
    "INDIA": "IN",
    "BR": "BR",
    "BRAZIL": "BR",
}


def _policy_prefilter_relevant(art: RawArticle) -> bool:
    """政策源预筛：US/UK 用关键词；IN/BR/EU 跳过预筛交给 LLM。"""
    tag = _policy_source_tag(art)
    if tag in POLICY_PREFILTER_SKIP_TAGS:
        return True
    text = f"{art.title} {art.trail_text or ''} {art.body_text or ''}".lower()
    return any(kw in text for kw in POLICY_AI_KEYWORDS)


def _policy_source_tag(art: RawArticle) -> str:
    """从 section_name 解析 source 标签，如 policy:US（与 source_registry 一致）。"""
    section = art.section_name or ""
    m = re.search(r"Policy\s*/\s*(\w+)", section, re.I)
    if m:
        raw = m.group(1).upper()
        code = _POLICY_COUNTRY_CODE.get(raw, raw)
        return f"policy:{code}"
    return "policy"


async def async_sync_policy(
    *,
    countries: Optional[List[str]] = None,
    max_articles_per_country: int = 15,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    rag_enabled: Optional[bool] = None,
    concurrency: int = EXTRACT_CONCURRENCY,
    force_reindex: bool = False,
    dry_run: bool = False,
    skip_prefilter: bool = False,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    policy_config: Optional[PolicyConfig] = None,
) -> SyncResult:
    """
    功能：多国政策/法规源同步 → LLM 抽取 → articles 入库。
    输入：countries 默认全池；date_from/date_to 启用历史回溯抓取。
    输出：SyncResult；source 为 policy:US 等。
    上下游：sync_policy、backfill_policy_historical.py。
    """
    result = SyncResult()
    log = result.debug_log
    llm_backend = _build_llm_backend(api_key, base_url)
    dry_tag = " [DRY-RUN，不入库]" if dry_run else ""
    countries_desc = "、".join(countries) if countries else "US/UK/EU/IN/BR"
    window_tag = ""
    if date_from and date_to:
        window_tag = f" | 窗 {date_from.isoformat()}..{date_to.isoformat()}"

    cfg = policy_config
    if cfg is None and date_from and date_to:
        cfg = build_backfill_policy_config(
            date_from=date_from,
            date_to=date_to,
            max_articles_per_country=max_articles_per_country,
        )

    try:
        log.append(
            f"📡 拉取政策源（国家：{countries_desc}，每国≤{max_articles_per_country}）"
            f"{window_tag}{dry_tag}"
        )
        fetch_result = await asyncio.to_thread(
            fetch_policy_articles,
            countries=countries,
            max_articles_per_country=max_articles_per_country,
            download_date=date_to or date.today(),
            config=cfg,
            date_from=date_from,
            date_to=date_to,
        )
        articles = fetch_result.articles
        for err in fetch_result.errors:
            log.append(f"⚠️ 政策源部分失败: {err}")
        log.append(f"✓ 拉取到 {len(articles)} 条")
        if not articles and fetch_result.errors:
            log.append("❌ 所有选定国家政策源均失败")
            result.failed += 1
            return result
    except Exception as e:
        log.append(f"❌ 抓取异常: {type(e).__name__}: {e}")
        result.failed += 1
        return result

    seen_urls: set[str] = set()
    deduped: List[RawArticle] = []
    for art in articles:
        if not art.web_url or art.web_url in seen_urls:
            continue
        seen_urls.add(art.web_url)
        if not skip_prefilter and not _policy_prefilter_relevant(art):
            _handle_prefilter_reject(
                art,
                source=_policy_source_tag(art),
                dry_run=dry_run,
                result=result,
                log=log,
            )
            continue
        if not dry_run and _url_already_in_mysql(art.web_url):
            result.skipped_url_dup += 1
            log.append(f"⏭ 已存在，跳过: {art.title[:55]}")
            continue
        deduped.append(art)
    log.append(f"✓ 去重/预筛后待 LLM 处理 {len(deduped)} 篇")

    if not deduped:
        log.append("💡 本次无新政策条目需要处理")
        return result

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _extract_one(art: RawArticle) -> Tuple[RawArticle, Optional[Dict[str, Any]], List[str]]:
        async with sem:
            return await async_extract_raw_article(art, _policy_source_tag(art), backend=llm_backend)

    log.append(f"🚀 并发抽取 {len(deduped)} 篇（并发上限 {concurrency}）{dry_tag}...")
    extract_results = await asyncio.gather(*[_extract_one(a) for a in deduped], return_exceptions=False)

    for art, article_dict, ext_log in extract_results:
        log.extend(ext_log)
        if not article_dict or not article_dict.get("is_relevant"):
            _handle_llm_reject(
                art,
                article_dict,
                source=_policy_source_tag(art),
                dry_run=dry_run,
                result=result,
            )
            continue
        if dry_run:
            log.append(f"  ✅ [DRY] {art.title[:50]}")
            log.append(f"     source        : {_policy_source_tag(art)}")
            log.append(f"     content_type  : {article_dict.get('content_type')}")
            log.append(f"     risk_domain   : {str(article_dict.get('risk_domain', ''))[:60]}")
            result.saved += 1
            continue

        incident_like = article_dict_to_incident_like(article_dict)
        try:
            from engine.rag_ingestion import apply_rag_to_incidents as _rag
            incidents_rag, rag_log = _rag(
                [incident_like],
                llm_backend=llm_backend,
                enabled=rag_enabled,
            )
        except ImportError:
            incidents_rag = [incident_like]
            rag_log = ["⚠️ chromadb 未安装，RAG 步骤跳过"]
        log.extend(rag_log)
        inc_rag = incidents_rag[0] if incidents_rag else incident_like
        merged = merge_article_with_rag(article_dict, inc_rag)
        _persist_mysql_phase1(
            art, merged, result, llm_backend=llm_backend,
            source=_policy_source_tag(art), force_reindex=force_reindex,
        )

    if dry_run:
        log.append(
            f"✅ DRY-RUN 完成 | 相关抽取 {result.saved} 条，"
            f"预筛/无关 {result.skipped_no_incident}，失败 {result.failed}（均未入库）"
        )
    else:
        log.append(
            f"✅ 完成 | 入库 {result.saved} 条，跳过已有 {result.skipped_url_dup}，"
            f"预筛/无关 {result.skipped_no_incident}，失败 {result.failed}"
        )
    return result


def sync_policy(
    *,
    countries: Optional[List[str]] = None,
    max_articles_per_country: int = 15,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    rag_enabled: Optional[bool] = None,
    concurrency: int = EXTRACT_CONCURRENCY,
    force_reindex: bool = False,
    dry_run: bool = False,
    skip_prefilter: bool = False,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    policy_config: Optional[PolicyConfig] = None,
) -> SyncResult:
    """sync_policy 是 async_sync_policy 的同步包装。"""
    return asyncio.run(
        async_sync_policy(
            countries=countries,
            max_articles_per_country=max_articles_per_country,
            api_key=api_key,
            base_url=base_url,
            rag_enabled=rag_enabled,
            concurrency=concurrency,
            force_reindex=force_reindex,
            dry_run=dry_run,
            skip_prefilter=skip_prefilter,
            date_from=date_from,
            date_to=date_to,
            policy_config=policy_config,
        )
    )


# ---------------------------------------------------------------------------
# 文献资料库（arXiv / Scopus / Springer → literature_items）
# ---------------------------------------------------------------------------


@dataclass
class LiteratureSyncResult:
    """文献同步结果摘要。"""

    saved: int = 0
    skipped_url_dup: int = 0
    failed: int = 0
    debug_log: List[str] = field(default_factory=list)


def _literature_field_ok(item: LiteratureItem) -> Dict[str, bool]:
    return {
        "url": bool(item.url),
        "title": bool(item.title),
        "abstract_or_meta": bool(item.abstract or item.publication_name or item.doi),
        "published_at": bool(item.published_at),
    }


def _persist_literature_item(item: LiteratureItem, result: LiteratureSyncResult) -> None:
    try:
        from core.mysql_db import get_literature_by_url, normalize_url, save_literature_item
    except Exception as e:
        result.debug_log.append(f"⚠️ literature_items 未启用: {type(e).__name__}: {e}")
        result.failed += 1
        return

    if get_literature_by_url(normalize_url(item.url)):
        result.skipped_url_dup += 1
        return

    try:
        save_literature_item(
            url=item.url,
            source=item.source,
            title=item.title,
            abstract=item.abstract or "",
            authors=item.authors,
            doi=item.doi or "",
            external_id=item.external_id or "",
            publication_name=item.publication_name or "",
            document_type=item.document_type or "",
            subject_area=item.subject_area or "",
            published_at=parse_literature_published_at(item.published_at),
            pdf_url=item.pdf_url or "",
            raw_metadata=item.raw_metadata,
        )
        result.saved += 1
    except Exception as e:
        result.failed += 1
        result.debug_log.append(f"⚠️ 文献入库失败 [{item.title[:40]}]: {type(e).__name__}: {e}")


def sync_literature(
    *,
    sources: Optional[List[str]] = None,
    max_arxiv_per_category: int = 3,
    arxiv_categories: Optional[List[str]] = None,
    max_springer_per_domain: int = 3,
    springer_domains: Optional[List[str]] = None,
    scopus_max_results: int = 10,
    scopus_days_back: int = 7,
    dry_run: bool = False,
) -> LiteratureSyncResult:
    """
    功能：拉取 arXiv/Scopus/Springer 并写入 literature_items（不跑 LLM）。
    输入：sources 如 ['arxiv','springer']；dry_run 只打日志不入库。
    输出：LiteratureSyncResult。
    """
    result = LiteratureSyncResult()
    log = result.debug_log
    selected = [s.strip().lower() for s in (sources or ["arxiv"]) if s.strip()]
    dry_tag = " [DRY-RUN，不入库]" if dry_run else ""
    all_items: List[LiteratureItem] = []

    if "arxiv" in selected:
        try:
            items = fetch_arxiv_literature(
                categories=arxiv_categories or ["cs.AI", "cs.CL"],
                max_articles_per_category=max_arxiv_per_category,
            )
            log.append(f"✓ arXiv 拉取 {len(items)} 条")
            all_items.extend(items)
        except Exception as e:
            log.append(f"❌ arXiv 失败: {type(e).__name__}: {e}")
            result.failed += 1

    if "springer" in selected:
        try:
            items = fetch_springer_literature(
                domains=springer_domains or ["Machine Learning", "Artificial Intelligence"],
                max_articles_per_domain=max_springer_per_domain,
            )
            log.append(f"✓ Springer 拉取 {len(items)} 条")
            all_items.extend(items)
        except Exception as e:
            log.append(f"❌ Springer 失败: {type(e).__name__}: {e}")
            result.failed += 1

    if "scopus" in selected:
        try:
            items = fetch_scopus_literature(
                days_back=scopus_days_back,
                max_results=scopus_max_results,
            )
            log.append(f"✓ Scopus 拉取 {len(items)} 条")
            all_items.extend(items)
        except Exception as e:
            log.append(f"❌ Scopus 失败: {type(e).__name__}: {e}")
            result.failed += 1

    log.append(f"📚 合计 {len(all_items)} 条待处理{dry_tag}")
    from crawler.sync_quality import literature_required_ok

    for i, item in enumerate(all_items, 1):
        ok, reason = literature_required_ok(item)
        fields = _literature_field_ok(item)
        log.append(
            f"  [{i}] {item.source} | {item.title[:50]} | "
            f"url={fields['url']} meta={fields['abstract_or_meta']} date={fields['published_at']}"
        )
        if not ok:
            result.failed += 1
            log.append(f"  ⏭ 缺关键字段跳过: {reason}")
            continue
        if dry_run:
            result.saved += 1
            continue
        _persist_literature_item(item, result)

    if dry_run:
        log.append(f"✅ DRY-RUN 完成 | 可入库 {result.saved} 条（未写入 literature_items）")
    else:
        log.append(
            f"✅ 完成 | 新入库 {result.saved} 条，"
            f"已有跳过 {result.skipped_url_dup}，失败 {result.failed}"
        )
    return result


# ---------------------------------------------------------------------------
# Agentic 单 URL（会议官网等）
# ---------------------------------------------------------------------------


async def async_sync_agentic_url(
    url: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    rag_enabled: Optional[bool] = None,
) -> SyncResult:
    """
    功能：抓取单页并写入 MySQL（供会议官网回溯）。
    输入：目标 URL。
    输出：SyncResult。
    """
    from crawler.agentic_crawl import run_agentic_crawl
    from crawler.sources.guardian import RawArticle

    result = SyncResult()
    log = result.debug_log
    llm_backend = _build_llm_backend(api_key, base_url)
    from core.meeting_catalog import resolve_meeting_official_hint

    _ck, hint = resolve_meeting_official_hint(url)
    art_dict, _incidents, _kws, dbg = await run_agentic_crawl(
        url, api_key=api_key, base_url=base_url, extra_hint=hint
    )
    log.extend(dbg)
    if not art_dict or not art_dict.get("is_relevant"):
        result.skipped_no_incident += 1
        return result
    title = str(art_dict.get("main_topic") or url)[:1024]
    summary = str(art_dict.get("summary_structured") or "")[:2000]
    raw = RawArticle(
        web_url=url,
        title=title,
        trail_text=summary or None,
        body_text=summary or None,
        web_publication_date=datetime.now().strftime("%Y-%m-%d"),
        section_name="agentic",
        api_url=None,
        guardian_id=None,
    )
    _persist_mysql_phase1(
        raw,
        art_dict,
        result,
        llm_backend=llm_backend,
        source="agentic",
        force_reindex=False,
    )
    return result


def sync_agentic_url(url: str, **kwargs: Any) -> SyncResult:
    """sync 包装 async_sync_agentic_url。"""
    return asyncio.run(async_sync_agentic_url(url, **kwargs))
