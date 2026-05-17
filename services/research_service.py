"""
功能：深度调研报告列表与单条加载（MySQL research_reports）。

输入：limit / report id。
输出：DataFrame（列表）或行 dict（详情）；异常降级为空。
上下游：`core.mysql_db`；`ui.pages.research` 读取。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd
import streamlit as st

from core.mysql_db import get_research_report_by_id, list_research_reports


@st.cache_data(ttl=30)
def cached_research_report_list(limit: int = 25) -> pd.DataFrame:
    """功能：近期深度调研报告列表。"""
    try:
        rows = list_research_reports(limit=limit)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


def fetch_research_report(hid: int) -> Optional[Dict[str, Any]]:
    """
    功能：按主键读取单条报告（含 markdown 正文与引用行）。
    输入：MySQL research_reports.id。
    输出：dict 或 None；不经 cache，避免刚写入后滞后。
    上下游：`ui.pages.research` 在固定结果容器中调用。
    """
    try:
        return get_research_report_by_id(hid)
    except Exception:
        return None
