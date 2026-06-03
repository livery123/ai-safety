"""
Streamlit UI 后台任务：信源同步、Agent 侦察、深度调研在长线程中执行。
手动同步，同样写入system_tasks（日志）表，方便展示

功能：daemon 线程执行业务，SQLite 存状态；jobs 落在 DB_PATH.ui_background_jobs。
输入：payload dict（须 JSON 可序列化）。
输出：get_job(job_id) 读 status / result_json / error_text。
上下游：ui.pages.system / research；crawler.orchestrator、news_sync_bundle。
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
from core.system_tasks import record_news_bundle_tasks, run_tracked_sync


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
            (jid, job_type, "pending", json.dumps(payload, ensure_ascii=False), "", ts, ts),
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
    """读一条任务；无则 None。"""
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
    """落库后立即起 daemon 线程执行。"""
    jid = insert_job(job_type, payload)

    def _run() -> None:
        update_job(jid, status="running")
        try:
            workers = {
                "guardian_sync": _work_guardian_sync,
                "nyt_sync": _work_nyt_sync,
                "wechat_rss_sync": _work_wechat_rss_sync,
                "xinhua_tech_sync": _work_xinhua_tech_sync,
                "sina_tech_sync": _work_sina_tech_sync,
                "policy_sync": _work_policy_sync,
                "literature_sync": _work_literature_sync,
                "news_bundle_sync": _work_news_bundle_sync,
                "agent_scout": _work_agent_scout,
                "deep_research": _work_deep_research,
            }
            fn = workers.get(job_type)
            if not fn:
                raise ValueError(f"unknown job_type: {job_type}")
            res = fn(payload)
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


def _sync_result_dict(r: Any) -> Dict[str, Any]:
    return {
        "saved": r.saved,
        "skipped_url_dup": r.skipped_url_dup,
        "skipped_no_incident": r.skipped_no_incident,
        "failed": r.failed,
        "new_keywords": list(getattr(r, "new_keywords", [])[:20]),
        "debug_log": list(getattr(r, "debug_log", [])),
    }


def _work_guardian_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    from crawler.orchestrator import sync_guardian

    r = sync_guardian(
        max_pages=int(payload.get("max_pages", 2)),
        page_size=int(payload.get("page_size", 8)),
        rag_enabled=bool(payload.get("rag_enabled", False)),
    )
    return _sync_result_dict(r)


def _work_nyt_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    from crawler.orchestrator import sync_nyt

    r = sync_nyt(
        max_pages=int(payload.get("max_pages", 2)),
        query=(payload.get("query") or "").strip() or None,
        rag_enabled=bool(payload.get("rag_enabled", False)),
    )
    return _sync_result_dict(r)


def _work_wechat_rss_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_feeds = payload.get("feed_names")
    feed_names: Optional[List[str]] = None
    if isinstance(raw_feeds, list) and raw_feeds:
        feed_names = [str(x).strip() for x in raw_feeds if str(x).strip()] or None
    from crawler.orchestrator import sync_wechat_rss

    r = sync_wechat_rss(
        feed_names=feed_names,
        max_articles_per_feed=int(payload.get("max_articles_per_feed", 5)),
        rag_enabled=bool(payload.get("rag_enabled", False)),
        dry_run=bool(payload.get("dry_run", False)),
    )
    return _sync_result_dict(r)


def _work_xinhua_tech_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_urls = payload.get("page_urls")
    page_urls: Optional[List[str]] = None
    if isinstance(raw_urls, list):
        page_urls = [str(u).strip() for u in raw_urls if str(u).strip()] or None
    from crawler.orchestrator import sync_xinhua_tech

    r = sync_xinhua_tech(
        max_articles=int(payload.get("max_articles", 10)),
        page_urls=page_urls,
        rag_enabled=bool(payload.get("rag_enabled", False)),
        dry_run=bool(payload.get("dry_run", False)),
    )
    return _sync_result_dict(r)


def _work_sina_tech_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_urls = payload.get("page_urls")
    page_urls: Optional[List[str]] = None
    if isinstance(raw_urls, list):
        page_urls = [str(u).strip() for u in raw_urls if str(u).strip()] or None
    from crawler.orchestrator import sync_sina_tech

    r = sync_sina_tech(
        max_articles=int(payload.get("max_articles", 10)),
        page_urls=page_urls,
        rag_enabled=bool(payload.get("rag_enabled", False)),
        dry_run=bool(payload.get("dry_run", False)),
    )
    return _sync_result_dict(r)


def _work_policy_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_countries = payload.get("countries")
    countries: Optional[List[str]] = None
    if isinstance(raw_countries, list) and raw_countries:
        countries = [str(x).strip().upper() for x in raw_countries if str(x).strip()] or None
    from crawler.orchestrator import sync_policy

    def _sync():
        return sync_policy(
            countries=countries,
            max_articles_per_country=int(payload.get("max_articles_per_country", 10)),
            rag_enabled=bool(payload.get("rag_enabled", False)),
            dry_run=bool(payload.get("dry_run", False)),
            skip_prefilter=bool(payload.get("skip_prefilter", False)),
        )

    r = run_tracked_sync(
        "policy",
        "crawl_policy",
        _sync,
        trigger_source="manual",
        action_label="完成政策采集",
    )
    return _sync_result_dict(r)


def _work_literature_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_sources = payload.get("sources")
    sources: Optional[List[str]] = None
    if isinstance(raw_sources, list) and raw_sources:
        sources = [str(x).strip().lower() for x in raw_sources if str(x).strip()] or None
    from crawler.orchestrator import sync_literature

    def _sync():
        return sync_literature(
            sources=sources or ["arxiv"],
            max_arxiv_per_category=int(payload.get("max_arxiv_per_category", 3)),
            max_springer_per_domain=int(payload.get("max_springer_per_domain", 3)),
            scopus_max_results=int(payload.get("scopus_max_results", 10)),
            scopus_days_back=int(payload.get("scopus_days_back", 7)),
            dry_run=bool(payload.get("dry_run", False)),
        )

    r = run_tracked_sync(
        "literature",
        "crawl_literature",
        _sync,
        trigger_source="manual",
        action_label="完成文献更新",
    )
    return {
        "saved": r.saved,
        "skipped_url_dup": r.skipped_url_dup,
        "skipped_no_incident": 0,
        "failed": r.failed,
        "new_keywords": [],
        "debug_log": list(r.debug_log),
    }


def _work_news_bundle_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    """全信源新闻包（与 scripts/sync_sources.py --source news 一致）。"""
    from crawler.news_sync_bundle import NewsSyncConfig, sync_all_news_for_policy_meeting

    cfg = NewsSyncConfig(
        guardian_max_pages=int(payload.get("guardian_max_pages", 2)),
        guardian_page_size=int(payload.get("guardian_page_size", 8)),
        nyt_max_pages=int(payload.get("nyt_max_pages", 2)),
        wechat_max_articles_per_feed=int(payload.get("wechat_max_articles_per_feed", 5)),
        xinhua_max_articles=int(payload.get("xinhua_max_articles", 10)),
        sina_max_articles=int(payload.get("sina_max_articles", 10)),
        policy_max_articles_per_country=int(payload.get("policy_max_articles_per_country", 10)),
        rag_enabled=bool(payload.get("rag_enabled", False)),
    )
    from datetime import datetime

    run_started = datetime.now()
    bundle = sync_all_news_for_policy_meeting(cfg, dry_run=bool(payload.get("dry_run", False)))
    if not bool(payload.get("dry_run", False)):
        record_news_bundle_tasks(
            bundle, trigger_source="manual", run_started_at=run_started
        )
    out = _sync_result_dict(bundle.merged)
    out["by_source"] = {k: _sync_result_dict(v) for k, v in bundle.by_source.items()}
    return out


def _work_agent_scout(payload: Dict[str, Any]) -> Dict[str, Any]:
    url = str(payload.get("url") or "").strip()
    api_key = (payload.get("api_key") or CONFIG_API_KEY or "").strip()
    base_url = (payload.get("base_url") or CONFIG_BASE_URL or "").strip()
    from crawler.agentic_crawl import run_agentic_crawl
    from core.db import incident_from_extraction, save_incident

    _art, incidents, new_kws, dbg = asyncio.run(
        run_agentic_crawl(url, api_key=api_key, base_url=base_url)
    )
    saved = 0
    for inc in incidents:
        try:
            save_incident(incident_from_extraction(inc))
            saved += 1
        except Exception:
            pass
    return {
        "extracted": len(incidents),
        "saved": saved,
        "new_keywords": new_kws[:20],
        "debug_info": dbg,
    }


def _work_deep_research(payload: Dict[str, Any]) -> Dict[str, Any]:
    from core.llm_client import OpenAICompatibleBackend
    from core.mysql_db import build_report_source_rows, save_research_report
    from engine.rag_ingestion.hybrid_retrieval import hybrid_retrieve
    from engine.research_report import generate_research_report_markdown

    question = str(payload.get("question") or "").strip()
    preview_only = bool(payload.get("preview_only", False))
    save_report = bool(payload.get("save_report", True))
    top_k = int(payload.get("top_k", 16))
    risk_domain = payload.get("risk_domain")
    source = (payload.get("source") or "").strip() or None
    api_key = (payload.get("api_key") or CONFIG_API_KEY or "").strip()
    base_url = (payload.get("base_url") or CONFIG_BASE_URL or "").strip()
    model = (payload.get("llm_model") or CONFIG_LLM_MODEL or "").strip()
    backend = OpenAICompatibleBackend(api_key=api_key, base_url=base_url)

    hits = hybrid_retrieve(
        question,
        top_k=top_k,
        risk_domain=risk_domain,
        source=source,
        backend=backend,
    )
    evidence_previews = [
        {
            "article_id": h.article_id,
            "rrf": round(h.rrf_score, 4),
            "snippet": (h.chunk_text or "")[:180],
        }
        for h in hits[:12]
    ]
    if preview_only:
        return {
            "preview_only": True,
            "hits_count": len(hits),
            "evidence_previews": evidence_previews,
        }

    report_md = generate_research_report_markdown(
        question, hits, backend=backend, model=model or None
    )
    saved_report_id = None
    if save_report and report_md.strip():
        hit_dicts = [
            {
                "vector_id": h.vector_id,
                "article_id": h.article_id,
                "rrf_score": h.rrf_score,
                "citation_label": f"来源 {i}",
            }
            for i, h in enumerate(hits, 1)
        ]
        sources = build_report_source_rows(hit_dicts)
        saved_report_id = save_research_report(
            question,
            {
                "top_k": top_k,
                "risk_domain": risk_domain,
                "source": source,
            },
            report_md,
            model_name=model,
            sources=sources,
        )

    return {
        "preview_only": False,
        "hits_count": len(hits),
        "evidence_previews": evidence_previews,
        "report_markdown": report_md,
        "saved_report_id": saved_report_id,
    }
