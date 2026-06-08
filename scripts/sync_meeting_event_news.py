#!/usr/bin/env python3
"""
功能：按 meeting_events 会期窗口定向检索 NYT（可选 Guardian）新闻并入库。
输入：--catalog-key、--event-id、--recent、--guardian、--resume；见 core.config MEETING_NEWS_*。
输出：checkpoint + stdout；复用 crawler.orchestrator sync_nyt/sync_guardian。
上下游：seed_meeting_catalog 之后；之后执行 link_meeting_articles、generate_meeting_briefs。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional, Set

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")


@dataclass
class SyncStats:
    saved: int = 0
    skipped_url_dup: int = 0
    skipped_no_incident: int = 0
    failed: int = 0


@dataclass
class Checkpoint:
    completed: Set[str] = field(default_factory=set)
    stats: SyncStats = field(default_factory=SyncStats)
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "completed": sorted(self.completed),
            "stats": {
                "saved": self.stats.saved,
                "skipped_url_dup": self.stats.skipped_url_dup,
                "skipped_no_incident": self.stats.skipped_no_incident,
                "failed": self.stats.failed,
            },
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Checkpoint":
        cp = cls()
        cp.completed = set(data.get("completed") or [])
        st = data.get("stats") or {}
        cp.stats = SyncStats(
            saved=int(st.get("saved", 0)),
            skipped_url_dup=int(st.get("skipped_url_dup", 0)),
            skipped_no_incident=int(st.get("skipped_no_incident", 0)),
            failed=int(st.get("failed", 0)),
        )
        cp.updated_at = str(data.get("updated_at") or "")
        return cp


DEFAULT_CHECKPOINT = Path("data/checkpoints/meeting_event_news.json")


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _event_date_window(
    row: dict,
    *,
    pre_days: int,
    post_days: int,
) -> tuple[Optional[date], Optional[date]]:
    """根据会期计算 NYT begin/end；无日期则用 edition_year 全年。"""
    start = _parse_date(row.get("start_date"))
    end = _parse_date(row.get("end_date")) or start
    if start:
        d0 = start - timedelta(days=pre_days)
        d1 = (end or start) + timedelta(days=post_days)
        return d0, d1
    yr = row.get("edition_year")
    if yr is not None and str(yr).isdigit():
        y = int(yr)
        return date(y, 1, 1), date(y, 12, 31)
    return None, None


def _build_query(row: dict) -> str:
    from core.meeting_catalog import (
        CatalogEventSeed,
        build_event_search_query,
        get_series_by_key,
    )

    ck = str(row.get("catalog_key") or "")
    series = get_series_by_key(ck)
    if not series:
        label = str(row.get("edition_label") or ck)
        return label[:128]
    ev = CatalogEventSeed(
        edition_label=str(row.get("edition_label") or ""),
        edition_year=int(row["edition_year"]) if row.get("edition_year") else None,
    )
    return build_event_search_query(series, ev)


def _merge_stats(cp: Checkpoint, res: Any) -> None:
    cp.stats.saved += int(getattr(res, "saved", 0) or 0)
    cp.stats.skipped_url_dup += int(getattr(res, "skipped_url_dup", 0) or 0)
    cp.stats.skipped_no_incident += int(getattr(res, "skipped_no_incident", 0) or 0)
    cp.stats.failed += int(getattr(res, "failed", 0) or 0)


def run(args: argparse.Namespace) -> int:
    from core.config import (
        MEETING_NEWS_GUARDIAN_MAX_PAGES,
        MEETING_NEWS_NYT_MAX_PAGES,
        MEETING_NEWS_POST_DAYS,
        MEETING_NEWS_PRE_DAYS,
    )
    from core.mysql_meeting_events import list_events_for_news_sync
    from crawler.orchestrator import sync_guardian, sync_nyt

    cp_path = Path(args.resume)
    cp = Checkpoint()
    if cp_path.is_file():
        cp = Checkpoint.from_dict(json.loads(cp_path.read_text(encoding="utf-8")))

    rows = list_events_for_news_sync(
        catalog_key=args.catalog_key or None,
        event_id=args.event_id or None,
        recent_only=args.recent,
        limit=args.limit,
    )
    if not rows:
        print("无待同步的 meeting_events。", flush=True)
        return 0

    print("=" * 60, flush=True)
    print("按届定向会议新闻检索", flush=True)
    print(f"  届次数: {len(rows)} | recent={args.recent} | guardian={args.guardian}", flush=True)
    print("=" * 60, flush=True)

    from core.meeting_catalog import get_preferred_sources

    for row in rows:
        eid = int(row["id"])
        ck = str(row.get("catalog_key") or "")
        label = str(row.get("edition_label") or "")[:40]
        sources = get_preferred_sources(ck)
        pending = [
            s
            for s in sources
            if f"event_news:{s}:{eid}" not in cp.completed
            and (s != "guardian" or args.guardian)
        ]
        if not pending:
            continue

        d0, d1 = _event_date_window(
            row, pre_days=MEETING_NEWS_PRE_DAYS, post_days=MEETING_NEWS_POST_DAYS
        )
        if not d0 or not d1:
            print(f"⏭ event_id={eid} 无有效日期窗，跳过: {label}", flush=True)
            for s in sources:
                if s == "guardian" and not args.guardian:
                    continue
                cp.completed.add(f"event_news:{s}:{eid}")
            save_checkpoint(cp_path, cp)
            continue

        query = _build_query(row)
        bgn = d0.strftime("%Y%m%d")
        end = d1.strftime("%Y%m%d")

        for src in sources:
            if src == "guardian" and not args.guardian:
                continue
            key = f"event_news:{src}:{eid}"
            if key in cp.completed:
                continue
            if src == "nyt":
                print(
                    f"▶ NYT event_id={eid} {label} | {bgn}-{end} | {query[:60]}...",
                    flush=True,
                )
                try:
                    res = sync_nyt(
                        query=query,
                        max_pages=MEETING_NEWS_NYT_MAX_PAGES,
                        begin_date=bgn,
                        end_date=end,
                        concurrency=args.concurrency,
                        rag_enabled=False,
                    )
                    _merge_stats(cp, res)
                    cp.completed.add(key)
                    save_checkpoint(cp_path, cp)
                except Exception as e:
                    print(f"  ❌ NYT {type(e).__name__}: {e}", flush=True)
                    cp.stats.failed += 1
            elif src == "guardian":
                print(f"▶ Guardian event_id={eid} {label} | {query[:60]}...", flush=True)
                try:
                    res_g = sync_guardian(
                        query=query,
                        max_pages=MEETING_NEWS_GUARDIAN_MAX_PAGES,
                        concurrency=args.concurrency,
                        rag_enabled=False,
                    )
                    _merge_stats(cp, res_g)
                    cp.completed.add(key)
                    save_checkpoint(cp_path, cp)
                except Exception as e:
                    print(f"  ❌ Guardian {type(e).__name__}: {e}", flush=True)
                    cp.stats.failed += 1
            time.sleep(args.pause_sec)

    print(
        f"完成 saved={cp.stats.saved} dup={cp.stats.skipped_url_dup} "
        f"irrelevant={cp.stats.skipped_no_incident} failed={cp.stats.failed}",
        flush=True,
    )
    if args.link_after:
        from services.meeting_event_linker import batch_link_meeting_articles

        st = batch_link_meeting_articles(limit=args.link_limit, only_unlinked=True)
        print(f"关联：linked={st['linked']} skipped={st['skipped']}", flush=True)
    return 0


def save_checkpoint(path: Path, cp: Checkpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cp.updated_at = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(cp.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    from core.config import MEETING_NEWS_GUARDIAN_DEFAULT

    p = argparse.ArgumentParser(description="按届定向检索会议新闻（NYT/Guardian）")
    p.add_argument("--catalog-key", default="", help="仅同步该系列")
    p.add_argument("--event-id", type=int, default=0, help="仅同步该届")
    p.add_argument("--recent", action="store_true", help="仅近/近期会期（cron 用）")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--guardian", action="store_true", help="每届跑 Guardian（默认见 MEETING_NEWS_GUARDIAN_DEFAULT）")
    p.add_argument("--no-guardian", action="store_false", dest="guardian")
    p.set_defaults(guardian=MEETING_NEWS_GUARDIAN_DEFAULT)
    p.add_argument("--resume", default=str(DEFAULT_CHECKPOINT))
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--pause-sec", type=float, default=2.0)
    p.add_argument("--link-after", action="store_true", help="结束后自动 link_meeting_articles")
    p.add_argument("--link-limit", type=int, default=2000)
    args = p.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
