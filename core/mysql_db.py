import json
from typing import Any, Dict, List, Type
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from pydantic import BaseModel
from config import MYSQL_CHARSET,MYSQL_HOST,MYSQL_PASSWORD,MYSQL_PORT,MYSQL_USER,MYSQL_DATABASE

# 数据库连接配置 (建议通过环境变量读取)
DB_CONFIG = {
    "host": MYSQL_HOST,
    "port": MYSQL_PORT,
    "user": MYSQL_USER,
    "password": "your_password",
    "database": "ai_governance"
}

class DBUtils:
    def __init__(self):
        self.connection_url = f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset=utf8mb4"
        self.engine: Engine = create_engine(
            self.connection_url, 
            pool_size=10, 
            max_overflow=20,
            pool_pre_ping=True
        )

    def init_db(self):
        """初始化数据库表结构 (基于你提供的最新字段)"""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS articles (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                normalized_url VARCHAR(1024) NOT NULL,
                source VARCHAR(128) NOT NULL DEFAULT 'guardian',
                title_raw VARCHAR(1024) NOT NULL,
                summary_raw TEXT NULL,
                content_raw MEDIUMTEXT NULL,
                published_at DATETIME NULL,
                content_hash CHAR(64) NOT NULL,
                rejected BOOLEAN DEFAULT TRUE,
                rejected_reason VARCHAR(255) NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_url (normalized_url(768)),
                INDEX idx_hash (content_hash)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS article_extractions (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                article_id BIGINT UNSIGNED NOT NULL,
                model_name VARCHAR(128) NOT NULL,
                content_type VARCHAR(32) DEFAULT 'other',
                main_topic VARCHAR(512) NULL,
                risk_domain VARCHAR(128) NULL,
                risk_subdomains_json JSON NULL,
                entities_json JSON NULL,
                summary_structured TEXT NULL,
                tags_raw JSON NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """,
            """
            CREATE TABLE IF NOT EXISTS research_reports (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                question TEXT NOT NULL,
                filters_json JSON NOT NULL,
                related_articles JSON NOT NULL,
                report_markdown MEDIUMTEXT NOT NULL,
                model_name VARCHAR(128) NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        ]
        
        with self.engine.begin() as conn:
            for query in queries:
                conn.execute(text(query))
        print("✅ 数据库表初始化完成")

    def save_pydantic(self, table_name: str, model_instance: BaseModel) -> int:
        """
        通用的 Pydantic 模型保存函数
        会自动处理 List/Dict 到 JSON 字符串的转换
        """
        # 将 Pydantic 转换为字典
        data = model_instance.model_dump(exclude={'id'})
        
        # 预处理：将 List 或 Dict 转换为 JSON 字符串以适配 MySQL JSON 字段
        for key, value in data.items():
            if isinstance(value, (list, dict)):
                data[key] = json.dumps(value, ensure_ascii=False)

        columns = ", ".join(data.keys())
        placeholders = ", ".join([f":{k}" for k in data.keys()])
        
        # 构建插入语句 (如果 URL 冲突则更新，此处根据业务需求调整)
        query = text(f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})")

        with self.engine.begin() as conn:
            result = conn.execute(query, data)
            return result.lastrowid

# ==========================================
# 使用示例 (Example Usage)
# ==========================================
if __name__ == "__main__":
    db = DBUtils()
    # 1. 建表
    db.init_db()

    # 2. 模拟保存一篇文章
    # from your_models import Article (此处假设已导入你定义的类)
    # sample_article = Article(
    #     normalized_url="https://theguardian.com/...",
    #     title_raw="AI Safety Summit",
    #     content_hash="hash123",
    #     rejected=False,
    #     rejected_reason="matched_ai_policy"
    # )
    # article_id = db.save_pydantic("articles", sample_article)
    # print(f"保存成功，ID: {article_id}")