-- 会议事件流：名录、事件实例、文章关联、专题分析
-- 用法：mysql -u USER -p ai_governance < scripts/migrate_meeting_events.sql
-- 幂等：CREATE TABLE IF NOT EXISTS；列变更请用 migrate_meeting_events.py

USE ai_governance;

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
