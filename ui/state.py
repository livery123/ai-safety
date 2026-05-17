"""
功能：集中定义主导航与会话状态 key，避免魔法字符串散落、减少拼写错误。

输入：无运行时参数；仅常量。
输出：供各页面与组件 import 的字符串常量。
上下游：`ui.layout`、`ui.pages.*` 读取；与 `st.session_state` 配合使用。
"""

from __future__ import annotations

# 主导航选项元组（顺序固定；与页面分发 if/elif 一致）
NAV_MAIN: tuple[str, ...] = (
    "📊 监测看板",
    "📋 情报详情",
    "📌 专项监测",
    "📚 深度调研",
    "⚙️ 系统状态",
)


class SessionKeys:
    """Streamlit session_state 键名：仅列本应用显式读写过的键。"""

    NAV_MAIN_RADIO = "nav_main_radio"
    """主导航水平 `st.radio` 的控件 key。"""
    SELECTED_RESEARCH_REPORT_ID = "selected_research_report_id"
    """深度调研页：用户点击「载入所选报告」后要展示的报告主键。"""