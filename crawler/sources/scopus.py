"""
Scopus crawler.

Fetches articles from the Elsevier Scopus Search API and converts entries
into the shared RawArticle structure used by the crawler pipeline.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
class ScopusPage:
    """Result returned by one Scopus API search."""

    articles: List[RawArticle]
    article_urls: List[str]
    api_url: str
    status_code: int
    total_results: int


@dataclass(frozen=True)
class ScopusConfig:
    """Runtime configuration for Scopus API crawling."""

    api_key: str = "0ca428eb9d1de051aad56aa2200f1fda"
    base_url: str = "https://api.elsevier.com/content/search/scopus"
    query_keyword: str = "artificial intelligence"
    subject_area: Optional[str] = "COMP"
    days_back: int = 3
    count: int = 25
    max_results: int = 200
    timeout_sec: float = 30.0
    retry_count: int = 3
    retry_delay_sec: float = 5.0
    rate_limit_delay_sec: float = 10.0
    page_delay_sec: float = 1.0


class ScopusError(Exception):
    """Raised when Scopus API request or parsing fails."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ScopusSubscriber:
    """Subscriber for Elsevier Scopus search results."""

    def __init__(self, config: ScopusConfig) -> None:
        self.config = config

    @classmethod
    def from_env(cls) -> "ScopusSubscriber":
        api_key = "0ca428eb9d1de051aad56aa2200f1fda"
        if not api_key:
            raise ScopusError(
                "Missing SCOPUS_API_KEY environment variable. "
                "Please set it before running the Scopus crawler."
            )

        return cls(ScopusConfig(api_key=api_key))

    def subscribe(self, *, download_date: Optional[date] = None) -> ScopusPage:
        """
        Fetch Scopus articles and return them as RawArticle objects.

        DB insert and deduplication should be handled by the outer pipeline.
        """
        download_date = download_date or date.today()
        query = self._build_query(download_date)

        all_articles: List[RawArticle] = []
        all_urls: List[str] = []
        seen_urls: set[str] = set()

        start = 0
        total_results = 0
        latest_status_code = 200

        with self._new_http_client() as client:
            while True:
                payload, status_code, api_url = self._fetch_page(
                    client=client,
                    query=query,
                    start=start,
                )

                latest_status_code = status_code

                entries = self._extract_entries(payload)
                total_results = self._extract_total_results(payload)

                if not entries:
                    break

                for entry in entries:
                    article = self._parse_entry(entry, api_url=api_url)

                    if not article.web_url:
                        continue

                    if article.web_url in seen_urls:
                        continue

                    seen_urls.add(article.web_url)
                    all_articles.append(article)
                    all_urls.append(article.web_url)

                start += self.config.count

                if start >= total_results:
                    break

                if start >= self.config.max_results:
                    break

                time.sleep(max(0.0, self.config.page_delay_sec))

        return ScopusPage(
            articles=all_articles,
            article_urls=all_urls,
            api_url=self.config.base_url,
            status_code=latest_status_code,
            total_results=total_results,
        )

    def _build_query(self, download_date: date) -> str:
        since_date = download_date - timedelta(days=self.config.days_back)
        date_str = since_date.strftime("%Y%m%d")

        query = (
            f'TITLE-ABS-KEY("{self.config.query_keyword}") '
            f"AND ORIG-LOAD-DATE > {date_str}"
        )

        if self.config.subject_area:
            query += f" AND SUBJAREA({self.config.subject_area})"

        return query

    def _new_http_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.config.timeout_sec,
            follow_redirects=True,
            headers={
                "X-ELS-APIKey": self.config.api_key,
                "Accept": "application/json",
            },
        )

    def _fetch_page(
        self,
        *,
        client: httpx.Client,
        query: str,
        start: int,
    ) -> Tuple[Dict[str, Any], int, str]:
        last_error: Optional[Exception] = None

        for attempt in range(self.config.retry_count):
            try:
                return self._request_page(
                    client=client,
                    query=query,
                    start=start,
                )
            except ScopusError as exc:
                last_error = exc

                if exc.status_code == 429:
                    time.sleep(max(0.0, self.config.rate_limit_delay_sec))
                elif attempt < self.config.retry_count - 1:
                    time.sleep(max(0.0, self.config.retry_delay_sec))
                else:
                    break

        raise ScopusError(
            f"Failed to fetch Scopus page after {self.config.retry_count} retries. "
            f"start={start}"
        ) from last_error

    def _request_page(
        self,
        *,
        client: httpx.Client,
        query: str,
        start: int,
    ) -> Tuple[Dict[str, Any], int, str]:
        response = client.get(
            self.config.base_url,
            params={
                "query": query,
                "start": start,
                "count": self.config.count,
            },
        )

        if response.status_code == 429:
            raise ScopusError(
                "Scopus API returned rate limit status 429.",
                status_code=429,
            )

        if not response.is_success:
            preview = response.text[:500] if response.text else ""
            raise ScopusError(
                f"Scopus API HTTP {response.status_code}: {preview}",
                status_code=response.status_code,
            )

        return response.json(), response.status_code, str(response.url)

    @staticmethod
    def _extract_entries(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        search_results = payload.get("search-results", {})
        entries = search_results.get("entry", [])

        if not isinstance(entries, list):
            return []

        return [
            entry
            for entry in entries
            if isinstance(entry, dict)
        ]

    @staticmethod
    def _extract_total_results(payload: Dict[str, Any]) -> int:
        search_results = payload.get("search-results", {})
        value = search_results.get("opensearch:totalResults", 0)

        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _parse_entry(self, entry: Dict[str, Any], *, api_url: str) -> RawArticle:
        title = self._truncate(self._get_string(entry, "dc:title"), 255) or "(no title)"

        doi = self._get_string(entry, "prism:doi")
        url = (
            self._get_string(entry, "prism:url")
            or self._get_string(entry, "link")
            or doi
        )

        publication = self._get_string(entry, "prism:publicationName")
        cover_date = self._get_string(entry, "prism:coverDate")
        article_type = self._get_string(entry, "prism:aggregationType")
        subtype = self._get_string(entry, "subtypeDescription")

        affiliations = self._extract_affiliations(entry)

        metadata = self._build_metadata(
            doi=doi,
            publication=publication,
            article_type=article_type,
            subtype=subtype,
            affiliations=affiliations,
        )

        return RawArticle(
            web_url=url,
            title=title,
            trail_text=publication or None,
            body_text=metadata or None,
            web_publication_date=cover_date or None,
            section_name=f"Scopus / {publication}" if publication else "Scopus",
            api_url=api_url,
        )

    @staticmethod
    def _extract_affiliations(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
        affiliations = entry.get("affiliation", [])

        if not isinstance(affiliations, list):
            return []

        return [
            affiliation
            for affiliation in affiliations
            if isinstance(affiliation, dict)
        ]

    @staticmethod
    def _build_metadata(
        *,
        doi: str,
        publication: str,
        article_type: str,
        subtype: str,
        affiliations: List[Dict[str, Any]],
    ) -> Optional[str]:
        parts: List[str] = []

        if doi:
            parts.append(f"DOI: {doi}")

        if publication:
            parts.append(f"Publication: {publication}")

        if article_type:
            parts.append(f"Article Type: {article_type}")

        if subtype:
            parts.append(f"Subtype: {subtype}")

        if affiliations:
            parts.append(
                "Affiliations: "
                + json.dumps(affiliations, ensure_ascii=False)
            )

        return "\n".join(parts) if parts else None

    @staticmethod
    def _get_string(data: Dict[str, Any], key: str) -> str:
        value = data.get(key)

        if isinstance(value, str):
            return value.strip()

        if value is None:
            return ""

        return str(value).strip()

    @staticmethod
    def _truncate(value: str, max_length: int) -> str:
        if len(value) <= max_length:
            return value

        return value[:max_length]


def main() -> None:
    subscriber = ScopusSubscriber.from_env()

    page = subscriber.subscribe(
        download_date=date.today(),
    )

    print(f"Fetched articles: {len(page.articles)}")
    print(f"Fetched URLs: {len(page.article_urls)}")
    print(f"Total results from API: {page.total_results}")
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