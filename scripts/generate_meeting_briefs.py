#!/usr/bin/env python3
"""
功能：为符合条件的 meeting_events 生成专题分析并写入 meeting_event_analyses。
输入：--event-id 指定单届；否则扫描 events_needing_brief。
输出：stdout 日志。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config import LLM_MODEL, MEETING_BRIEF_DAYS_AFTER_END, MEETING_BRIEF_MIN_ARTICLES  # noqa: E402
from core.mysql_meeting_events import events_needing_brief, save_event_analysis  # noqa: E402
from engine.meeting_brief import generate_meeting_brief_markdown  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="生成会议专题分析")
    p.add_argument("--event-id", type=int, default=0)
    p.add_argument("--min-articles", type=int, default=MEETING_BRIEF_MIN_ARTICLES)
    p.add_argument("--days-after-end", type=int, default=MEETING_BRIEF_DAYS_AFTER_END)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    if args.event_id:
        ids = [args.event_id]
    else:
        rows = events_needing_brief(
            min_articles=args.min_articles,
            days_after_end=args.days_after_end,
        )
        ids = [int(r["id"]) for r in rows]

    if not ids:
        print("无待生成专题的会议事件。")
        return 0

    for eid in ids:
        print(f"▶ 生成 event_id={eid} ...", flush=True)
        md = generate_meeting_brief_markdown(eid)
        aid = save_event_analysis(eid, md, model_name=LLM_MODEL)
        print(f"  ✓ analysis_id={aid}，字数≈{len(md)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
