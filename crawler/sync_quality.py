"""
功能：同步入库前的关键字段质量校验（时间、URL、文献元数据）。

输入：RawArticle / LiteratureItem。
输出：是否通过、失败原因；无副作用。
上下游：crawler.orchestrator 入库前调用。
"""

from __future__ import annotations

from typing import Tuple

from crawler.sources.guardian import RawArticle
from crawler.sources.literature import LiteratureItem


def article_has_valid_publication_date(art: RawArticle) -> bool:
    """新闻/政策 RawArticle 是否含可解析的发布时间字符串。"""
    raw = (art.web_publication_date or "").strip()
    if not raw:
        return False
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return True
    if "T" in raw and len(raw) >= 10:
        return True
    return len(raw) >= 8


def literature_required_ok(item: LiteratureItem) -> Tuple[bool, str]:
    """
    功能：文献入库必填校验。
    必填：url、title、published_at、source；摘要或期刊名或 DOI 至少一项。
    """
    if not (item.url or "").strip():
        return False, "缺少 URL"
    if not (item.title or "").strip():
        return False, "缺少标题"
    if not (item.source or "").strip():
        return False, "缺少 source"
    if not (item.published_at or "").strip():
        return False, "缺少 published_at"
    if not (item.abstract or item.publication_name or item.doi):
        return False, "缺少摘要/期刊/DOI"
    return True, ""
