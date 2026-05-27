"""
文献资料适配层：将 arXiv / Scopus / Springer 抓取结果映射为 LiteratureItem，供文献表入库与展示。

功能：统一文献元数据形状；调用各 Subscriber 拉取后转换；不做 LLM 抽取。
输入：各 fetch_* 的参数（分类、国家、条数上限等）。
输出：LiteratureItem 列表；副作用：HTTP 请求（由各 Subscriber 执行）。
上下游：crawler.orchestrator.sync_literature_*；下游 core.mysql_db.save_literature_item。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

from crawler.sources.arxiv import ArxivComputerScienceSubscriber, ArxivRSSConfig
from crawler.sources.scopus import ScopusConfig, ScopusError, ScopusSubscriber
from crawler.sources.springer import SpringerConfig, SpringerSubscriber


@dataclass(frozen=True)
class LiteratureItem:
    """
    功能：文献库单行展示与去重用的标准结构。
    输入：由 arxiv/scopus/springer 适配函数构造。
    输出：只读数据对象；写入 literature_items 表。
    """

    source: str
    url: str
    title: str
    abstract: Optional[str] = None
    published_at: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    doi: Optional[str] = None
    external_id: Optional[str] = None
    publication_name: Optional[str] = None
    document_type: Optional[str] = None
    subject_area: Optional[str] = None
    pdf_url: Optional[str] = None
    raw_metadata: Dict[str, Any] = field(default_factory=dict)


def _preview(text: Optional[str], max_len: int = 160) -> str:
    if not text:
        return ""
    one = " ".join(text.split())
    return one if len(one) <= max_len else one[: max_len - 3] + "..."


def _parse_metadata_lines(body: Optional[str]) -> Dict[str, str]:
    """从 body_text 末尾 metadata 块解析 Key: Value 行。"""
    out: Dict[str, str] = {}
    if not body:
        return out
    for line in body.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if key and val:
            out[key] = val
    return out


def _arxiv_id_from_url(url: str) -> Optional[str]:
    m = re.search(r"arxiv\.org/abs/([^/?#]+)", url or "", re.I)
    return m.group(1) if m else None


def _authors_from_meta(meta: Dict[str, str]) -> List[str]:
    raw = meta.get("Authors", "")
    if not raw:
        return []
    return [a.strip() for a in raw.split(",") if a.strip()]


def _arxiv_item_from_raw(raw: Any) -> LiteratureItem:
    """将 arxiv 模块 RawArticle（pydantic）转为 LiteratureItem。"""
    url = (getattr(raw, "web_url", None) or "").strip()
    title = (getattr(raw, "title", None) or "").strip() or "(no title)"
    trail = getattr(raw, "trail_text", None) or ""
    body = getattr(raw, "body_text", None) or ""
    section = getattr(raw, "section_name", None) or ""
    meta = _parse_metadata_lines(body)
    subject = section.replace("arXiv /", "").strip() if "arXiv" in section else meta.get("Category", "")
    abstract = trail.strip() or body.split("\n\n")[0].strip() if body else None
    return LiteratureItem(
        source="arxiv",
        url=url,
        title=title,
        abstract=abstract or None,
        published_at=getattr(raw, "web_publication_date", None),
        authors=_authors_from_meta(meta),
        external_id=meta.get("arXiv ID") or _arxiv_id_from_url(url),
        subject_area=subject or None,
        document_type="preprint",
        raw_metadata={"section_name": section, "api_url": getattr(raw, "api_url", None)},
    )


def _scopus_item_from_raw(raw: Any, *, api_url: str = "") -> LiteratureItem:
    body = getattr(raw, "body_text", None) or ""
    meta = _parse_metadata_lines(body)
    trail = getattr(raw, "trail_text", None) or ""
    section = getattr(raw, "section_name", None) or ""
    pub = meta.get("Publication") or trail.strip()
    affiliations_raw = meta.get("Affiliations", "")
    affiliations: Any = []
    if affiliations_raw.startswith("["):
        try:
            affiliations = json.loads(affiliations_raw)
        except json.JSONDecodeError:
            affiliations = affiliations_raw
    return LiteratureItem(
        source="scopus",
        url=(getattr(raw, "web_url", None) or "").strip(),
        title=(getattr(raw, "title", None) or "").strip() or "(no title)",
        abstract=None,
        published_at=getattr(raw, "web_publication_date", None),
        authors=[],
        doi=meta.get("DOI") or None,
        publication_name=pub or None,
        document_type=meta.get("Article Type") or meta.get("Subtype") or None,
        subject_area="COMP",
        raw_metadata={
            "section_name": section,
            "api_url": api_url or getattr(raw, "api_url", None),
            "affiliations": affiliations,
        },
    )


def _springer_item_from_raw(raw: Any) -> LiteratureItem:
    body = getattr(raw, "body_text", None) or ""
    trail = getattr(raw, "trail_text", None) or ""
    meta = _parse_metadata_lines(body)
    section = getattr(raw, "section_name", None) or ""
    domain = section.replace("Springer /", "").strip() if "Springer" in section else meta.get("Domain", "")
    authors: List[str] = []
    if meta.get("Authors"):
        try:
            parsed = json.loads(meta["Authors"])
            if isinstance(parsed, list):
                for a in parsed:
                    if isinstance(a, dict) and a.get("name"):
                        authors.append(str(a["name"]))
                    elif isinstance(a, str):
                        authors.append(a)
        except json.JSONDecodeError:
            pass
    keywords: List[str] = []
    if meta.get("Keywords"):
        try:
            kw = json.loads(meta["Keywords"])
            if isinstance(kw, list):
                keywords = [str(x) for x in kw]
        except json.JSONDecodeError:
            pass
    return LiteratureItem(
        source="springer",
        url=(getattr(raw, "web_url", None) or "").strip(),
        title=(getattr(raw, "title", None) or "").strip() or "(no title)",
        abstract=trail.strip() or None,
        published_at=getattr(raw, "web_publication_date", None),
        authors=authors,
        doi=meta.get("DOI") or None,
        publication_name=meta.get("Publication") or None,
        document_type="article",
        subject_area=domain or None,
        pdf_url=meta.get("PDF") or None,
        raw_metadata={"keywords": keywords, "section_name": section},
    )


def fetch_arxiv_literature(
    *,
    categories: Optional[Iterable[str]] = None,
    max_articles_per_category: int = 5,
) -> List[LiteratureItem]:
    """
    功能：拉取 arXiv CS RSS 并转为 LiteratureItem 列表。
    输入：categories 默认 cs.AI/cs.CL；max_articles_per_category 每分类上限。
    输出：去重后的 LiteratureItem 列表。
    """
    cfg = ArxivRSSConfig(max_articles_per_category=max(1, max_articles_per_category))
    sub = ArxivComputerScienceSubscriber(cfg)
    page = sub.subscribe(categories=categories or ["cs.AI", "cs.CL"])
    seen: set[str] = set()
    out: List[LiteratureItem] = []
    for raw in page.articles:
        item = _arxiv_item_from_raw(raw)
        if not item.url or item.url in seen:
            continue
        seen.add(item.url)
        out.append(item)
    return out


def fetch_scopus_literature(
    *,
    api_key: Optional[str] = None,
    days_back: int = 7,
    max_results: int = 25,
    download_date: Optional[date] = None,
) -> List[LiteratureItem]:
    """
    功能：拉取 Scopus Search API 并转为 LiteratureItem。
    输入：api_key 缺省读环境变量 SCOPUS_API_KEY；max_results 总上限。
    输出：LiteratureItem 列表；无 key 时抛 ScopusError。
    """
    import os

    key = (api_key or os.getenv("SCOPUS_API_KEY", "") or "").strip()
    if not key:
        raise ScopusError("未配置 SCOPUS_API_KEY")
    cfg = ScopusConfig(api_key=key, days_back=days_back, max_results=max_results, count=min(25, max_results))
    sub = ScopusSubscriber(cfg)
    page = sub.subscribe(download_date=download_date or date.today())
    seen: set[str] = set()
    out: List[LiteratureItem] = []
    for raw in page.articles:
        item = _scopus_item_from_raw(raw, api_url=page.api_url)
        key_u = item.doi or item.url
        if not key_u or key_u in seen:
            continue
        seen.add(key_u)
        out.append(item)
    return out


def fetch_springer_literature(
    *,
    domains: Optional[Iterable[str]] = None,
    max_articles_per_domain: int = 5,
    cutoff_days: int = 14,
    download_date: Optional[date] = None,
) -> List[LiteratureItem]:
    """
    功能：拉取 Springer 搜索/文章页并转为 LiteratureItem。
    输入：domains 默认 Machine Learning + Artificial Intelligence；每域文章上限。
    输出：LiteratureItem 列表。
    """
    cfg = SpringerConfig(
        max_pages_per_domain=3,
        max_articles_per_domain=max(1, max_articles_per_domain),
        cutoff_days=cutoff_days,
    )
    sub = SpringerSubscriber(cfg)
    page = sub.subscribe(
        domains=domains or ["Machine Learning", "Artificial Intelligence"],
        download_date=download_date or date.today(),
    )
    seen: set[str] = set()
    out: List[LiteratureItem] = []
    for raw in page.articles:
        item = _springer_item_from_raw(raw)
        if not item.url or item.url in seen:
            continue
        seen.add(item.url)
        out.append(item)
    return out


def parse_literature_published_at(raw: Optional[str]) -> Optional[datetime]:
    """
    将 LiteratureItem.published_at 规范化为 UTC naive datetime（供 MySQL）。

    输入：ISO8601（可含时区）、YYYY-MM-DD 或空格分隔 datetime。
    输出：无时区的 UTC datetime；无法解析则 None。
    """
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None

    # 优先解析带时区的 ISO8601，避免截断偏移量导致日期/时刻错误。
    try:
        from datetime import timezone

        normalized = s.replace("Z", "+00:00")
        if "T" in normalized and (
            normalized.endswith(("+00:00", "-00:00"))
            or (len(normalized) >= 6 and normalized[-6] in "+-" and normalized[-3] == ":")
        ):
            dt_aware = datetime.fromisoformat(normalized)
            if dt_aware.tzinfo is not None:
                return dt_aware.astimezone(timezone.utc).replace(tzinfo=None)
            return dt_aware
    except ValueError:
        pass

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            if fmt == "%Y-%m-%d":
                return datetime.strptime(s[:10], fmt)
            slice_len = 16 if fmt == "%Y-%m-%d %H:%M" else 19
            return datetime.strptime(s[:slice_len], fmt)
        except ValueError:
            continue
    return None
