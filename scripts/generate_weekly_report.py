#!/usr/bin/env python3
"""
监测周报 / 简报自动生成 CLI（cron 入口）。

功能：按自然周拉取监测数据 → LLM 四维周报 → 写入 monitoring_weekly_reports + system_tasks。
输入：--system、--week-start、--all、--dry-run、--skip-llm、--force。
输出：exit 0/1；stdout 日志行。
上下游：engine/weekly_report.py、core/mysql_weekly_reports.py、core/system_tasks.py。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from core.config import LLM_MODEL
from core.llm_client import OpenAICompatibleBackend
from core.mysql_weekly_reports import (
    compute_week_range,
    get_report_by_week,
    save_weekly_report,
)
from core.system_tasks import begin_task, finish_task
from core.weekly_report_data import fetch_context_entries, fetch_entries_for_week
from engine.weekly_report import build_report_title, generate_weekly_report_markdown

VALID_SYSTEMS = ("policy", "meeting", "literature", "platform")
REPORT_TYPES = ("weekly", "brief")


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    print(f"{ts} [weekly] {msg}", flush=True)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s.strip()[:10])


def generate_one(
    system_key: str,
    report_type: str,
    week_start: date,
    week_end: date,
    *,
    trigger_source: str = "cron",
    task_id: int | None = None,
    dry_run: bool = False,
    skip_llm: bool = False,
    force: bool = False,
    with_brief: bool = True,
) -> dict:
    """
    功能：为单系统生成 weekly（及可选 brief）报告。
    输出：{system_key, report_type, report_id, skipped, article_count}。
    """
    if report_type not in REPORT_TYPES:
        report_type = "weekly"

    existing = get_report_by_week(system_key, report_type, week_start)
    if existing and not force and not dry_run:
        _log(f"SKIP {system_key}/{report_type} week={week_start} id={existing.get('id')} (already exists)")
        return {
            "system_key": system_key,
            "report_type": report_type,
            "report_id": existing.get("id"),
            "skipped": True,
            "article_count": existing.get("article_count", 0),
        }

    entries = fetch_entries_for_week(system_key, week_start, week_end)
    context = fetch_context_entries(system_key, week_start, week_end)
    article_ids = [e.article_id for e in entries if e.article_id > 0]

    backend = OpenAICompatibleBackend()
    use_skip = skip_llm or dry_run
    md = generate_weekly_report_markdown(
        system_key=system_key,
        week_start=week_start,
        week_end=week_end,
        entries=entries,
        context_entries=context,
        report_type=report_type,
        backend=backend,
        model=LLM_MODEL,
        skip_llm=use_skip,
    )
    title = build_report_title(system_key, week_start, week_end, report_type)

    report_id = None
    if not dry_run:
        report_id = save_weekly_report(
            system_key=system_key,
            report_type=report_type,
            week_start=week_start,
            week_end=week_end,
            title=title,
            report_markdown=md,
            source_article_ids=article_ids,
            article_count=len(entries),
            model_name=LLM_MODEL if not use_skip else "template",
            task_id=task_id,
            trigger_source=trigger_source,
            status="success",
        )
    _log(
        f"{'DRY-RUN' if dry_run else 'OK'} {system_key}/{report_type} "
        f"week={week_start} articles={len(entries)} report_id={report_id}"
    )
    return {
        "system_key": system_key,
        "report_type": report_type,
        "report_id": report_id,
        "skipped": False,
        "article_count": len(entries),
    }


def run_batch(
    systems: list[str],
    week_start: date | None,
    *,
    trigger_source: str = "cron",
    dry_run: bool = False,
    skip_llm: bool = False,
    force: bool = False,
    with_brief: bool = True,
) -> int:
    """批量生成；写 system_tasks；返回 exit code。"""
    ws, we = compute_week_range(week_start=week_start)
    _log(f"batch start week={ws}~{we} systems={systems} trigger={trigger_source}")

    task_id = None
    if not dry_run:
        task_id = begin_task("platform", "weekly_report", trigger_source=trigger_source)

    t0 = time.time()
    results: list[dict] = []
    report_ids: dict[str, int | None] = {}
    errors: list[str] = []

    try:
        for sk in systems:
            try:
                r = generate_one(
                    sk,
                    "weekly",
                    ws,
                    we,
                    trigger_source=trigger_source,
                    task_id=task_id,
                    dry_run=dry_run,
                    skip_llm=skip_llm,
                    force=force,
                )
                results.append(r)
                if r.get("report_id"):
                    report_ids[f"{sk}/weekly"] = r["report_id"]
                if with_brief:
                    rb = generate_one(
                        sk,
                        "brief",
                        ws,
                        we,
                        trigger_source=trigger_source,
                        task_id=task_id,
                        dry_run=dry_run,
                        skip_llm=skip_llm,
                        force=force,
                    )
                    results.append(rb)
                    if rb.get("report_id"):
                        report_ids[f"{sk}/brief"] = rb["report_id"]
            except Exception as e:
                errors.append(f"{sk}: {e}")
                _log(f"ERROR {sk}: {e}")

        duration = round(time.time() - t0, 1)
        summary = (
            f"weekly_report {ws}~{we} "
            f"systems={len(systems)} errors={len(errors)} duration={duration}s"
        )
        if errors:
            if not dry_run and task_id:
                finish_task(
                    task_id,
                    status="failed",
                    data_count=sum(r.get("article_count", 0) for r in results),
                    message=json.dumps(
                        {
                            "summary": summary,
                            "week_start": ws.isoformat(),
                            "week_end": we.isoformat(),
                            "report_ids": report_ids,
                            "errors": errors,
                        },
                        ensure_ascii=False,
                    ),
                )
            _log(f"FAILED {summary} errors={errors}")
            return 1

        if not dry_run and task_id:
            finish_task(
                task_id,
                status="success",
                data_count=sum(r.get("article_count", 0) for r in results if not r.get("skipped")),
                message=json.dumps(
                    {
                        "summary": summary,
                        "week_start": ws.isoformat(),
                        "week_end": we.isoformat(),
                        "report_ids": report_ids,
                        "duration_sec": duration,
                        "results_count": len(results),
                    },
                    ensure_ascii=False,
                ),
            )
        _log(f"DONE {summary} report_ids={report_ids}")
        return 0
    except Exception as e:
        if not dry_run and task_id:
            finish_task(
                task_id,
                status="failed",
                data_count=0,
                message=json.dumps({"summary": str(e)}, ensure_ascii=False),
            )
        _log(f"FATAL {e}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 治理监测周报自动生成")
    parser.add_argument(
        "--system",
        choices=VALID_SYSTEMS,
        action="append",
        help="子系统（可多次指定）；默认 policy",
    )
    parser.add_argument("--all", action="store_true", help="生成 policy/meeting/literature/platform")
    parser.add_argument("--week-start", type=str, help="周起始周一 YYYY-MM-DD（默认上一完整自然周）")
    parser.add_argument("--dry-run", action="store_true", help="不写入 MySQL")
    parser.add_argument("--skip-llm", action="store_true", help="不调用 LLM，使用模板正文")
    parser.add_argument("--force", action="store_true", help="覆盖已有同周报告")
    parser.add_argument("--no-brief", action="store_true", help="不生成 brief")
    parser.add_argument("--trigger", default="cron", choices=("cron", "manual", "backfill"))
    args = parser.parse_args()

    if args.all:
        systems = list(VALID_SYSTEMS)
    elif args.system:
        systems = args.system
    else:
        systems = ["policy"]

    week_start = _parse_date(args.week_start) if args.week_start else None
    return run_batch(
        systems,
        week_start,
        trigger_source=args.trigger,
        dry_run=args.dry_run,
        skip_llm=args.skip_llm,
        force=args.force,
        with_brief=not args.no_brief,
    )


if __name__ == "__main__":
    raise SystemExit(main())
