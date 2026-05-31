"""
功能：三子系统新闻/文献来源注册表与筛选范围（门户左栏来源面板唯一配置源）。

输入：track key（policy | meeting | literature）、DB source 字符串、前端 filter key 列表。
输出：中文标签、分组、SQL 过滤片段；无副作用。
上下游：core/mysql_monitor_tracks、api/services/portal_data、web SourceFilterPanel。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 文献三源（literature_items.source）
LITERATURE_SOURCES = frozenset({"arxiv", "scopus", "springer"})

# 五国政策官方源（articles.source）
POLICY_OFFICIAL_SOURCES = frozenset(
    {"policy:US", "policy:UK", "policy:EU", "policy:IN", "policy:BR"}
)

# 新闻/RSS 固定源
NEWS_FIXED_SOURCES = frozenset(
    {"guardian", "nyt", "xinhua_tech", "sina_tech"}
)

WECHAT_PREFIX = "wechat_rss:"


@dataclass(frozen=True)
class SourceOptionDef:
    """左栏筛选项定义。"""

    key: str
    label: str
    group: str
    db_match: str  # exact | prefix | prefix_all_wechat


# 静态筛选项（政策页完整集；会议页去掉 policy_official 组）
STATIC_SOURCE_DEFS: Dict[str, SourceOptionDef] = {
    "guardian": SourceOptionDef("guardian", "The Guardian", "rss_api", "exact"),
    "nyt": SourceOptionDef("nyt", "The New York Times", "rss_api", "exact"),
    "xinhua_tech": SourceOptionDef("xinhua_tech", "新华网科技", "cn_media", "exact"),
    "sina_tech": SourceOptionDef("sina_tech", "新浪科技", "cn_media", "exact"),
    "wechat_rss": SourceOptionDef("wechat_rss", "微信公众号（全部）", "wechat", "prefix_all_wechat"),
    "policy:US": SourceOptionDef("policy:US", "美国 · Federal Register", "policy_official", "exact"),
    "policy:UK": SourceOptionDef("policy:UK", "英国 · GOV.UK", "policy_official", "exact"),
    "policy:EU": SourceOptionDef("policy:EU", "欧盟 · EUR-Lex", "policy_official", "exact"),
    "policy:IN": SourceOptionDef("policy:IN", "印度 · PRS / LexML", "policy_official", "exact"),
    "policy:BR": SourceOptionDef("policy:BR", "巴西 · 官方法规", "policy_official", "exact"),
    "papers_policy": SourceOptionDef("papers_policy", "政策文献库（迁移）", "policy_official", "exact"),
    "arxiv": SourceOptionDef("arxiv", "arXiv 预印本", "literature", "exact"),
    "scopus": SourceOptionDef("scopus", "Scopus 引文库", "literature", "exact"),
    "springer": SourceOptionDef("springer", "Springer 期刊", "literature", "exact"),
}

# 文献左栏每项附简短说明（展示用）
LITERATURE_SOURCE_HINTS: Dict[str, str] = {
    "arxiv": "开放预印本论文库",
    "scopus": "爱思唯尔摘要与引文数据库",
    "springer": "Springer Nature 学术出版",
}

GROUP_LABELS: Dict[str, str] = {
    "rss_api": "RSS/API 来源",
    "cn_media": "中文媒体",
    "wechat": "微信公众号",
    "policy_official": "国家政策官方",
    "literature": "文献数据库",
    "other": "其他来源",
}

TRACK_PANEL_TITLE: Dict[str, str] = {
    "policy": "新闻来源",
    "meeting": "新闻来源",
    "literature": "文献来源",
}

# 各 track 静态 key 顺序（动态 wechat 子号插入 wechat 组）
TRACK_STATIC_KEYS: Dict[str, List[str]] = {
    "policy": [
        "guardian",
        "nyt",
        "xinhua_tech",
        "sina_tech",
        "wechat_rss",
        "policy:US",
        "policy:UK",
        "policy:EU",
        "policy:IN",
        "policy:BR",
        "papers_policy",
    ],
    "meeting": [
        "guardian",
        "nyt",
        "xinhua_tech",
        "sina_tech",
        "wechat_rss",
    ],
    "literature": ["arxiv", "scopus", "springer"],
}


def label_for_db_source(db_source: str) -> str:
    """DB source → 展示名。"""
    src = (db_source or "").strip()
    if not src:
        return "未知来源"
    if src in STATIC_SOURCE_DEFS:
        return STATIC_SOURCE_DEFS[src].label
    if src.startswith(WECHAT_PREFIX):
        name = src[len(WECHAT_PREFIX) :].strip()
        return name or "微信公众号"
    if src.startswith("policy:"):
        return STATIC_SOURCE_DEFS.get(src, SourceOptionDef(src, src, "policy_official", "exact")).label
    return src


def filter_key_for_db_source(db_source: str) -> str:
    """DB source → 筛选项 key（与 DB 值一致，便于精确过滤）。"""
    return (db_source or "").strip()


def is_db_source_allowed(track: str, db_source: str) -> bool:
    """
    功能：判断某 DB source 是否属于该 track 左栏可展示范围。
    政策：除文献三源外均可；会议：再排除五国政策源；文献：仅三源。
    """
    src = (db_source or "").strip()
    if not src:
        return False
    if track == "literature":
        return src in LITERATURE_SOURCES
    if src in LITERATURE_SOURCES:
        return False
    if track == "meeting":
        if src in POLICY_OFFICIAL_SOURCES or src == "policy" or src == "papers_policy":
            return False
        if src.startswith("policy:"):
            return False
    return True


def scope_exclude_sql_articles(track: str) -> Tuple[str, List[Any]]:
    """articles 表基础排除（无 binds 时用常量 IN）。"""
    excluded = list(LITERATURE_SOURCES)
    binds: List[Any] = list(excluded)
    extra = ""
    if track == "meeting":
        excluded.extend(sorted(POLICY_OFFICIAL_SOURCES))
        excluded.extend(["policy", "papers_policy"])
        binds = list(excluded)
        extra = " AND a.source NOT LIKE %s"
        binds.append("policy:%")
    if not excluded and not extra:
        return "", []
    placeholders = ", ".join(["%s"] * len(excluded))
    return f" AND a.source NOT IN ({placeholders}){extra}", binds


def build_sources_filter_sql(
    selected_keys: Optional[Sequence[str]],
    *,
    column: str = "a.source",
) -> Tuple[str, List[Any]]:
    """
    功能：将前端选中的 filter key 列表转为 SQL AND (... OR ...) 片段。
    输入：selected_keys 为空则不追加条件（表示全部）。
    输出：(sql_fragment, binds)。
    """
    keys = [k.strip() for k in (selected_keys or []) if k and str(k).strip()]
    if not keys:
        return "", []
    parts: List[str] = []
    binds: List[Any] = []
    for key in keys:
        if key == "wechat_rss":
            parts.append(f"{column} LIKE %s")
            binds.append(f"{WECHAT_PREFIX}%")
        elif key.startswith(WECHAT_PREFIX):
            parts.append(f"{column} = %s")
            binds.append(key)
        else:
            parts.append(f"{column} = %s")
            binds.append(key)
    return f" AND ({' OR '.join(parts)})", binds


def build_literature_sources_filter_sql(
    selected_keys: Optional[Sequence[str]],
) -> Tuple[str, List[Any]]:
    """literature_items.source 多选过滤。"""
    return build_sources_filter_sql(selected_keys, column="source")


def normalize_selected_keys(track: str, raw_keys: Optional[Sequence[str]]) -> List[str]:
    """校验 key 属于该 track 允许范围。"""
    allowed_static = set(TRACK_STATIC_KEYS.get(track, []))
    out: List[str] = []
    for k in raw_keys or []:
        key = (k or "").strip()
        if not key:
            continue
        if key in allowed_static:
            out.append(key)
            continue
        if key.startswith(WECHAT_PREFIX) and track in ("policy", "meeting"):
            out.append(key)
            continue
        if track == "literature" and key in LITERATURE_SOURCES:
            out.append(key)
    return list(dict.fromkeys(out))


def build_source_options(
    track: str,
    counts: Dict[str, int],
) -> List[Dict[str, Any]]:
    """
    功能：组装左栏筛选项（含 count），合并静态定义与 DB 动态微信公众号。
    输入：track；counts 为 db_source → 条数。
    输出：{key, label, group, group_label, count} 列表。
    """
    options: List[Dict[str, Any]] = []
    seen: set[str] = set()

    static_keys = TRACK_STATIC_KEYS.get(track, [])

    # 文献仅三源：固定顺序、扁平展示，附中文说明
    if track == "literature":
        for key in static_keys:
            defn = STATIC_SOURCE_DEFS.get(key)
            if not defn:
                continue
            cnt = counts.get(key, 0)
            options.append(
                {
                    "key": key,
                    "label": defn.label,
                    "group": defn.group,
                    "group_label": "",
                    "count": cnt,
                    "hint": LITERATURE_SOURCE_HINTS.get(key, ""),
                }
            )
        return options

    def _append(key: str, label: str, group: str, count: int) -> None:
        if key in seen:
            return
        seen.add(key)
        options.append(
            {
                "key": key,
                "label": label,
                "group": group,
                "group_label": GROUP_LABELS.get(group, group),
                "count": count,
            }
        )

    for key in static_keys:
        defn = STATIC_SOURCE_DEFS.get(key)
        if not defn:
            continue
        if defn.db_match == "prefix_all_wechat":
            cnt = sum(v for s, v in counts.items() if s.startswith(WECHAT_PREFIX))
        else:
            cnt = counts.get(key, 0)
        _append(key, defn.label, defn.group, cnt)

    if track in ("policy", "meeting"):
        for db_src, cnt in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
            if not is_db_source_allowed(track, db_src):
                continue
            if db_src.startswith(WECHAT_PREFIX) and db_src != "wechat_rss":
                feed = db_src[len(WECHAT_PREFIX) :].strip()
                if feed:
                    _append(db_src, feed, "wechat", cnt)
            elif db_src not in static_keys and db_src not in seen:
                _append(db_src, label_for_db_source(db_src), "other", cnt)

    return options
