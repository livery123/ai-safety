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
输入：各 sync_* 函数的 query/max_pages 等参数；api_key/base_url 可覆盖 .env；rag_enabled 控制 RAG。
输出：SyncResult（MySQL 入库篇数、跳过数、debug 日志）；副作用：HTTP 拉取 + 并发 LLM + MySQL/Chroma。
上下游：app.py、scripts/sync_sources；下游 core.mysql_db。
扩展点：新增信源只需实现对应 async_sync_* 函数，共用 _persist_mysql_phase1 与 _run_sync_pipeline。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
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
from crawler.sources.policy import fetch_policy_articles
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
EXTRACT_CONCURRENCY = 5


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

    # 解析发布时间：兼容多种来源格式，统一存为 naive datetime（不含时区）写入 MySQL。
    # - Guardian/NYT ISO 8601（含 T 分隔，可能带 Z 或 +HH:MM 时区）
    # - 新华网/新浪科技 _extract_date 返回的 "YYYY-MM-DD HH:MM:SS" 或 "YYYY-MM-DD HH:MM"
    # - 新浪 meta article:published_time 带时区 ISO（如 "2026-05-15T09:07:47+08:00"）
    published_at: Optional[datetime] = None
    if art.web_publication_date:
        raw_date = art.web_publication_date.strip()
        # 先尝试不含时区的格式（截取前 19 位统一处理）
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",   # ISO 无时区 / Guardian Z 结尾截取前19
            "%Y-%m-%d %H:%M:%S",   # 新华/新浪空格格式含秒
            "%Y-%m-%d %H:%M",      # 新华/新浪空格格式不含秒
            "%Y-%m-%d",            # 政策源等仅日期
        ):
            try:
                slice_len = 19 if "H" in fmt else 10
                dt = datetime.strptime(raw_date[:slice_len], fmt)
                published_at = dt
                break
            except ValueError:
                continue
        # 若以上均失败，再尝试带时区的 ISO 8601（新浪 meta 返回 +08:00 格式）
        if published_at is None:
            try:
                from datetime import timezone
                # Python 3.7+ fromisoformat 支持 +HH:MM（但不支持 Z）
                normalized = raw_date.replace("Z", "+00:00")
                dt_aware = datetime.fromisoformat(normalized)
                published_at = dt_aware.replace(tzinfo=None)
            except (ValueError, AttributeError):
                pass

    if not (art.title or "").strip():
        result.debug_log.append("⏭ 缺少标题，跳过入库")
        result.failed += 1
        return

    if published_at is None:
        result.debug_log.append(f"⏭ 缺少有效发布时间，跳过入库: {(art.title or '')[:60]}")
        result.failed += 1
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
            result.skipped_no_incident += 1
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
            result.skipped_no_incident += 1
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
            result.skipped_no_incident += 1
            if dry_run and article_dict:
                log.append(
                    f"  ⛔ 无关: {art.title[:50]} | reason={article_dict.get('reject_reason','')}"
                )
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
            result.skipped_no_incident += 1
            if dry_run and article_dict:
                log.append(
                    f"  ⛔ 无关: {art.title[:50]} | reason={article_dict.get('reject_reason','')}"
                )
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

        if not article_dict or not article_dict.get("is_relevant"):
            result.skipped_no_incident += 1
            if dry_run and article_dict:
                log.append(
                    f"  ⛔ 无关: {art.title[:50]} | reason={article_dict.get('reject_reason','')}"
                )
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
        feed_name = (art.section_name or "").replace("WeChat / ", "").strip()
        source_tag = f"wechat_rss:{feed_name}" if feed_name else "wechat_rss"

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
)


def _policy_prefilter_relevant(art: RawArticle) -> bool:
    """政策源预筛：标题/摘要/正文含 AI 治理相关关键词才进入 LLM。"""
    text = f"{art.title} {art.trail_text or ''} {art.body_text or ''}".lower()
    return any(kw in text for kw in POLICY_AI_KEYWORDS)


def _policy_source_tag(art: RawArticle) -> str:
    """从 section_name 解析 source 标签，如 policy:US。"""
    section = art.section_name or ""
    m = re.search(r"Policy\s*/\s*(\w+)", section, re.I)
    if m:
        return f"policy:{m.group(1).upper()}"
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
) -> SyncResult:
    """
    功能：多国政策/法规源同步 → LLM 抽取 → articles 入库。
    输入：countries 默认全池；skip_prefilter 跳过 AI 关键词预筛（调试用）。
    输出：SyncResult；source 为 policy:US 等。
    上下游：sync_policy、ui_jobs、scripts/sync_sources.py。
    """
    result = SyncResult()
    log = result.debug_log
    llm_backend = _build_llm_backend(api_key, base_url)
    dry_tag = " [DRY-RUN，不入库]" if dry_run else ""
    countries_desc = "、".join(countries) if countries else "US/UK/EU/IN/BR"

    try:
        log.append(
            f"📡 拉取政策源（国家：{countries_desc}，每国≤{max_articles_per_country}）{dry_tag}"
        )
        fetch_result = await asyncio.to_thread(
            fetch_policy_articles,
            countries=countries,
            max_articles_per_country=max_articles_per_country,
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
            result.skipped_no_incident += 1
            log.append(f"⏭ 预筛无关: {art.title[:55]}")
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
            result.skipped_no_incident += 1
            if dry_run and article_dict:
                log.append(
                    f"  ⛔ 无关: {art.title[:50]} | reason={article_dict.get('reject_reason', '')}"
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
