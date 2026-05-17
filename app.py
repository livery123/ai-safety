"""
Streamlit 应用入口：AI 治理监测演示看板（汇报版）。

功能：配置页面与应用壳后，按主导航分派至 `ui.pages.*`；数据访问经 `services.*` 聚合缓存。
输入：环境与 MySQL/SQLite；由 Streamlit rerun 驱动。
输出：无；由各页面模块渲染组件。
上下游：`ui.layout`、`ui.pages.*`、`services.*`、`core.db.init_db`。
"""

from __future__ import annotations

import asyncio
import sys

from ui.layout import configure_page, render_app_shell, render_sidebar_about
from ui.pages.dashboard import render_dashboard_page
from ui.pages.incidents import render_incidents_page
from ui.pages.research import render_research_page
from ui.pages.system import render_system_page
from ui.pages.tracks import render_tracks_page
from ui.state import NAV_MAIN


# Windows 下 Playwright 子进程兼容
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def main() -> None:
    """
    功能：`set_page_config`、初始化库表、挂载全局样式与顶栏；按主导渲染单页内容；最后写侧边栏简介。
    输入：无显式参数；依赖 session 与控件状态。
    输出：无；副作用包含数据库初始化与按需查询。
    """
    configure_page()
    page = render_app_shell()

    if page == NAV_MAIN[0]:
        render_dashboard_page()
    elif page == NAV_MAIN[1]:
        render_incidents_page()
    elif page == NAV_MAIN[2]:
        render_tracks_page()
    elif page == NAV_MAIN[3]:
        render_research_page()
    elif page == NAV_MAIN[4]:
        render_system_page()

    render_sidebar_about()


if __name__ == "__main__":
    main()
