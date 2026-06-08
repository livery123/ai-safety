#!/usr/bin/env python3
"""
功能：创建 meeting_discovery_candidates 表（未归并 meeting 稿审计）。
输入：无。
输出：stdout；exit 0/1。
上下游：services/meeting_event_linker；scripts/list_meeting_discovery.py。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.mysql_db import mysql_conn  # noqa: E402

_DDL = """
CREATE TABLE IF NOT EXISTS meeting_discovery_candidates (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  article_id BIGINT UNSIGNED NOT NULL,
  title VARCHAR(512) NOT NULL DEFAULT '',
  proposed_series_name VARCHAR(256) NOT NULL DEFAULT '',
  meeting_catalog_key VARCHAR(64) NOT NULL DEFAULT '',
  match_score DECIMAL(5,4) NOT NULL DEFAULT 0.0000,
  reason VARCHAR(512) NOT NULL DEFAULT '',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_discovery_article (article_id),
  KEY idx_discovery_score (match_score DESC),
  KEY idx_discovery_catalog (meeting_catalog_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def main() -> int:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
    print("meeting_discovery_candidates 表已就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
