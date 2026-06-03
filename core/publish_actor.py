"""
功能：政策/报告发布地理与主体字段的 hint 解析、LLM 输出归一化与校验。

输入：articles.source、section_name、正文 metadata 行；LLM 抽取 dict。
输出：publish_country/region/international_orgs/publish_authority 规范值。
上下游：crawler/extraction、orchestrator、scripts/backfill_publish_geo.py。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# 主权国家：中文规范名
SOVEREIGN_COUNTRY_CN: Dict[str, str] = {
    "CN": "中国",
    "CHN": "中国",
    "CHINA": "中国",
    "PRC": "中国",
    "中国": "中国",
    "US": "美国",
    "USA": "美国",
    "UNITED STATES": "美国",
    "美国": "美国",
    "UK": "英国",
    "GB": "英国",
    "UNITED KINGDOM": "英国",
    "英国": "英国",
    "IN": "印度",
    "INDIA": "印度",
    "印度": "印度",
    "BR": "巴西",
    "BRAZIL": "巴西",
    "巴西": "巴西",
    "JP": "日本",
    "JAPAN": "日本",
    "日本": "日本",
    "KR": "韩国",
    "KOREA": "韩国",
    "韩国": "韩国",
    "RU": "俄罗斯",
    "RUSSIA": "俄罗斯",
    "俄罗斯": "俄罗斯",
    "DE": "德国",
    "GERMANY": "德国",
    "德国": "德国",
    "FR": "法国",
    "FRANCE": "法国",
    "法国": "法国",
}

# policy:XX → 国家/区域 hint
POLICY_SOURCE_HINTS: Dict[str, Dict[str, str]] = {
    "policy:US": {"publish_country": "美国", "publish_authority_hint": "美国联邦政府"},
    "policy:UK": {"publish_country": "英国", "publish_authority_hint": "英国政府"},
    "policy:EU": {"publish_region": "欧盟", "publish_authority_hint": "欧盟机构"},
    "policy:IN": {"publish_country": "印度", "publish_authority_hint": "印度政府"},
    "policy:BR": {"publish_country": "巴西", "publish_authority_hint": "巴西政府"},
}

# 中国境内地区：强制 publish_country=中国
CN_REGION_ALIASES: Dict[str, str] = {
    "台湾": "台湾",
    "台灣": "台湾",
    "taiwan": "台湾",
    "台北": "台湾",
    "香港": "香港",
    "hong kong": "香港",
    "hk": "香港",
    "澳门": "澳门",
    "澳門": "澳门",
    "macau": "澳门",
    "macao": "澳门",
}

# 非主权区域（无 publish_country 时可用 region）
SUPRANATIONAL_REGIONS = frozenset({"欧盟", "欧洲联盟", "EU", "European Union"})

INTERNATIONAL_ORG_ALIASES: Dict[str, str] = {
    "un": "联合国",
    "united nations": "联合国",
    "联合国": "联合国",
    "who": "世界卫生组织",
    "世界卫生组织": "世界卫生组织",
    "wto": "世界贸易组织",
    "世界贸易组织": "世界贸易组织",
    "oecd": "经合组织",
    "经合组织": "经合组织",
    "iso": "国际标准化组织",
    "国际标准化组织": "国际标准化组织",
    "ieee": "IEEE",
    "unesco": "联合国教科文组织",
    "联合国教科文组织": "联合国教科文组织",
    "icao": "国际民航组织",
    "itu": "国际电信联盟",
}


@dataclass
class CrawlHints:
    """爬虫侧传给 LLM 的发布地理线索。"""

    source_tag: str = ""
    section_name: str = ""
    country_code: str = ""
    creator: str = ""
    publish_country: str = ""
    publish_region: str = ""
    publish_authority_hint: str = ""

    def to_prompt_block(self) -> str:
        lines = ["【采集线索——仅供参考，须结合正文核实】"]
        if self.source_tag:
            lines.append(f"SourceTag: {self.source_tag}")
        if self.section_name:
            lines.append(f"Section: {self.section_name}")
        if self.country_code:
            lines.append(f"CountryCode: {self.country_code}")
        if self.creator:
            lines.append(f"Creator: {self.creator}")
        if self.publish_country:
            lines.append(f"PublishCountryHint: {self.publish_country}")
        if self.publish_region:
            lines.append(f"PublishRegionHint: {self.publish_region}")
        if self.publish_authority_hint:
            lines.append(f"PublishAuthorityHint: {self.publish_authority_hint}")
        return "\n".join(lines) if len(lines) > 1 else ""


def _norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _as_str_list(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        t = val.strip()
        if not t:
            return []
        if "," in t or "、" in t:
            parts = re.split(r"[、,，;；]", t)
            return [p.strip() for p in parts if p.strip()]
        return [t]
    return []


def normalize_country(raw: str) -> str:
    """主权国家名 → 中文规范名；台湾/香港/澳门不作为国家返回。"""
    t = (raw or "").strip()
    if not t:
        return ""
    key = _norm_key(t)
    if key in ("台湾", "taiwan", "台湾国", "中华民国"):
        return "中国"
    if key in CN_REGION_ALIASES:
        return "中国"
    up = t.upper()
    if up in SOVEREIGN_COUNTRY_CN:
        return SOVEREIGN_COUNTRY_CN[up]
    if t in SOVEREIGN_COUNTRY_CN.values():
        return t
    # 常见英文
    for code, cn in SOVEREIGN_COUNTRY_CN.items():
        if _norm_key(code) == key or _norm_key(cn) == key:
            return cn
    return t[:64]


def normalize_region(raw: str, country: str = "") -> str:
    t = (raw or "").strip()
    if not t:
        return ""
    key = _norm_key(t)
    if key in CN_REGION_ALIASES:
        return CN_REGION_ALIASES[key]
    if t in CN_REGION_ALIASES.values():
        return t
    if key in ("eu", "european union", "欧洲联盟"):
        return "欧盟"
    if t in SUPRANATIONAL_REGIONS or _norm_key(t) in {_norm_key(x) for x in SUPRANATIONAL_REGIONS}:
        return "欧盟"
    if country == "中国" and key in ("mainland", "中国大陆", "内地"):
        return "中国大陆"
    return t[:128]


def normalize_international_orgs(raw: Any) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in _as_str_list(raw):
        key = _norm_key(item)
        canon = INTERNATIONAL_ORG_ALIASES.get(key, item)
        if canon and canon not in seen:
            seen.add(canon)
            out.append(canon[:128])
    return out[:12]


def apply_china_region_rules(country: str, region: str, text_blob: str = "") -> tuple[str, str]:
    """
    台湾/香港/澳门规则：country=中国，region 对应分区。
    若文本明确涉台/港/澳而 country 误为独立国家，强制纠正。
    """
    blob = (text_blob or "").lower()
    region_out = region
    country_out = country

    tw_signals = ("台湾", "台灣", "taiwan", "台北", "中华民国")
    hk_signals = ("香港", "hong kong", "hk")
    mo_signals = ("澳门", "澳門", "macau", "macao")

    if any(s.lower() in blob for s in tw_signals) or _norm_key(region) in ("台湾", "taiwan"):
        country_out = "中国"
        region_out = "台湾"
    elif any(s.lower() in blob for s in hk_signals) or _norm_key(region) == "香港":
        country_out = "中国"
        region_out = "香港"
    elif any(s.lower() in blob for s in mo_signals) or _norm_key(region) == "澳门":
        country_out = "中国"
        region_out = "澳门"

    if country_out in ("台湾", "Taiwan", "中华民国"):
        country_out = "中国"
        if not region_out:
            region_out = "台湾"

    return country_out, region_out


def _parse_metadata_lines(body_text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in (body_text or "").splitlines():
        line = line.strip()
        for sep in (":", "："):
            if sep not in line:
                continue
            k, _, v = line.partition(sep)
            k, v = k.strip(), v.strip()
            if not k or not v:
                continue
            kl = k.lower()
            if kl in ("country", "国家", "发布国代码"):
                out["country_code"] = v
            elif kl in ("creator", "author", "发布机构"):
                out["creator"] = v
            elif kl in ("source", "来源"):
                out["source_line"] = v
            break
    return out


def hints_from_raw_article(
    *,
    source: str = "",
    section_name: str = "",
    body_text: str = "",
) -> CrawlHints:
    """
    功能：从 source 标签、section、正文 metadata 组装 CrawlHints。
    输入：articles.source 或 policy:XX；RawArticle 字段。
    输出：CrawlHints；无副作用。
    """
    tag = (source or "").strip()
    meta = _parse_metadata_lines(body_text or "")
    hints = CrawlHints(
        source_tag=tag,
        section_name=(section_name or "").strip(),
        country_code=meta.get("country_code", ""),
        creator=meta.get("creator", ""),
    )

    if tag in POLICY_SOURCE_HINTS:
        ph = POLICY_SOURCE_HINTS[tag]
        hints.publish_country = ph.get("publish_country", "")
        hints.publish_region = ph.get("publish_region", "")
        hints.publish_authority_hint = ph.get("publish_authority_hint", "")

    if not hints.publish_country and hints.country_code:
        cc = hints.country_code.upper()
        if cc == "EU":
            hints.publish_region = "欧盟"
        elif cc in SOVEREIGN_COUNTRY_CN:
            hints.publish_country = SOVEREIGN_COUNTRY_CN[cc]

    if not hints.publish_country and hints.source_tag.startswith("policy:"):
        code = hints.source_tag.split(":", 1)[-1].upper()
        if code == "EU":
            hints.publish_region = "欧盟"
        elif code in SOVEREIGN_COUNTRY_CN:
            hints.publish_country = SOVEREIGN_COUNTRY_CN[code]

    # 中文媒体默认中国
    if tag in ("xinhua_tech", "sina_tech") and not hints.publish_country:
        hints.publish_country = "中国"

    return hints


def normalize_publish_fields(
    art: Dict[str, Any],
    hints: Optional[CrawlHints] = None,
    *,
    text_blob: str = "",
) -> Dict[str, Any]:
    """
    功能：合并 LLM 输出与 crawl hints，写入规范四字段到 art dict。
    输入：抽取 dict；可选 CrawlHints 与原文摘要（台湾规则）。
    输出：同一 dict（mutate）；供 save_extraction。
    """
    if not art or not art.get("is_relevant"):
        return art

    h = hints or CrawlHints()
    blob = text_blob or str(art.get("main_topic") or "")

    country = normalize_country(str(art.get("publish_country") or ""))
    region = normalize_region(str(art.get("publish_region") or ""), country)
    authority = str(art.get("publish_authority") or "").strip()[:256]
    orgs = normalize_international_orgs(
        art.get("international_orgs") or art.get("international_orgs_json")
    )

    if not country and h.publish_country:
        country = h.publish_country
    if not region and h.publish_region:
        region = h.publish_region
    if not authority and h.publish_authority_hint:
        authority = h.publish_authority_hint[:256]
    if not authority and h.creator:
        authority = h.creator[:256]

    country, region = apply_china_region_rules(country, region, blob)

    # 欧盟：region 优先，不强行填主权国
    if region == "欧盟" and country in ("欧盟", "EU", "欧洲联盟"):
        country = ""

    # 从 entities 移除与 publish_authority 重复项
    ents = _as_str_list(art.get("entities"))
    if authority:
        auth_l = _norm_key(authority)
        ents = [e for e in ents if _norm_key(e) != auth_l and auth_l not in _norm_key(e)]

    art["publish_country"] = country or None
    art["publish_region"] = region or None
    art["publish_authority"] = authority or None
    art["international_orgs"] = orgs
    art["entities"] = ents[:50]
    return art


def validate_publish_fields(art: Dict[str, Any]) -> List[str]:
    """policy/report 缺关键字段时返回警告文案列表（不阻断入库）。"""
    ct = str(art.get("content_type") or "").lower()
    if ct not in ("policy", "report"):
        return []
    warnings: List[str] = []
    country = art.get("publish_country") or ""
    region = art.get("publish_region") or ""
    authority = art.get("publish_authority") or ""
    if not country and not region:
        warnings.append("缺少 publish_country 或 publish_region")
    if not authority:
        warnings.append("缺少 publish_authority")
    return warnings
