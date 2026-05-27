"""
Streamlit UI 后台任务：新闻信源同步、Agent 侦察、深度调研在长线程中执行，SQLite 存状态便于轮询。

功能：daemon 线程执行业务，主进程不写阻塞式 spinner；jobs 落在 DB_PATH.ui_background_jobs。
输入：payload 为 dict，须 json 可序列化；输出：get_job(job_id) 读 status / result_json / error_text。
上下游：仅 app.py 演示操作区与深度调研按钮调用；不复用 Celery/redis。
扩展点：新增信源 Worker 只需在 _run 的 if-elif 分支中加 job_type（如 xinhua_tech_sync）与对应 _work_* 函数。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.config import API_KEY as CONFIG_API_KEY
from core.config import BASE_URL as CONFIG_BASE_URL
from core.config import DB_PATH
from core.config import LLM_MODEL as CONFIG_LLM_MODEL


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def insert_job(job_type: str, payload: Dict[str, Any]) -> str:
    """写入 pending；返回 UUID job_id。"""
    jid = str(uuid.uuid4())
    ts = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO ui_background_jobs (
                id, job_type, status, payload_json, result_json, error_text,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                jid,
                job_type,
                "pending",
                json.dumps(payload, ensure_ascii=False),
                "",
                ts,
                ts,
            ),
        )
        conn.commit()
    return jid


def update_job(
    jid: str,
    *,
    status: Optional[str] = None,
    result_json: Optional[Dict[str, Any]] = None,
    error_text: Optional[str] = None,
) -> None:
    ts = _now_iso()
    with _connect() as conn:
        if status:
            conn.execute(
                "UPDATE ui_background_jobs SET status = ?, updated_at = ? WHERE id = ?",
                (status, ts, jid),
            )
        if result_json is not None:
            conn.execute(
                "UPDATE ui_background_jobs SET result_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(result_json, ensure_ascii=False), ts, jid),
            )
        if error_text is not None:
            conn.execute(
                "UPDATE ui_background_jobs SET error_text = ?, updated_at = ? WHERE id = ?",
                (error_text[:16000], ts, jid),
            )
        conn.commit()


def get_job(jid: str) -> Optional[Dict[str, Any]]:
    """读一条任务（含解析后的 payload/result）；无则 None。"""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT id, job_type, status, payload_json, result_json, error_text,
                          created_at, updated_at FROM ui_background_jobs WHERE id = ? LIMIT 1""",
            (jid,),
        )
        row = cur.fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["payload"] = json.loads(out.pop("payload_json") or "{}")
    except json.JSONDecodeError:
        out["payload"] = {}
    rj = out.pop("result_json")
    try:
        out["result"] = json.loads(rj) if rj else None
    except json.JSONDecodeError:
        out["result"] = None
    return out


def start_job_thread(job_type: str, payload: Dict[str, Any]) -> str:
    """
    落库后立即起 daemon 线程执行；阻塞极短。
    """
    jid = insert_job(job_type, payload)

    def _run() -> None:
        update_job(jid, status="running")
        try:
            if job_type == "guardian_sync":
                res = _work_guardian_sync(payload)
            elif job_type == "nyt_sync":
                res = _work_nyt_sync(payload)
            elif job_type == "wechat_rss_sync":
                res = _work_wechat_rss_sync(payload)
            elif job_type == "xinhua_tech_sync":
                res = _work_xinhua_tech_sync(payload)
            elif job_type == "sina_tech_sync":
                res = _work_sina_tech_sync(payload)
            elif job_type == "policy_sync":
                res = _work_policy_sync(payload)
            elif job_type == "literature_sync":
                res = _work_literature_sync(payload)
            elif job_type == "agent_scout":
                res = _work_agent_scout(payload)
            elif job_type == "deep_research":
                res = _work_deep_research(payload)
            else:
                raise ValueError(f"unknown job_type: {job_type}")
            update_job(jid, status="completed", result_json=res, error_text="")
        except Exception as e:
            update_job(
                jid,
                status="failed",
                error_text=str(e),
                result_json={"error_class": type(e).__name__, "detail": str(e)},
            )

    threading.Thread(target=_run, daemon=True, name=f"ui_job_{jid[:8]}").start()
    return jid


