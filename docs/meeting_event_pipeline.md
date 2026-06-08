# 会议事件流运维流水线（双轨采集）

## 架构简述

| 轨道 | 脚本 | 作用 |
|------|------|------|
| A 结构化真相 | `seed_meeting_catalog.py` | 名录 + 各届时间/地点/主办（分钟级可展示） |
| B 按届新闻 | `sync_meeting_event_news.py` | 会期窗 + 峰会名；默认 NYT + Guardian（`preferred_sources`） |
| C 官网成果 | `sync_meeting_officials.py` / `ingest_meeting_urls.py` | agentic 抓取官网/宣言/人工 URL |
| D 归并 | `link_meeting_articles.py` | meeting 报道 → meeting_events + 会前/会中/会后 |
| E 专题 | `generate_meeting_briefs.py` | 基于关联报道写 Markdown 专题 |

**日常媒体池**（不替代以上）：`sync_sources.py --source news` → LLM 标 `meeting` → 再 link。

**历史补洞**（非主路径）：`backfill_meeting_historical.py` 仅在某届定向检索仍缺稿时使用。

## 一次性初始化

```bash
python3 scripts/migrate_meeting_events.py
python3 scripts/migrate_meeting_discovery.py
python3 scripts/seed_meeting_catalog.py
```

维护名录：编辑 [data/conference_catalog.json](../data/conference_catalog.json) 后重新 `seed`。

## 首期填充（推荐顺序）

```bash
# 1. 结构化（若已 seed 可跳过）
python3 scripts/seed_meeting_catalog.py

# 2. 按届定向新闻（默认含 Guardian；断点见 meeting_event_news.json）
python3 scripts/sync_meeting_event_news.py --link-after

# 3. 官网/宣言/人工 URL
python3 scripts/sync_meeting_officials.py --link-after
python3 scripts/ingest_meeting_urls.py --from-catalog --link-after --limit 50

# 4. 中文/泛搜补池（WAIC 等）
python3 scripts/sync_sources.py --source news
python3 scripts/link_meeting_articles.py --limit 3000

# 5. 专题（仅关联报道 >= MEETING_BRIEF_MIN_ARTICLES）
python3 scripts/generate_meeting_briefs.py

# 缺稿盘点
python3 scripts/report_meeting_coverage.py --json data/reports/meeting_coverage.json
python3 scripts/list_meeting_discovery.py
```

**说明**：门户顶栏「系统累计」为 meeting **文章**条数，不是 16 届事件数。部署 API 变更后需重启 `127.0.0.1:8000`，Next 会议页已 `force-dynamic`。

单届调试：

```bash
python3 scripts/sync_meeting_event_news.py --event-id 12 --link-after
python3 scripts/generate_meeting_briefs.py --event-id 12
```

## 环境变量（core/config.py）

- `MEETING_NEWS_PRE_DAYS` / `MEETING_NEWS_POST_DAYS`：会期前后检索窗（默认 30 / 60 天）
- `MEETING_NEWS_NYT_MAX_PAGES`：每届 NYT 页数
- `NYT_API_KEY`：NYT 检索必填
- `MEETING_BRIEF_MIN_ARTICLES`：专题最少关联报道数
- `MEETING_NEWS_GUARDIAN_DEFAULT`：定向新闻默认是否跑 Guardian（默认 true）
- `MEETING_DISCOVERY_MIN_SCORE`：未匹配 meeting 稿写入审计表的规则分阈值

Checkpoint：

- `data/checkpoints/meeting_event_news.json`
- `data/checkpoints/meeting_officials.json`
- `data/checkpoints/meeting_manual_urls.json`

## 日常 / cron

见 [deploy/cron-ai-safety-sync.example](../deploy/cron-ai-safety-sync.example)：

1. 每 6h：`sync_sources --source news`
2. +10min：`link_meeting_articles`
3. 每周：`sync_meeting_event_news --recent --link-after`
4. 每周：`generate_meeting_briefs`
5. 每月：人工更新 catalog + `seed`

## API / 前端

- `GET /api/meetings/catalog`
- `GET /api/meetings/events`
- `GET /api/meetings/events/{id}/timeline`
- 门户 `/meetings`、`/meetings/[eventId]`
