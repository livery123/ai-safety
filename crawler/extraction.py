"""
统一 LLM 抽取模块：将「正文 + 来源 URL」抽成单篇文章一条结构化 JSON（ArticleExtractionPayload）。

功能：与 agentic_crawl 提示词语义对齐；提供同步/异步入口；异步版供 orchestrator 并发调用。
输入：body_text、source_url；可选 llm_backend。
输出：(article dict | None, debug)；无关或失败时 article 为 None。
下游：article_dict_to_incident_like → apply_rag_to_incidents（单元素列表）→ incident_from_extraction / save_extraction。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.config import API_KEY, BASE_URL
from core.llm_client import OpenAICompatibleBackend
from engine.prompts import EXTRACTION_SYSTEM, EXTRACTION_USER_TAIL, RISK_DOMAIN_LLM_GUIDANCE

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
_USER_INSTRUCTION = EXTRACTION_USER_TAIL

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

    out: Dict[str, Any] = {
        "is_relevant": True,
        "content_type": _normalize_content_type(d.get("content_type")),
        "main_topic": str(d.get("main_topic") or d.get("title") or "")[:512],
        "risk_domain": str(d.get("risk_domain") or "").strip(),
        "risk_subdomains": subs[:20],
        "entities": ents[:50],
        "summary_structured": summary,
        "tags": tags[:24],
        "relevance_reason": str(d.get("relevance_reason") or "")[:512],
        "reject_reason": "",
    }
    return out


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


async def async_extract_incidents_from_text(
    body_text: str,
    source_url: str = "",
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    backend: Optional[OpenAICompatibleBackend] = None,
    temperature: float = 0.1,
    timeout: float = 120.0,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    art, dbg = await async_extract_article_from_text(
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