# ---------------------------------------------------------------------------
# Workers（在线程内 import 重型依赖，缩短 app 导入链）
# ---------------------------------------------------------------------------


def _work_guardian_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    功能：卫报同步 Worker，在 daemon 线程内执行。
    输入：payload 含 max_pages / page_size / rag_enabled。
    输出：SyncResult 摘要 dict（saved / skipped / debug_log）。
    上下游：start_job_thread → 此函数 → crawler.orchestrator.sync_guardian。
    """
    max_pages = int(payload.get("max_pages", 2))
    page_size = int(payload.get("page_size", 8))
    rag_enabled = bool(payload.get("rag_enabled", False))
    from crawler.orchestrator import sync_guardian

    r = sync_guardian(max_pages=max_pages, page_size=page_size, rag_enabled=rag_enabled)
    return {
        "saved": r.saved,
        "skipped_url_dup": r.skipped_url_dup,
        "skipped_no_incident": r.skipped_no_incident,
        "failed": r.failed,
        "new_keywords": list(r.new_keywords[:20]),
        "debug_log": list(r.debug_log),
    }


def _work_nyt_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    功能：NYT 同步 Worker，在 daemon 线程内执行。
    输入：payload 含 max_pages / rag_enabled / query（可选）。
    输出：SyncResult 摘要 dict（saved / skipped / debug_log）。
    上下游：start_job_thread → 此函数 → crawler.orchestrator.sync_nyt。
    """
    max_pages = int(payload.get("max_pages", 2))
    rag_enabled = bool(payload.get("rag_enabled", False))
    query = (payload.get("query") or "").strip() or None
    from crawler.orchestrator import sync_nyt

    r = sync_nyt(max_pages=max_pages, query=query, rag_enabled=rag_enabled)
    return {
        "saved": r.saved,
        "skipped_url_dup": r.skipped_url_dup,
        "skipped_no_incident": r.skipped_no_incident,
        "failed": r.failed,
        "new_keywords": list(r.new_keywords[:20]),
        "debug_log": list(r.debug_log),
    }


def _work_xinhua_tech_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    功能：新华网科技频道同步 Worker，在 daemon 线程内执行。
    输入：payload 含 max_articles（默认 10）、rag_enabled；
         page_urls 可选为非空字符串列表（自定义列表页），缺省则由适配器使用默认科技频道 URL。
    输出：SyncResult 摘要 dict（saved / skipped / debug_log）。
    上下游：start_job_thread → crawler.orchestrator.sync_xinhua_tech。
    """
    max_articles = int(payload.get("max_articles", 10))
    rag_enabled = bool(payload.get("rag_enabled", False))
    raw_urls = payload.get("page_urls")
    page_urls: Optional[List[str]] = None
    if isinstance(raw_urls, list):
        page_urls = [str(u).strip() for u in raw_urls if str(u).strip()]
        page_urls = page_urls or None
    dry_run = bool(payload.get("dry_run", False))
    from crawler.orchestrator import sync_xinhua_tech

    r = sync_xinhua_tech(
        max_articles=max_articles,
        page_urls=page_urls,
        rag_enabled=rag_enabled,
        dry_run=dry_run,
    )
    return {
        "saved": r.saved,
        "skipped_url_dup": r.skipped_url_dup,
        "skipped_no_incident": r.skipped_no_incident,
        "failed": r.failed,
        "new_keywords": list(r.new_keywords[:20]),
        "debug_log": list(r.debug_log),
    }


def _work_sina_tech_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    功能：新浪科技频道同步 Worker，在 daemon 线程内执行。
    输入：payload 含 max_articles（默认 10）、rag_enabled；
         page_urls 可选为非空字符串列表（自定义列表页），缺省则用默认 tech.sina.com.cn。
    输出：SyncResult 摘要 dict。
    上下游：start_job_thread → crawler.orchestrator.sync_sina_tech。
    """
    max_articles = int(payload.get("max_articles", 10))
    rag_enabled = bool(payload.get("rag_enabled", False))
    raw_urls = payload.get("page_urls")
    page_urls: Optional[List[str]] = None
    if isinstance(raw_urls, list):
        page_urls = [str(u).strip() for u in raw_urls if str(u).strip()]
        page_urls = page_urls or None
    dry_run = bool(payload.get("dry_run", False))
    from crawler.orchestrator import sync_sina_tech

    r = sync_sina_tech(
        max_articles=max_articles,
        page_urls=page_urls,
        rag_enabled=rag_enabled,
        dry_run=dry_run,
    )
    return {
        "saved": r.saved,
        "skipped_url_dup": r.skipped_url_dup,
        "skipped_no_incident": r.skipped_no_incident,
        "failed": r.failed,
        "new_keywords": list(r.new_keywords[:20]),
        "debug_log": list(r.debug_log),
    }


