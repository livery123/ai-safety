"""
信源适配器包：将第三方 API / RSS 等统一为 RawArticle 等形状。

功能：导出各信源 Content API / RSS 类型与入口，供编排器与脚本使用。
输入：无（包级说明）。
输出：见 __all__。
上下游：crawler.orchestrator、scripts；下游 LLM 抽取见 crawler.extraction。
"""

from crawler.sources.guardian import (
    DEFAULT_AI_GOVERNANCE_QUERY,
    GuardianAPIError,
    GuardianSearchPage,
    RawArticle,
    map_result_to_raw_article,
    raw_article_to_llm_context,
    search_articles,
    search_articles_multipage,
)
from crawler.sources.nyt import (
    DEFAULT_NYT_AI_GOVERNANCE_QUERY,
    NYTAPIError,
    NYTSearchPage,
    map_nyt_doc_to_raw_article,
    search_nyt_articles,
    search_nyt_articles_multipage,
)
from crawler.sources.xinhua_net import (
    XINHUA_TECH_URL,
    XinhuaNetError,
    XinhuaTechPage,
    extract_xinhua_tech_links,
    fetch_xinhua_tech_article,
    parse_xinhua_article,
    search_xinhua_tech_articles,
    search_xinhua_tech_articles_multipage,
)
from crawler.sources.sina_tech import (
    SINA_TECH_URL,
    SinaTechError,
    SinaTechPage,
    extract_sina_tech_links,
    parse_sina_tech_article,
    search_sina_tech_articles,
    search_sina_tech_articles_multipage,
)
from crawler.sources.wechat2rss import (
    WECHAT_RSS_POOL,
    WechatFeedPage,
    WechatRSSError,
    fetch_wechat_feed,
    fetch_wechat_pool,
)

__all__ = [
    "DEFAULT_AI_GOVERNANCE_QUERY",
    "DEFAULT_NYT_AI_GOVERNANCE_QUERY",
    "GuardianAPIError",
    "GuardianSearchPage",
    "NYTAPIError",
    "NYTSearchPage",
    "RawArticle",
    "WECHAT_RSS_POOL",
    "WechatFeedPage",
    "WechatRSSError",
    "SINA_TECH_URL",
    "XINHUA_TECH_URL",
    "SinaTechError",
    "SinaTechPage",
    "XinhuaNetError",
    "XinhuaTechPage",
    "fetch_wechat_feed",
    "fetch_wechat_pool",
    "extract_sina_tech_links",
    "extract_xinhua_tech_links",
    "fetch_xinhua_tech_article",
    "map_nyt_doc_to_raw_article",
    "map_result_to_raw_article",
    "parse_sina_tech_article",
    "parse_xinhua_article",
    "raw_article_to_llm_context",
    "search_articles",
    "search_articles_multipage",
    "search_nyt_articles",
    "search_nyt_articles_multipage",
    "search_sina_tech_articles",
    "search_sina_tech_articles_multipage",
    "search_xinhua_tech_articles",
    "search_xinhua_tech_articles_multipage",
]
