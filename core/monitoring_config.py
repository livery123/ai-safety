"""
功能：三子系统监测调度与 SLA 配置（门户运行中心唯一来源）。

输入：无（静态配置，后续可改为 YAML/环境变量）。
输出：各 system_key 的 cron 描述、SLA 小时数、展示文案。
上下游：core/system_tasks、api/services/monitoring_data。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class SubsystemSchedule:
    """单个子系统的调度与展示配置。"""

    system_key: str
    task_name: str
    label: str
    status_label_healthy: str
    sla_hours: float
    # 每日计划运行时刻 (hour, minute)，用于估算「下次计划运行」
    daily_run_times: tuple[tuple[int, int], ...]


SUBSYSTEM_SCHEDULES: Dict[str, SubsystemSchedule] = {
    "policy": SubsystemSchedule(
        system_key="policy",
        task_name="crawl_policy",
        label="政策法规/科技政策监测系统",
        status_label_healthy="自动采集中",
        sla_hours=7.0,
        daily_run_times=((0, 0), (6, 0), (12, 0), (18, 0)),
    ),
    "meeting": SubsystemSchedule(
        system_key="meeting",
        task_name="crawl_meeting",
        label="重大国际会议监测系统",
        status_label_healthy="自动分析中",
        sla_hours=26.0,
        daily_run_times=((8, 0),),
    ),
    "literature": SubsystemSchedule(
        system_key="literature",
        task_name="crawl_literature",
        label="国内外相关文献监测系统",
        status_label_healthy="自动更新中",
        sla_hours=13.0,
        daily_run_times=((3, 0), (15, 0)),
    ),
}

ALL_SYSTEM_KEYS: List[str] = list(SUBSYSTEM_SCHEDULES.keys())

# 监测周报 cron（周一 08:05，生成上一完整自然周报告）
WEEKLY_REPORT_CRON = "5 8 * * 1"
WEEKLY_REPORT_TASK_NAME = "weekly_report"
WEEKLY_REPORT_SYSTEM_KEY = "platform"