def _work_wechat_rss_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    功能：微信公众号 RSS（wechat2rss）同步 Worker，在 daemon 线程内执行。
    输入：payload 可选 feed_names（公众号名称列表，空则全池）、max_articles_per_feed（默认 10）、
         rag_enabled（默认 False）；dry_run 仅脚本调试使用，UI 不传则为 False（真实入库）。
    输出：SyncResult 摘要 dict（saved / skipped / debug_log）。
    上下游：start_job_thread → crawler.orchestrator.sync_wechat_rss。
    """
    raw_feeds = payload.get("feed_names")
    feed_names: Optional[List[str]] = None
    if isinstance(raw_feeds, list) and raw_feeds:
        feed_names = [str(x).strip() for x in raw_feeds if str(x).strip()]
        feed_names = feed_names or None
    max_per = int(payload.get("max_articles_per_feed", 10))
    rag_enabled = bool(payload.get("rag_enabled", False))
    dry_run = bool(payload.get("dry_run", False))
    from crawler.orchestrator import sync_wechat_rss

    r = sync_wechat_rss(
        feed_names=feed_names,
        max_articles_per_feed=max_per,
        rag_enabled=rag_enabled,
        dry_run=dry_run,
    )
    return {
        "saved": r.saved,
        "skipped_url_dup": r.skipped_url_dup,
        "skipped_no_incident": r.skipped_no_incident,
        "failed": r.failed,
        "new_keywords": list(r.new_keywords[:20]),
        "debug_log": list(r.debug_log),
    }


def _work_policy_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    功能：政策/法规源同步 Worker。
    输入：payload 含 countries（列表）、max_articles_per_country、rag_enabled、dry_run。
    输出：SyncResult 摘要 dict。
    上下游：crawler.orchestrator.sync_policy。
    """
    raw_countries = payload.get("countries")
    countries: Optional[List[str]] = None
    if isinstance(raw_countries, list) and raw_countries:
        countries = [str(x).strip().upper() for x in raw_countries if str(x).strip()]
        countries = countries or None
    max_per = int(payload.get("max_articles_per_country", 10))
    rag_enabled = bool(payload.get("rag_enabled", False))
    dry_run = bool(payload.get("dry_run", False))
    skip_prefilter = bool(payload.get("skip_prefilter", False))
    from crawler.orchestrator import sync_policy

    r = sync_policy(
        countries=countries,
        max_articles_per_country=max_per,
        rag_enabled=rag_enabled,
        dry_run=dry_run,
        skip_prefilter=skip_prefilter,
    )
    return {
        "saved": r.saved,
        "skipped_url_dup": r.skipped_url_dup,
        "skipped_no_incident": r.skipped_no_incident,
        "failed": r.failed,
        "new_keywords": list(r.new_keywords[:20]),
        "debug_log": list(r.debug_log),
    }


