-- =============================================================================
-- ai_governance MySQL 初始化 DDL
-- =============================================================================
-- 功能：新建/空库时一次性创建全部业务表（MySQL 8.0+，需 JSON 与 utf8mb4）。
-- 用法：mysql -u USER -p < scripts/init_mysql.sql
--       或：mysql -u USER -p ai_governance < scripts/init_mysql.sql（库已存在时）
--
-- 与代码对应：
--   articles / article_extractions / article_chunks → core/mysql_db.py（爬虫入库、门户查询）
--   literature_items                                  → core/mysql_literature.py
--   research_reports / research_report_sources        → core/mysql_db.py（研究助手 RAG）
--   system_tasks                                      → core/system_tasks.py（同步/任务审计）
--   monitoring_weekly_reports                         → core/mysql_weekly_reports.py
--   unmatched_articles                                → LLM/预筛未命中审计（orchestrator 写入）
--
-- 增量迁移（已有库勿重复执行本文件，改用对应脚本）：
--   article_extractions 发布地理四列 → scripts/migrate_extraction_publish_fields.sql
--
-- Git 历史：c349ec3 首次引入；c070c32 末版；556f118 清理仓库时移除。
-- 本文件 2026-06-03 自本地 MySQL SHOW CREATE TABLE / mysqldump --no-data 恢复并合并上述迁移列。
-- =============================================================================

CREATE DATABASE IF NOT EXISTS ai_governance
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE ai_governance;

