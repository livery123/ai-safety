#!/usr/bin/env python3
"""
功能：幂等为 article_extractions 增加发布地理/主体四列。
输入：无；读 core.config MySQL 连接。
输出：stdout 日志；exit 0/1。
上下游：部署时先于 backfill_publish_geo.py 执行。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.mysql_db import mysql_conn  # noqa: E402

_COLUMNS = (
    ("publish_country", "VARCHAR(64) NULL COMMENT '主权国家中文规范名'"),
    ("publish_region", "VARCHAR(128) NULL COMMENT '次级区域（欧盟、台湾等）'"),
    ("international_orgs_json", "JSON NULL COMMENT '国际组织数组'"),
    ("publish_authority", "VARCHAR(256) NULL COMMENT '法定发文机关'"),
)


def _existing_columns() -> set[str]:
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
    existing = _existing_columns()
    added = 0
    with mysql_conn() as conn:
        with conn.cursor() as cur:
            for name, ddl in _COLUMNS:
                if name in existing:
                    print(f"跳过（已存在）: {name}")
                    continue
                cur.execute(f"ALTER TABLE article_extractions ADD COLUMN {name} {ddl}")
                print(f"已添加: {name}")
                added += 1
    print(f"完成，新增 {added} 列。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
