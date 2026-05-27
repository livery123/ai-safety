"""
Policy crawler.

功能：从多国公开政策/法规站点抓取条目，映射为 RawArticle，供 orchestrator 入库与 LLM 抽取。
输入：国家列表、每国条数上限、download_date。
输出：PolicyFetchResult（articles + 各国错误列表）；副作用：HTTP 请求。
上下游：crawler.orchestrator.async_sync_policy；下游 articles 表。

信源：
- US: Federal Register RSS
- UK: GOV.UK Atom（主）；legislation.gov.uk 在 437 等阻断时自动降级
- EU: EUR-Lex daily view
- India: PRS India bill track
- Brazil: LexML search
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

from crawler.sources.guardian import RawArticle


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


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
    eu_days_back: int = 3
    brazil_start_days_back: int = 5
    brazil_end_days_back: int = 9
    brazil_max_offsets_per_day: int = 3
    brazil_lookback_days: int = 120
    max_articles_per_country: int = 30
    user_agent: str = DEFAULT_USER_AGENT
    fetch_eu_full_text: bool = True


class PolicyError(Exception):
    """Raised when a policy source request or parse step fails."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PolicySubscriber:
    """Subscriber for public policy/legal sources."""

    DEFAULT_COUNTRIES = ["US", "UK", "EU", "IN", "BR"]

    FEDERAL_REGISTER_RSS = "https://www.federalregister.gov/api/v1/documents.rss"
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
            return self.subscribe_india(client=client)

        if country in {"BR", "BRAZIL"}:
            return self.subscribe_brazil(client=client, download_date=download_date)

        raise PolicyError(f"Unknown country/source: {country}")

    # ------------------------------------------------------------------
    # US: Federal Register RSS
    # ------------------------------------------------------------------

    def subscribe_us(self, *, client: httpx.Client) -> PolicyPage:
        return self._subscribe_feed(
            client=client,
            feed_url=self.FEDERAL_REGISTER_RSS,
            country="US",
            section_name="Policy / US / Federal Register",
        )

    # ------------------------------------------------------------------
    # UK: GOV.UK Atom（legislation.gov.uk 常被 437 阻断）
    # ------------------------------------------------------------------

    def subscribe_uk(self, *, client: httpx.Client) -> PolicyPage:
        """
        功能：拉取英国政策条目。
        优先 legislation.gov.uk；若 HTTP 437/403 等则改用 GOV.UK Atom。
        """
        try:
            return self._subscribe_feed(
                client=client,
                feed_url=self.UK_LEGISLATION_RSS,
                country="UK",
                section_name="Policy / UK / legislation.gov.uk",
            )
        except PolicyError as exc:
            blocked = exc.status_code in {403, 429, 437} or "437" in str(exc)
            if not blocked:
                raise

        articles: List[RawArticle] = []
        page_urls: List[str] = []
        latest_status = 200
        seen: set[str] = set()

        for feed_url, section in (
            (self.UK_GOVUK_AI_ATOM, "Policy / UK / GOV.UK AI"),
            (self.UK_GOVUK_POLICY_ATOM, "Policy / UK / GOV.UK Policy"),
        ):
            page = self._subscribe_feed(
                client=client,
                feed_url=feed_url,
                country="UK",
                section_name=section,
                limit_remaining=max(0, self.config.max_articles_per_country - len(articles)),
            )
            page_urls.append(page.page_url)
            latest_status = page.status_code
            for art in page.articles:
                if art.web_url in seen:
                    continue
                seen.add(art.web_url)
                articles.append(art)
                if len(articles) >= self.config.max_articles_per_country:
                    break
            if len(articles) >= self.config.max_articles_per_country:
                break

        if not articles:
            raise PolicyError(
                "UK policy feeds unavailable (legislation.gov.uk blocked and GOV.UK empty)"
            )

        return PolicyPage(
            articles=articles,
            article_urls=[a.web_url for a in articles],
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
        articles: List[RawArticle] = []
        page_urls: List[str] = []
        latest_status_code = 200
        cap = max(0, int(self.config.max_articles_per_country))

        for offset in range(1, self.config.eu_days_back + 1):
            if len(articles) >= cap:
                break

            target_date = download_date - timedelta(days=offset)
            oj_date = target_date.strftime("%d%m%Y")
            pub_date = target_date.isoformat()

            page_url = (
                f"{self.EUR_LEX_BASE}/oj/daily-view/L-series/default.html"
                f"?ojDate={oj_date}"
            )
            page_urls.append(page_url)

            html, status_code = self._request_text_with_retry(client, page_url)
            latest_status_code = status_code

            soup = BeautifulSoup(html, "html.parser")
            articles.extend(
                self._parse_eu_daily_view(
                    soup,
                    client=client,
                    page_url=page_url,
                    pub_date=pub_date,
                    max_items=cap - len(articles),
                )
            )

            time.sleep(max(0.0, self.config.page_delay_sec))

        return PolicyPage(
            articles=articles[:cap],
            article_urls=[article.web_url for article in articles[:cap]],
            page_url=",".join(page_urls),
            status_code=latest_status_code,
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
        html, status_code = self._request_text_with_retry(
            client,
            self.PRS_BILL_TRACK_URL,
        )

        soup = BeautifulSoup(html, "html.parser")
        articles: List[RawArticle] = []
        cap = max(0, int(self.config.max_articles_per_country))

        for row in soup.select("#parliament_view div.views-row"):
            if len(articles) >= cap:
                break

            status = self._clean_text(
                row.select_one(".views-field-field-bill-status").get_text(" ")
            ) if row.select_one(".views-field-field-bill-status") else ""

            if status != "Passed":
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

        return PolicyPage(
            articles=articles,
            article_urls=[article.web_url for article in articles],
            page_url=self.PRS_BILL_TRACK_URL,
            status_code=status_code,
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
        功能：LexML 立法检索（按更新时间倒序分页，客户端按日期过滤）。
        LexML 的 f3-date 过滤器对近几日条目常返回空，故不用该参数。
        """
        articles: List[RawArticle] = []
        page_urls: List[str] = []
        latest_status_code = 200
        cap = max(0, int(self.config.max_articles_per_country))
        cutoff = download_date - timedelta(days=max(7, int(self.config.brazil_lookback_days)))

        offset = 1
        offset_count = 0

        while offset_count < self.config.brazil_max_offsets_per_day:
            if len(articles) >= cap:
                break

            page_url = (
                f"{self.LEXML_BASE}/busca/search"
                f"?sort=reverse-year"
                f";f2-tipoDocumento=Legisla%C3%A7%C3%A3o"
                f";startDoc={offset}"
            )
            page_urls.append(page_url)

            html, status_code = self._request_text_with_retry(client, page_url)
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
                articles.append(article)

            offset += 20
            offset_count += 1
            time.sleep(max(0.0, self.config.page_delay_sec))

        return PolicyPage(
            articles=articles[:cap],
            article_urls=[article.web_url for article in articles[:cap]],
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
) -> PolicyFetchResult:
    """
    功能：拉取多国政策源并返回 PolicyFetchResult。
    输入：countries 默认 PolicySubscriber.DEFAULT_COUNTRIES；max_articles_per_country 单国上限。
    输出：PolicyFetchResult（articles + errors）；副作用：HTTP 请求。
    上下游：crawler.orchestrator.async_sync_policy。
    """
    cfg = PolicyConfig(max_articles_per_country=max(1, max_articles_per_country))
    sub = PolicySubscriber(cfg)
    return sub.subscribe(countries=countries, download_date=download_date)


def main() -> None:
    config = PolicyConfig(
        max_articles_per_country=3,
        eu_days_back=1,
        brazil_start_days_back=5,
        brazil_end_days_back=5,
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
