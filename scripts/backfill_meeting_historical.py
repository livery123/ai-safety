#!/usr/bin/env python3
"""
功能：2023 至今会议相关新闻历史回溯（NYT 日期窗 + 会议 query 轮换 + Guardian + 官网 agentic）。
输入：--date-from/to、--window-days、--resume checkpoint；环境变量见 core.config。
输出：stdout 进度与 checkpoint JSON。
上下游：crawler.orchestrator sync_nyt/sync_guardian；core.meeting_catalog 官网 URL。
"""
from __future__ import annotations

import os

# 须在首次 import chromadb 之前设置
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@dataclass
class BackfillStats:
    saved: int = 0
    skipped_url_dup: int = 0
    skipped_no_incident: int = 0
    failed: int = 0


@dataclass
class Checkpoint:
    completed: Set[str] = field(default_factory=set)
    stats: BackfillStats = field(default_factory=BackfillStats)
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
        cp.stats = BackfillStats(
            saved=int(st.get("saved", 0)),
            skipped_url_dup=int(st.get("skipped_url_dup", 0)),
            skipped_no_incident=int(st.get("skipped_no_incident", 0)),
            failed=int(st.get("failed", 0)),
        )
        cp.updated_at = str(data.get("updated_at") or "")
        return cp


def _parse_date(value: str) -> date:
    return date.fromisoformat(value.strip()[:10])


def split_windows(date_from: date, date_to: date, window_days: int) -> List[Tuple[date, date]]:
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    span = max(1, int(window_days))
    windows: List[Tuple[date, date]] = []
    cursor = date_from
    while cursor <= date_to:
        win_end = min(cursor + timedelta(days=span - 1), date_to)
        windows.append((cursor, win_end))
        cursor = win_end + timedelta(days=1)
    return windows


def task_key(source: str, query: str, win_start: date, win_end: date) -> str:
    q = query.replace(" ", "_")[:40]
    return f"meeting:{source}:{q}:{win_start.isoformat()}:{win_end.isoformat()}"


