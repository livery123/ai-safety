#!/usr/bin/env python3
"""
功能：列出 meeting_discovery_candidates 供人工补 catalog。
输入：--limit、--min-score。
输出：stdout 表格。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.mysql_meeting_events import list_discovery_candidates  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="列出未归并 meeting 发现候选")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--min-score", type=float, default=0.0)
    args = p.parse_args()
    rows = list_discovery_candidates(limit=args.limit, min_score=args.min_score)
    if not rows:
        print("无候选记录。")
        return 0
    print(f"{'id':>4} | {'score':>5} | {'article':>7} | catalog_key | title")
    print("-" * 90)
    for r in rows:
        print(
            f"{int(r['id']):>4} | {float(r.get('match_score') or 0):>5.2f} | "
            f"{int(r['article_id']):>7} | {str(r.get('meeting_catalog_key') or '')[:14]:14} | "
            f"{str(r.get('title') or '')[:50]}"
        )
        if r.get("proposed_series_name"):
            print(f"      proposed: {r['proposed_series_name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
