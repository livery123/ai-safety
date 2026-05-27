"""
arXiv Computer Science RSS subscriber.

Fetches arXiv CS RSS feeds and converts entries into RawArticle objects
used by the crawler pipeline.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import feedparser
import httpx
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
class ArxivRSSPage:
    """Result returned by one or multiple arXiv RSS subscriptions."""

    articles: List[RawArticle]
    article_urls: List[str]
    page_url: str
    status_code: int
    category: Optional[str] = None


@dataclass(frozen=True)
class ArxivRSSConfig:
    """Runtime configuration for arXiv RSS subscription."""

    base_url: str = "https://rss.arxiv.org/rss/"
    timeout_sec: float = 45.0
    retry_count: int = 3
    retry_delay_sec: float = 2.0
    category_delay_sec: float = 0.3
    max_articles_per_category: int = 20
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )


class ArxivRSSError(Exception):
    """Raised when arXiv RSS request or parsing fails."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ArxivComputerScienceSubscriber:
    """Subscriber for arXiv computer science RSS feeds."""

    DEFAULT_CATEGORIES = [
        "cs.AI",  # Artificial Intelligence
        "cs.CL",  # Computation and Language
        "cs.CV",  # Computer Vision and Pattern Recognition
        "cs.LG",  # Machine Learning
        "cs.IR",  # Information Retrieval
        "cs.NE",  # Neural and Evolutionary Computing
        "cs.RO",  # Robotics
        "cs.SE",  # Software Engineering
        "cs.CR",  # Cryptography and Security
        "cs.DB",  # Databases
        "cs.DC",  # Distributed, Parallel, and Cluster Computing
        "cs.HC",  # Human-Computer Interaction
        "cs.MM",  # Multimedia
        "cs.SI",  # Social and Information Networks
    ]

    def __init__(self, config: Optional[ArxivRSSConfig] = None) -> None:
        self.config = config or ArxivRSSConfig()

    def subscribe(
        self,
        categories: Optional[Iterable[str]] = None,
    ) -> ArxivRSSPage:
        """
        Subscribe to multiple arXiv CS categories and merge results.

        The caller can pass a smaller category list for testing, for example:
            subscriber.subscribe(["cs.AI", "cs.CL"])
        """
        selected_categories = list(categories or self.DEFAULT_CATEGORIES)

        all_articles: List[RawArticle] = []
        all_urls: List[str] = []
        all_page_urls: List[str] = []
        seen_urls: set[str] = set()
        latest_status_code = 200

        for index, category in enumerate(selected_categories):
            if index > 0:
                time.sleep(max(0.0, self.config.category_delay_sec))

            page = self.subscribe_category(category)

            latest_status_code = page.status_code
            all_page_urls.append(page.page_url)

            for article in page.articles:
                if article.web_url in seen_urls:
                    continue

                seen_urls.add(article.web_url)
                all_articles.append(article)
                all_urls.append(article.web_url)

        return ArxivRSSPage(
            articles=all_articles,
            article_urls=all_urls,
            page_url=",".join(all_page_urls),
            status_code=latest_status_code,
            category=None,
        )

    def subscribe_category(self, category: str) -> ArxivRSSPage:
        """Subscribe to one arXiv RSS category."""
        page_url = self._build_category_url(category)

        with self._new_http_client() as client:
            rss_text, status_code = self._request_text_with_retry(client, page_url)

        feed = feedparser.parse(rss_text)

        if getattr(feed, "bozo", False):
            raise ArxivRSSError(
                f"Failed to parse arXiv RSS for category {category}: "
                f"{feed.bozo_exception}"
            )

        entries = list(feed.entries)
        max_items = self.config.max_articles_per_category

        if max_items > 0:
            entries = entries[:max_items]

        articles = [
            self._parse_entry(entry, category=category)
            for entry in entries
        ]

        article_urls = [
            article.web_url
            for article in articles
            if article.web_url
        ]

        return ArxivRSSPage(
            articles=articles,
            article_urls=article_urls,
            page_url=page_url,
            status_code=status_code,
            category=category,
        )

    def _build_category_url(self, category: str) -> str:
        return f"{self.config.base_url}{category}"

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

        raise ArxivRSSError(
            f"Failed to fetch arXiv RSS after {self.config.retry_count} retries: {url}"
        ) from last_error

    @staticmethod
    def _request_text(client: httpx.Client, url: str) -> Tuple[str, int]:
        response = client.get(url)

        if response.status_code == 429:
            raise ArxivRSSError(
                "arXiv returned rate limit status 429",
                status_code=429,
            )

        if not response.is_success:
            preview = response.text[:300] if response.text else ""
            raise ArxivRSSError(
                f"arXiv HTTP {response.status_code}: {preview}",
                status_code=response.status_code,
            )

        return response.text, response.status_code

    def _parse_entry(self, entry, *, category: str) -> RawArticle:
        url = self._normalize_arxiv_url(getattr(entry, "link", ""))
        title = self._clean_text(getattr(entry, "title", "")) or "(no title)"

        abstract = (
            getattr(entry, "summary", None)
            or getattr(entry, "description", None)
            or ""
        )
        abstract = self._clean_text(abstract)

        authors = self._extract_authors(entry)
        arxiv_id = self._extract_arxiv_id(url)

        metadata = self._build_metadata(
            authors=authors,
            arxiv_id=arxiv_id,
            category=category,
        )

        body_text = abstract
        if metadata:
            body_text = f"{abstract}\n\n{metadata}" if abstract else metadata

        return RawArticle(
            web_url=url,
            title=title,
            trail_text=abstract or None,
            body_text=body_text or None,
            web_publication_date=self._extract_publication_date(entry),
            section_name=f"arXiv / {category}",
            api_url=None,
        )

    @staticmethod
    def _build_metadata(
        *,
        authors: str,
        arxiv_id: Optional[str],
        category: str,
    ) -> str:
        parts: List[str] = []

        if authors:
            parts.append(f"Authors: {authors}")

        if arxiv_id:
            parts.append(f"arXiv ID: {arxiv_id}")

        parts.append(f"Category: {category}")

        return "\n".join(parts)

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    @classmethod
    def _normalize_arxiv_url(cls, url: str) -> str:
        url = cls._canonical_url(url)

        if "/pdf/" in url:
            url = url.replace("/pdf/", "/abs/")
            url = re.sub(r"\.pdf$", "", url)

        return url

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                "",
                parsed.query,
                "",
            )
        )

    @staticmethod
    def _extract_arxiv_id(url: str) -> Optional[str]:
        match = re.search(r"arxiv\.org/abs/([^/?#]+)", url)
        if match:
            return match.group(1)
        return None

    @classmethod
    def _extract_authors(cls, entry) -> str:
        authors: List[str] = []

        if hasattr(entry, "authors"):
            for author in entry.authors:
                name = author.get("name", "")
                if name:
                    authors.append(cls._clean_text(name))

        if not authors and hasattr(entry, "author"):
            authors.append(cls._clean_text(entry.author))

        author_text = ", ".join(author for author in authors if author)

        if len(author_text) > 4096:
            author_text = author_text[:4090] + "..."

        return author_text

    @classmethod
    def _extract_publication_date(cls, entry) -> Optional[str]:
        for key in ("published", "updated"):
            value = getattr(entry, key, None)
            if not value:
                continue

            try:
                dt_utc = parsedate_to_datetime(value).astimezone(timezone.utc)
                return dt_utc.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return cls._clean_text(value)

        return None


def main() -> None:
    config = ArxivRSSConfig(
        max_articles_per_category=3,
        category_delay_sec=0.5,
    )

    subscriber = ArxivComputerScienceSubscriber(config)

    page = subscriber.subscribe(
        categories=[
            "cs.AI",
            "cs.CL",
            "cs.CV",
        ]
    )

    print(f"Fetched articles: {len(page.articles)}")
    print(f"Fetched URLs: {len(page.article_urls)}")
    print(f"RSS pages: {page.page_url}")
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