def load_checkpoint(path: Path) -> Checkpoint:
    if not path.is_file():
        return Checkpoint()
    return Checkpoint.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_checkpoint(path: Path, cp: Checkpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cp.updated_at = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(cp.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _merge_stats(cp: Checkpoint, res: Any) -> None:
    cp.stats.saved += int(getattr(res, "saved", 0) or 0)
    cp.stats.skipped_url_dup += int(getattr(res, "skipped_url_dup", 0) or 0)
    cp.stats.skipped_no_incident += int(getattr(res, "skipped_no_incident", 0) or 0)
    cp.stats.failed += int(getattr(res, "failed", 0) or 0)


def run_agentic_officials(
    cp: Checkpoint,
    *,
    checkpoint_path: Path,
    pause_sec: float,
) -> None:
    from core.meeting_catalog import load_catalog_series
    from crawler.orchestrator import sync_agentic_url

    urls: List[str] = []
    for s in load_catalog_series():
        urls.extend(s.official_urls)
    urls = list(dict.fromkeys(u for u in urls if u.strip()))
    for url in urls:
        key = f"meeting:agentic:{url}"
        if key in cp.completed:
            continue
        print(f"▶ agentic: {url}", flush=True)
        try:
            res = sync_agentic_url(url, rag_enabled=False)
            _merge_stats(cp, res)
            cp.completed.add(key)
            save_checkpoint(checkpoint_path, cp)
        except Exception as e:
            print(f"  ❌ {type(e).__name__}: {e}", flush=True)
        time.sleep(max(1.0, pause_sec))


def run_backfill(args: argparse.Namespace) -> int:
    from core.config import (
        BACKFILL_MEETING_GUARDIAN_MAX_PAGES,
        BACKFILL_MEETING_NYT_MAX_PAGES,
        MEETING_BACKFILL_QUERIES,
    )
    from crawler.orchestrator import sync_guardian, sync_nyt

    date_from = _parse_date(args.date_from)
    date_to = _parse_date(args.date_to)
    windows = split_windows(date_from, date_to, args.window_days)
    checkpoint_path = Path(args.resume)
    cp = load_checkpoint(checkpoint_path)
    queries = list(MEETING_BACKFILL_QUERIES) or [
        "AI Safety Summit governance",
    ]

    print("=" * 60, flush=True)
    print("会议新闻历史回溯", flush=True)
    print(f"  日期: {date_from} .. {date_to} | 窗: {len(windows)} | query 数: {len(queries)}", flush=True)
    print("=" * 60, flush=True)

    for win_idx, (win_start, win_end) in enumerate(windows, start=1):
        bgn = win_start.strftime("%Y%m%d")
        end = win_end.strftime("%Y%m%d")
        for q in queries:
            key = task_key("nyt", q, win_start, win_end)
            if key in cp.completed:
                continue
            print(f"[{win_idx}/{len(windows)}] NYT {bgn}-{end} | {q[:50]}...", flush=True)
            try:
                res = sync_nyt(
                    query=q,
                    max_pages=BACKFILL_MEETING_NYT_MAX_PAGES,
                    begin_date=bgn,
                    end_date=end,
                    concurrency=args.concurrency,
                    rag_enabled=False,
                )
                _merge_stats(cp, res)
                cp.completed.add(key)
                save_checkpoint(checkpoint_path, cp)
            except Exception as e:
                print(f"  ❌ {type(e).__name__}: {e}", flush=True)
                cp.stats.failed += 1
            time.sleep(args.pause_sec)

        if args.guardian:
            for q in queries:
                gkey = task_key("guardian", q, win_start, win_end)
                if gkey in cp.completed:
                    continue
                print(f"[{win_idx}/{len(windows)}] Guardian | {q[:50]}...", flush=True)
                try:
                    res = sync_guardian(
                        query=q,
                        max_pages=BACKFILL_MEETING_GUARDIAN_MAX_PAGES,
                        concurrency=args.concurrency,
                        rag_enabled=False,
                    )
                    _merge_stats(cp, res)
                    cp.completed.add(gkey)
                    save_checkpoint(checkpoint_path, cp)
                except Exception as e:
                    print(f"  ❌ {type(e).__name__}: {e}", flush=True)
                    cp.stats.failed += 1
                time.sleep(args.pause_sec)

    if args.agentic:
        run_agentic_officials(cp, checkpoint_path=checkpoint_path, pause_sec=args.pause_sec)

    print(
        f"完成 saved={cp.stats.saved} dup={cp.stats.skipped_url_dup} "
        f"irrelevant={cp.stats.skipped_no_incident} failed={cp.stats.failed}",
        flush=True,
    )
    return 0


DEFAULT_CHECKPOINT_PATH = Path("data/checkpoints/meeting_backfill.json")


def main() -> int:
    from core.config import BACKFILL_MEETING_DATE_FROM, BACKFILL_MEETING_WINDOW_DAYS

    p = argparse.ArgumentParser(description="会议新闻历史回溯（2023 至今）")
    p.add_argument("--date-from", default=BACKFILL_MEETING_DATE_FROM)
    p.add_argument("--date-to", default=date.today().isoformat())
    p.add_argument("--window-days", type=int, default=BACKFILL_MEETING_WINDOW_DAYS)
    p.add_argument("--resume", default=str(DEFAULT_CHECKPOINT_PATH))
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--pause-sec", type=float, default=2.0)
    p.add_argument("--guardian", action="store_true", default=True)
    p.add_argument("--no-guardian", action="store_false", dest="guardian")
    p.add_argument("--agentic", action="store_true", help="抓取名录官网 URL")
    args = p.parse_args()
    return run_backfill(args)


if __name__ == "__main__":
    raise SystemExit(main())
