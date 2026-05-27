"""
功能：「📌 专项监测」路由——Hub 三系统大厅或单个子系统全屏页。

输入：session_state[TRACK_SYSTEM]。
输出：无。
上下游：`app.main` 在 NAV_MAIN[2] 时分派；子模块 hub / policy / meeting / literature。
"""

from __future__ import annotations

import streamlit as st

from ui.components.track_themes import TrackSystemKey
from ui.pages.tracks.hub import render_tracks_hub
from ui.pages.tracks.literature import render_literature_system_page
from ui.pages.tracks.meeting import render_meeting_system_page
from ui.pages.tracks.policy import render_policy_system_page
from ui.state import SessionKeys


def render_tracks_page() -> None:
    """
    功能：专项监测总入口；None 渲染 Hub，否则进入对应子系统。
    输入：无显式参数。
    输出：无。
    """
    system = st.session_state.get(SessionKeys.TRACK_SYSTEM)
    if not system:
        render_tracks_hub()
        return

    key = str(system)
    if key == "policy":
        render_policy_system_page()
    elif key == "meeting":
        render_meeting_system_page()
    elif key == "literature":
        render_literature_system_page()
    else:
        st.session_state.pop(SessionKeys.TRACK_SYSTEM, None)
        render_tracks_hub()


__all__ = ["render_tracks_page"]
