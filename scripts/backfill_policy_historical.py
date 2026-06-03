#!/usr/bin/env python3
"""
五国政策源历史回溯：按时间窗分批拉取并入库，直至政策赛道总量达标。

功能：仅 policy 五国（US/UK/EU/IN/BR），不含 Guardian/NYT；支持 checkpoint 断点续跑、
      达标自动停止（count_policy_track_rows >= target-count）。
输入：--date-from/to、--window-days、--target-count、--resume 等；见 --help。
输出：stdout 进度；checkpoint JSON；日志文件；副作用：HTTP + LLM + MySQL。
上下游：crawler.orchestrator.sync_policy；core.mysql_monitor_tracks.count_policy_track_rows。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@dataclass
class BackfillStats:
    """回溯累计统计。"""

    saved: int = 0
    skipped_url_dup: int = 0
    skipped_no_incident: int = 0
    failed: int = 0


@dataclass
class Checkpoint:
    """断点：已完成 task key 集合 + 累计 stats。"""

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


def split_windows(
    date_from: date,
    date_to: date,
    window_days: int,
) -> List[Tuple[date, date]]:
    """
    功能：将 [date_from, date_to] 切分为若干闭区间窗。
    输入：起止日、窗长（天）。
    输出：[(win_start, win_end), ...]。
    """
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


def task_key(country: str, win_start: date, win_end: date) -> str:
    """单国单窗 checkpoint key。"""
    return f"policy:{country.upper()}:{win_start.isoformat()}:{win_end.isoformat()}"


def _legacy_key_to_new(key: str) -> Optional[str]:
    """
    旧 checkpoint 格式 policy:2023-06-01:2023-06-30:US → 新格式。
    """
    parts = key.split(":")
    if len(parts) != 4 or parts[0] != "policy":
        return None
    d1, d2, country = parts[1], parts[2], parts[3]
    try:
        _parse_date(d1)
        _parse_date(d2)
    except ValueError:
        return None
    return task_key(country, _parse_date(d1), _parse_date(d2))


def load_checkpoint(path: Path) -> Checkpoint:
    if not path.is_file():
        return Checkpoint()
    data = json.loads(path.read_text(encoding="utf-8"))
    return Checkpoint.from_dict(data)


def save_checkpoint(path: Path, cp: Checkpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cp.updated_at = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(cp.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def import_legacy_policy_keys(cp: Checkpoint, legacy_path: Path) -> int:
    """从旧全信源 checkpoint 导入已完成的 policy 窗。"""
    if not legacy_path.is_file():
        return 0
    data = json.loads(legacy_path.read_text(encoding="utf-8"))
    added = 0
    for key in data.get("completed") or []:
        if not str(key).startswith("policy:"):
            continue
        new_key = _legacy_key_to_new(str(key))
        if new_key and new_key not in cp.completed:
            cp.completed.add(new_key)
            added += 1
    return added


def _maybe_cron_quiet(cron_quiet_hours: Optional[Iterable[int]], pause_minutes: int) -> None:
    """cron 整点附近短暂暂停，减少 LLM 争抢。"""
    if not cron_quiet_hours:
        return
    hour = datetime.now().hour
    if hour in set(cron_quiet_hours):
        print(f"⏸ cron 错峰：当前 {hour} 点，暂停 {pause_minutes} 分钟...", flush=True)
        time.sleep(max(60, pause_minutes * 60))


def run_backfill(args: argparse.Namespace) -> int:
    from core.mysql_monitor_tracks import count_policy_track_rows
    from crawler.orchestrator import sync_policy

    date_from = _parse_date(args.date_from)
    date_to = _parse_date(args.date_to)
    countries = [c.strip().upper() for c in args.countries.split(",") if c.strip()]
    windows = split_windows(date_from, date_to, args.window_days)
    cp_path = Path(args.resume)
    cp = load_checkpoint(cp_path)

    if args.import_legacy:
        legacy = Path(args.import_legacy)
        n = import_legacy_policy_keys(cp, legacy)
        if n:
            print(f"✓ 从 {legacy} 导入 {n} 条已完成 policy 窗", flush=True)
            save_checkpoint(cp_path, cp)

    target = int(args.target_count)
    initial_track = count_policy_track_rows()
    print("=" * 60, flush=True)
    print("五国政策历史回溯", flush=True)
    print(f"  日期: {date_from} .. {date_to} | 窗数: {len(windows)} | 窗长: {args.window_days}d", flush=True)
    print(f"  国家: {', '.join(countries)} | 每国每窗≤{args.max_per_window}", flush=True)
    print(f"  政策赛道: {initial_track} / 目标 {target}", flush=True)
    print(f"  checkpoint: {cp_path}", flush=True)
    print("=" * 60, flush=True)

    if initial_track >= target:
        print(f"✅ 已达目标 policy_track={initial_track} >= {target}，无需回溯", flush=True)
        return 0

    for win_idx, (win_start, win_end) in enumerate(windows, start=1):
        if count_policy_track_rows() >= target:
            print(f"✅ 政策赛道已达 {target}，停止回溯", flush=True)
            break

        print(f"\n{'=' * 60}", flush=True)
        print(f"窗口 [{win_idx}/{len(windows)}] {win_start} .. {win_end}", flush=True)
        print(f"{'=' * 60}", flush=True)

        for country in countries:
            key = task_key(country, win_start, win_end)
            if key in cp.completed:
                print(f"  ⏭ {country} 已完成，跳过", flush=True)
                continue

            _maybe_cron_quiet(args.cron_quiet_hours, args.cron_quiet_minutes)

            print(f"  ▶ policy:{country}", flush=True)
            try:
                result = sync_policy(
                    countries=[country],
                    max_articles_per_country=args.max_per_window,
                    date_from=win_start,
                    date_to=win_end,
                    concurrency=args.concurrency,
                    rag_enabled=True if args.rag else False,
                    dry_run=args.dry_run,
                )
            except Exception as exc:
                print(f"     ❌ 异常: {type(exc).__name__}: {exc}", flush=True)
                cp.stats.failed += 1
                save_checkpoint(cp_path, cp)
                continue

            cp.stats.saved += result.saved
            cp.stats.skipped_url_dup += result.skipped_url_dup
            cp.stats.skipped_no_incident += result.skipped_no_incident
            cp.stats.failed += result.failed
            cp.completed.add(key)
            save_checkpoint(cp_path, cp)

            track_now = count_policy_track_rows()
            print(
                f"     入库 {result.saved} | 跳过 {result.skipped_url_dup} | "
                f"无关 {result.skipped_no_incident} | 政策赛道 {track_now}/{target}",
                flush=True,
            )

            if track_now >= target:
                print(f"✅ 政策赛道已达 {target}", flush=True)
                break

        if count_policy_track_rows() >= target:
            break

    final_track = count_policy_track_rows()
    print("\n" + "=" * 60, flush=True)
    print(
        f"回溯结束 | 政策赛道 {final_track}/{target} | "
        f"本次 saved={cp.stats.saved} dup={cp.stats.skipped_url_dup} "
        f"无关={cp.stats.skipped_no_incident} fail={cp.stats.failed}",
        flush=True,
    )
    print("=" * 60, flush=True)
    return 0 if final_track >= target else 0


def main() -> int:
    import functools
    global print
    print = functools.partial(print, flush=True)  # type: ignore[assignment]

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    default_from = (date.today() - timedelta(days=365 * 3)).isoformat()

    parser = argparse.ArgumentParser(description="五国政策源历史回溯（policy-only）")
    parser.add_argument("--date-from", default=default_from, help="起始日 YYYY-MM-DD")
    parser.add_argument("--date-to", default=yesterday, help="结束日 YYYY-MM-DD")
    parser.add_argument("--window-days", type=int, default=30, help="每窗天数")
    parser.add_argument("--max-per-window", type=int, default=100, help="每国每窗 raw 上限")
    parser.add_argument("--target-count", type=int, default=1000, help="政策赛道目标条数")
    parser.add_argument(
        "--countries",
        default="US,UK,EU,IN,BR",
        help="逗号分隔国家代码",
    )
    parser.add_argument("--concurrency", type=int, default=2, help="LLM 并发")
    parser.add_argument("--rag", action="store_true", help="开启 RAG（默认关闭以提速）")
    parser.add_argument("--dry-run", action="store_true", help="不入库")
    parser.add_argument(
        "--resume",
        default="logs/backfill_policy_checkpoint.json",
        help="checkpoint 路径",
    )
    parser.add_argument(
        "--import-legacy",
        default="",
        help="导入旧 logs/backfill_checkpoint.json 中 policy 窗（可选）",
    )
    parser.add_argument(
        "--cron-quiet-hours",
        default="0,6,12,18",
        help="这些整点小时前暂停（逗号分隔，空=关闭）",
    )
    parser.add_argument("--cron-quiet-minutes", type=int, default=30, help="错峰暂停分钟数")
    args = parser.parse_args()

    quiet_raw = (args.cron_quiet_hours or "").strip()
    args.cron_quiet_hours = (
        [int(x.strip()) for x in quiet_raw.split(",") if x.strip()]
        if quiet_raw
        else None
    )

    resume_path = Path(args.resume)
    if not resume_path.is_absolute():
        resume_path = _ROOT / resume_path
    args.resume = str(resume_path)

    if args.import_legacy:
        leg = Path(args.import_legacy)
        if not leg.is_absolute():
            leg = _ROOT / leg
        args.import_legacy = str(leg)
    else:
        args.import_legacy = ""

    try:
        from core import config as app_config

        if args.target_count == 1000 and hasattr(app_config, "BACKFILL_POLICY_TARGET"):
            args.target_count = int(app_config.BACKFILL_POLICY_TARGET)
        if args.window_days == 30 and hasattr(app_config, "BACKFILL_POLICY_WINDOW_DAYS"):
            args.window_days = int(app_config.BACKFILL_POLICY_WINDOW_DAYS)
        if args.max_per_window == 100 and hasattr(app_config, "BACKFILL_POLICY_MAX_PER_WINDOW"):
            args.max_per_window = int(app_config.BACKFILL_POLICY_MAX_PER_WINDOW)
    except ImportError:
        pass

    return run_backfill(args)


if __name__ == "__main__":
    raise SystemExit(main())
