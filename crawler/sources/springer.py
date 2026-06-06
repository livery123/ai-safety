"""
Springer crawler.

Fetches Springer search results and article pages, then maps them into
the shared RawArticle structure used by the crawler pipeline.

This module does not write to DB. Deduplication and persistence should be
handled by the outer crawler pipeline.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin

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
class SpringerPage:
    """Result returned by Springer crawling."""

    articles: List[RawArticle]
    article_urls: List[str]
    page_url: str
    status_code: int
    domain: Optional[str] = None


@dataclass(frozen=True)
class SpringerConfig:
    """Runtime configuration for Springer crawling."""

    base_url: str = "https://link.springer.com"
    max_pages_per_domain: int = 50
    max_articles_per_domain: int = 100
    cutoff_days: int = 7
    timeout_sec: float = 30.0
    retry_count: int = 3
    retry_delay_sec: float = 3.0
    page_delay_min_sec: float = 0.8
    page_delay_max_sec: float = 2.0
    article_delay_min_sec: float = 0.8
    article_delay_max_sec: float = 2.5
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0 Safari/537.36"
    )


class SpringerError(Exception):
    """Raised when Springer request or parsing fails."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SpringerSubscriber:
    """Subscriber for Springer articles."""

    DEFAULT_DOMAINS = [
        "Machine Learning",
        "Artificial Intelligence",
        "Computational Intelligence",
        "Learning algorithms",
        "Computer Vision",
        "Optimization",
        "Knowledge Based Systems",
        "Statistical Learning",
        "Automated Pattern Recognition",
        "Categorization",
        "Object Recognition",
        "Symbolic AI",
        "Robotics",
    ]

    def __init__(self, config: Optional[SpringerConfig] = None) -> None:
        self.config = config or SpringerConfig()

    def subscribe(
        self,
        *,
        domains: Optional[Iterable[str]] = None,
        download_date: Optional[date] = None,
    ) -> SpringerPage:
        """
        Crawl multiple Springer domains and merge articles.

        The caller can pass fewer domains for testing:
            subscriber.subscribe(domains=["Machine Learning"])
        """
        selected_domains = list(domains or self.DEFAULT_DOMAINS)
        download_date = download_date or date.today()
        cutoff_date = download_date - timedelta(days=self.config.cutoff_days)

        all_articles: List[RawArticle] = []
        all_urls: List[str] = []
        all_page_urls: List[str] = []

        seen_urls: set[str] = set()
        latest_status_code = 200

        with self._new_http_client() as client:
            for domain in selected_domains:
                page = self.subscribe_domain(
                    client=client,
                    domain=domain,
                    cutoff_date=cutoff_date,
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

        return SpringerPage(
            articles=all_articles,
            article_urls=all_urls,
            page_url=",".join(all_page_urls),
            status_code=latest_status_code,
            domain=None,
        )

    def subscribe_domain(
        self,
        *,
        client: httpx.Client,
        domain: str,
        cutoff_date: date,
    ) -> SpringerPage:
        """Crawl one Springer domain."""
        articles: List[RawArticle] = []
        article_urls: List[str] = []
        page_urls: List[str] = []

        latest_status_code = 200
        should_stop = False

        for page_no in range(1, self.config.max_pages_per_domain + 1):
            search_url = self._build_search_url(domain=domain, page_no=page_no)
            page_urls.append(search_url)

            html, status_code = self._request_text_with_retry(client, search_url)
            latest_status_code = status_code

            search_items = self._parse_search_items(html)

            if not search_items:
                break

            for item in search_items:
                if len(articles) >= self.config.max_articles_per_domain:
                    should_stop = True
                    break

                if item.publish_date and item.publish_date < cutoff_date:
                    should_stop = True
                    break

                try:
                    article = self.fetch_article(
                        client=client,
                        url=item.url,
                        domain=domain,
                        page_no=page_no,
                        fallback_title=item.title,
                        fallback_date=item.publish_date,
                    )
                except Exception as exc:
                    # Keep the source robust: bad article pages should not stop the whole domain.
                    print(f"[Springer] Failed article {item.url}: {exc}")
                    continue

                articles.append(article)
                article_urls.append(article.web_url)

                self._sleep_random(
                    self.config.article_delay_min_sec,
                    self.config.article_delay_max_sec,
                )

            if should_stop:
                break

            self._sleep_random(
                self.config.page_delay_min_sec,
                self.config.page_delay_max_sec,
            )

        return SpringerPage(
            articles=articles,
            article_urls=article_urls,
            page_url=",".join(page_urls),
            status_code=latest_status_code,
            domain=domain,
        )

    def fetch_article(
        self,
        *,
        client: httpx.Client,
        url: str,
        domain: str,
        page_no: int,
        fallback_title: Optional[str] = None,
        fallback_date: Optional[date] = None,
    ) -> RawArticle:
        """Fetch and parse one Springer article page."""
        html, _status_code = self._request_text_with_retry(client, url)
        return self._parse_article(
            html,
            url=url,
            domain=domain,
            page_no=page_no,
            fallback_title=fallback_title,
            fallback_date=fallback_date,
        )

    def _build_search_url(self, *, domain: str, page_no: int) -> str:
        taxonomy = quote_plus(f'"{domain}"')

        return (
            f"{self.config.base_url}/search"
            f"?new-search=true"
            f"&content-type=article"
            f"&content-type=conference+paper"
            f"&taxonomy={taxonomy}"
            f"&sortBy=newestFirst"
            f"&page={page_no}"
        )

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

        raise SpringerError(
            f"Failed to fetch Springer page after {self.config.retry_count} retries: {url}"
        ) from last_error

    @staticmethod
    def _request_text(client: httpx.Client, url: str) -> Tuple[str, int]:
        response = client.get(url)

        if response.status_code == 429:
            raise SpringerError(
                "Springer returned rate limit status 429.",
                status_code=429,
            )

        if not response.is_success:
            preview = response.text[:300] if response.text else ""
            raise SpringerError(
                f"Springer HTTP {response.status_code}: {preview}",
                status_code=response.status_code,
            )

        return response.text, response.status_code

    def _parse_search_items(self, html: str) -> List["SpringerSearchItem"]:
        soup = BeautifulSoup(html, "html.parser")
        items: List[SpringerSearchItem] = []

        for node in soup.select("li[data-test='search-result-item']"):
            title_node = node.select_one("h3 a")
            if not title_node:
                continue

            title = self._clean_text(title_node.get_text(" "))
            href = title_node.get("href", "")

            if not title or not href:
                continue

            url = urljoin(self.config.base_url, href)

            publish_text = self._clean_text(
                node.select_one("span[data-test='published']").get_text(" ")
            ) if node.select_one("span[data-test='published']") else ""

            items.append(
                SpringerSearchItem(
                    title=title,
                    url=url,
                    publish_date=self._parse_date(publish_text),
                )
            )

        return items

    def _parse_article(
        self,
        html: str,
        *,
        url: str,
        domain: str,
        page_no: int,
        fallback_title: Optional[str],
        fallback_date: Optional[date],
    ) -> RawArticle:
        soup = BeautifulSoup(html, "html.parser")

        title = (
            self._text_from_first(soup, ["h1.c-article-title"])
            or fallback_title
            or "(no title)"
        )
        title = self._truncate(title, 255)

        doi = self._meta_content(soup, "DOI")
        abstract = self._text_from_first(
            soup,
            [
                "div#Abs1-content p",
                "section#Abs1 p",
                "div.c-article-section__content p",
            ],
        )

        pdf_link = self._extract_pdf_link(soup)
        publication = (
            self._meta_content(soup, "prism.publicationName")
            or self._meta_content(soup, "citation_conference_title")
        )

        publish_date = self._extract_publish_date(soup) or fallback_date
        publish_date_str = publish_date.isoformat() if publish_date else None

        authors = self._extract_authors(soup)
        keywords = self._extract_keywords(soup)

        metadata = self._build_metadata(
            doi=doi,
            publication=publication,
            pdf_link=pdf_link,
            domain=domain,
            page_no=page_no,
            authors=authors,
            keywords=keywords,
        )

        body_text = abstract
        if metadata:
            body_text = f"{abstract}\n\n{metadata}" if abstract else metadata

        return RawArticle(
            web_url=url,
            title=title,
            trail_text=abstract or None,
            body_text=body_text or None,
            web_publication_date=publish_date_str,
            section_name=f"Springer / {domain}",
            api_url=None,
        )

    def _extract_pdf_link(self, soup: BeautifulSoup) -> Optional[str]:
        node = soup.select_one("a.c-pdf-download__link")
        if not node:
            return None

        href = node.get("href", "")
        if not href:
            return None

        return urljoin(self.config.base_url, href)

    @staticmethod
    def _extract_publish_date(soup: BeautifulSoup) -> Optional[date]:
        time_node = soup.select_one("time")
        if not time_node:
            return None

        value = time_node.get("datetime", "") or time_node.get_text(" ")
        return SpringerSubscriber._parse_date(value)

    @staticmethod
    def _extract_authors(soup: BeautifulSoup) -> dict[str, dict]:
        """
        Extract authors and affiliations from Springer article page.

        This version uses the bottom section:
            Author information -> Authors and Affiliations

        Return format keeps compatibility with the old crawler:
            {
                "author-Aoqi-Yin-Aff1": {
                    "popup_id": "author-Aoqi-Yin-Aff1",
                    "popup_html_id": None,
                    "name": "Aoqi Yin",
                    "affiliations": ["School of Engineering, ..."],
                    "search_publications_url": None,
                    "external_links": {},
                    "source": "authors_and_affiliations"
                }
            }
        """
        author_map: dict[str, dict] = {}

        def split_author_names(author_text: str) -> list[str]:
            author_text = SpringerSubscriber._clean_text(author_text)

            if not author_text:
                return []

            author_text = author_text.replace(" & ", ", ")
            author_text = author_text.replace(" and ", ", ")

            return [
                SpringerSubscriber._clean_text(name)
                for name in author_text.split(",")
                if SpringerSubscriber._clean_text(name)
            ]

        author_info_heading = None

        for heading in soup.find_all(["h2", "h3"]):
            heading_text = SpringerSubscriber._clean_text(heading.get_text(" "))
            if heading_text.lower() == "author information":
                author_info_heading = heading
                break

        if not author_info_heading:
            return author_map

        container = author_info_heading.find_parent("section") or author_info_heading.parent
        if not container:
            return author_map

        aff_heading = None

        for heading in container.find_all(["h2", "h3", "h4"]):
            heading_text = SpringerSubscriber._clean_text(heading.get_text(" "))
            if "authors and affiliations" in heading_text.lower():
                aff_heading = heading
                break

        if not aff_heading:
            return author_map

        aff_list = aff_heading.find_next("ol")
        if not aff_list:
            return author_map

        for aff_index, li in enumerate(aff_list.find_all("li", recursive=False), start=1):
            parts = [
                SpringerSubscriber._clean_text(node.get_text(" "))
                for node in li.find_all(["p", "div"], recursive=False)
                if SpringerSubscriber._clean_text(node.get_text(" "))
            ]

            if not parts:
                text = SpringerSubscriber._clean_text(li.get_text(" "))
                if text:
                    parts = [text]

            if not parts:
                continue

            affiliation = parts[0]
            author_text = parts[1] if len(parts) > 1 else ""
            author_names = split_author_names(author_text)

            if not author_names:
                author_key = f"affiliation-{aff_index}"

                author_map[author_key] = {
                    "popup_id": author_key,
                    "popup_html_id": None,
                    "name": None,
                    "affiliations": [affiliation],
                    "search_publications_url": None,
                    "external_links": {},
                    "source": "authors_and_affiliations",
                }

                continue

            for author_name in author_names:
                safe_name = re.sub(r"[^A-Za-z0-9]+", "-", author_name).strip("-")
                author_key = f"author-{safe_name}-Aff{aff_index}"

                author_map[author_key] = {
                    "popup_id": author_key,
                    "popup_html_id": None,
                    "name": author_name,
                    "affiliations": [affiliation],
                    "search_publications_url": None,
                    "external_links": {},
                    "source": "authors_and_affiliations",
                }

        return author_map

    @staticmethod
    def _extract_keywords(soup: BeautifulSoup) -> List[str]:
        keywords: List[str] = []

        for node in soup.select("li.c-article-subject-list__subject"):
            keyword = SpringerSubscriber._clean_text(node.get_text(" "))
            if keyword:
                keywords.append(keyword)

        return keywords

    @staticmethod
    def _build_metadata(
        *,
        doi: Optional[str],
        publication: Optional[str],
        pdf_link: Optional[str],
        domain: str,
        page_no: int,
        authors: List[dict],
        keywords: List[str],
    ) -> Optional[str]:
        parts: List[str] = []

        if doi:
            parts.append(f"DOI: {doi}")

        if publication:
            parts.append(f"Publication: {publication}")

        if pdf_link:
            parts.append(f"PDF: {pdf_link}")

        parts.append(f"Domain: {domain}")
        parts.append(f"Search Page: {page_no}")

        if authors:
            parts.append(
                "Authors: "
                + json.dumps(authors, ensure_ascii=False)
            )

        if keywords:
            parts.append(
                "Keywords: "
                + json.dumps(keywords, ensure_ascii=False)
            )

        return "\n".join(parts) if parts else None

    @staticmethod
    def _meta_content(soup: BeautifulSoup, name: str) -> Optional[str]:
        node = soup.select_one(f'meta[name="{name}"]')
        if not node:
            return None

        content = node.get("content", "")
        content = SpringerSubscriber._clean_text(content)

        return content or None

    @staticmethod
    def _text_from_first(soup: BeautifulSoup, selectors: List[str]) -> Optional[str]:
        for selector in selectors:
            node = soup.select_one(selector)
            if not node:
                continue

            text = SpringerSubscriber._clean_text(node.get_text(" "))
            if text:
                return text

        return None

    @staticmethod
    def _parse_date(value: str) -> Optional[date]:
        value = SpringerSubscriber._clean_text(value)

        if not value:
            return None

        formats = [
            "%Y-%m-%d",
            "%B %d, %Y",
            "%b %d, %Y",
            "%d %B %Y",
            "%d %b %Y",
            "%Y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue

        return None

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    @staticmethod
    def _truncate(value: str, max_length: int) -> str:
        if len(value) <= max_length:
            return value

        return value[:max_length]

    @staticmethod
    def _sleep_random(min_sec: float, max_sec: float) -> None:
        time.sleep(random.uniform(max(0.0, min_sec), max(0.0, max_sec)))


@dataclass(frozen=True)
class SpringerSearchItem:
    """A lightweight item parsed from Springer search result page."""

    title: str
    url: str
    publish_date: Optional[date]


def main() -> None:
    config = SpringerConfig(
        max_pages_per_domain=2,
        max_articles_per_domain=5,
        cutoff_days=7,
    )

    subscriber = SpringerSubscriber(config)

    page = subscriber.subscribe(
        domains=[
            "Machine Learning",
            "Artificial Intelligence",
        ],
        download_date=date.today(),
    )

    print(f"Fetched articles: {len(page.articles)}")
    print(f"Fetched URLs: {len(page.article_urls)}")
    print(f"Search pages: {page.page_url}")
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