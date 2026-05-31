"""
功能：门户「运行监控中心」数据聚合。

输入：MySQL system_tasks + 三系统 track 统计。
输出：MonitoringOverviewResponse 所需 plain dict。
上下游：api/routers/monitoring.py 调用；读 core/system_tasks、core/mysql_monitor_tracks。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.monitoring_config import ALL_SYSTEM_KEYS, SUBSYSTEM_SCHEDULES
from core.mysql_monitor_tracks import (
    aggregate_literature_by_source,
    aggregate_meeting_by_source,
    aggregate_policy_by_source,
    count_literature_recent_days,
    count_literature_track_rows,
    count_meeting_recent_days,
    count_meeting_track_rows,
    count_policy_recent_days,
    count_policy_track_rows,
)
from core.system_tasks import (
    count_today_runs,
    fetch_last_success_by_system,
    fetch_recent_tasks,
)

# 子系统 timeline 展示短名
_SYSTEM_SHORT = {
    "policy": "政策系统",
    "meeting": "会议系统",
    "literature": "文献系统",
}


def _parse_message(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except (json.JSONDecodeError, TypeError):
        return {"summary": str(raw)}


def _to_iso(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def _format_ago(dt: Optional[datetime], now: Optional[datetime] = None) -> str:
    """相对时间中文文案。"""
    if dt is None:
        return "—"
    now = now or datetime.now()
    if dt.tzinfo:
        dt = dt.replace(tzinfo=None)
    delta = now - dt
    sec = int(delta.total_seconds())
    if sec < 0:
        return "刚刚"
    if sec < 60:
        return "刚刚"
    if sec < 3600:
        return f"{sec // 60} 分钟前"
    if sec < 86400:
        return f"{sec // 3600} 小时前"
    return f"{sec // 86400} 天前"


def _format_until(dt: datetime, now: Optional[datetime] = None) -> str:
    """距未来时刻的相对文案。"""
    now = now or datetime.now()
    sec = int((dt - now).total_seconds())
    if sec <= 0:
        return "即将运行"
    if sec < 3600:
        return f"{max(1, sec // 60)} 分钟后"
    if sec < 86400:
        return f"{sec // 3600} 小时后"
    return dt.strftime("%m-%d %H:%M")


def _next_run_at(schedule_times: tuple[tuple[int, int], ...], now: Optional[datetime] = None) -> datetime:
    """根据每日计划时刻列表，计算下一次运行时间。"""
    now = now or datetime.now()
    candidates: List[datetime] = []
    for day_offset in (0, 1):
        base = (now + timedelta(days=day_offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        for hour, minute in schedule_times:
            t = base.replace(hour=hour, minute=minute)
            if t > now:
                candidates.append(t)
    if not candidates:
        h, m = schedule_times[0]
        return (now + timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)
    return min(candidates)


def _subsystem_health(
    system_key: str,
    last_success: Optional[Dict[str, Any]],
    now: datetime,
) -> str:
    """healthy | running | degraded | stale | unknown"""
    cfg = SUBSYSTEM_SCHEDULES[system_key]
    if not last_success or not last_success.get("end_time"):
        return "unknown"
    end = last_success["end_time"]
    if not isinstance(end, datetime):
        try:
            end = datetime.fromisoformat(str(end))
        except ValueError:
            return "unknown"
    hours = (now - end).total_seconds() / 3600.0
    if hours <= cfg.sla_hours:
        return "healthy"
    if hours <= cfg.sla_hours * 2:
        return "degraded"
    return "stale"


def _platform_status(sub_statuses: List[str]) -> str:
    if not sub_statuses or all(s == "unknown" for s in sub_statuses):
        return "unknown"
    if any(s == "stale" for s in sub_statuses):
        return "degraded"
    if any(s == "degraded" for s in sub_statuses):
        return "degraded"
    if all(s == "healthy" for s in sub_statuses):
        return "healthy"
    return "unknown"


def _status_label(status: str) -> str:
    return {
        "healthy": "系统运行正常",
        "degraded": "部分子系统延迟",
        "stale": "监测任务超时",
        "unknown": "等待首次运行",
    }.get(status, "状态未知")


def _subsystem_metrics(system_key: str) -> Dict[str, Any]:
    """各子系统业务指标（与卡片字段对应）。"""
    if system_key == "policy":
        src_df = aggregate_policy_by_source(limit=50)
        return {
            "today_new": count_policy_recent_days(1),
            "total": count_policy_track_rows(),
            "source_count": len(src_df) if not src_df.empty else 0,
            "extra_label": "新闻源",
        }
    if system_key == "meeting":
        return {
            "today_new": count_meeting_recent_days(1),
            "total": count_meeting_track_rows(),
            "source_count": len(aggregate_meeting_by_source(limit=50)) or 0,
            "extra_label": "监测来源",
            "highlight_count": count_meeting_recent_days(90),
            "highlight_label": "重点会议",
        }
    # literature
    lit_sources = aggregate_literature_by_source(limit=10)
    platform_n = len(lit_sources) if not lit_sources.empty else 3
    return {
        "today_new": count_literature_recent_days(1),
        "total": count_literature_track_rows(),
        "source_count": max(platform_n, 1),
        "extra_label": "覆盖平台",
    }


def get_monitoring_overview() -> Dict[str, Any]:
    """聚合门户运行监控中心完整载荷。"""
    now = datetime.now()
    last_by_system = fetch_last_success_by_system()
    sub_statuses: List[str] = []
    subsystems: List[Dict[str, Any]] = []

    next_runs: List[datetime] = []
    for key in ALL_SYSTEM_KEYS:
        cfg = SUBSYSTEM_SCHEDULES[key]
        next_runs.append(_next_run_at(cfg.daily_run_times, now))
        last = last_by_system.get(key)
        health = _subsystem_health(key, last, now)
        sub_statuses.append(health)
        metrics = _subsystem_metrics(key)
        end_time = last.get("end_time") if last else None
        if end_time and not isinstance(end_time, datetime):
            try:
                end_time = datetime.fromisoformat(str(end_time))
            except ValueError:
                end_time = None

        status_label = cfg.status_label_healthy if health == "healthy" else {
            "degraded": "运行延迟",
            "stale": "待重新同步",
            "unknown": "等待首次运行",
        }.get(health, "状态未知")

        card: Dict[str, Any] = {
            "key": key,
            "name": cfg.label,
            "status": health,
            "status_label": status_label,
            "last_run_at": _to_iso(end_time),
            "last_run_ago": _format_ago(end_time, now),
            "today_new": metrics["today_new"],
            "total": metrics["total"],
            "source_count": metrics["source_count"],
            "source_label": metrics.get("extra_label", "数据源"),
            "detail_href": f"/{key}" if key != "meeting" else "/meetings",
        }
        if "highlight_count" in metrics:
            card["highlight_count"] = metrics["highlight_count"]
            card["highlight_label"] = metrics.get("highlight_label", "重点项")
        subsystems.append(card)

    platform_status = _platform_status(sub_statuses)
    online = sum(1 for s in sub_statuses if s == "healthy")

    # 全局最近运行
    all_ends = [
        last_by_system[k]["end_time"]
        for k in ALL_SYSTEM_KEYS
        if k in last_by_system and last_by_system[k].get("end_time")
    ]
    last_run_dt: Optional[datetime] = None
    for e in all_ends:
        dt = e if isinstance(e, datetime) else None
        if dt is None:
            try:
                dt = datetime.fromisoformat(str(e))
            except ValueError:
                continue
        if last_run_dt is None or dt > last_run_dt:
            last_run_dt = dt

    next_dt = min(next_runs) if next_runs else _next_run_at((0, 0), now)
    today_new_total = sum(s["today_new"] for s in subsystems)

    timeline: List[Dict[str, Any]] = []
    for row in fetch_recent_tasks(limit=20):
        msg = _parse_message(row.get("message"))
        sk = str(row.get("system_key") or "")
        st = str(row.get("status") or "")
        start = row.get("start_time")
        summary = msg.get("summary") or f"{_SYSTEM_SHORT.get(sk, sk)}任务"
        if st == "success" and row.get("data_count") is not None:
            dc = int(row.get("data_count") or 0)
            if "新增" not in summary:
                summary = f"完成同步（新增 {dc} 条）"
        timeline.append(
            {
                "at": _to_iso(start),
                "system_key": sk,
                "system_label": _SYSTEM_SHORT.get(sk, sk),
                "summary": summary,
                "status": st,
                "data_count": int(row.get("data_count") or 0),
            }
        )

    return {
        "platform": {
            "status": platform_status,
            "status_label": _status_label(platform_status),
            "online_subsystems": online,
            "total_subsystems": len(ALL_SYSTEM_KEYS),
            "today_run_count": count_today_runs(),
            "today_new_data": today_new_total,
            "last_run_at": _to_iso(last_run_dt),
            "last_run_ago": _format_ago(last_run_dt, now),
            "next_scheduled_at": _to_iso(next_dt),
            "next_scheduled_ago": _format_until(next_dt, now),
        },
        "subsystems": subsystems,
        "timeline": timeline,
    }
