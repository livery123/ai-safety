#!/usr/bin/env python3
"""
功能：幂等创建会议事件流表，并为 article_extractions 增加会议关联列。
输入：无；读 core.config MySQL 连接。
输出：stdout 日志；exit 0/1。
上下游：部署后执行 scripts/seed_meeting_catalog.py；linker / backfill 依赖本迁移。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.mysql_db import mysql_conn  # noqa: E402

_EXTRACTION_COLUMNS = (
    ("meeting_catalog_key", "VARCHAR(64) NULL COMMENT '名录 catalog_key'"),
    ("meeting_phase", "VARCHAR(16) NULL COMMENT 'pre|during|post|unknown'"),
)

_DDL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS conference_catalog (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      catalog_key VARCHAR(64) NOT NULL,
      series_name VARCHAR(256) NOT NULL DEFAULT '',
      category VARCHAR(128) NOT NULL DEFAULT '',
      aliases_json JSON NOT NULL,
      topics_json JSON NOT NULL,
      is_major TINYINT(1) NOT NULL DEFAULT 1,
      official_urls_json JSON NOT NULL,
      reference_url VARCHAR(1024) NOT NULL DEFAULT '',
      sort_order INT NOT NULL DEFAULT 0,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (id),
      UNIQUE KEY uk_catalog_key (catalog_key),
      KEY idx_catalog_major (is_major, sort_order)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS meeting_events (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      catalog_key VARCHAR(64) NOT NULL,
      edition_label VARCHAR(256) NOT NULL DEFAULT '',
      edition_year SMALLINT UNSIGNED NULL,
      start_date DATE NULL,
      end_date DATE NULL,
      location VARCHAR(256) NOT NULL DEFAULT '',
      host VARCHAR(256) NOT NULL DEFAULT '',
      countries_json JSON NOT NULL,
      official_url VARCHAR(1024) NOT NULL DEFAULT '',
      status VARCHAR(32) NOT NULL DEFAULT 'scheduled',
      notes TEXT NULL,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (id),
      KEY idx_events_catalog (catalog_key, edition_year),
      KEY idx_events_dates (start_date, end_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS meeting_event_articles (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      event_id BIGINT UNSIGNED NOT NULL,
      article_id BIGINT UNSIGNED NOT NULL,
      phase ENUM('pre', 'during', 'post', 'unknown') NOT NULL DEFAULT 'unknown',
      link_score DECIMAL(5,4) NOT NULL DEFAULT 0.0000,
      link_method VARCHAR(32) NOT NULL DEFAULT 'rule',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (id),
      UNIQUE KEY uk_event_article (event_id, article_id),
      KEY idx_mea_article (article_id),
      KEY idx_mea_phase (event_id, phase),
      CONSTRAINT fk_mea_event FOREIGN KEY (event_id) REFERENCES meeting_events (id) ON DELETE CASCADE,
      CONSTRAINT fk_mea_article FOREIGN KEY (article_id) REFERENCES articles (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS meeting_event_analyses (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      event_id BIGINT UNSIGNED NOT NULL,
      analysis_markdown MEDIUMTEXT NOT NULL,
      structured_json JSON NULL,
      model_name VARCHAR(128) NOT NULL DEFAULT '',
      generated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (id),
      KEY idx_mea_event_time (event_id, generated_at DESC),
      CONSTRAINT fk_mea_analysis_event FOREIGN KEY (event_id) REFERENCES meeting_events (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
)


def _existing_extraction_columns() -> set[str]:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COLUMN_NAME FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'article_extractions'
                """
            )
            return {str(r["COLUMN_NAME"]) for r in cur.fetchall() or []}


def main() -> int:
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            for stmt in _DDL_STATEMENTS:
                cur.execute(stmt)
                print("已确保表存在（CREATE IF NOT EXISTS）")

    existing = _existing_extraction_columns()
    added = 0
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            for name, ddl in _EXTRACTION_COLUMNS:
                if name in existing:
                    print(f"跳过（已存在）: {name}")
                    continue
                cur.execute(f"ALTER TABLE article_extractions ADD COLUMN {name} {ddl}")
                print(f"已添加列: {name}")
                added += 1
    print(f"完成，新增 extraction 列 {added} 个。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
