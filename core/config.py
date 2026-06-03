"""
环境与运行配置（单一事实来源）。

功能：集中从 .env / 环境变量读取 LLM、嵌入、SQLite、爬虫与 RAG 开关，避免业务模块散落 os.getenv。
输入：进程环境；部分项支持非法数字时回退默认值。
输出：模块级常量（字符串/整型/布尔）；副作用：首次 import 时 load_dotenv(override=True)。
上下游：被 core.db、core.llm_client、crawler（含卫报 Content API 客户端）、engine.rag_ingestion 读取；Streamlit 侧展示 API 状态时可复用。
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv(override=True)

# Chroma 与 posthog 版本不匹配时会刷屏 telemetry 错误
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

# --- LLM（OpenAI 兼容 Chat）---
API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
LLM_MODEL: str = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-72B-Instruct")

# --- 嵌入（可与 Chat 同 base_url / 同 key；模型名独立配置）---
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
# AIHubMix 等网关可选附加头（见 https://docs.aihubmix.com ）
AIHUBMIX_APP_CODE: str = (os.getenv("AIHUBMIX_APP_CODE", "") or "").strip()

# --- 持久化 ---
DB_PATH: str = os.getenv("DB_PATH", "ai_governance.db")
# Chroma 子域向量库（本地目录，非独立服务）
CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "chroma_data")
# Chroma 文章全文向量库（与子域向量库独立存放，避免 collection 命名冲突）
ARTICLE_CHROMA_DIR: str = os.getenv("ARTICLE_CHROMA_DIR", "chroma_articles")

# 文章正文分块（字符近似；后续可换 tokenizer）
try:
    INDEX_CHUNK_TARGET_CHARS: int = int(os.getenv("INDEX_CHUNK_TARGET_CHARS", "3200"))
except ValueError:
    INDEX_CHUNK_TARGET_CHARS = 3200
try:
    INDEX_CHUNK_MAX_CHARS: int = int(os.getenv("INDEX_CHUNK_MAX_CHARS", "4500"))
except ValueError:
    INDEX_CHUNK_MAX_CHARS = 4500
try:
    INDEX_CHUNK_OVERLAP_CHARS: int = int(os.getenv("INDEX_CHUNK_OVERLAP_CHARS", "150"))
except ValueError:
    INDEX_CHUNK_OVERLAP_CHARS = 150
try:
    INDEX_SUMMARY_MAX_CHARS: int = int(os.getenv("INDEX_SUMMARY_MAX_CHARS", "6000"))
except ValueError:
    INDEX_SUMMARY_MAX_CHARS = 6000

# --- Guardian Open Platform---
# 功能：供 crawler.sources.guardian 等模块构造 search 请求；空字符串表示未配置。
# 输入：环境变量 GUARDIAN_API_KEY；根地址可选 GUARDIAN_API_BASE，未设时可回退 GUARDIAN_BASE。
# 输出：字符串常量；无额外 IO。
GUARDIAN_API_KEY: str = (os.getenv("GUARDIAN_API_KEY", "") or "").strip()
GUARDIAN_API_BASE: str = (
    (os.getenv("GUARDIAN_API_BASE") or os.getenv("GUARDIAN_BASE") or "").strip()
    or "https://content.guardianapis.com"
).rstrip("/")

# --- New York Times Developer APIs ---
NYT_API_KEY: str = (os.getenv("NYT_API_KEY", "") or "").strip()
NYT_API_BASE: str = ((os.getenv("NYT_API_BASE") or "").strip() or "https://api.nytimes.com").rstrip("/")

# --- Elsevier Scopus Search API ---
SCOPUS_API_KEY: str = (os.getenv("SCOPUS_API_KEY", "") or "").strip()

# --- Crawl4ai / Playwright ---
try:
    CRAWL_PAGE_TIMEOUT_MS: int = int(os.getenv("CRAWL_PAGE_TIMEOUT_MS", "90000"))
except ValueError:
    CRAWL_PAGE_TIMEOUT_MS = 90000
CRAWL_WAIT_UNTIL: str = (os.getenv("CRAWL_WAIT_UNTIL", "commit") or "commit").strip()

# --- RAG 子域路由（高频路径）---
RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "8"))
RAG_ENABLED: bool = os.getenv("RAG_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")

# --- MySQL (Phase 1 event-driven schema) ---
MYSQL_HOST: str = os.getenv("MYSQL_HOST", "localhost").strip()
try:
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
except ValueError:
    MYSQL_PORT = 3306
MYSQL_USER: str = os.getenv("MYSQL_USER", "root").strip()
MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE", "ai_governance").strip()
MYSQL_CHARSET: str = os.getenv("MYSQL_CHARSET", "utf8mb4").strip() or "utf8mb4"

# Structured extraction version for reproducible pipelines.
EXTRACTOR_VERSION: str = os.getenv("EXTRACTOR_VERSION", "v1").strip() or "v1"

# --- 五国政策源抓取（policy.py）---
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


POLICY_EU_DAYS_BACK: int = _env_int("POLICY_EU_DAYS_BACK", 14)
POLICY_BR_MAX_OFFSETS: int = _env_int("POLICY_BR_MAX_OFFSETS", 20)
POLICY_BR_LOOKBACK_DAYS: int = _env_int("POLICY_BR_LOOKBACK_DAYS", 120)
POLICY_EU_FETCH_FULL_TEXT: bool = _env_bool("POLICY_EU_FETCH_FULL_TEXT", True)
POLICY_US_USE_API: bool = _env_bool("POLICY_US_USE_API", True)
POLICY_US_API_DAYS_BACK: int = _env_int("POLICY_US_API_DAYS_BACK", 90)
POLICY_EU_USE_SEARCH: bool = _env_bool("POLICY_EU_USE_SEARCH", True)
POLICY_IN_MAX_PAGES: int = _env_int("POLICY_IN_MAX_PAGES", 3)

# --- 五国政策历史回溯（backfill_policy_historical.py）---
BACKFILL_POLICY_TARGET: int = _env_int("BACKFILL_POLICY_TARGET", 1000)
BACKFILL_POLICY_WINDOW_DAYS: int = _env_int("BACKFILL_POLICY_WINDOW_DAYS", 30)
BACKFILL_POLICY_MAX_PER_WINDOW: int = _env_int("BACKFILL_POLICY_MAX_PER_WINDOW", 100)

# --- 会议历史回溯（backfill_meeting_historical.py）---
BACKFILL_MEETING_DATE_FROM: str = os.getenv("BACKFILL_MEETING_DATE_FROM", "2023-01-01").strip()
BACKFILL_MEETING_WINDOW_DAYS: int = _env_int("BACKFILL_MEETING_WINDOW_DAYS", 30)
BACKFILL_MEETING_NYT_MAX_PAGES: int = _env_int("BACKFILL_MEETING_NYT_MAX_PAGES", 3)
BACKFILL_MEETING_GUARDIAN_MAX_PAGES: int = _env_int("BACKFILL_MEETING_GUARDIAN_MAX_PAGES", 4)
MEETING_BRIEF_MIN_ARTICLES: int = _env_int("MEETING_BRIEF_MIN_ARTICLES", 2)
MEETING_BRIEF_DAYS_AFTER_END: int = _env_int("MEETING_BRIEF_DAYS_AFTER_END", 7)

# --- 按届定向新闻检索（sync_meeting_event_news.py）---
MEETING_NEWS_PRE_DAYS: int = _env_int("MEETING_NEWS_PRE_DAYS", 30)
MEETING_NEWS_POST_DAYS: int = _env_int("MEETING_NEWS_POST_DAYS", 60)
MEETING_NEWS_NYT_MAX_PAGES: int = _env_int("MEETING_NEWS_NYT_MAX_PAGES", 3)
MEETING_NEWS_GUARDIAN_MAX_PAGES: int = _env_int("MEETING_NEWS_GUARDIAN_MAX_PAGES", 2)
MEETING_NEWS_RECENT_PAST_DAYS: int = _env_int("MEETING_NEWS_RECENT_PAST_DAYS", 30)
MEETING_NEWS_RECENT_FUTURE_DAYS: int = _env_int("MEETING_NEWS_RECENT_FUTURE_DAYS", 90)

# NYT/Guardian 会议增强检索词（轮换）
MEETING_BACKFILL_QUERIES: tuple[str, ...] = tuple(
    q.strip()
    for q in (
        os.getenv(
            "MEETING_BACKFILL_QUERIES",
            "AI Safety Summit;REAIM summit;WAIC artificial intelligence;"
            "AI governance summit;Bletchley AI safety;Seoul AI summit;"
            "Paris AI Action Summit;India AI safety;UN AI governance dialogue;"
            "responsible AI military;global AI standards summit",
        ).split(";")
    )
    if q.strip()
)
