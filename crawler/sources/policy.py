"""
Policy crawler.

Fetches policy documents from several public policy/legal sources and maps
them into the shared RawArticle structure used by the crawler pipeline.

Sources:
- US: Federal Register RSS
- UK: legislation.gov.uk RSS
- EU: EUR-Lex daily view
- India: PRS India bill track
- Brazil: LexML search

This module does not write to DB. Deduplication and persistence should be
handled by the outer crawler pipeline.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field


class RawArticle(BaseModel):
    """Shared raw article structure for crawler pipeline."""

    web_url: str = Field(..., description="原始文章链接")
    title: str = Field(..., description="原始文章标题")
    trail_text: Optional[str] = Field(None, description="原始导语或摘要")
    body_text: Optional[str] = Field(None, description="原始正文全文")
    web_publication_date: Optional[str] = Field(None, description="原始发布时间")
    section_name: Optional[str] = Field(None, description="所属新闻版块")
    api_url: Optional[str] = Field(None, description="API 请求链接")


@dataclass(frozen=True)
class PolicyPage:
    """Result returned by policy crawling."""

    articles: List[RawArticle]
    article_urls: List[str]
    page_url: str
    status_code: int
    country: Optional[str] = None


@dataclass(frozen=True)
class PolicyConfig:
    """Runtime configuration for policy crawling."""

    timeout_sec: float = 30.0
    retry_count: int = 3
    retry_delay_sec: float = 3.0
    page_delay_sec: float = 1.0
    eu_days_back: int = 3
    brazil_start_days_back: int = 5
    brazil_end_days_back: int = 9
    brazil_max_offsets_per_day: int = 10
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36"
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
    UK_LEGISLATION_RSS = "https://www.legislation.gov.uk/new/data.feed"
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
    ) -> PolicyPage:
        """
        Crawl multiple policy sources and merge results.

        Example:
            subscriber.subscribe(countries=["US", "EU"])
        """
        selected_countries = list(countries or self.DEFAULT_COUNTRIES)
        download_date = download_date or date.today()

        all_articles: List[RawArticle] = []
        all_urls: List[str] = []
        all_page_urls: List[str] = []

        latest_status_code = 200
        seen_urls: set[str] = set()

        with self._new_http_client() as client:
            for country in selected_countries:
                page = self.subscribe_country(
                    client=client,
                    country=country,
                    download_date=download_date,
                )

                latest_status_code = page.status_code
                all_page_urls.append(page.page_url)

                for article in page.articles:
                    if not article.web_url:
                        continue

                    if article.web_url in seen_urls:
                        continue

                    seen_urls.add(article.web_url)
                    all_articles.append(article)
                    all_urls.append(article.web_url)

        return PolicyPage(
            articles=all_articles,
            article_urls=all_urls,
            page_url=",".join(all_page_urls),
            status_code=latest_status_code,
            country=None,
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
        rss_text, status_code = self._request_text_with_retry(
            client,
            self.FEDERAL_REGISTER_RSS,
        )

        feed = feedparser.parse(rss_text)
        articles = [
            self._parse_rss_entry(
                entry,
                country="US",
                section_name="Policy / US / Federal Register",
                api_url=self.FEDERAL_REGISTER_RSS,
            )
            for entry in feed.entries
        ]

        return PolicyPage(
            articles=articles,
            article_urls=[article.web_url for article in articles],
            page_url=self.FEDERAL_REGISTER_RSS,
            status_code=status_code,
            country="US",
        )

    # ------------------------------------------------------------------
    # UK: legislation.gov.uk RSS
    # ------------------------------------------------------------------

    def subscribe_uk(self, *, client: httpx.Client) -> PolicyPage:
        rss_text, status_code = self._request_text_with_retry(
            client,
            self.UK_LEGISLATION_RSS,
        )

        feed = feedparser.parse(rss_text)
        articles = [
            self._parse_rss_entry(
                entry,
                country="UK",
                section_name="Policy / UK / legislation.gov.uk",
                api_url=self.UK_LEGISLATION_RSS,
            )
            for entry in feed.entries
        ]

        return PolicyPage(
            articles=articles,
            article_urls=[article.web_url for article in articles],
            page_url=self.UK_LEGISLATION_RSS,
            status_code=status_code,
            country="UK",
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

        for offset in range(1, self.config.eu_days_back + 1):
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
                )
            )

            time.sleep(max(0.0, self.config.page_delay_sec))

        return PolicyPage(
            articles=articles,
            article_urls=[article.web_url for article in articles],
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
    ) -> List[RawArticle]:
        articles: List[RawArticle] = []

        for panel in soup.select("div.panel"):
            policy_type = self._clean_text(
                panel.select_one("button").get_text(" ")
            ) if panel.select_one("button") else ""

            for row in panel.select("div.daily-view-row-spacing"):
                link_node = row.select_one("a")
                if not link_node:
                    continue

                title = self._clean_text(link_node.get_text(" "))
                href = link_node.get("href", "")

                if not title or not href:
                    continue

                full_url = urljoin(self.EUR_LEX_BASE, href)
                summary = self._fetch_eu_summary(client, full_url)

                articles.append(
                    RawArticle(
                        web_url=full_url,
                        title=title,
                        trail_text=summary or None,
                        body_text=summary or None,
                        web_publication_date=pub_date,
                        section_name=f"Policy / EU / {policy_type}" if policy_type else "Policy / EU",
                        api_url=page_url,
                    )
                )

        return articles

    def _fetch_eu_summary(self, client: httpx.Client, url: str) -> str:
        try:
            html, _ = self._request_text_with_retry(client, url)
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

        for row in soup.select("#parliament_view div.views-row"):
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
            html, _ = self._request_text_with_retry(client, url)
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

        body_text = summary
        if metadata:
            body_text = f"{summary}\n\n{metadata}" if summary else metadata

        return RawArticle(
            web_url=url,
            title=title,
            trail_text=summary or None,
            body_text=body_text or None,
            web_publication_date=pub_date,
            section_name="Policy / India / PRS",
            api_url=self.PRS_BILL_TRACK_URL,
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
        articles: List[RawArticle] = []
        page_urls: List[str] = []
        latest_status_code = 200

        for days_back in range(
            self.config.brazil_start_days_back,
            self.config.brazil_end_days_back + 1,
        ):
            target_date = download_date - timedelta(days=days_back)
            decade = (target_date.year // 10) * 10
            date_param = f"{decade}s::{target_date.strftime('%Y::%m::%d')}"

            offset = 1
            offset_count = 0

            while offset_count < self.config.brazil_max_offsets_per_day:
                page_url = (
                    f"{self.LEXML_BASE}/busca/search"
                    f"?sort=reverse-year"
                    f";f2-tipoDocumento=Legisla%C3%A7%C3%A3o"
                    f";f3-date={date_param}"
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
                    article = self._parse_brazil_hit(hit, fallback_date=target_date)
                    if article:
                        articles.append(article)

                offset += 20
                offset_count += 1
                time.sleep(max(0.0, self.config.page_delay_sec))

        return PolicyPage(
            articles=articles,
            article_urls=[article.web_url for article in articles],
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

        body_text = summary
        if metadata:
            body_text = f"{summary}\n\n{metadata}" if summary else metadata

        return RawArticle(
            web_url=url,
            title=title,
            trail_text=summary or None,
            body_text=body_text or None,
            web_publication_date=pub_date.isoformat(),
            section_name="Policy / Brazil / LexML",
            api_url=None,
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
        url = getattr(entry, "link", "") or api_url

        summary = (
            getattr(entry, "summary", None)
            or getattr(entry, "description", None)
            or ""
        )
        summary = self._clean_text(summary)

        author = ""
        if hasattr(entry, "author"):
            author = self._clean_text(entry.author)

        pub_date = self._extract_rss_date(entry)

        metadata = self._build_metadata(
            country=country,
            creator=author,
            source=section_name,
        )

        body_text = summary
        if metadata:
            body_text = f"{summary}\n\n{metadata}" if summary else metadata

        return RawArticle(
            web_url=url,
            title=title,
            trail_text=summary or None,
            body_text=body_text or None,
            web_publication_date=pub_date,
            section_name=section_name,
            api_url=api_url,
        )

    @staticmethod
    def _extract_rss_date(entry) -> Optional[str]:
        for key in ("published", "updated"):
            value = getattr(entry, key, None)
            if not value:
                continue

            try:
                return parsedate_to_datetime(value).date().isoformat()
            except Exception:
                parsed = PolicySubscriber._parse_date(value)
                if parsed:
                    return parsed.isoformat()

        return None

    def _new_http_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.config.timeout_sec,
            follow_redirects=True,
            headers={"User-Agent": self.config.user_agent},
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

        raise PolicyError(
            f"Failed to fetch policy page after {self.config.retry_count} retries: {url}"
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

    @staticmethod
    def _parse_date(value: str) -> Optional[date]:
        value = PolicySubscriber._clean_text(value)

        if not value:
            return None

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
                return datetime.strptime(value, fmt).date()
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


def main() -> None:
    config = PolicyConfig(
        eu_days_back=1,
        brazil_start_days_back=5,
        brazil_end_days_back=5,
        brazil_max_offsets_per_day=1,
    )

    subscriber = PolicySubscriber(config)

    page = subscriber.subscribe(
        countries=[
            "US",
            "UK",
            "EU",
            "IN",
            "BR",
        ],
        download_date=date.today(),
    )

    print(f"Fetched articles: {len(page.articles)}")
    print(f"Fetched URLs: {len(page.article_urls)}")
    print(f"Source pages: {page.page_url}")
    print("-" * 80)

    for index, article in enumerate(page.articles, start=1):
        print(f"[{index}]")
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