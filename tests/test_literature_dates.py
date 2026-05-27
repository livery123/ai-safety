"""文献发表时间解析单元测试。"""

from datetime import datetime

from crawler.sources.literature import parse_literature_published_at


def test_parse_literature_published_at_iso_with_timezone_to_utc():
    """arXiv RSS 美东偏移应转为 UTC，而非截断偏移量。"""
    dt = parse_literature_published_at("2026-05-25T00:00:00-04:00")
    assert dt == datetime(2026, 5, 25, 4, 0, 0)


def test_parse_literature_published_at_date_only():
    dt = parse_literature_published_at("2026-05-15")
    assert dt == datetime(2026, 5, 15, 0, 0, 0)


def test_parse_literature_published_at_z_suffix():
    dt = parse_literature_published_at("2026-05-15T12:30:00Z")
    assert dt == datetime(2026, 5, 15, 12, 30, 0)
