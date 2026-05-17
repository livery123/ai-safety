"""
功能：演示操作区密码校验（环境变量 DEMO_PASSWORD）。

输入：`st.session_state["demo_pwd"]` 与用户输入；`os.getenv("DEMO_PASSWORD")`。
输出：布尔；是否解锁操作区。
上下游：`ui.pages.system` 在渲染演示区前调用；不访问数据库。
"""

from __future__ import annotations

import os

import streamlit as st


def demo_unlocked() -> bool:
    """
    功能：校验演示密码；未配置 DEMO_PASSWORD 时默认开放。
    输入：依赖 session_state 中 `demo_pwd`（由密码框写入）。
    输出：True 表示可展示同步/侦察等危险操作。
    上下游：仅系统状态页使用。
    """
    required = os.getenv("DEMO_PASSWORD", "").strip()
    if not required:
        return True
    entered = st.session_state.get("demo_pwd", "")
    return entered == required
