"""
ui 包：Streamlit 页面壳、分页面与可复用组件。

功能：将 `app.py` 中的 UI 与业务展示层拆分为可维护子模块，便于单页排查与演进。
输入：由 `app.main` 在进程内 import 并调用各 `render_*`。
输出：无包级副作用；各子模块在调用时向 Streamlit 写入组件。
上下游：`app.py` → `ui.layout` / `ui.pages.*` → `ui.components.*` / `services.*`。
"""
