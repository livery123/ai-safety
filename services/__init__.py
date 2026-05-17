"""
services 包：面向页面聚合的只读/缓存数据访问。

功能：封装 MySQL、专项监测等与 UI 无关的读取逻辑，`@st.cache_data` 集中于本包。
输入：由各 `ui.pages.*` 按需调用函数参数。
输出：DataFrame、标量或与 core 层一致的纯数据结构。
上下游：`core.mysql_*` ← `services.*` → `ui.pages.*`。
"""
