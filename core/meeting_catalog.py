"""
功能：加载重大会议名录（JSON 种子），提供别名匹配与届次推断。
输入：data/conference_catalog.json；可选 MySQL conference_catalog 表（seed 后）。
输出：CatalogSeries / CatalogEvent 结构；match_catalog_key() 返回匹配结果。
上下游：services/meeting_event_linker、scripts/seed_meeting_catalog.py、engine/meeting_brief。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CATALOG_JSON = _DATA_DIR / "conference_catalog.json"

_YEAR_RE = re.compile(r"(20[2-3][0-9])")


@dataclass
class CatalogEventSeed:
    """种子文件中的一届会议实例。"""

    edition_label: str
    edition_year: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    location: str = ""
    host: str = ""
    official_url: str = ""
    status: str = "scheduled"
    notes: str = ""
    crawl_urls: List[str] = field(default_factory=list)
    participating_countries: List[str] = field(default_factory=list)
    outcomes_summary: str = ""


@dataclass
class CatalogSeries:
    """会议系列（名录一项）。"""

    catalog_key: str
    series_name: str
    category: str = ""
    is_major: bool = True
    sort_order: int = 0
    aliases: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    official_urls: List[str] = field(default_factory=list)
    reference_url: str = ""
    events: List[CatalogEventSeed] = field(default_factory=list)


@dataclass
class CatalogMatch:
    """规则匹配结果。"""

    catalog_key: str
    score: float
    edition_year: Optional[int] = None
    matched_alias: str = ""


def _norm_text(s: str) -> str:
    t = (s or "").lower().strip()
    t = re.sub(r"\s+", " ", t)
    return t


def load_catalog_document() -> Dict[str, Any]:
    """
    功能：读取名录 JSON 文档。
    输入：无（固定路径 conference_catalog.json）。
    输出：dict；文件缺失时返回 {"series": []}。
    """
    if not _CATALOG_JSON.is_file():
        return {"series": []}
    return json.loads(_CATALOG_JSON.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_catalog_series() -> List[CatalogSeries]:
    """解析并缓存全部系列。"""
    doc = load_catalog_document()
    ref = str(doc.get("reference_url") or "")
    out: List[CatalogSeries] = []
    for raw in doc.get("series") or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("catalog_key") or "").strip()
        if not key:
            continue
        evs: List[CatalogEventSeed] = []
        for ev in raw.get("events") or []:
            if not isinstance(ev, dict):
                continue
            yr = ev.get("edition_year")
            edition_year = int(yr) if yr is not None and str(yr).isdigit() else None
            crawl_urls = [
                str(u).strip() for u in (ev.get("crawl_urls") or []) if str(u).strip()
            ]
            countries = [
                str(c).strip()
                for c in (ev.get("participating_countries") or [])
                if str(c).strip()
            ]
            notes_parts = [str(ev.get("notes") or "").strip()]
            outcomes = str(ev.get("outcomes_summary") or "").strip()
            if outcomes:
                notes_parts.append(f"成果摘要：{outcomes}")
            evs.append(
                CatalogEventSeed(
                    edition_label=str(ev.get("edition_label") or "")[:256],
                    edition_year=edition_year,
                    start_date=str(ev.get("start_date") or "")[:10] or None,
                    end_date=str(ev.get("end_date") or "")[:10] or None,
                    location=str(ev.get("location") or "")[:256],
                    host=str(ev.get("host") or "")[:256],
                    official_url=str(ev.get("official_url") or "")[:1024],
                    status=str(ev.get("status") or "scheduled")[:32],
                    notes="；".join(p for p in notes_parts if p),
                    crawl_urls=crawl_urls,
                    participating_countries=countries,
                    outcomes_summary=outcomes,
                )
            )
        aliases = [str(a).strip() for a in (raw.get("aliases") or []) if str(a).strip()]
        topics = [str(t).strip() for t in (raw.get("topics") or []) if str(t).strip()]
        urls = [str(u).strip() for u in (raw.get("official_urls") or []) if str(u).strip()]
        out.append(
            CatalogSeries(
                catalog_key=key,
                series_name=str(raw.get("series_name") or key)[:256],
                category=str(raw.get("category") or "")[:128],
                is_major=bool(raw.get("is_major", True)),
                sort_order=int(raw.get("sort_order") or 0),
                aliases=aliases,
                topics=topics,
                official_urls=urls,
                reference_url=ref,
                events=evs,
            )
        )
    out.sort(key=lambda s: s.sort_order)
    return out


def catalog_keys_for_prompt() -> str:
    """供 LLM 抽取提示：catalog_key 与系列名列表。"""
    lines = []
    for s in load_catalog_series():
        lines.append(f"- {s.catalog_key}: {s.series_name}")
    return "\n".join(lines) if lines else "（名录为空）"


def _extract_year_from_text(text: str) -> Optional[int]:
    m = _YEAR_RE.search(text or "")
    if not m:
        return None
    y = int(m.group(1))
    if 2020 <= y <= 2035:
        return y
    return None


def match_catalog_key(
    *,
    title: str = "",
    summary: str = "",
    main_topic: str = "",
    tags: Optional[List[str]] = None,
    entities: Optional[List[str]] = None,
    llm_catalog_key: str = "",
    edition_hint: str = "",
) -> Optional[CatalogMatch]:
    """
    功能：在名录别名中匹配 catalog_key。
    输入：文章标题/摘要/抽取字段；可选 LLM 给出的 catalog_key。
    输出：CatalogMatch 或 None。
    """
    llm_key = (llm_catalog_key or "").strip().lower()
    if llm_key:
        for s in load_catalog_series():
            if s.catalog_key.lower() == llm_key:
                yr = _extract_year_from_text(edition_hint or title or main_topic)
                return CatalogMatch(catalog_key=s.catalog_key, score=0.92, edition_year=yr, matched_alias=llm_key)

    parts: List[str] = [title, summary, main_topic, edition_hint]
    if tags:
        parts.extend(tags)
    if entities:
        parts.extend(entities)
    blob = _norm_text(" ".join(p for p in parts if p))
    if not blob:
        return None

    best: Optional[CatalogMatch] = None
    for series in load_catalog_series():
        for alias in series.aliases:
            al = _norm_text(alias)
            if len(al) < 4:
                continue
            if al in blob or blob in al:
                score = min(1.0, len(al) / max(len(blob), 1) + 0.5)
                yr = _extract_year_from_text(blob)
                cand = CatalogMatch(
                    catalog_key=series.catalog_key,
                    score=score,
                    edition_year=yr,
                    matched_alias=alias,
                )
                if best is None or cand.score > best.score:
                    best = cand
    return best


def find_seed_event(series: CatalogSeries, edition_year: Optional[int]) -> Optional[CatalogEventSeed]:
    """按届次年份选取种子事件；无年份时返回首条。"""
    if edition_year is not None:
        for ev in series.events:
            if ev.edition_year == edition_year:
                return ev
    for ev in series.events:
        if ev.edition_year is None:
            return ev
    return series.events[0] if series.events else None


def get_series_by_key(catalog_key: str) -> Optional[CatalogSeries]:
    k = (catalog_key or "").strip().lower()
    for s in load_catalog_series():
        if s.catalog_key.lower() == k:
            return s
    return None


def all_major_series() -> List[CatalogSeries]:
    return [s for s in load_catalog_series() if s.is_major]


def build_event_search_query(series: CatalogSeries, event: CatalogEventSeed) -> str:
    """
    功能：为 NYT/Guardian 构造按届检索 query。
    输入：系列与届次种子。
    输出：空格分隔检索串（≤128 字符）。
    """
    parts: List[str] = []
    if event.edition_label:
        parts.append(event.edition_label)
    if series.series_name and series.series_name not in " ".join(parts):
        parts.append(series.series_name)
    for alias in series.aliases[:3]:
        if alias and alias not in parts:
            parts.append(alias)
    q = " ".join(parts)
    return q[:128] if q else series.catalog_key


@dataclass(frozen=True)
class CrawlUrlItem:
    """待 agentic 抓取的 URL 项。"""

    url: str
    catalog_key: str
    edition_label: str


def iter_catalog_crawl_urls() -> List[CrawlUrlItem]:
    """
    功能：汇总名录中全部待抓取 URL（系列 official_urls + 届次 official_url + crawl_urls）。
    输出：去重后的 CrawlUrlItem 列表。
    """
    seen: set[str] = set()
    out: List[CrawlUrlItem] = []
    for series in load_catalog_series():
        for u in series.official_urls:
            u = u.strip()
            if u and u not in seen:
                seen.add(u)
                out.append(
                    CrawlUrlItem(url=u, catalog_key=series.catalog_key, edition_label="")
                )
        for ev in series.events:
            for u in [ev.official_url] + list(ev.crawl_urls):
                u = (u or "").strip()
                if u and u not in seen:
                    seen.add(u)
                    out.append(
                        CrawlUrlItem(
                            url=u,
                            catalog_key=series.catalog_key,
                            edition_label=ev.edition_label,
                        )
                    )
    return out


def reload_catalog_cache() -> None:
    """种子文件更新后清除解析缓存。"""
    load_catalog_series.cache_clear()
