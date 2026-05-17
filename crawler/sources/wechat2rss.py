"""
微信公众号 RSS 适配器（via wechat2rss.xlab.app）。

功能：通过 feedparser 解析 wechat2rss 提供的公众号 RSS 源，
     将每篇推文映射为标准 RawArticle（与 Guardian/NYT/新华/新浪一致）。
输入：公众号 RSS URL 与公众号名称；fetch_body=False 时仅用 RSS content/summary。
输出：RawArticle 列表；副作用：HTTP 请求（RSS XML）。
上下游：被 orchestrator.async_sync_wechat_rss 调用；下游 LLM 抽取 + MySQL 入库。
配置扩展：在 WECHAT_RSS_POOL 中增删公众号，无需修改编排层代码。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Dict, Iterable, List, Optional

import feedparser
from bs4 import BeautifulSoup

from crawler.sources.guardian import RawArticle

# ---------------------------------------------------------------------------
# 公众号 RSS 池配置
# 说明：key 为展示名称（写入 section_name），value 为 wechat2rss 的 RSS URL。
# 中国信息安全（HTTP 500 已失效，暂不启用）。
# 扩展：在此添加新公众号 RSS URL 即可，无需修改编排层代码。
# ---------------------------------------------------------------------------
WECHAT_RSS_POOL: Dict[str, str] = {
    "量子位": "https://wechat2rss.xlab.app/feed/7131b577c61365cb47e81000738c10d872685908.xml",
    "新智元": "https://wechat2rss.xlab.app/feed/ede30346413ea70dbef5d485ea5cbb95cca446e7.xml",
    "机器之心": "https://wechat2rss.xlab.app/feed/51e92aad2728acdd1fda7314be32b16639353001.xml",
    "信息安全国家工程研究中心": "https://wechat2rss.xlab.app/feed/7caad9bdb6b168fe174bc815a9b44b7f52d7198b.xml",
    # 暂停：中国信息安全 RSS HTTP 500
    # "中国信息安全": "https://wechat2rss.xlab.app/feed/567cb1a8cf49f3e2c141d9d8085712f42ffc2fef.xml",
}

class WechatRSSError(Exception):
    """RSS 拉取或解析失败。"""

    def __init__(self, message: str, *, feed_name: str = "", status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.feed_name = feed_name
        self.status_code = status_code


@dataclass(frozen=True)
class WechatFeedPage:
    """单个公众号 RSS 拉取结果。"""

    articles: List[RawArticle]
    feed_name: str
    rss_url: str
    status_code: int
    entry_count: int


def _clean_html(raw_html: str) -> str:
    """去除 HTML 标签，保留纯文本；微信推文 content 为富文本 HTML。"""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(separator="\n", strip=True)).strip()


def _normalize_pub_date(raw: Optional[str]) -> Optional[str]:
    """
    功能：将 RSS 的 RFC 2822 时间字符串（如 "Fri, 15 May 2026 08:02:00 +0800"）
         规范化为 "YYYY-MM-DD HH:MM:SS"，与 orchestrator 的日期解析格式一致。
    输入：RSS entry 的 published 字段（可能为 None）。
    输出：规范化字符串，无法解析时返回 None。
    """
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    # 尝试直接匹配 ISO 风格
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}(?::\d{2})?)", raw)
    if m:
        t = m.group(2)
        if len(t) == 5:
            t += ":00"
        return f"{m.group(1)} {t}"
    return None


def _entry_to_raw_article(entry: object, feed_name: str, rss_url: str) -> Optional[RawArticle]:
    """
    功能：将单条 feedparser entry 映射为 RawArticle。
    输入：feedparser entry 对象、公众号名称、RSS URL。
    输出：RawArticle；web_url 为空时返回 None（调用方跳过）。
    """
    web_url = (getattr(entry, "link", None) or "").strip()
    if not web_url:
        return None

    title = _clean_html(getattr(entry, "title", None) or "") or "(no title)"

    # 全文优先（wechat2rss 大多提供 content 字段），退而用 summary
    body_text: Optional[str] = None
    content_list = getattr(entry, "content", None)
    if content_list and isinstance(content_list, list) and content_list[0].get("value"):
        body_text = _clean_html(content_list[0]["value"]) or None

    summary_raw = getattr(entry, "summary", None) or ""
    summary_text = _clean_html(summary_raw) or None

    # trail_text 用 summary；若 summary 与 title 完全一致或过短则留 None
    trail_text = summary_text
    if trail_text and (trail_text == title or len(trail_text) < 10):
        trail_text = None

    pub_raw = (
        getattr(entry, "published", None)
        or getattr(entry, "pubDate", None)
        or getattr(entry, "updated", None)
    )

    return RawArticle(
        web_url=web_url,
        title=title,
        trail_text=trail_text,
        body_text=body_text,
        web_publication_date=_normalize_pub_date(pub_raw),
        section_name=f"WeChat / {feed_name}",
        api_url=rss_url,
        guardian_id=None,
    )


def fetch_wechat_feed(
    feed_name: str,
    rss_url: str,
    *,
    max_articles: int = 20,
) -> WechatFeedPage:
    """
    功能：拉取单个公众号 RSS，解析为 RawArticle 列表。
    输入：feed_name 公众号名称；rss_url wechat2rss XML 地址；max_articles 最多取前 N 篇。
    输出：WechatFeedPage；HTTP 非 200 时抛 WechatRSSError。
    副作用：一次 HTTP GET（feedparser 内部使用 urllib）。
    上下游：被 fetch_wechat_pool 调用；供 orchestrator 在线程池内执行。
    """
    parsed = feedparser.parse(rss_url)
    status = parsed.get("status", 0)

    if status and status >= 400:
        raise WechatRSSError(
            f"RSS HTTP {status}: {rss_url}",
            feed_name=feed_name,
            status_code=status,
        )

    entries = list(parsed.entries or [])
    if max_articles > 0:
        entries = entries[:max_articles]

    articles: List[RawArticle] = []
    for entry in entries:
        art = _entry_to_raw_article(entry, feed_name, rss_url)
        if art:
            articles.append(art)

    return WechatFeedPage(
        articles=articles,
        feed_name=feed_name,
        rss_url=rss_url,
        status_code=status or 200,
        entry_count=len(parsed.entries or []),
    )


def fetch_wechat_pool(
    pool: Optional[Dict[str, str]] = None,
    *,
    max_articles_per_feed: int = 20,
    feed_names: Optional[Iterable[str]] = None,
    feed_delay_sec: float = 0.5,
) -> List[RawArticle]:
    """
    功能：遍历公众号池，拉取所有 RSS，去重后返回 RawArticle 列表。
    输入：pool 默认 WECHAT_RSS_POOL；feed_names 可选只拉指定公众号（白名单）；
         max_articles_per_feed 每个公众号最多取文章数；feed_delay_sec 公众号间延迟。
    输出：RawArticle 列表（按 web_url 去重）；失败的 feed 打印警告后跳过。
    副作用：多次 HTTP GET。
    上下游：被 orchestrator.async_sync_wechat_rss 在线程池内调用。
    """
    target_pool = pool or WECHAT_RSS_POOL
    if feed_names is not None:
        allowed = set(feed_names)
        target_pool = {k: v for k, v in target_pool.items() if k in allowed}

    all_rows: List[RawArticle] = []
    seen: set[str] = set()

    for index, (name, url) in enumerate(target_pool.items()):
        if index > 0:
            time.sleep(max(0.0, float(feed_delay_sec)))
        try:
            page = fetch_wechat_feed(name, url, max_articles=max_articles_per_feed)
            for art in page.articles:
                if art.web_url not in seen:
                    seen.add(art.web_url)
                    all_rows.append(art)
        except WechatRSSError as e:
            print(f"⚠️ [{name}] RSS 失败（跳过）: {e}")
        except Exception as e:
            print(f"⚠️ [{name}] 异常（跳过）: {type(e).__name__}: {e}")

    return all_rows
