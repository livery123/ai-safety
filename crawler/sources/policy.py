"""
Policy crawler.

功能：从多国公开政策/法规站点抓取条目，映射为 RawArticle，供 orchestrator 入库与 LLM 抽取。
输入：国家列表、每国条数上限、download_date。
输出：PolicyFetchResult（articles + 各国错误列表）；副作用：HTTP 请求。
上下游：crawler.orchestrator.async_sync_policy；下游 articles 表。

信源：
    - US: Federal Register JSON API（AI term，可配置）+ RSS 备选
    - UK: GOV.UK AI Atom 优先；legislation.gov.uk RSS 补充
    - EU: EUR-Lex daily view + 可选关键词搜索
    - India: PRS India bill track（多状态 + 分页）
    - Brazil: LexML search（葡语 AI 检索词）
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

from crawler.sources.guardian import RawArticle


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# 印度 PRS 法案状态：Passed 以外亦可能含 AI 相关立法进程
INDIA_ALLOWED_STATUSES: frozenset[str] = frozenset(
    {"Passed", "Introduced", "Pending", "Lapsed", "Withdrawn"}
)

# LexML 葡语 AI 检索词（URL 编码前）
BRAZIL_LEXML_AI_QUERY = "inteligência artificial"


@dataclass(frozen=True)
class PolicyPage:
    """单国政策抓取结果。"""

    articles: List[RawArticle]
    article_urls: List[str]
    page_url: str
    status_code: int
    country: Optional[str] = None


@dataclass(frozen=True)
class PolicyFetchResult:
    """
    功能：多国政策抓取汇总。
    输入：由 fetch_policy_articles 构造。
    输出：articles 与 errors（某国失败不影响他国）。
    """

    articles: List[RawArticle]
    errors: List[str] = field(default_factory=list)
    page_urls: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class PolicyConfig:
    """Runtime configuration for policy crawling."""

    timeout_sec: float = 45.0
    retry_count: int = 3
    retry_delay_sec: float = 3.0
    page_delay_sec: float = 0.5
    eu_days_back: int = 14
    brazil_max_offsets_per_day: int = 20
    brazil_lookback_days: int = 120
    max_articles_per_country: int = 30
    user_agent: str = DEFAULT_USER_AGENT
    fetch_eu_full_text: bool = True
    us_use_api: bool = True
    us_api_days_back: int = 90
    eu_use_search: bool = True
    india_max_pages: int = 3
    india_allowed_statuses: frozenset[str] = INDIA_ALLOWED_STATUSES
    backfill_mode: bool = False
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    us_fr_search_term: str = "artificial intelligence"


def policy_config_from_env() -> PolicyConfig:
    """
    功能：从 core.config / 环境变量构建 PolicyConfig。
    输入：POLICY_* 环境变量（可选）。
    输出：PolicyConfig 实例；无副作用。
    上下游：fetch_policy_articles、诊断脚本。
    """
    try:
        from core import config as app_config
    except ImportError:
        return PolicyConfig()

    return PolicyConfig(
        eu_days_back=getattr(app_config, "POLICY_EU_DAYS_BACK", 14),
        brazil_max_offsets_per_day=getattr(app_config, "POLICY_BR_MAX_OFFSETS", 20),
        brazil_lookback_days=getattr(app_config, "POLICY_BR_LOOKBACK_DAYS", 120),
        fetch_eu_full_text=getattr(app_config, "POLICY_EU_FETCH_FULL_TEXT", True),
        us_use_api=getattr(app_config, "POLICY_US_USE_API", True),
        us_api_days_back=getattr(app_config, "POLICY_US_API_DAYS_BACK", 90),
        eu_use_search=getattr(app_config, "POLICY_EU_USE_SEARCH", True),
        india_max_pages=getattr(app_config, "POLICY_IN_MAX_PAGES", 3),
    )


def build_backfill_policy_config(
    *,
    date_from: date,
    date_to: date,
    max_articles_per_country: int,
    fetch_eu_full_text: bool = False,
) -> PolicyConfig:
    """
    功能：构造历史回溯专用 PolicyConfig。
    输入：date_from/date_to 时间窗、每国条数上限。
    输出：PolicyConfig（禁用 EU 全文、按窗逐日抓取）。
    上下游：backfill_policy_historical.py、orchestrator.async_sync_policy。
    """
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    window_days = max(1, (date_to - date_from).days + 1)
    base = policy_config_from_env()
    return PolicyConfig(
        timeout_sec=base.timeout_sec,
        retry_count=base.retry_count,
        retry_delay_sec=base.retry_delay_sec,
        page_delay_sec=base.page_delay_sec,
        eu_days_back=window_days,
        brazil_max_offsets_per_day=max(30, base.brazil_max_offsets_per_day),
        brazil_lookback_days=window_days + 90,
        max_articles_per_country=max(1, max_articles_per_country),
        user_agent=base.user_agent,
        fetch_eu_full_text=fetch_eu_full_text,
        us_use_api=True,
        us_api_days_back=window_days,
        eu_use_search=False,
        india_max_pages=max(5, base.india_max_pages),
        india_allowed_statuses=base.india_allowed_statuses,
        backfill_mode=True,
        date_from=date_from,
        date_to=date_to,
        us_fr_search_term=base.us_fr_search_term,
    )


class PolicyError(Exception):
    """Raised when a policy source request or parse step fails."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PolicySubscriber:
    """Subscriber for public policy/legal sources."""

    DEFAULT_COUNTRIES = ["US", "UK", "EU", "IN", "BR"]

    FEDERAL_REGISTER_RSS = "https://www.federalregister.gov/api/v1/documents.rss"
    FEDERAL_REGISTER_API = "https://www.federalregister.gov/api/v1/documents.json"
    UK_LEGISLATION_RSS = "https://www.legislation.gov.uk/new/data.feed"
    UK_GOVUK_POLICY_ATOM = (
        "https://www.gov.uk/search/policy-papers-and-consultations.atom?order=updated-newest"
    )
    UK_GOVUK_AI_ATOM = (
        "https://www.gov.uk/search/policy-papers-and-consultations.atom"
        "?keywords=artificial+intelligence"
        "&order=updated-newest"
    )
    EUR_LEX_BASE = "https://eur-lex.europa.eu"
    PRS_BILL_TRACK_URL = "https://prsindia.org/billtrack"
    PRS_BASE = "https://prsindia.org"
    LEXML_BASE = "https://www.lexml.gov.br"

    def __init__(self, config: Optional[PolicyConfig] = None) -> None:
        self.config = config or PolicyConfig()

    def _backfill_window(self) -> Tuple[Optional[date], Optional[date]]:
        """回溯模式下的 [date_from, date_to]。"""
        if not self.config.backfill_mode:
            return None, None
        return self.config.date_from, self.config.date_to

    @classmethod
    def _article_pub_date(cls, art: RawArticle) -> Optional[date]:
        raw = art.web_publication_date
        if not raw:
            return None
        return cls._parse_date(str(raw))

    def _filter_window(self, articles: List[RawArticle]) -> List[RawArticle]:
        """回溯模式下按发布日期过滤；无日期条目保留供 LLM 判断。"""
        df, dt = self._backfill_window()
        if not df or not dt:
            return articles
        kept: List[RawArticle] = []
        for art in articles:
            pub = self._article_pub_date(art)
            if pub is None or (df <= pub <= dt):
                kept.append(art)
        return kept

    def subscribe(
        self,
        *,
        countries: Optional[Iterable[str]] = None,
        download_date: Optional[date] = None,
    ) -> PolicyFetchResult:
        """
        功能：按国家依次抓取并合并；单国失败记 errors 并继续。
        输入：countries 默认全池；download_date 默认今天。
        输出：PolicyFetchResult。
        """
        selected_countries = list(countries or self.DEFAULT_COUNTRIES)
        download_date = download_date or date.today()

        all_articles: List[RawArticle] = []
        all_page_urls: List[str] = []
        errors: List[str] = []
        seen_urls: set[str] = set()

        with self._new_http_client() as client:
            for country in selected_countries:
                code = country.upper()
                try:
                    page = self.subscribe_country(
                        client=client,
                        country=code,
                        download_date=download_date,
                    )
                except PolicyError as exc:
                    errors.append(f"{code}: {exc}")
                    continue
                except Exception as exc:
                    errors.append(f"{code}: {type(exc).__name__}: {exc}")
                    continue

                all_page_urls.append(page.page_url)
                for article in page.articles:
                    if not article.web_url or article.web_url in seen_urls:
                        continue
                    seen_urls.add(article.web_url)
                    all_articles.append(article)

        return PolicyFetchResult(
            articles=all_articles,
            errors=errors,
            page_urls=all_page_urls,
        )

    def subscribe_country(
        self,
        *,
        client: httpx.Client,
        country: str,
        download_date: date,
    ) -> PolicyPage:
        country = country.upper()

        if country in {"US", "USA"}:
            return self.subscribe_us(client=client)

        if country in {"UK", "GB"}:
            return self.subscribe_uk(client=client)

        if country in {"EU", "EC"}:
            return self.subscribe_eu(client=client, download_date=download_date)

        if country in {"IN", "INDIA"}:
            page = self.subscribe_india(client=client)
            filtered = self._filter_window(page.articles)[: self.config.max_articles_per_country]
            return PolicyPage(
                articles=filtered,
                article_urls=[a.web_url for a in filtered],
                page_url=page.page_url,
                status_code=page.status_code,
                country=page.country,
            )

        if country in {"BR", "BRAZIL"}:
            return self.subscribe_brazil(client=client, download_date=download_date)

        raise PolicyError(f"Unknown country/source: {country}")

    # ------------------------------------------------------------------
    # US: Federal Register API（AI term）+ RSS 备选
    # ------------------------------------------------------------------

    def subscribe_us(self, *, client: httpx.Client) -> PolicyPage:
        """
        功能：拉取美国联邦公报 AI 相关公告。
        输入：client；config.us_use_api 为真时走 JSON API，否则 RSS。
        输出：PolicyPage。
        """
        cap = max(0, int(self.config.max_articles_per_country))
        if self.config.us_use_api or self.config.backfill_mode:
            try:
                page = self.subscribe_us_api(client=client)
                if page.articles:
                    return page
            except PolicyError:
                if self.config.backfill_mode:
                    raise

        return self._subscribe_feed(
            client=client,
            feed_url=self.FEDERAL_REGISTER_RSS,
            country="US",
            section_name="Policy / US / Federal Register",
            limit_remaining=cap,
        )

    def subscribe_us_api(
        self,
        *,
        client: httpx.Client,
        download_date: Optional[date] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> PolicyPage:
        """Federal Register documents.json：按关键词与发布日期范围检索（支持多页）。"""
        cap = max(0, int(self.config.max_articles_per_country))
        df, dt = self._backfill_window()
        start = date_from or df
        end = date_to or dt or download_date or date.today()
        if start is None:
            start = end - timedelta(days=max(7, int(self.config.us_api_days_back)))

        term = quote_plus(self.config.us_fr_search_term or "artificial intelligence")
        articles: List[RawArticle] = []
        page_num = 1
        api_urls: List[str] = []
        latest_status = 200
        per_page = min(100, max(cap, 20))

        while len(articles) < cap:
            query_parts = [
                f"conditions[term]={term}",
                f"conditions[publication_date][gte]={start.isoformat()}",
                f"conditions[publication_date][lte]={end.isoformat()}",
                f"per_page={per_page}",
                f"page={page_num}",
                "order=newest",
                "fields[]=title",
                "fields[]=html_url",
                "fields[]=publication_date",
                "fields[]=abstract",
            ]
            api_url = f"{self.FEDERAL_REGISTER_API}?{'&'.join(query_parts)}"
            api_urls.append(api_url)

            raw_text, status_code = self._request_text_with_retry(client, api_url)
            latest_status = status_code
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise PolicyError(f"Federal Register API invalid JSON: {exc}") from exc

            results = payload.get("results") or []
            if not results:
                break

            for doc in results:
                if len(articles) >= cap:
                    break
                title = self._clean_text(str(doc.get("title") or ""))
                url = self._clean_text(str(doc.get("html_url") or ""))
                if not title or not url:
                    continue
                abstract = self._clean_text(str(doc.get("abstract") or ""))
                pub = self._clean_text(str(doc.get("publication_date") or ""))
                metadata = self._build_metadata(country="US", creator="", source="Federal Register")
                body_text = abstract or title
                if metadata:
                    body_text = f"{abstract}\n\n{metadata}" if abstract else metadata

                articles.append(
                    RawArticle(
                        web_url=url,
                        title=title,
                        trail_text=abstract or title,
                        body_text=body_text or None,
                        web_publication_date=pub or None,
                        section_name="Policy / US / Federal Register API",
                        api_url=api_url,
                        guardian_id=None,
                    )
                )

            total_pages = int(payload.get("total_pages") or 1)
            if page_num >= total_pages:
                break
            page_num += 1
            time.sleep(max(0.0, self.config.page_delay_sec))

        articles = self._filter_window(articles)[:cap]
        if not articles:
            raise PolicyError(
                f"Federal Register API returned no documents for {start}..{end}"
            )

        return PolicyPage(
            articles=articles,
            article_urls=[a.web_url for a in articles],
            page_url=",".join(api_urls[:3]),
            status_code=latest_status,
            country="US",
        )

    # ------------------------------------------------------------------
    # UK: GOV.UK AI Atom 优先；legislation.gov.uk RSS 补充
    # ------------------------------------------------------------------

    def subscribe_uk(self, *, client: httpx.Client) -> PolicyPage:
        """
        功能：拉取英国政策条目。
        回溯模式：GOV.UK Atom 带 public_timestamp 窗；增量优先 AI Atom。
        """
        articles: List[RawArticle] = []
        page_urls: List[str] = []
        latest_status = 200
        seen: set[str] = set()
        cap = max(0, int(self.config.max_articles_per_country))
        df, dt = self._backfill_window()

        feed_specs: List[Tuple[str, str]] = []
        if df and dt:
            ts_from = df.isoformat()
            ts_to = dt.isoformat()
            feed_specs = [
                (
                    f"{self.UK_GOVUK_AI_ATOM}&public_timestamp[from]={ts_from}&public_timestamp[to]={ts_to}",
                    "Policy / UK / GOV.UK AI",
                ),
                (
                    f"{self.UK_GOVUK_POLICY_ATOM}&public_timestamp[from]={ts_from}&public_timestamp[to]={ts_to}",
                    "Policy / UK / GOV.UK Policy",
                ),
            ]
        else:
            feed_specs = [
                (self.UK_GOVUK_AI_ATOM, "Policy / UK / GOV.UK AI"),
                (self.UK_GOVUK_POLICY_ATOM, "Policy / UK / GOV.UK Policy"),
            ]

        for feed_url, section in feed_specs:
            if len(articles) >= cap:
                break
            try:
                page = self._subscribe_feed(
                    client=client,
                    feed_url=feed_url,
                    country="UK",
                    section_name=section,
                    limit_remaining=cap - len(articles),
                )
            except PolicyError:
                continue
            page_urls.append(page.page_url)
            latest_status = page.status_code
            for art in self._filter_window(page.articles):
                if art.web_url in seen:
                    continue
                seen.add(art.web_url)
                articles.append(art)
                if len(articles) >= cap:
                    break

        if len(articles) < cap and not (df and dt):
            try:
                leg_page = self._subscribe_feed(
                    client=client,
                    feed_url=self.UK_LEGISLATION_RSS,
                    country="UK",
                    section_name="Policy / UK / legislation.gov.uk",
                    limit_remaining=cap - len(articles),
                )
                page_urls.append(leg_page.page_url)
                latest_status = leg_page.status_code
                for art in self._filter_window(leg_page.articles):
                    if art.web_url in seen:
                        continue
                    seen.add(art.web_url)
                    articles.append(art)
                    if len(articles) >= cap:
                        break
            except PolicyError:
                pass

        if not articles:
            raise PolicyError("UK policy feeds unavailable (GOV.UK and legislation.gov.uk empty)")

        return PolicyPage(
            articles=articles[:cap],
            article_urls=[a.web_url for a in articles[:cap]],
            page_url=",".join(page_urls),
            status_code=latest_status,
            country="UK",
        )

    def _subscribe_feed(
        self,
        *,
        client: httpx.Client,
        feed_url: str,
        country: str,
        section_name: str,
        limit_remaining: Optional[int] = None,
    ) -> PolicyPage:
        """通用 RSS/Atom 订阅：拉 feed 并解析为 RawArticle。"""
        rss_text, status_code = self._request_text_with_retry(client, feed_url)
        feed = feedparser.parse(rss_text)
        if getattr(feed, "bozo", False) and not feed.entries:
            raise PolicyError(
                f"Failed to parse feed {feed_url}: {getattr(feed, 'bozo_exception', '')}"
            )

        cap = limit_remaining if limit_remaining is not None else self.config.max_articles_per_country
        cap = max(0, int(cap))
        entries = list(feed.entries)[:cap] if cap else []

        articles = [
            self._parse_rss_entry(
                entry,
                country=country,
                section_name=section_name,
                api_url=feed_url,
            )
            for entry in entries
        ]

        return PolicyPage(
            articles=articles,
            article_urls=[article.web_url for article in articles],
            page_url=feed_url,
            status_code=status_code,
            country=country,
        )

    # ------------------------------------------------------------------
    # EU: EUR-Lex Daily View
    # ------------------------------------------------------------------

    def subscribe_eu(
        self,
        *,
        client: httpx.Client,
        download_date: date,
    ) -> PolicyPage:
        """
        功能：EUR-Lex 关键词搜索 + daily-view 按日补充。
        输入：download_date 为基准日；eu_days_back 控制回溯天数。
        输出：PolicyPage；不在抓取层做 AI 标题硬过滤。
        """
        articles: List[RawArticle] = []
        page_urls: List[str] = []
        latest_status_code = 200
        cap = max(0, int(self.config.max_articles_per_country))
        seen: set[str] = set()
        df, dt = self._backfill_window()

        if not (df and dt):
            if self.config.eu_use_search and cap > 0:
                try:
                    search_page = self._subscribe_eu_search(client=client, max_items=cap)
                    page_urls.append(search_page.page_url)
                    latest_status_code = search_page.status_code
                    for art in search_page.articles:
                        if art.web_url in seen:
                            continue
                        seen.add(art.web_url)
                        articles.append(art)
                except PolicyError:
                    pass

            for offset in range(1, self.config.eu_days_back + 1):
                if len(articles) >= cap:
                    break
                target_date = download_date - timedelta(days=offset)
                self._eu_daily_view_for_date(
                    client=client,
                    target_date=target_date,
                    articles=articles,
                    seen=seen,
                    page_urls=page_urls,
                    cap=cap,
                    latest_status_code_ref=[latest_status_code],
                )
        else:
            day_cursor = df
            while day_cursor <= dt and len(articles) < cap:
                self._eu_daily_view_for_date(
                    client=client,
                    target_date=day_cursor,
                    articles=articles,
                    seen=seen,
                    page_urls=page_urls,
                    cap=cap,
                    latest_status_code_ref=[latest_status_code],
                )
                day_cursor += timedelta(days=1)

        return PolicyPage(
            articles=articles[:cap],
            article_urls=[article.web_url for article in articles[:cap]],
            page_url=",".join(page_urls[:5]),
            status_code=latest_status_code,
            country="EU",
        )

    def _eu_daily_view_for_date(
        self,
        *,
        client: httpx.Client,
        target_date: date,
        articles: List[RawArticle],
        seen: set[str],
        page_urls: List[str],
        cap: int,
        latest_status_code_ref: List[int],
    ) -> None:
        """单日 EUR-Lex daily-view 解析并追加到 articles。"""
        if len(articles) >= cap:
            return
        oj_date = target_date.strftime("%d%m%Y")
        pub_date = target_date.isoformat()
        page_url = (
            f"{self.EUR_LEX_BASE}/oj/daily-view/L-series/default.html"
            f"?ojDate={oj_date}"
        )
        page_urls.append(page_url)
        html, status_code = self._request_text_with_retry(client, page_url)
        latest_status_code_ref[0] = status_code
        soup = BeautifulSoup(html, "html.parser")
        for art in self._parse_eu_daily_view(
            soup,
            client=client,
            page_url=page_url,
            pub_date=pub_date,
            max_items=cap - len(articles),
        ):
            if art.web_url in seen:
                continue
            seen.add(art.web_url)
            articles.append(art)
            if len(articles) >= cap:
                break
        time.sleep(max(0.0, self.config.page_delay_sec))

    def _subscribe_eu_search(
        self,
        *,
        client: httpx.Client,
        max_items: int,
    ) -> PolicyPage:
        """EUR-Lex 快速搜索：artificial intelligence / AI Act 相关法规。"""
        search_url = (
            f"{self.EUR_LEX_BASE}/search.html"
            f"?scope=EURLEX&type=quick&lang=en"
            f"&text={quote_plus('artificial intelligence')}"
            f"&sortOne=DD&sortOneOrder=desc"
        )
        html, status_code = self._request_text_with_retry(client, search_url)
        soup = BeautifulSoup(html, "html.parser")
        articles: List[RawArticle] = []
        seen: set[str] = set()

        for link in soup.select("a.title"):
            if len(articles) >= max_items:
                break
            title = self._clean_text(link.get_text(" "))
            href = link.get("href", "")
            if not title or not href:
                continue
            full_url = urljoin(self.EUR_LEX_BASE, href)
            if full_url in seen:
                continue
            seen.add(full_url)
            summary = ""
            if self.config.fetch_eu_full_text:
                summary = self._fetch_eu_summary(client, full_url)
            articles.append(
                RawArticle(
                    web_url=full_url,
                    title=title,
                    trail_text=summary or title,
                    body_text=summary or title,
                    web_publication_date=None,
                    section_name="Policy / EU / EUR-Lex Search",
                    api_url=search_url,
                    guardian_id=None,
                )
            )

        # 备选选择器（EUR-Lex 页面结构可能变化）
        if not articles:
            for row in soup.select("div.SearchResult"):
                if len(articles) >= max_items:
                    break
                link_node = row.select_one("a")
                if not link_node:
                    continue
                title = self._clean_text(link_node.get_text(" "))
                href = link_node.get("href", "")
                if not title or not href:
                    continue
                full_url = urljoin(self.EUR_LEX_BASE, href)
                if full_url in seen:
                    continue
                seen.add(full_url)
                articles.append(
                    RawArticle(
                        web_url=full_url,
                        title=title,
                        trail_text=title,
                        body_text=title,
                        web_publication_date=None,
                        section_name="Policy / EU / EUR-Lex Search",
                        api_url=search_url,
                        guardian_id=None,
                    )
                )

        return PolicyPage(
            articles=articles,
            article_urls=[a.web_url for a in articles],
            page_url=search_url,
            status_code=status_code,
            country="EU",
        )

    def _parse_eu_daily_view(
        self,
        soup: BeautifulSoup,
        *,
        client: httpx.Client,
        page_url: str,
        pub_date: str,
        max_items: int,
    ) -> List[RawArticle]:
        articles: List[RawArticle] = []
        if max_items <= 0:
            return articles

        for panel in soup.select("div.panel"):
            if len(articles) >= max_items:
                break

            policy_type = self._clean_text(
                panel.select_one("button").get_text(" ")
            ) if panel.select_one("button") else ""

            for row in panel.select("div.daily-view-row-spacing"):
                if len(articles) >= max_items:
                    break

                link_node = row.select_one("a")
                if not link_node:
                    continue

                title = self._clean_text(link_node.get_text(" "))
                href = link_node.get("href", "")

                if not title or not href:
                    continue

                full_url = urljoin(self.EUR_LEX_BASE, href)
                summary = ""
                if self.config.fetch_eu_full_text:
                    summary = self._fetch_eu_summary(client, full_url)

                articles.append(
                    RawArticle(
                        web_url=full_url,
                        title=title,
                        trail_text=summary or None,
                        body_text=summary or title,
                        web_publication_date=pub_date,
                        section_name=f"Policy / EU / {policy_type}" if policy_type else "Policy / EU",
                        api_url=page_url,
                        guardian_id=None,
                    )
                )

        return articles

    def _fetch_eu_summary(self, client: httpx.Client, url: str) -> str:
        try:
            html, _ = self._request_text(client, url)
        except Exception:
            return ""

        soup = BeautifulSoup(html, "html.parser")
        container = soup.select_one("div.eli-container")

        if not container:
            return ""

        return self._truncate(self._clean_text(container.get_text(" ")), 65535)

    # ------------------------------------------------------------------
    # India: PRS India Bill Track
    # ------------------------------------------------------------------

    def subscribe_india(self, *, client: httpx.Client) -> PolicyPage:
        """
        功能：PRS India 法案追踪（多状态 + 分页）。
        输入：config.india_allowed_statuses、india_max_pages。
        输出：PolicyPage；详情页正文供 LLM 质检。
        """
        articles: List[RawArticle] = []
        page_urls: List[str] = []
        latest_status = 200
        cap = max(0, int(self.config.max_articles_per_country))
        allowed = self.config.india_allowed_statuses

        for page_idx in range(max(1, int(self.config.india_max_pages))):
            if len(articles) >= cap:
                break

            page_url = self.PRS_BILL_TRACK_URL if page_idx == 0 else f"{self.PRS_BILL_TRACK_URL}?page={page_idx}"
            html, status_code = self._request_text_with_retry(client, page_url)
            latest_status = status_code
            page_urls.append(page_url)

            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("#parliament_view div.views-row")
            if not rows:
                break

            for row in rows:
                if len(articles) >= cap:
                    break

                status = self._clean_text(
                    row.select_one(".views-field-field-bill-status").get_text(" ")
                ) if row.select_one(".views-field-field-bill-status") else ""

                if status and status not in allowed:
                    continue

                title_node = row.select_one(".views-field-title-field a")
                if not title_node:
                    continue

                title = self._clean_text(title_node.get_text(" "))
                href = title_node.get("href", "")

                if not title or not href:
                    continue

                full_url = urljoin(self.PRS_BASE, href)
                articles.append(
                    self._fetch_india_detail(
                        client=client,
                        url=full_url,
                        title=title,
                    )
                )

                if len(articles) < cap:
                    time.sleep(max(0.0, self.config.page_delay_sec))

            time.sleep(max(0.0, self.config.page_delay_sec))

        return PolicyPage(
            articles=articles,
            article_urls=[article.web_url for article in articles],
            page_url=",".join(page_urls),
            status_code=latest_status,
            country="IN",
        )

    def _fetch_india_detail(
        self,
        *,
        client: httpx.Client,
        url: str,
        title: str,
    ) -> RawArticle:
        creator = ""
        summary = ""
        pub_date: Optional[str] = None

        try:
            html, _ = self._request_text(client, url)
            soup = BeautifulSoup(html, "html.parser")

            ministry_node = soup.select_one(".field-name-field-ministry")
            if ministry_node:
                creator = self._clean_text(ministry_node.get_text(" "))

            body_node = soup.select_one(".body_content")
            if body_node:
                summary = self._clean_text(body_node.get_text(" "))

            date_nodes = soup.select("span.date-display-single")
            if date_nodes:
                raw_date = self._clean_text(date_nodes[-1].get_text(" "))
                parsed = self._parse_date(raw_date)
                pub_date = parsed.isoformat() if parsed else None

        except Exception:
            pass

        metadata = self._build_metadata(
            country="IN",
            creator=creator,
            source="PRS India",
        )

        body_text = summary or title
        if metadata:
            body_text = f"{summary}\n\n{metadata}" if summary else metadata

        return RawArticle(
            web_url=url,
            title=title,
            trail_text=summary or title,
            body_text=body_text or None,
            web_publication_date=pub_date,
            section_name="Policy / India / PRS",
            api_url=self.PRS_BILL_TRACK_URL,
            guardian_id=None,
        )

    # ------------------------------------------------------------------
    # Brazil: LexML
    # ------------------------------------------------------------------

    def subscribe_brazil(
        self,
        *,
        client: httpx.Client,
        download_date: date,
    ) -> PolicyPage:
        """
        功能：LexML 立法检索（葡语 AI 关键词 + 分页）。
        输入：download_date 用于 lookback 过滤；brazil_max_offsets_per_day 控制分页深度。
        输出：PolicyPage。
        """
        articles: List[RawArticle] = []
        page_urls: List[str] = []
        latest_status_code = 200
        cap = max(0, int(self.config.max_articles_per_country))
        df, dt = self._backfill_window()
        if df and dt:
            cutoff = df
            upper = dt
        else:
            cutoff = download_date - timedelta(days=max(7, int(self.config.brazil_lookback_days)))
            upper = download_date
        ai_term = quote_plus(BRAZIL_LEXML_AI_QUERY)

        offset = 1
        offset_count = 0

        while offset_count < self.config.brazil_max_offsets_per_day:
            if len(articles) >= cap:
                break

            page_url = (
                f"{self.LEXML_BASE}/busca/search"
                f"?keyword={ai_term}"
                f";sort=reverse-year"
                f";f2-tipoDocumento=Legisla%C3%A7%C3%A3o"
                f";startDoc={offset}"
            )
            page_urls.append(page_url)

            html, status_code = self._request_text_with_retry(client, page_url)
            latest_status_code = status_code

            soup = BeautifulSoup(html, "html.parser")
            hits = soup.select("div.docHit")

            # LexML 的 f1-texto 已失效；keyword 无结果时回退全量立法检索（预筛已跳过，由 LLM 质检）
            if not hits and offset == 1:
                fallback_url = (
                    f"{self.LEXML_BASE}/busca/search"
                    f"?sort=reverse-year"
                    f";f2-tipoDocumento=Legisla%C3%A7%C3%A3o"
                    f";startDoc={offset}"
                )
                page_urls.append(fallback_url)
                html, status_code = self._request_text_with_retry(client, fallback_url)
                latest_status_code = status_code
                soup = BeautifulSoup(html, "html.parser")
                hits = soup.select("div.docHit")

            if not hits:
                break

            for hit in hits:
                if len(articles) >= cap:
                    break
                article = self._parse_brazil_hit(hit, fallback_date=download_date)
                if not article:
                    continue
                pub = self._parse_date(article.web_publication_date or "")
                if pub and pub < cutoff:
                    continue
                if pub and pub > upper:
                    continue
                articles.append(article)

            offset += 20
            offset_count += 1
            time.sleep(max(0.0, self.config.page_delay_sec))

        filtered = self._filter_window(articles)[:cap]
        return PolicyPage(
            articles=filtered,
            article_urls=[article.web_url for article in filtered],
            page_url=",".join(page_urls),
            status_code=latest_status_code,
            country="BR",
        )

    def _parse_brazil_hit(
        self,
        hit: BeautifulSoup,
        *,
        fallback_date: date,
    ) -> Optional[RawArticle]:
        info: dict[str, str] = {}

        for tr in hit.select("tr"):
            tds = tr.select("td")
            if len(tds) < 3:
                continue

            key = self._clean_text(tds[1].get_text(" "))
            value = self._clean_text(tds[2].get_text(" "))

            if key == "Título":
                link_node = tds[2].select_one("a")
                if link_node and link_node.get("href"):
                    info["url"] = urljoin(self.LEXML_BASE, link_node.get("href"))

            if key:
                info[key] = value

        title = info.get("Título", "")
        url = info.get("url", "")
        creator = info.get("Autoridade", "")
        summary = info.get("Ementa", "")

        if not title or not url:
            return None

        pub_date = fallback_date
        raw_date = info.get("Data", "")

        parsed = self._parse_date(raw_date)
        if parsed:
            pub_date = parsed

        metadata = self._build_metadata(
            country="BR",
            creator=creator,
            source="LexML",
        )

        body_text = summary or title
        if metadata:
            body_text = f"{summary}\n\n{metadata}" if summary else metadata

        return RawArticle(
            web_url=url,
            title=title,
            trail_text=summary or title,
            body_text=body_text or None,
            web_publication_date=pub_date.isoformat(),
            section_name="Policy / Brazil / LexML",
            api_url=None,
            guardian_id=None,
        )

    # ------------------------------------------------------------------
    # Shared RSS / request / utility logic
    # ------------------------------------------------------------------

    def _parse_rss_entry(
        self,
        entry,
        *,
        country: str,
        section_name: str,
        api_url: str,
    ) -> RawArticle:
        title = self._clean_text(getattr(entry, "title", "")) or "(no title)"
        url = self._entry_link(entry, fallback=api_url)

        summary = (
            getattr(entry, "summary", None)
            or getattr(entry, "description", None)
            or ""
        )
        summary = self._clean_text(summary)

        author = ""
        if hasattr(entry, "author"):
            author = self._clean_text(entry.author)

        pub_date = self._extract_feed_date(entry)

        metadata = self._build_metadata(
            country=country,
            creator=author,
            source=section_name,
        )

        body_text = summary or title
        if metadata:
            body_text = f"{summary}\n\n{metadata}" if summary else metadata

        return RawArticle(
            web_url=url,
            title=title,
            trail_text=summary or title,
            body_text=body_text or None,
            web_publication_date=pub_date,
            section_name=section_name,
            api_url=api_url,
            guardian_id=None,
        )

    @staticmethod
    def _entry_link(entry, *, fallback: str) -> str:
        """从 RSS/Atom entry 提取 canonical 链接。"""
        link = getattr(entry, "link", "") or ""
        if isinstance(link, str) and link.strip():
            return link.strip()

        for item in getattr(entry, "links", []) or []:
            rel = (item.get("rel") or "").lower()
            href = (item.get("href") or "").strip()
            if href and rel in {"alternate", "self", ""}:
                return href

        entry_id = getattr(entry, "id", "") or ""
        if isinstance(entry_id, str) and entry_id.startswith("http"):
            return entry_id.strip()

        return fallback

    @classmethod
    def _extract_feed_date(cls, entry) -> Optional[str]:
        """解析 RSS/Atom 发布时间（RFC822 或 ISO8601）。"""
        for key in ("published", "updated", "created", "issued"):
            value = getattr(entry, key, None)
            if not value:
                continue

            parsed = cls._parse_date(str(value))
            if parsed:
                return parsed.isoformat()

        return None

    def _new_http_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.config.timeout_sec,
            follow_redirects=True,
            headers={
                "User-Agent": self.config.user_agent,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "application/atom+xml,text/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    def _request_text_with_retry(
        self,
        client: httpx.Client,
        url: str,
    ) -> Tuple[str, int]:
        last_error: Optional[Exception] = None

        for attempt in range(self.config.retry_count):
            try:
                return self._request_text(client, url)
            except Exception as exc:
                last_error = exc

                is_last_attempt = attempt == self.config.retry_count - 1
                if not is_last_attempt:
                    time.sleep(max(0.0, self.config.retry_delay_sec))

        detail = f"{type(last_error).__name__}: {last_error}" if last_error else "unknown"
        raise PolicyError(
            f"Failed to fetch policy page after {self.config.retry_count} retries: {url} ({detail})"
        ) from last_error

    @staticmethod
    def _request_text(client: httpx.Client, url: str) -> Tuple[str, int]:
        response = client.get(url)

        if response.status_code == 429:
            raise PolicyError(
                "Policy source returned rate limit status 429.",
                status_code=429,
            )

        if not response.is_success:
            preview = response.text[:300] if response.text else ""
            raise PolicyError(
                f"Policy source HTTP {response.status_code}: {preview}",
                status_code=response.status_code,
            )

        return response.text, response.status_code

    @staticmethod
    def _build_metadata(
        *,
        country: str,
        creator: str,
        source: str,
    ) -> Optional[str]:
        parts: List[str] = []

        if country:
            parts.append(f"Country: {country}")

        if creator:
            parts.append(f"Creator: {creator}")

        if source:
            parts.append(f"Source: {source}")

        return "\n".join(parts) if parts else None

    @classmethod
    def _parse_date(cls, value: str) -> Optional[date]:
        value = cls._clean_text(value)

        if not value:
            return None

        # ISO8601（Atom 常见）
        iso_candidate = value.replace("Z", "+00:00")
        if "T" in iso_candidate:
            try:
                dt = datetime.fromisoformat(iso_candidate[:26])
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt.date()
            except ValueError:
                pass

        formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%b %d, %Y",
            "%B %d, %Y",
            "%d %B %Y",
            "%d %b %Y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(value[:10] if fmt == "%Y-%m-%d" else value, fmt).date()
            except ValueError:
                continue

        try:
            return parsedate_to_datetime(value).date()
        except Exception:
            return None

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    @staticmethod
    def _truncate(value: str, max_length: int) -> str:
        if len(value) <= max_length:
            return value

        return value[:max_length]


def fetch_policy_articles(
    *,
    countries: Optional[Iterable[str]] = None,
    max_articles_per_country: int = 30,
    download_date: Optional[date] = None,
    config: Optional[PolicyConfig] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> PolicyFetchResult:
    """
    功能：拉取多国政策源并返回 PolicyFetchResult。
    输入：countries 默认全池；date_from/date_to 触发回溯配置。
    输出：PolicyFetchResult（articles + errors）；副作用：HTTP 请求。
    上下游：crawler.orchestrator.async_sync_policy。
    """
    if config is None and date_from and date_to:
        config = build_backfill_policy_config(
            date_from=date_from,
            date_to=date_to,
            max_articles_per_country=max_articles_per_country,
        )
    base = config or policy_config_from_env()
    cfg = PolicyConfig(
        timeout_sec=base.timeout_sec,
        retry_count=base.retry_count,
        retry_delay_sec=base.retry_delay_sec,
        page_delay_sec=base.page_delay_sec,
        eu_days_back=base.eu_days_back,
        brazil_max_offsets_per_day=base.brazil_max_offsets_per_day,
        brazil_lookback_days=base.brazil_lookback_days,
        max_articles_per_country=max(1, max_articles_per_country),
        user_agent=base.user_agent,
        fetch_eu_full_text=base.fetch_eu_full_text,
        us_use_api=base.us_use_api,
        us_api_days_back=base.us_api_days_back,
        eu_use_search=base.eu_use_search,
        india_max_pages=base.india_max_pages,
        india_allowed_statuses=base.india_allowed_statuses,
        backfill_mode=base.backfill_mode,
        date_from=base.date_from,
        date_to=base.date_to,
        us_fr_search_term=base.us_fr_search_term,
    )
    effective_date = download_date or cfg.date_to or date.today()
    sub = PolicySubscriber(cfg)
    return sub.subscribe(countries=countries, download_date=effective_date)


def main() -> None:
    config = PolicyConfig(
        max_articles_per_country=3,
        eu_days_back=2,
        brazil_max_offsets_per_day=1,
    )

    subscriber = PolicySubscriber(config)
    result = subscriber.subscribe(
        countries=["US", "UK", "EU", "IN", "BR"],
        download_date=date.today(),
    )

    print(f"Fetched articles: {len(result.articles)}")
    if result.errors:
        print("Errors:")
        for err in result.errors:
            print(f"  - {err}")
    print("-" * 80)

    for index, article in enumerate(result.articles, start=1):
        print(f"[{index}] {article.section_name}")
        print(
            json.dumps(
                article.model_dump(),
                ensure_ascii=False,
                indent=2,
            )
        )
        print("-" * 80)


if __name__ == "__main__":
    main()
