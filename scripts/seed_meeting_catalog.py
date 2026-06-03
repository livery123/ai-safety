#!/usr/bin/env python3
"""
功能：将 data/conference_catalog.json 写入 MySQL 名录表并创建种子 meeting_events。
输入：JSON 种子文件。
输出：stdout 统计；exit 0。
上下游：migrate_meeting_events.py 之后执行。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.meeting_catalog import (  # noqa: E402
    load_catalog_document,
    load_catalog_series,
    reload_catalog_cache,
)
from core.mysql_meeting_events import get_or_create_event, upsert_catalog_row  # noqa: E402


def main() -> int:
    reload_catalog_cache()
    doc = load_catalog_document()
    ref = str(doc.get("reference_url") or "")
    series_list = load_catalog_series()
    cat_n = 0
    ev_n = 0
    for s in series_list:
        upsert_catalog_row(
            catalog_key=s.catalog_key,
            series_name=s.series_name,
            category=s.category,
            aliases=s.aliases,
            topics=s.topics,
            is_major=s.is_major,
            official_urls=s.official_urls,
            reference_url=ref,
            sort_order=s.sort_order,
        )
        cat_n += 1
        for ev in s.events:
            get_or_create_event(
                catalog_key=s.catalog_key,
                edition_label=ev.edition_label,
                edition_year=ev.edition_year,
                start_date=ev.start_date,
                end_date=ev.end_date,
                location=ev.location,
                host=ev.host,
                countries=ev.participating_countries,
                official_url=ev.official_url,
                status=ev.status,
                notes=ev.notes,
            )
            ev_n += 1
    print(f"已同步名录 {cat_n} 条、会议实例 {ev_n} 条。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
