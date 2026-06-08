#!/usr/bin/env python3
"""
功能：批量 agentic 抓取 catalog 中 manual_ingest_urls / crawl_urls / official_url。
输入：--event-id、--catalog-key、--from-catalog、--resume、--link-after。
输出：checkpoint + stdout。
上下游：core.meeting_catalog.iter_meeting_ingest_urls；crawler.sync_agentic_url。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Set

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

DEFAULT_CHECKPOINT = Path("data/checkpoints/meeting_manual_urls.json")


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


def _merge_stats(cp: Checkpoint, res: Any) -> None:
    cp.stats.saved += int(getattr(res, "saved", 0) or 0)
    cp.stats.skipped_url_dup += int(getattr(res, "skipped_url_dup", 0) or 0)
    cp.stats.skipped_no_incident += int(getattr(res, "skipped_no_incident", 0) or 0)
    cp.stats.failed += int(getattr(res, "failed", 0) or 0)


def save_checkpoint(path: Path, cp: Checkpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cp.updated_at = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(cp.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    from core.meeting_catalog import iter_meeting_ingest_urls, reload_catalog_cache
    from core.mysql_meeting_events import get_event_by_id
    from crawler.orchestrator import sync_agentic_url

    p = argparse.ArgumentParser(description="批量 agentic 入库会议 URL")
    p.add_argument("--event-id", type=int, default=0)
    p.add_argument("--catalog-key", default="")
    p.add_argument("--from-catalog", action="store_true", help="处理全部 catalog URL")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--resume", default=str(DEFAULT_CHECKPOINT))
    p.add_argument("--pause-sec", type=float, default=1.5)
    p.add_argument("--link-after", action="store_true")
    p.add_argument("--link-limit", type=int, default=2000)
    args = p.parse_args()

    reload_catalog_cache()
    cp_path = Path(args.resume)
    cp = Checkpoint()
    if cp_path.is_file():
        cp = Checkpoint.from_dict(json.loads(cp_path.read_text(encoding="utf-8")))

    ck_filter = args.catalog_key.strip() or None
    yr_filter = None
    if args.event_id:
        ev = get_event_by_id(args.event_id)
        if not ev:
            print(f"未找到 event_id={args.event_id}")
            return 1
        ck_filter = str(ev.get("catalog_key") or "")
        yr = ev.get("edition_year")
        yr_filter = int(yr) if yr is not None else None

    if not args.from_catalog and not args.event_id and not ck_filter:
        print("请指定 --from-catalog、--event-id 或 --catalog-key")
        return 1

    items = iter_meeting_ingest_urls(catalog_key=ck_filter, edition_year=yr_filter)
    if args.limit:
        items = items[: args.limit]

    print(f"待抓取 URL：{len(items)}", flush=True)
    for item in items:
        if item.url in cp.completed:
            continue
        print(f"▶ {item.catalog_key} | {item.edition_label[:30]} | {item.url[:70]}...", flush=True)
        try:
            res = sync_agentic_url(item.url, rag_enabled=False)
            _merge_stats(cp, res)
            cp.completed.add(item.url)
            save_checkpoint(cp_path, cp)
        except Exception as e:
            print(f"  ❌ {type(e).__name__}: {e}", flush=True)
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


if __name__ == "__main__":
    raise SystemExit(main())
