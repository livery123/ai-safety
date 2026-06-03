-- 功能：article_extractions 增加发布地理与主体四列（政策词云/国家统计用）。
-- 用法：mysql -u ... ai_governance < scripts/migrate_extraction_publish_fields.sql
-- 注意：MySQL 8 无 IF NOT EXISTS for ADD COLUMN，重复执行会报错；请用 migrate_extraction_publish_fields.py 幂等安装。

ALTER TABLE article_extractions
  ADD COLUMN publish_country VARCHAR(64) NULL COMMENT '主权国家中文规范名' AFTER tags_raw,
  ADD COLUMN publish_region VARCHAR(128) NULL COMMENT '次级区域（欧盟、台湾等）' AFTER publish_country,
  ADD COLUMN international_orgs_json JSON NULL COMMENT '国际组织数组' AFTER publish_region,
  ADD COLUMN publish_authority VARCHAR(256) NULL COMMENT '法定发文机关' AFTER international_orgs_json;