def _work_literature_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    功能：文献库同步 Worker（arXiv/Scopus/Springer → literature_items）。
    输入：payload 含 sources 列表及各源条数上限；dry_run 不入库。
    输出：LiteratureSyncResult 摘要 dict。
    """
    raw_sources = payload.get("sources")
    sources: Optional[List[str]] = None
    if isinstance(raw_sources, list) and raw_sources:
        sources = [str(x).strip().lower() for x in raw_sources if str(x).strip()]
        sources = sources or None
    dry_run = bool(payload.get("dry_run", False))
    from crawler.orchestrator import sync_literature

    r = sync_literature(
        sources=sources or ["arxiv"],
        max_arxiv_per_category=int(payload.get("max_arxiv_per_category", 3)),
        max_springer_per_domain=int(payload.get("max_springer_per_domain", 3)),
        scopus_max_results=int(payload.get("scopus_max_results", 10)),
        scopus_days_back=int(payload.get("scopus_days_back", 7)),
        dry_run=dry_run,
    )
    return {
        "saved": r.saved,
        "skipped_url_dup": r.skipped_url_dup,
        "skipped_no_incident": 0,
        "failed": r.failed,
        "new_keywords": [],
        "debug_log": list(r.debug_log),
    }


def _work_agent_scout(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = str(payload.get("url") or "").strip()
    api_key = (payload.get("api_key") or CONFIG_API_KEY or "").strip()
    base_url = (payload.get("base_url") or CONFIG_BASE_URL or "").strip()
    from crawler.agentic_crawl import run_agentic_crawl
    from core.db import incident_from_extraction, save_incident

    incidents_data: List[Any] = []
    new_keywords: List[str] = []
    debug_info: List[str] = []
    try:
        incidents_data, new_keywords, debug_info = asyncio.run(
            run_agentic_crawl(url, api_key=api_key or None, base_url=base_url or None)
        )
    except Exception as e:
        debug_info = [f"执行异常: {type(e).__name__}: {e}"]

    saved_count = 0
    for inc_dict in incidents_data or []:
        try:
            inc = incident_from_extraction(inc_dict)
            ok, _ = save_incident(inc, url)
            if ok:
                saved_count += 1
        except Exception:
            continue
    return {
        "extracted": len(incidents_data or []),
        "saved": saved_count,
        "new_keywords": list((new_keywords or [])[:16]),
        "debug_info": list(debug_info or []),
    }


def _work_deep_research(payload: Dict[str, Any]) -> Dict[str, Any]:
    question = str(payload.get("question") or "").strip()
    preview_only = bool(payload.get("preview_only", False))
    save_report = bool(payload.get("save_report", True))
    top_k = int(payload.get("top_k", 16))
    pool = min(64, max(28, top_k * 4))
    risk_domain = payload.get("risk_domain")
    risk_domain = risk_domain.strip() if isinstance(risk_domain, str) and risk_domain.strip() else None
    source = payload.get("source")
    source = source.strip() if isinstance(source, str) and source.strip() else None
    llm_model = str(payload.get("llm_model") or CONFIG_LLM_MODEL).strip()

    from core.llm_client import OpenAICompatibleBackend
    from core.mysql_db import save_research_report
    from engine.rag_ingestion.hybrid_retrieval import evidence_hits_to_report_sources, hybrid_retrieve
    from engine.research_report import generate_research_report_markdown

    api_key_inner = str(payload.get("api_key") or CONFIG_API_KEY or "").strip()
    base_url_inner = str(payload.get("base_url") or CONFIG_BASE_URL or "").strip() or CONFIG_BASE_URL
    if not preview_only and not api_key_inner:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY，无法在后台生成完整报告")

    embed_backend = OpenAICompatibleBackend(
        api_key=api_key_inner or CONFIG_API_KEY or "",
        base_url=base_url_inner,
    )

    hits = hybrid_retrieve(
        question,
        top_k=top_k,
        risk_domain=risk_domain,
        source=source or None,
        vector_top_n=pool,
        sparse_top_n=pool,
        max_chunks_per_article=3,
        backend=embed_backend,
    )

    out: Dict[str, Any] = {
        "question": question,
        "hits_count": len(hits),
        "preview_only": preview_only,
        "evidence_previews": [
            {
                "article_id": h.article_id,
                "rrf": round(float(h.rrf_score), 4),
                "snippet": ((h.chunk_text or "").replace("\n", " "))[:220],
            }
            for h in hits[:32]
        ],
    }

    report_md = ""
    if preview_only:
        out["report_markdown"] = ""
        return out

    report_md = generate_research_report_markdown(
        question,
        hits,
        backend=embed_backend,
        model=llm_model,
    )
    out["report_markdown"] = report_md

    filt = {"risk_domain": risk_domain or "", "source": source or "", "top_k": top_k}
    if save_report:
        rid = save_research_report(
            question,
            filt,
            report_md,
            model_name=llm_model,
            sources=evidence_hits_to_report_sources(hits),
        )
        out["saved_report_id"] = rid
    else:
        out["saved_report_id"] = None
    return out
