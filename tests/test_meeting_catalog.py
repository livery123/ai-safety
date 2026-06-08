"""会议名录匹配单元测试。"""

from core.meeting_catalog import load_catalog_series, match_catalog_key


def test_match_ai_safety_summit():
    m = match_catalog_key(
        title="Leaders gather at Bletchley for AI Safety Summit 2023",
        main_topic="AI Safety Summit 2023",
    )
    assert m is not None
    assert m.catalog_key == "ai_safety_summit"


def test_match_india_ai_safety():
    m = match_catalog_key(title="India AI Safety Summit opens in New Delhi")
    assert m is not None
    assert m.catalog_key == "india_ai_safety"


def test_catalog_loads():
    series = load_catalog_series()
    assert len(series) >= 6
    keys = {s.catalog_key for s in series}
    assert "reaim" in keys
    assert "waic" in keys


def test_build_query_and_crawl_urls():
    from core.meeting_catalog import (
        build_event_search_query,
        get_series_by_key,
        iter_catalog_crawl_urls,
        find_seed_event,
    )

    s = get_series_by_key("ai_safety_summit")
    assert s is not None
    ev = find_seed_event(s, 2023)
    assert ev is not None
    q = build_event_search_query(s, ev)
    assert "2023" in q or "Safety" in q or "Bletchley" in q
    urls = iter_catalog_crawl_urls()
    assert len(urls) >= 10
    assert any("gov.uk" in u.url for u in urls)


def test_preferred_sources_and_manual_urls():
    from core.meeting_catalog import (
        get_preferred_sources,
        iter_meeting_ingest_urls,
        reload_catalog_cache,
    )

    reload_catalog_cache()
    assert "guardian" in get_preferred_sources("waic")
    manual = iter_meeting_ingest_urls(catalog_key="ai_safety_summit", edition_year=2024)
    assert any("seoul-declaration" in u.url or "gov.uk" in u.url for u in manual)


def test_resolve_official_hint():
    from core.meeting_catalog import reload_catalog_cache, resolve_meeting_official_hint

    reload_catalog_cache()
    ck, hint = resolve_meeting_official_hint(
        "https://www.gov.uk/government/topical-events/ai-safety-summit-2023"
    )
    assert ck == "ai_safety_summit"
    assert "meeting" in hint
