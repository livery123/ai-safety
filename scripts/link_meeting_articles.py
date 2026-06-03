#!/usr/bin/env python3
"""
功能：批量将存量 content_type=meeting 文章关联到 meeting_events。
输入：--limit、--offset、--all（含已关联）。
输出：stdout 统计。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.meeting_event_linker import batch_link_meeting_articles  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="批量关联会议文章到事件")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--all", action="store_true", help="包含已关联文章（默认仅未关联）")
    args = p.parse_args()
    stats = batch_link_meeting_articles(
        limit=args.limit,
        offset=args.offset,
        only_unlinked=not args.all,
    )
    print(f"完成：关联 {stats['linked']} 条，跳过 {stats['skipped']} 条。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
