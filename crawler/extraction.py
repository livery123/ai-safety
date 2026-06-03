"""
统一 LLM 抽取模块：将「正文 + 来源 URL」抽成单篇文章一条结构化 JSON（ArticleExtractionPayload）。

功能：与 agentic_crawl 提示词语义对齐；提供同步/异步入口；异步版供 orchestrator 并发调用。
输入：body_text、source_url；可选 llm_backend。
输出：(article dict | None, debug)；无关或失败时 article 为 None。
下游：article_dict_to_incident_like → apply_rag_to_incidents（单元素列表）→ incident_from_extraction / save_extraction。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from core.config import API_KEY, BASE_URL
from core.llm_client import OpenAICompatibleBackend
from core.publish_actor import (
    CrawlHints,
    hints_from_raw_article,
    normalize_publish_fields,
    validate_publish_fields,
)
from engine.prompts import (
    EXTRACTION_SYSTEM,
    PUBLISH_GEO_ONLY_USER,
    RISK_DOMAIN_LLM_GUIDANCE,
    build_extraction_user_tail,
)

_ALLOWED_CONTENT_TYPES = frozenset(
    {"news", "meeting", "report", "policy", "opinion", "literature", "other"}
)
_CONTENT_TYPE_ALIASES = {
    "policy_paper": "policy",
    "op_ed": "opinion",
    "research": "report",
    "paper": "literature",
    "journal_article": "literature",
    "arxiv": "literature",
}

# ---------------------------------------------------------------------------
# 提示词：与 agentic_crawl 对齐，统一更新入口。
# ---------------------------------------------------------------------------
# 与 models.schema.RISK_DOMAIN_CHOICES 一致；定义见 engine.prompts（供 agentic_crawl 等 re-export）。
_SYSTEM_PROMPT = EXTRACTION_SYSTEM
_USER_INSTRUCTION = build_extraction_user_tail()

_BODY_TRUNCATE_CHARS = 8000


def _normalize_content_type(raw: Any) -> str:
    t = str(raw or "other").strip().lower()
    t = _CONTENT_TYPE_ALIASES.get(t, t)
    if t in _ALLOWED_CONTENT_TYPES:
        return t
    return "other"


def _as_str_list(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        t = val.strip()
        return [t] if t else []
    return []


def _parse_article_obj(raw_obj: Any) -> Optional[Dict[str, Any]]:
    """
    将 LLM 返回对象规范为 article 级 dict；无法识别时返回 None。
    兼容旧版 {\"incidents\":[...]}：取首条若含 title/summary 则尽量回填为单篇结构（不推荐）。
    """
    d: Optional[Dict[str, Any]] = None
    if isinstance(raw_obj, dict):
        if "is_relevant" in raw_obj:
            d = raw_obj
        elif isinstance(raw_obj.get("incidents"), list) and raw_obj["incidents"]:
            first = raw_obj["incidents"][0]
            if isinstance(first, dict) and (first.get("title") or first.get("summary")):
                d = {
                    "is_relevant": True,
                    "content_type": "news",
                    "main_topic": str(first.get("title") or "")[:512],
                    "risk_domain": first.get("risk_domain", ""),
                    "risk_subdomains": _as_str_list(first.get("risk_subdomain")),
                    "entities": [str(first.get("entity") or "").strip()] if first.get("entity") else [],
                    "summary_structured": str(first.get("summary") or "")[:512],
                    "tags": _as_str_list(first.get("tags")),
                    "relevance_reason": "legacy incidents[0]",
                    "reject_reason": "",
                }
    elif isinstance(raw_obj, list) and raw_obj and isinstance(raw_obj[0], dict):
        first = raw_obj[0]
        if "is_relevant" in first:
            d = first
        elif first.get("title") or first.get("summary"):
            return _parse_article_obj({"incidents": raw_obj})
        else:
            d = None

    if not d or not isinstance(d, dict):
        return None

    rel = bool(d.get("is_relevant", False))
    if not rel:
        rr = str(d.get("reject_reason") or "").strip() or "no_ai_governance_content"
        return {"is_relevant": False, "reject_reason": rr[:255]}

    subs = _as_str_list(d.get("risk_subdomains"))
    if not subs and d.get("risk_subdomain"):
        subs = _as_str_list(d.get("risk_subdomain"))
    ents = _as_str_list(d.get("entities"))
    if not ents and d.get("entity"):
        ents = _as_str_list(str(d.get("entity")).replace("，", ",").split(","))

    summary = str(d.get("summary_structured") or d.get("summary") or "")[:512]
    tags = _as_str_list(d.get("tags"))
    intl = _as_str_list(d.get("international_orgs"))

    phase_raw = str(d.get("meeting_phase") or "").strip().lower()
    if phase_raw not in ("pre", "during", "post", "unknown"):
        phase_raw = ""

    out: Dict[str, Any] = {
        "is_relevant": True,
        "content_type": _normalize_content_type(d.get("content_type")),
        "main_topic": str(d.get("main_topic") or d.get("title") or "")[:512],
        "risk_domain": str(d.get("risk_domain") or "").strip(),
        "risk_subdomains": subs[:20],
        "entities": ents[:50],
        "publish_country": str(d.get("publish_country") or "").strip()[:64],
        "publish_region": str(d.get("publish_region") or "").strip()[:128],
        "international_orgs": intl[:12],
        "publish_authority": str(d.get("publish_authority") or "").strip()[:256],
        "summary_structured": summary,
        "tags": tags[:24],
        "relevance_reason": str(d.get("relevance_reason") or "")[:512],
        "reject_reason": "",
        "meeting_catalog_key": str(d.get("meeting_catalog_key") or "")[:64],
        "meeting_edition_hint": str(d.get("meeting_edition_hint") or "")[:128],
        "meeting_phase": phase_raw,
        "proposed_series_name": str(d.get("proposed_series_name") or "")[:256],
    }
    return out


def _apply_publish_normalization(
    art: Optional[Dict[str, Any]],
    crawl_hints: Optional[CrawlHints],
    body_text: str,
) -> Optional[Dict[str, Any]]:
    if not art or not art.get("is_relevant"):
        return art
    normalize_publish_fields(art, crawl_hints, text_blob=body_text)
    return art


def article_dict_to_incident_like(art: Dict[str, Any]) -> Dict[str, Any]:
    """将文章级抽取转为 RAG / SQLite / 事件匹配使用的单条「情报」dict。"""
    subs = art.get("risk_subdomains") or []
    if not isinstance(subs, list):
        subs = []
    sub_first = str(subs[0]).strip() if subs else "未指定子域"
    ents = art.get("entities") or []
    if not isinstance(ents, list):
        ents = []
    entity_str = ", ".join(str(e).strip() for e in ents if str(e).strip())
    tags = art.get("tags") if isinstance(art.get("tags"), list) else []
    tags = [str(t).strip() for t in tags if str(t).strip()]
    mt = str(art.get("main_topic") or "").strip()
    return {
        "title": mt,
        "entity": entity_str,
        "risk_level": "中",
        "risk_domain": art.get("risk_domain"),
        "risk_subdomain": sub_first,
        "summary": str(art.get("summary_structured") or "").strip(),
        "tags": tags,
        "action_type": "其他",
        "place": "",
        "stance": "未知",
        "topic_raw": sub_first if sub_first != "未指定子域" else mt[:160],
    }


def merge_article_with_rag(art: Dict[str, Any], inc_rag: Dict[str, Any]) -> Dict[str, Any]:
    """把 RAG 精炼后的主域/首子域写回文章级 dict，供 MySQL save_extraction。"""
    m = dict(art)
    m["risk_domain"] = str(inc_rag.get("risk_domain") or m.get("risk_domain") or "").strip()
    refined = str(inc_rag.get("risk_subdomain") or "").strip()
    subs = list(m.get("risk_subdomains") or [])
    if not isinstance(subs, list):
        subs = []
    subs = [str(s).strip() for s in subs if str(s).strip()]
    if refined and refined != "未指定子域":
        subs = [refined] + [s for s in subs if s != refined]
    elif refined == "未指定子域" and not subs:
        subs = []
    m["risk_subdomains"] = subs[:20]
    return m


def _build_backend(api_key: Optional[str], base_url: Optional[str]) -> OpenAICompatibleBackend:
    k = (api_key or "").strip() or API_KEY
    b = (base_url or "").strip() or BASE_URL
    return OpenAICompatibleBackend(api_key=k, base_url=b)


def extract_article_from_text(
    body_text: str,
    source_url: str = "",
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    backend: Optional[OpenAICompatibleBackend] = None,
    temperature: float = 0.1,
    timeout: float = 120.0,
    crawl_hints: Optional[CrawlHints] = None,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    debug: List[str] = []
    text = body_text.strip()
    if not text:
        debug.append(f"❌ 文本为空，跳过 [{source_url[:80]}]")
        return None, debug
    if len(text) > _BODY_TRUNCATE_CHARS:
        text = text[:_BODY_TRUNCATE_CHARS]
        debug.append(f"⚠️ 正文截断为 {_BODY_TRUNCATE_CHARS} 字符")

    llm = backend or _build_backend(api_key, base_url)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"{_USER_INSTRUCTION}\n\n---\n{text}"},
    ]
    try:
        raw_obj = llm.chat_completion_json(messages, temperature=temperature, timeout=timeout)
    except Exception as e:
        debug.append(f"❌ LLM 调用失败 [{source_url[:60]}]: {type(e).__name__}: {e}")
        return None, debug

    art = _parse_article_obj(raw_obj)
    if art is None:
        debug.append(f"❌ 无法解析为文章级抽取 [{source_url[:60]}]")
        return None, debug
    art = _apply_publish_normalization(art, crawl_hints, text)
    if art and art.get("is_relevant"):
        for w in validate_publish_fields(art):
            debug.append(f"⚠️ 发布地理字段: {w} [{source_url[:40]}]")
    debug.append(f"✓ 抽取完成，is_relevant:{art['is_relevant']} [{source_url[:60]}]")
    return art, debug


async def async_extract_article_from_text(
    body_text: str,
    source_url: str = "",
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    backend: Optional[OpenAICompatibleBackend] = None,
    temperature: float = 0.1,
    timeout: float = 120.0,
    crawl_hints: Optional[CrawlHints] = None,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    debug: List[str] = []
    text = body_text.strip()
    if not text:
        debug.append(f"❌ 文本为空，跳过 [{source_url[:80]}]")
        return None, debug
    if len(text) > _BODY_TRUNCATE_CHARS:
        text = text[:_BODY_TRUNCATE_CHARS]
        debug.append(f"⚠️ 正文截断为 {_BODY_TRUNCATE_CHARS} 字符")

    llm = backend or _build_backend(api_key, base_url)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"{_USER_INSTRUCTION}\n\n---\n{text}"},
    ]
    try:
        raw_obj = await llm.async_chat_completion_json(
            messages, temperature=temperature, timeout=timeout
        )
    except Exception as e:
        debug.append(f"❌ LLM 异步调用失败 [{source_url[:60]}]: {type(e).__name__}: {e}")
        return None, debug

    art = _parse_article_obj(raw_obj)
    if art is None:
        debug.append(f"❌ 无法解析为文章级抽取 [{source_url[:60]}]")
        return None, debug
    art = _apply_publish_normalization(art, crawl_hints, text)
    if art and art.get("is_relevant"):
        for w in validate_publish_fields(art):
            debug.append(f"⚠️ 发布地理字段: {w} [{source_url[:40]}]")
    debug.append(f"✓ 异步抽取完成，is_relevant:{art['is_relevant']} [{source_url[:60]}]")
    return art, debug


def extract_incidents_from_text(
    body_text: str,
    source_url: str = "",
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    backend: Optional[OpenAICompatibleBackend] = None,
    temperature: float = 0.1,
    timeout: float = 120.0,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """兼容旧签名：内部走文章级抽取，仅当 is_relevant 时返回单元素列表（incident_like）。"""
    art, dbg = extract_article_from_text(
        body_text,
        source_url,
        api_key=api_key,
        base_url=base_url,
        backend=backend,
        temperature=temperature,
        timeout=timeout,
    )
    if not art or not art.get("is_relevant"):
        return [], dbg
    return [article_dict_to_incident_like(art)], dbg


async def async_extract_raw_article(
    art: Any,
    source_tag: str = "",
    *,
    backend: Optional[OpenAICompatibleBackend] = None,
) -> Tuple[Any, Optional[Dict[str, Any]], List[str]]:
    """
    功能：RawArticle → LLM 上下文（含采集 hint）→ 抽取 → 发布地理归一化。
    输入：RawArticle；source_tag 如 policy:US、guardian。
    输出：(art, article_dict, debug_log)。
    """
    from crawler.sources.guardian import raw_article_to_llm_context

    hints = hints_from_raw_article(
        source=source_tag,
        section_name=str(getattr(art, "section_name", "") or ""),
        body_text=str(getattr(art, "body_text", "") or ""),
    )
    context = raw_article_to_llm_context(art, crawl_hints=hints)
    article_dict, ext_log = await async_extract_article_from_text(
        context,
        source_url=str(getattr(art, "web_url", "") or ""),
        backend=backend,
        crawl_hints=hints,
    )
    return art, article_dict, ext_log


async def async_extract_incidents_from_text(
    body_text: str,
    source_url: str = "",
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    backend: Optional[OpenAICompatibleBackend] = None,
    temperature: float = 0.1,
    timeout: float = 120.0,
    crawl_hints: Optional[CrawlHints] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """兼容旧签名：异步文章级抽取，仅当 is_relevant 时返回单元素 incident_like 列表。"""
    art, dbg = await async_extract_article_from_text(
        body_text,
        source_url,
        api_key=api_key,
        base_url=base_url,
        backend=backend,
        temperature=temperature,
        timeout=timeout,
        crawl_hints=crawl_hints,
    )
    if not art or not art.get("is_relevant"):
        return [], dbg
    return [article_dict_to_incident_like(art)], dbg


def _is_llm_retryable(exc: BaseException) -> bool:
    """超时/网络类错误可重试。"""
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError)):
        return True
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return "timeout" in name or "timeout" in msg or "timed out" in msg


def extract_publish_geo_fields(
    body_text: str,
    source_url: str = "",
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    backend: Optional[OpenAICompatibleBackend] = None,
    temperature: float = 0.0,
    timeout: float = 180.0,
    max_retries: int = 3,
    retry_delay: float = 3.0,
    crawl_hints: Optional[CrawlHints] = None,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    功能：轻量 LLM 调用，仅抽取发布地理四字段（回填脚本用）。
    输入：正文、URL、可选 crawl_hints；timeout/max_retries/retry_delay 控制超时重试。
    输出：(含四字段的 dict | None, debug_log)。
    """
    debug: List[str] = []
    text = (body_text or "").strip()
    if not text:
        debug.append(f"❌ 文本为空 [{source_url[:60]}]")
        return None, debug
    if len(text) > _BODY_TRUNCATE_CHARS:
        text = text[:_BODY_TRUNCATE_CHARS]
        debug.append(f"⚠️ 正文截断为 {_BODY_TRUNCATE_CHARS} 字符")

    hint_block = ""
    if crawl_hints is not None:
        hint_block = crawl_hints.to_prompt_block().strip()
        if hint_block:
            hint_block = hint_block + "\n\n"

    llm = backend or _build_backend(api_key, base_url)
    messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM},
        {
            "role": "user",
            "content": f"{PUBLISH_GEO_ONLY_USER}\n\n---\n{hint_block}{text}",
        },
    ]

    retries = max(1, int(max_retries))
    raw_obj: Any = None
    for attempt in range(1, retries + 1):
        attempt_timeout = timeout * (1.0 + 0.25 * (attempt - 1))
        try:
            raw_obj = llm.chat_completion_json(
                messages, temperature=temperature, timeout=attempt_timeout
            )
            if attempt > 1:
                debug.append(
                    f"✓ 第 {attempt} 次重试成功 (timeout={attempt_timeout:.0f}s) [{source_url[:40]}]"
                )
            break
        except Exception as e:
            if attempt < retries and _is_llm_retryable(e):
                debug.append(
                    f"⚠️ LLM {type(e).__name__}，{retry_delay:.0f}s 后重试 "
                    f"({attempt}/{retries}, timeout={attempt_timeout:.0f}s) [{source_url[:40]}]"
                )
                time.sleep(retry_delay)
                continue
            debug.append(f"❌ LLM 调用失败 [{source_url[:60]}]: {type(e).__name__}: {e}")
            return None, debug

    if raw_obj is None:
        debug.append(f"❌ LLM 无响应 [{source_url[:60]}]")
        return None, debug

    if not isinstance(raw_obj, dict):
        debug.append(f"❌ 非 JSON 对象 [{source_url[:60]}]")
        return None, debug

    out: Dict[str, Any] = {
        "content_type": "policy",
        "is_relevant": True,
        "publish_country": str(raw_obj.get("publish_country") or "").strip()[:64],
        "publish_region": str(raw_obj.get("publish_region") or "").strip()[:128],
        "international_orgs": _as_str_list(raw_obj.get("international_orgs"))[:12],
        "publish_authority": str(raw_obj.get("publish_authority") or "").strip()[:256],
    }
    normalize_publish_fields(out, crawl_hints, text_blob=text)
    debug.append(f"✓ geo 抽取完成 [{source_url[:60]}]")
    return out, debug

