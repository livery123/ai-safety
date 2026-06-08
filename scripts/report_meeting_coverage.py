#!/usr/bin/env python3
"""
功能：输出各届 meeting_events 关联报道与专题覆盖率，便于补洞运维。
输入：--json 可选写入 data/reports/meeting_coverage.json。
输出：stdout 表格。
上下游：P0 缺稿盘点；cron 可选周报。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.mysql_meeting_events import mysql_conn  # noqa: E402


def fetch_coverage_rows() -> list[dict]:
    sql = """
        SELECT e.id, e.catalog_key, e.edition_label, e.start_date, e.end_date,
               COUNT(m.id) AS article_count,
               (SELECT COUNT(*) FROM meeting_event_analyses a WHERE a.event_id = e.id) AS brief_count,
               (SELECT CHAR_LENGTH(COALESCE(
                    (SELECT analysis_markdown FROM meeting_event_analyses a2
                     WHERE a2.event_id = e.id ORDER BY a2.id DESC LIMIT 1), ''))
                ) AS brief_chars
        FROM meeting_events e
        LEFT JOIN meeting_event_articles m ON m.event_id = e.id
        GROUP BY e.id
        ORDER BY article_count ASC, e.id ASC
    """
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return list(cur.fetchall() or [])


def main() -> int:
    p = argparse.ArgumentParser(description="会议届次证据覆盖率报告")
    p.add_argument("--json", default="", help="写入 JSON 路径")
    args = p.parse_args()
    rows = fetch_coverage_rows()
    print(f"{'id':>3} | {'articles':>8} | {'brief':>5} | {'chars':>6} | edition")
    print("-" * 72)
    zero = 0
    for r in rows:
        ac = int(r.get("article_count") or 0)
        if ac == 0:
            zero += 1
        label = str(r.get("edition_label") or "")[:42]
        print(
            f"{int(r['id']):>3} | {ac:>8} | {int(r.get('brief_count') or 0):>5} | "
            f"{int(r.get('brief_chars') or 0):>6} | {label}"
        )
    print(f"\n合计 {len(rows)} 届，article_count=0 共 {zero} 届。")
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "rows": [
                {
                    "id": int(r["id"]),
                    "catalog_key": str(r.get("catalog_key") or ""),
                    "edition_label": str(r.get("edition_label") or ""),
                    "article_count": int(r.get("article_count") or 0),
                    "brief_count": int(r.get("brief_count") or 0),
                    "brief_chars": int(r.get("brief_chars") or 0),
                }
                for r in rows
            ]
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