-- -----------------------------------------------------------------------------
-- articles：原始文章主表（政策/新闻/监测爬虫入库）
-- 上游：crawler/orchestrator.py → core/mysql_db.save_article
-- 下游：article_extractions、article_chunks、research_report_sources
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS articles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  normalized_url VARCHAR(1024) NOT NULL,
  source VARCHAR(128) NOT NULL DEFAULT '',
  title_raw VARCHAR(1024) NOT NULL,
  summary_raw TEXT NULL,
  content_raw MEDIUMTEXT NULL,
  published_at DATETIME NULL,
  content_hash CHAR(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_articles_normalized_url (normalized_url(768)),
  KEY idx_articles_published_at (published_at),
  KEY idx_articles_source_time (source, published_at),
  KEY idx_articles_content_hash (content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- unmatched_articles：LLM/去重/抓取阶段未命中审计（预留，表结构已建）
-- 说明：orchestrator 在 is_relevant=false、LLM 解析失败、政策关键词预筛跳过时 upsert 写入
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS unmatched_articles (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  normalized_url VARCHAR(1024) NOT NULL,
  source VARCHAR(128) NOT NULL DEFAULT '',
  title_raw VARCHAR(1024) NOT NULL DEFAULT '',
  summary_raw TEXT NULL,
  content_preview MEDIUMTEXT NULL,
  published_at DATETIME NULL,
  section_name VARCHAR(255) NOT NULL DEFAULT '',
  content_hash CHAR(64) NOT NULL DEFAULT '',
  reject_stage ENUM('fetch', 'llm', 'dedup', 'manual') NOT NULL DEFAULT 'llm',
  reject_reason VARCHAR(255) NOT NULL DEFAULT '',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_unmatched_normalized_url (normalized_url(768)),
  KEY idx_unmatched_source_time (source, published_at),
  KEY idx_unmatched_stage (reject_stage),
  KEY idx_unmatched_content_hash (content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- article_extractions：LLM 结构化抽取（一条 article 对应一条 extraction）
-- content_type 含 policy/report/news 等；政策赛道统计见 core/mysql_monitor_tracks.py
-- publish_* 四列供政策词云与国家/机构统计（原 migrate_extraction_publish_fields）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS article_extractions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  article_id BIGINT UNSIGNED NOT NULL,
  model_name VARCHAR(128) NOT NULL DEFAULT '',
  content_type VARCHAR(32) NOT NULL DEFAULT 'other',
  main_topic VARCHAR(512) NOT NULL DEFAULT '',
  risk_domain VARCHAR(128) NOT NULL DEFAULT '',
  risk_subdomains_json JSON NOT NULL,
  entities_json JSON NOT NULL,
  summary_structured VARCHAR(512) NOT NULL DEFAULT '',
  tags_raw JSON NOT NULL,
  publish_country VARCHAR(64) NULL COMMENT '主权国家中文规范名',
  publish_region VARCHAR(128) NULL COMMENT '次级区域（欧盟、台湾等）',
  international_orgs_json JSON NULL COMMENT '国际组织数组',
  publish_authority VARCHAR(256) NULL COMMENT '法定发文机关',
  meeting_catalog_key VARCHAR(64) NULL COMMENT '会议名录 catalog_key',
  meeting_phase VARCHAR(16) NULL COMMENT 'pre|during|post|unknown',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_extractions_article (article_id),
  KEY idx_extractions_domain (risk_domain),
  KEY idx_extractions_main_topic (main_topic(191)),
  CONSTRAINT fk_extractions_article
    FOREIGN KEY (article_id)
    REFERENCES articles (id)
    ON DELETE CASCADE
    ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- article_chunks：分块 + 向量检索（RAG / 研究助手引用）
-- vector_id 对应向量库；chunk_type summary|body
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS article_chunks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  article_id BIGINT UNSIGNED NOT NULL,
  chunk_uid CHAR(64) NOT NULL,
  chunk_type ENUM('summary', 'body') NOT NULL DEFAULT 'body',
  chunk_index INT NOT NULL DEFAULT 0,
  chunk_text MEDIUMTEXT NOT NULL,
  token_estimate INT NOT NULL DEFAULT 0,
  embedding_model VARCHAR(128) NOT NULL DEFAULT '',
  vector_id CHAR(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_article_chunk_uid (chunk_uid),
  KEY idx_chunks_article_id (article_id),
  KEY idx_chunks_vector_id (vector_id),
  FULLTEXT KEY ft_chunk_text (chunk_text),
  CONSTRAINT fk_chunks_article
    FOREIGN KEY (article_id)
    REFERENCES articles (id)
    ON DELETE CASCADE
    ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- research_reports：用户提问生成的研究报告 Markdown
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research_reports (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  question TEXT NOT NULL,
  filters_json JSON NOT NULL,
  report_markdown MEDIUMTEXT NOT NULL,
  model_name VARCHAR(128) NOT NULL DEFAULT '',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_reports_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- research_report_sources：报告引用的文章/分块及相关性分数
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research_report_sources (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  report_id BIGINT UNSIGNED NOT NULL,
  article_id BIGINT UNSIGNED NOT NULL,
  chunk_id BIGINT UNSIGNED NULL,
  relevance_score DECIMAL(6, 5) NOT NULL DEFAULT 0.00000,
  citation_label VARCHAR(64) NOT NULL DEFAULT '',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_report_sources_report (report_id),
  KEY idx_report_sources_article (article_id),
  KEY idx_report_sources_chunk (chunk_id),
  CONSTRAINT fk_report_sources_report
    FOREIGN KEY (report_id)
    REFERENCES research_reports (id)
    ON DELETE CASCADE
    ON UPDATE RESTRICT,
  CONSTRAINT fk_report_sources_article
    FOREIGN KEY (article_id)
    REFERENCES articles (id)
    ON DELETE CASCADE
    ON UPDATE RESTRICT,
  CONSTRAINT fk_report_sources_chunk
    FOREIGN KEY (chunk_id)
    REFERENCES article_chunks (id)
    ON DELETE SET NULL
    ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- literature_items：文献库（arXiv / Scopus / Springer 等，不做 risk 抽取）
-- 上游：crawler/literature 同步 → core/mysql_literature.py
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS literature_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  normalized_url VARCHAR(1024) NOT NULL,
  source VARCHAR(64) NOT NULL DEFAULT '',
  external_id VARCHAR(191) NOT NULL DEFAULT '',
  doi VARCHAR(255) NOT NULL DEFAULT '',
  title VARCHAR(1024) NOT NULL,
  abstract TEXT NULL,
  authors_json JSON NULL,
  publication_name VARCHAR(512) NOT NULL DEFAULT '',
  document_type VARCHAR(128) NOT NULL DEFAULT '',
  subject_area VARCHAR(255) NOT NULL DEFAULT '',
  published_at DATETIME NULL,
  pdf_url VARCHAR(1024) NOT NULL DEFAULT '',
  landing_url VARCHAR(1024) NOT NULL DEFAULT '',
  raw_metadata_json JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_literature_url (normalized_url(768)),
  KEY idx_literature_source_time (source, published_at),
  KEY idx_literature_doi (doi(191)),
  KEY idx_literature_external (source, external_id),
  FULLTEXT KEY ft_literature_title_abs (title, abstract)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- system_tasks：各子系统 cron/手动任务运行记录（同步、周报生成等）
-- 上游：core/system_tasks.py；scripts/sync_sources.py、generate_weekly_report.py
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_tasks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  system_key VARCHAR(32) NOT NULL,
  task_name VARCHAR(64) NOT NULL,
  status ENUM('running', 'success', 'failed') NOT NULL DEFAULT 'running',
  start_time DATETIME NOT NULL,
  end_time DATETIME NULL,
  data_count INT NOT NULL DEFAULT 0,
  message TEXT NULL,
  trigger_source VARCHAR(32) NOT NULL DEFAULT 'cron',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_system_tasks_system_time (system_key, start_time DESC),
  KEY idx_system_tasks_status_time (status, start_time DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- monitoring_weekly_reports：监测四维周报 Markdown 持久化
-- 上游：engine/weekly_report.py、scripts/generate_weekly_report.py
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS monitoring_weekly_reports (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  system_key VARCHAR(32) NOT NULL,
  report_type VARCHAR(16) NOT NULL DEFAULT 'weekly',
  week_start DATE NOT NULL,
  week_end DATE NOT NULL,
  title VARCHAR(512) NOT NULL DEFAULT '',
  report_markdown MEDIUMTEXT NOT NULL,
  source_article_ids JSON NULL,
  article_count INT NOT NULL DEFAULT 0,
  model_name VARCHAR(64) NOT NULL DEFAULT '',
  task_id BIGINT UNSIGNED NULL,
  trigger_source VARCHAR(32) NOT NULL DEFAULT 'cron',
  status ENUM('success', 'failed', 'pending') NOT NULL DEFAULT 'success',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_system_report_week (system_key, report_type, week_start),
  KEY idx_week_start (week_start),
  KEY idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- 会议事件流（详见 scripts/migrate_meeting_events.sql）
-- -----------------------------------------------------------------------------
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
