"""
Streamlit 应用入口：AI 治理监测演示看板（汇报版）。

功能：水平导航单页渲染；情报 MySQL 分页；卫报同步 / Agent 侦察 / 深度调研走 SQLite 队列后台线程；
     侧边栏受密码保护的操作区供现场演示。
输入：MySQL（articles / article_extractions）；DB_PATH SQLite（Agent 演示 + ui_background_jobs 队列）。
输出：页面渲染与任务状态轮询。
上下游：core.mysql_dashboard、core.db、core.ui_jobs、crawler.*
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from core.config import (
    API_KEY,
    BASE_URL,
    DB_PATH,
    GUARDIAN_API_KEY,
    LLM_MODEL,
    MYSQL_DATABASE,
    MYSQL_HOST,
    MYSQL_PORT,
    NYT_API_KEY,
)
from core.db import init_db
from core.mysql_dashboard import (
    count_dashboard_incidents,
    fetch_dashboard_incidents_page,
    fetch_dashboard_latest_rows,
    fetch_distinct_content_types,
    get_dashboard_keywords_df,
    get_dashboard_stats,
    get_dashboard_taxonomy_df,
)
from core.mysql_monitor_tracks import (
    count_meeting_recent_days,
    count_meeting_track_rows,
    count_policy_recent_days,
    count_policy_track_rows,
    fetch_meeting_track_page,
    fetch_policy_track_page,
    literature_monitor_status,
)
from core.mysql_db import get_research_report_by_id, list_research_reports
from core.ui_jobs import get_job, start_job_thread
from crawler.sources import SINA_TECH_URL, XINHUA_TECH_URL
from crawler.sources.wechat2rss import WECHAT_RSS_POOL
from models.schema import RISK_DOMAIN_CHOICES

# Windows 下 Playwright 子进程兼容
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# 环形图配色（与原先 Plotly 版监测看板一致，用于 Altair donut）
_DONUT_COLORS = (
    "#4f8ef7",
    "#3db88a",
    "#a78bfa",
    "#f0ab43",
    "#e879a8",
    "#5eb3f6",
    "#7dd3c0",
    "#c4b5fd",
    "#fbbf24",
    "#fb923c",
    "#38bdf8",
    "#94a3b8",
)
# 与旧 `go.Pie(hole=0.54)` 一致的内孔比例（内半径 / 外半径）
_DONUT_HOLE_RATIO = 0.54


def _donut_color_list(n: int) -> list[str]:
    """
    功能：为扇区序列循环分配颜色，与原先 Plotly 环形图同源色板一致。
    输入：扇区个数 n。
    输出：长度 n 的十六进制颜色列表。
    上下游：监测看板 Altair 环形 encode.color Scale.range。
    """
    base = list(_DONUT_COLORS)
    out: list[str] = []
    while len(out) < n:
        out.extend(base)
    return out[:n]


def _altair_dash_empty_state(msg: str, *, height_px: int) -> alt.Chart:
    """
    功能：无数据时在仍使用 `st.altair_chart` 挂载点的前提下展示居中提示，避免「图表 vs 文案」组件类型切换。
    输入：提示文案、画布高度（像素）。
    输出：Altair Chart（仅文本层）。
    上下游：监测看板右侧「风险主域 / 子域」空态。
    """
    src = pd.DataFrame({"msg": [msg]})
    return (
        alt.Chart(src)
        .mark_text(align="center", baseline="middle", color="#94a3b8", fontSize=14)
        .encode(
            x=alt.value(0),
            y=alt.value(0),
            text=alt.Text("msg:N"),
        )
        .properties(height=int(height_px), width="container")
        .configure_view(strokeWidth=0)
    )


def _altair_dash_donut(
    labels: list[str],
    values: list[int],
    *,
    height_px: int,
    width_px: int,
    mode: str,
) -> alt.Chart:
    """
    功能：甜甜圈图（Altair arc，无 Plotly iframe），视觉上对齐原先 Plotly 版：hole≈54%、边框色、扇区间隙、内侧百分比字号、右侧图例规格（主域/子域两套参数）。
    输入：类别与计数；画布尺寸；mode 取 `domain`（风险主域）或 `subdomain`（高频子域）；篇数为 0 的扇区跳过。
    输出：composite Chart（弧形 + 居扇区百分比文字 + 图例 + tooltip）。
    上下游：`st.altair_chart`；数据源于 `_cached_taxonomy`。
    """
    rows: List[Tuple[str, int]] = []
    for lb, vv in zip(labels, values):
        v = int(vv or 0)
        if v > 0:
            rows.append(((lb or "").strip(), v))
    if not rows:
        return _altair_dash_empty_state("暂无有效数据。", height_px=height_px)

    df = pd.DataFrame(rows, columns=["label", "count"])
    if mode == "subdomain":
        df = df.sort_values("count", ascending=False).reset_index(drop=True)
    else:
        df = df.reset_index(drop=True)
    tot = int(df["count"].sum())
    if tot <= 0:
        return _altair_dash_empty_state("暂无有效数据。", height_px=height_px)

    df["lab_ord"] = range(len(df))
    palette = _donut_color_list(len(df))
    domain_ord = df["label"].astype(str).tolist()

    outer_r = int(max(88, min(142, height_px // 2 - 20)))
    inner_r = int(round(outer_r * _DONUT_HOLE_RATIO))
    mid_r = (inner_r + outer_r) / 2

    # 对齐旧 Pie 的视觉「外拉」感：padAngle≈两度级微缝 + 边框；domain 略大对应 pull=0.025 / subdomain 对应 0.018
    pad = 0.028 if mode == "domain" else 0.018
    pct_font = 13 if mode == "domain" else 11
    leg_fs = 11 if mode == "domain" else 9
    leg_limit = 300 if mode == "domain" else 280

    prep = (
        alt.Chart(df)
        .transform_joinaggregate(total="sum(count)", groupby=[])
        .transform_calculate(
            frac="datum.count / datum.total",
            pct_txt="format(datum.frac,'.1%')",
            mid_r=str(mid_r),
        )
    )

    arcs = prep.encode(
        theta=alt.Theta("count:Q", stack=True, title=None),
        color=alt.Color(
            "label:N",
            scale=alt.Scale(domain=domain_ord, range=palette),
            sort=None,
            legend=alt.Legend(
                title=None,
                orient="right",
                labelColor="#a8b3cf",
                labelFontSize=leg_fs,
                labelLimit=leg_limit,
                symbolType="square",
                symbolSize=76,
                padding=10,
                rowPadding=8,
            ),
            title=None,
        ),
        order=alt.Order("lab_ord:Q", sort="ascending"),
        tooltip=[
            alt.Tooltip("label:N", title="类别"),
            alt.Tooltip("count:Q", title="篇数"),
            alt.Tooltip("frac:Q", title="占比", format=".1%"),
        ],
    ).mark_arc(
        innerRadius=inner_r,
        outerRadius=outer_r,
        stroke="#0f1424",
        strokeWidth=2,
        padAngle=pad,
    )

    labels_layer = prep.encode(
        theta=alt.Theta("count:Q", stack="center"),
        radius=alt.Radius("mid_r:Q"),
        text=alt.Text("pct_txt:N"),
    ).mark_text(
        align="center",
        baseline="middle",
        fill="#e8eaf6",
        fontSize=pct_font,
    )

    return (
        (arcs + labels_layer)
        .properties(height=int(height_px), width=int(width_px))
        .configure_view(strokeOpacity=0)
        .configure_legend(symbolStrokeWidth=0)
    )


# ---------------------------------------------------------------------------
# 缓存包装：只读查询短 TTL；改用水平导航后仅在进入对应页时调用
# ---------------------------------------------------------------------------

@st.cache_data(ttl=120)
def _cached_stats() -> Tuple[int, int, int]:
    """功能：缓存版 MySQL 汇总；输出：(extractions 数, 标签去重数, 主域×子域组合种数)。"""
    try:
        return get_dashboard_stats()
    except Exception:
        return 0, 0, 0


@st.cache_data(ttl=120)
def _cached_taxonomy() -> pd.DataFrame:
    """功能：缓存版主域×子域频次（MySQL JSON 展开聚合）。"""
    try:
        return get_dashboard_taxonomy_df()
    except Exception:
        return pd.DataFrame(columns=["domain", "subdomain", "tax_count", "first_seen"])


@st.cache_data(ttl=120)
def _cached_keywords() -> pd.DataFrame:
    """功能：缓存版 tags_raw 聚合高频词（Top 60）。"""
    try:
        return get_dashboard_keywords_df()
    except Exception:
        return pd.DataFrame(columns=["keyword", "count"])


@st.cache_data(ttl=60)
def _cached_latest_incidents(limit: int = 20) -> pd.DataFrame:
    """功能：缓存最新情报列表（MySQL）；输入：limit；输出：DataFrame。"""
    try:
        return fetch_dashboard_latest_rows(limit)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=45)
def _cached_distinct_content_types() -> List[str]:
    """功能：资讯类别下拉；与全量 DF 无关，避免拉整表 DISTINCT。"""
    try:
        return fetch_distinct_content_types()
    except Exception:
        return []


@st.cache_data(ttl=30)
def _cached_incidents_count(fdom: str, flevel: str, fkw: str) -> int:
    """功能：分页总条数；空串表示不按该维度筛选。"""
    try:
        return count_dashboard_incidents(
            risk_domain=fdom.strip() or None,
            content_type=flevel.strip() or None,
            keyword=fkw.strip() or None,
        )
    except Exception:
        return 0


@st.cache_data(ttl=30)
def _cached_incidents_page(fdom: str, flevel: str, fkw: str, offset: int, limit: int) -> pd.DataFrame:
    """功能：情报详情分页；limit 由页面控件传入（50～200）。"""
    try:
        return fetch_dashboard_incidents_page(
            offset,
            limit,
            risk_domain=fdom.strip() or None,
            content_type=flevel.strip() or None,
            keyword=fkw.strip() or None,
        )
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=30)
def _cached_research_report_list(limit: int = 25) -> pd.DataFrame:
    """近期深度调研报告列表（MySQL research_reports）。"""
    try:
        rows = list_research_reports(limit=limit)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=45)
def _cached_policy_track_count(kw: str) -> int:
    """政策法规赛道总行数（可选关键词 AND）。"""
    try:
        return count_policy_track_rows(keyword=(kw or "").strip() or None)
    except Exception:
        return 0


@st.cache_data(ttl=45)
def _cached_policy_track_recent7(kw: str) -> int:
    """政策法规赛道近 7 日条数。"""
    try:
        return count_policy_recent_days(7, keyword=(kw or "").strip() or None)
    except Exception:
        return 0


@st.cache_data(ttl=45)
def _cached_policy_track_page_rows(kw: str, offset: int, limit: int) -> pd.DataFrame:
    """政策法规分页明细。"""
    try:
        return fetch_policy_track_page(
            offset,
            limit,
            keyword=(kw or "").strip() or None,
        )
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=45)
def _cached_meeting_track_count(kw: str) -> int:
    """国际会议赛道总行数。"""
    try:
        return count_meeting_track_rows(keyword=(kw or "").strip() or None)
    except Exception:
        return 0


@st.cache_data(ttl=45)
def _cached_meeting_track_recent30(kw: str) -> int:
    """国际会议赛道近 30 日条数。"""
    try:
        return count_meeting_recent_days(30, keyword=(kw or "").strip() or None)
    except Exception:
        return 0


@st.cache_data(ttl=45)
def _cached_meeting_track_page_rows(kw: str, offset: int, limit: int) -> pd.DataFrame:
    """国际会议分页明细。"""
    try:
        return fetch_meeting_track_page(
            offset,
            limit,
            keyword=(kw or "").strip() or None,
        )
    except Exception:
        return pd.DataFrame()


def _textarea_urls_to_list(raw: str) -> Optional[List[str]]:
    """
    功能：演示区「每行一个列表页 URL」转为 orchestrator 的 page_urls；留空则用适配器默认频道。
    输入：用户粘贴的多行字符串。
    输出：去首尾空白的 URL 列表；若无有效行则 None。
    """
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    return lines or None


# ---------------------------------------------------------------------------
# 后台任务轮询：SQLite 仅存状态（core.ui_jobs）；不阻塞 Streamlit 请求线程。
# ---------------------------------------------------------------------------


def _background_job_panel(slot_key: str, title: str) -> None:
    """功能：展示 ui_background_jobs 单条进度；completed 时顺带清一次 st 数据缓存以便看板刷新。"""
    jid = st.session_state.get(slot_key)
    row = get_job(str(jid)) if jid else None

    # 始终占位同一 bordered 容器，避免 jid 从无到有或任务状态跳转时整块子树凭空插入/删减，诱发前端 insertBefore/reconcile 异常。
    with st.container(border=True):
        if not jid:
            st.caption(f"**{title}**：当前无进行中任务；如需查看进度请先提交后台任务。")
            return

        if row is None:
            st.warning(f"{title}：任务记录不存在。")
            if st.button("关闭", key=f"dismiss_{slot_key}"):
                st.session_state.pop(slot_key, None)
                st.rerun()
            return
        stat = str(row.get("status") or "")
        st.markdown(f"**{title}** · `{str(jid)[:8]}…` · 状态：**{stat}**")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔄 刷新状态", key=f"refresh_{slot_key}"):
                st.rerun()
        with c2:
            if stat in ("completed", "failed") and st.button("收起", key=f"close_{slot_key}"):
                st.session_state.pop(slot_key, None)
                st.session_state.pop(f"_{slot_key}_cleared_cache", None)
                st.rerun()

        res: Dict[str, Any] = row.get("result") or {}
        err_msg = (row.get("error_text") or "").strip()
        jt = str(row.get("job_type") or "")

        if stat == "failed":
            st.error(err_msg or "任务失败")
            return

        if stat != "completed":
            st.caption("任务在后台执行中，稍后点「刷新状态」或与本页任一控件交互以重跑脚本。")
            return

        if not st.session_state.get(f"_{slot_key}_cleared_cache"):
            st.cache_data.clear()
            st.session_state[f"_{slot_key}_cleared_cache"] = True

        if jt in (
            "guardian_sync",
            "nyt_sync",
            "wechat_rss_sync",
            "xinhua_tech_sync",
            "sina_tech_sync",
        ):
            labels = {
                "guardian_sync": "卫报",
                "nyt_sync": "NYT",
                "wechat_rss_sync": "微信 RSS",
                "xinhua_tech_sync": "新华网科技",
                "sina_tech_sync": "新浪科技",
            }
            label = labels.get(jt, jt)
            st.success(
                f"✅ {label}同步完成：入库 **{res.get('saved', 0)}**，"
                f"跳过已有 {res.get('skipped_url_dup', 0)}，"
                f"无关 {res.get('skipped_no_incident', 0)}，失败 {res.get('failed', 0)}"
            )
            nkw = res.get("new_keywords") or []
            if nkw:
                st.info("新增关键词：" + ", ".join(str(x) for x in nkw[:8]))
            dlog = res.get("debug_log") or []
            if dlog:
                with st.expander("详细日志"):
                    for line in dlog:
                        st.caption(str(line))
        elif jt == "agent_scout":
            st.success(
                f"✅ Agent 完成：提取 **{res.get('extracted', 0)}** 条，入库 **{res.get('saved', 0)}** 条"
            )
            nkw = res.get("new_keywords") or []
            if nkw:
                st.info("新增关键词：" + ", ".join(str(x) for x in nkw[:6]))
            dbg = res.get("debug_info") or []
            if dbg:
                with st.expander("调试日志"):
                    for line in dbg:
                        st.caption(str(line))
        elif jt == "deep_research":
            prev_only = bool(res.get("preview_only"))
            if prev_only:
                st.info("已选择「仅检索」：跳过 LLM。")
            hits_n = int(res.get("hits_count") or 0)
            st.success(f"深度调研已完成：检索 **{hits_n}** 条证据。")
            evs = res.get("evidence_previews") or []
            if evs:
                with st.expander("证据摘要", expanded=False):
                    for i, it in enumerate(evs, 1):
                        st.caption(
                            f"**{i}.** article_id={it.get('article_id')} rrf={it.get('rrf')} — "
                            f"{it.get('snippet', '')}…"
                        )
            if not prev_only:
                report_md = str(res.get("report_markdown") or "")
                if report_md.strip():
                    st.markdown(report_md)
                    rid = res.get("saved_report_id")
                    if rid:
                        st.caption(f"已保存至 MySQL，`research_reports.id` = **{rid}**")
                    fn = f"DeepResearch_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
                    st.download_button(
                        "下载 Markdown 报告",
                        data=report_md.encode("utf-8"),
                        file_name=fn,
                        mime="text/markdown",
                        key=f"dr_dl_{slot_key}",
                    )
                else:
                    st.warning("报告正文为空，请检查模型与 API。")


# ---------------------------------------------------------------------------
# 密码验证：从环境变量读取演示密码；未设置则关闭保护
# ---------------------------------------------------------------------------

def _demo_unlocked() -> bool:
    """
    功能：校验侧边栏密码输入，未设 DEMO_PASSWORD 时始终返回 True。
    输入：st.session_state 中的 demo_pwd 字段。
    输出：布尔；无 IO。
    """
    required = os.getenv("DEMO_PASSWORD", "").strip()
    if not required:
        return True
    entered = st.session_state.get("demo_pwd", "")
    return entered == required


# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------

def main() -> None:
    """
    功能：配置页面、水平导航单页渲染（避免 st.tabs 全量执行），演示操作区长任务走后台线程。
    输入：无参数；依赖 Streamlit session 与环境变量。
    输出：无；副作用：init_db（Agent SQLite + ui_background_jobs）；按需查 MySQL。
    """
    st.set_page_config(
        page_title="全球 AI 治理监测系统",
        layout="wide",
        page_icon="🛡️",
        initial_sidebar_state="collapsed",
    )
    init_db()

    # 全局 CSS：统一卡片与标签样式
    st.markdown("""
    <style>
    .metric-card {
        background: linear-gradient(135deg, #1a1f35 0%, #242b4a 100%);
        border: 1px solid #2a3563;
        border-left: 4px solid #4f8ef7;
        border-radius: 10px;
        padding: 18px 22px;
        margin-bottom: 8px;
    }
    .metric-card .label { color: #8892b0; font-size: 13px; margin-bottom: 4px; }
    .metric-card .value { color: #e8eaf6; font-size: 32px; font-weight: 700; line-height: 1; }
    .metric-card .delta { color: #4ade80; font-size: 12px; margin-top: 4px; }
    .tag-chip {
        background: #1e2130; color: #7eb8f7; padding: 3px 10px;
        border-radius: 12px; margin: 2px; border: 1px solid #2a3563;
        display: inline-block; font-size: 12px;
    }
    .section-header {
        border-bottom: 2px solid #2a3563;
        padding-bottom: 6px;
        margin-bottom: 16px;
        color: #c7d0e8;
    }
    </style>
    """, unsafe_allow_html=True)

    # --- 标题区 ---
    col_title, col_ts = st.columns([4, 1])
    with col_title:
        st.markdown("## 🛡️ 国际动态监测平台")
        st.caption("基于大语言模型的 AI 安全动态智能感知平台 · 实时追踪监管政策、技术风险与治理事件")
    with col_ts:
        st.caption(f"数据更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        if st.button("🔄 刷新数据", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    st.divider()

    # 水平单选：只执行当前功能区脚本，避免 st.tabs 预渲染所有子页。
    _NAV_MAIN = ("📊 监测看板", "📋 情报详情", "📌 专项监测", "📚 深度调研", "⚙️ 系统状态")
    page = st.radio(
        "主导航",
        _NAV_MAIN,
        horizontal=True,
        label_visibility="collapsed",
        key="nav_main_radio",
    )
    if page == _NAV_MAIN[0]:
        total_incidents, total_tags, taxonomy_kinds = _cached_stats()
        kw_df = _cached_keywords()
        kw_total = len(kw_df) if not kw_df.empty else 0

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("识别风险情报", total_incidents, help="已入库的 AI 治理/安全事件总数")
        with c2:
            st.metric("去重关键词总量", total_tags, help="从所有情报标签中提取的独立关键词数")
        with c3:
            st.metric("风险子域种数", taxonomy_kinds, help="动态演化的风险分类体系中不同子域数量")
        with c4:
            st.metric("自增长词库节点", kw_total, help="系统自动发现并持续追踪的领域术语数量")

        st.divider()

        # ================================================================
        # 监测看板
        # ================================================================
        left, right = st.columns([3, 2])

        with left:
            st.markdown('<div class="section-header">📍 最新监测情报</div>', unsafe_allow_html=True)
            df_latest = _cached_latest_incidents(20)
            # 无论有无数据始终用 dataframe 挂载点，避免 info ↔ dataframe DOM 结构切换
            if not df_latest.empty:
                if "主域" in df_latest.columns:
                    df_latest["主域"] = (
                        df_latest["主域"].astype(str)
                        .str.replace(r"\s*\(.+$", "", regex=True)
                        .str.strip()
                    )
                st.dataframe(
                    df_latest.drop(columns=["来源"], errors="ignore"),
                    use_container_width=True,
                    hide_index=True,
                    height=380,
                )
            else:
                st.dataframe(
                    pd.DataFrame({"提示": ["暂无监测数据，请从演示操作区触发同步。"]}),
                    use_container_width=True,
                    hide_index=True,
                    height=100,
                )

            # 三元主域分布——始终创建 columns(3)，保持 DOM 结构稳定
            st.markdown(
                '<div class="section-header" style="margin-top:24px">'
                "🌳 动态风险分类体系（三元主域 → 子域）</div>",
                unsafe_allow_html=True,
            )
            st.caption(
                "主域划分对齐 AI 安全与治理领域通行的「意图—来源」三类风险表述，便于与主流政策与学术话语对接；"
                "子域由抽取结果与语料统计动态演化。"
            )
            with st.expander("分类口径与依据（说明）", expanded=False):
                st.markdown(
                    """
**三元主域**对应学界与产业常用的风险分层：**恶意滥用**（Malicious Use）、**意外失效**
（Accidental Failure / 可靠性）、**系统性与伦理风险**（Systemic & Ethical），与 NIST AI RMF、
OECD AI 原则、欧盟《人工智能法案》等国内外治理框架中的风险维度在**语义上可对齐**（非对某一条款的逐字映射）。

**子域**为在各主域下由模型标注、检索增强与词频统计共同沉淀的议题标签，会随监测语料扩充而**自动演化**。
                    """.strip()
                )
            tax_df = _cached_taxonomy()
            # 始终创建 columns(3)，避免条件创建引发 DOM 错位
            dom_cols = st.columns(3)
            for i, domain_label in enumerate(RISK_DOMAIN_CHOICES):
                short = domain_label.split("(")[0].strip()
                sub_df = tax_df[tax_df["domain"] == domain_label].head(10) if not tax_df.empty else pd.DataFrame()
                with dom_cols[i]:
                    st.markdown(f"**{short}**")
                    if sub_df.empty:
                        st.caption("积累中…" if tax_df.empty else "—")
                    else:
                        for _, row in sub_df.iterrows():
                            st.caption(f"· {row['subdomain']}（×{int(row['tax_count'])}）")

        with right:
            st.markdown('<div class="section-header">📊 风险主域分布</div>', unsafe_allow_html=True)
            tax_df_r = _cached_taxonomy()

            # 监测看板右侧使用 Altair 甜甜圈图（无 Plotly iframe），保持 `st.altair_chart` 与稳定 key。
            if not tax_df_r.empty:
                domain_agg = tax_df_r.groupby("domain")["tax_count"].sum().reset_index()
                domain_agg["主域"] = (
                    domain_agg["domain"].str.replace(r"\s*\(.+$", "", regex=True).str.strip()
                )
                domain_agg = domain_agg.rename(columns={"tax_count": "情报数"})
                chart_domain = _altair_dash_donut(
                    domain_agg["主域"].tolist(),
                    pd.to_numeric(domain_agg["情报数"], errors="coerce").fillna(0).astype(int).tolist(),
                    height_px=360,
                    width_px=420,
                    mode="domain",
                )
            else:
                chart_domain = _altair_dash_empty_state("暂无分类统计数据。", height_px=360)
            st.altair_chart(
                chart_domain,
                use_container_width=True,
                theme="streamlit",
                key="dash_risk_domain_donut",
            )

            st.markdown(
                '<div class="section-header" style="margin-top:20px">'
                "🔥 高频风险子域 (Top 8 + 其他)</div>",
                unsafe_allow_html=True,
            )

            if not tax_df_r.empty:
                sub_sorted = tax_df_r.sort_values("tax_count", ascending=False).reset_index(drop=True)
                short_dom = sub_sorted["domain"].str.replace(r"\s*\(.+$", "", regex=True).str.strip()
                if len(sub_sorted) > 8:
                    head = sub_sorted.head(8)
                    short_h = short_dom.head(8)
                    labels = (head["subdomain"] + " · " + short_h).tolist()
                    vals = pd.to_numeric(head["tax_count"], errors="coerce").fillna(0).astype(int).tolist()
                    other_count = int(pd.to_numeric(sub_sorted["tax_count"].iloc[8:], errors="coerce").fillna(0).sum())
                    if other_count > 0:
                        labels.append("其他")
                        vals.append(other_count)
                else:
                    labels = (sub_sorted["subdomain"] + " · " + short_dom).tolist()
                    vals = pd.to_numeric(sub_sorted["tax_count"], errors="coerce").fillna(0).astype(int).tolist()
                chart_sub = _altair_dash_donut(labels, vals, height_px=400, width_px=560, mode="subdomain")
            else:
                chart_sub = _altair_dash_empty_state("暂无子域数据。", height_px=400)
            st.altair_chart(
                chart_sub,
                use_container_width=True,
                theme="streamlit",
                key="dash_subdomain_donut",
            )

            # 关键词池：始终渲染同一元素类型（markdown），避免 markdown ↔ caption 切换
            st.markdown('<div class="section-header" style="margin-top:20px">🧬 自增长关键词池</div>', unsafe_allow_html=True)
            if not kw_df.empty:
                top_kw = kw_df.head(40)
                tag_html = "".join([
                    f'<span class="tag-chip">{row["keyword"]}'
                    f'<span style="opacity:0.5;font-size:10px"> ×{row["count"]}</span></span>'
                    for _, row in top_kw.iterrows()
                ])
            else:
                tag_html = '<span style="color:#64748b;font-size:13px">🌱 词库为空，触发一次同步后自动填充。</span>'
            st.markdown(tag_html, unsafe_allow_html=True)

    elif page == _NAV_MAIN[1]:
        st.markdown('<div class="section-header">📋 情报库（筛选 + MySQL 分页）</div>', unsafe_allow_html=True)
        st.caption("列表按时间倒序；仅加载当前页，避免多人访问时一次性拉全表。")

        domains = ["全部"] + list(RISK_DOMAIN_CHOICES)
        fc1, fc2, fc3, fc4 = st.columns([1, 1, 2, 1])
        with fc1:
            sel_domain = st.selectbox("按主域筛选（三元模型）", domains, key="filter_domain")
        with fc2:
            lev_opts = ["全部"] + list(_cached_distinct_content_types())
            sel_level = st.selectbox("按资讯类别筛选", lev_opts, key="filter_level")
        with fc3:
            kw_search = st.text_input("关键词搜索（标题/摘要）", key="kw_search")
        with fc4:
            page_lim = int(
                st.select_slider("每页条数", options=[50, 100, 150, 200], value=50, key="inc_page_limit")
            )

        fdom = "" if sel_domain == "全部" else sel_domain
        flev = "" if sel_level == "全部" else sel_level
        fkw_s = (kw_search or "").strip()

        total_n = _cached_incidents_count(fdom, flev, fkw_s)
        pages = max(1, (total_n + page_lim - 1) // page_lim) if total_n > 0 else 1
        _pkey = "inc_page_no_val"
        if _pkey not in st.session_state:
            st.session_state[_pkey] = 1
        if int(st.session_state[_pkey]) > pages:
            st.session_state[_pkey] = pages
        pg_cur = int(st.number_input("页码", min_value=1, max_value=pages, step=1, key=_pkey))
        offset = (pg_cur - 1) * page_lim
        df_page = _cached_incidents_page(fdom, flev, fkw_s, offset, page_lim)

        # 始终渲染 dataframe + download_button 挂载点，避免 info ↔ dataframe DOM 切换
        if total_n == 0:
            _show_df = pd.DataFrame({"提示": ["暂无数据或当前筛选无结果；可先放宽筛选或从演示操作区触发同步。"]})
            _show_cap = ""
        else:
            _show_df = df_page.drop(columns=["id"], errors="ignore")
            _show_cap = f"符合条件 **{total_n}** 条 · 本页展示 **{len(df_page)}** 条 · 页 **{pg_cur}** / **{pages}**"
        if _show_cap:
            st.caption(_show_cap)
        st.dataframe(_show_df, use_container_width=True, hide_index=True, height=420)
        if total_n > 0 and not df_page.empty:
            csv_bytes = df_page.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "📥 导出本页（CSV）",
                data=csv_bytes,
                file_name=f"AI_Governance_page{pg_cur}_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                key="dl_page_csv",
            )

        st.divider()

        st.markdown('<div class="section-header">📄 自动化监测日报</div>', unsafe_allow_html=True)
        if st.button("📥 一键生成 AI 治理监测日报", key="gen_report"):
            df_report = _cached_latest_incidents(10)
            kw_meta = _cached_keywords()
            kw_daily_total = len(kw_meta) if not kw_meta.empty else 0
            if not df_report.empty:
                report_md = f"## AI 治理动态监测内参（{datetime.now().strftime('%Y-%m-%d')}）\n\n"
                report_md += "### 一、最新情报摘要\n\n"
                for _, row in df_report.iterrows():
                    ctype = str(row.get("资讯类别", "") or "").strip()
                    dom = str(row.get("主域", "") or "").strip()
                    sub = str(row.get("子域", "") or "").strip()
                    entity = str(row.get("涉及主体", "") or "").strip()
                    tri = f"{dom} / {sub}".strip(" /")
                    report_md += f"- **[{ctype or '—'}]** {row['title']}（涉及主体：{entity or '—'}）"
                    if tri:
                        report_md += f" — 分类：{tri}"
                    report_md += "\n"

                report_md += "\n### 二、新兴术语感知\n\n"
                kw_top = kw_meta.head(10)
                if not kw_top.empty:
                    report_md += "- 高频新词：" + "、".join(kw_top["keyword"].tolist()) + "\n"

                report_md += "\n### 三、系统统计\n\n"
                stats = _cached_stats()
                report_md += (
                    f"- 已监测情报：{stats[0]} 条\n"
                    f"- 风险子域种数：{stats[2]} 种\n"
                    f"- 关键词库节点：{kw_daily_total} 个\n"
                )

                st.code(report_md, language="markdown")
                st.download_button(
                    "下载 Markdown 日报",
                    data=report_md.encode("utf-8"),
                    file_name=f"AI_Governance_Daily_{datetime.now().strftime('%Y%m%d')}.md",
                    mime="text/markdown",
                    key="dl_report",
                )
            else:
                st.warning("数据库暂无数据，请先触发同步。")

    elif page == _NAV_MAIN[2]:
        # 单列纵向三张表：无子 tabs / 无 Plotly，与顶层 radio+if 主导航保持一致。
        _TRACK_PREVIEW = 80
        _POL_COLS = ["标题", "资讯类别", "主域", "子域", "摘要", "来源平台", "时间"]
        _MTG_COLS = ["标题", "资讯类别", "主域", "议题(main_topic)", "子域", "摘要", "来源平台", "时间"]

        st.markdown('<div class="section-header">📌 专项监测</div>', unsafe_allow_html=True)
        st.caption(
            "自上而下三块：**政策法规/科技政策**（policy+report）、**重大国际会议**（meeting）、"
            "**文献占位**。各块仅展示关键词筛选下最新若干条；主导航仍为水平 radio。"
        )

        st.markdown("### 📋 政策法规 / 科技政策")
        kw_p_in = st.text_input(
            "关键词（可选，标题/摘要，与 policy+report 类型 AND）",
            key="track_policy_kw",
        )
        kw_ps = (kw_p_in or "").strip()
        total_p = _cached_policy_track_count(kw_ps)
        recent7_p = _cached_policy_track_recent7(kw_ps)
        st.caption(
            f"符合条件 **{total_p:,}** 条 · 近 7 日 **{recent7_p:,}** 条 · "
            f"下表最多 **{_TRACK_PREVIEW}** 条（倒序）"
        )
        df_pol = _cached_policy_track_page_rows(kw_ps, 0, _TRACK_PREVIEW)
        if total_p <= 0 or df_pol.empty:
            show_pol = pd.DataFrame(columns=_POL_COLS)
        else:
            show_pol = df_pol.drop(columns=["id", "main_topic"], errors="ignore")
        st.dataframe(show_pol, use_container_width=True, hide_index=True, height=340)

        st.divider()

        st.markdown("### 🌐 重大国际会议")
        kw_m_in = st.text_input(
            "关键词（可选，标题/摘要/main_topic AND meeting 类型）",
            key="track_meeting_kw",
        )
        kw_ms = (kw_m_in or "").strip()
        total_m = _cached_meeting_track_count(kw_ms)
        recent30_m = _cached_meeting_track_recent30(kw_ms)
        st.caption(
            f"符合条件 **{total_m:,}** 条 · 近 30 日 **{recent30_m:,}** 条 · "
            f"下表最多 **{_TRACK_PREVIEW}** 条（倒序）"
        )
        df_mtg = _cached_meeting_track_page_rows(kw_ms, 0, _TRACK_PREVIEW)
        if total_m <= 0 or df_mtg.empty:
            show_mtg = pd.DataFrame(columns=_MTG_COLS)
        else:
            show_mtg = df_mtg.drop(columns=["id"], errors="ignore").copy()
            if "main_topic" in show_mtg.columns:
                show_mtg.rename(columns={"main_topic": "议题(main_topic)"}, inplace=True)
        st.dataframe(show_mtg, use_container_width=True, hide_index=True, height=340)

        st.divider()

        st.markdown("### 📖 国内外文献（预留）")
        lit = literature_monitor_status()
        planned_join = ", ".join(lit.get("planned_tables") or []) or "—"
        df_lit = pd.DataFrame(
            [
                {
                    "已实现": bool(lit.get("implemented")),
                    "规划表": planned_join,
                    "说明": str(lit.get("message") or "文献监测尚未实现。"),
                }
            ]
        )
        st.dataframe(df_lit, use_container_width=True, hide_index=True, height=90)

    elif page == _NAV_MAIN[3]:
        # 使用原生 Markdown 标题，不使用 raw `<div>`，减轻与后继表单/ dataframe 并排时的前端 reconciler insertBefore 错误。
        st.markdown("### 📚 问答式深度调研")
        st.caption(
            "基于 Chroma 向量 + MySQL 全文（若已迁移）混合检索证据，由大模型生成带引用的 Markdown 报告；"
            "可选择写入 `research_reports` 便于留痕。"
        )
        rq = st.text_area(
            "研究问题",
            height=88,
            placeholder="例如：欧盟 AI 法案执法近期有哪些公开讨论？",
            key="deep_research_question",
        )
        dr1, dr2, dr3 = st.columns(3)
        with dr1:
            dom_opts = ["（不筛选）"] + list(RISK_DOMAIN_CHOICES)
            dr_domain_sel = st.selectbox("主域筛选（可选）", dom_opts, key="dr_domain")
            dr_risk_domain = None if dr_domain_sel.startswith("（") else dr_domain_sel
        with dr2:
            dr_source = st.text_input("信源 source 精确匹配（可选）", "", key="dr_source")
        with dr3:
            dr_top_k = st.slider("纳入证据条数", 6, 32, 16, key="dr_top_k")

        dr_save = st.checkbox("生成后写入 MySQL（research_reports + 引用行）", value=True, key="dr_save")
        dr_preview = st.checkbox("仅检索证据、暂不调用 LLM（调试用）", value=False, key="dr_preview")

        st.caption(
            "检索与报告生成在**后台线程**执行，不阻塞其他访客；提交后在下方卡片点「刷新状态」查看进度。"
        )

        if st.button("🔎 后台提交：检索并生成报告", type="primary", use_container_width=True, key="dr_run"):
            if not (rq or "").strip():
                st.warning("请先填写研究问题。")
            elif not dr_preview and not (API_KEY or "").strip():
                st.error("未配置 DASHSCOPE_API_KEY，无法调用大模型生成报告（可勾选「仅检索」跳过 LLM）。")
            else:
                payload = {
                    "question": (rq or "").strip(),
                    "preview_only": bool(dr_preview),
                    "save_report": bool(dr_save),
                    "top_k": int(dr_top_k),
                    "risk_domain": dr_risk_domain,
                    "source": (dr_source or "").strip(),
                    "llm_model": LLM_MODEL,
                    "api_key": (API_KEY or "").strip(),
                    "base_url": (BASE_URL or "").strip(),
                }
                jid = start_job_thread("deep_research", payload)
                st.session_state["bg_deep_job"] = jid
                st.session_state.pop("_bg_deep_job_cleared_cache", None)
                st.rerun()

        _background_job_panel("bg_deep_job", "深度调研")

        st.divider()
        st.markdown("**近期已保存报告**")
        hist = _cached_research_report_list(30)
        records = hist.to_dict("records") if not hist.empty else []
        n_hist = len(records)

        # 始终保持「文案 + selectbox + 载入按钮」三件组件，有空数据时控件禁用，
        # 避免 hist 空/非空导致子节点数量翻转（与用户侧专项监测→深度调研切换时的 insertBefore 报错相关）。
        st.caption(
            "暂无保存的报告；成功后约 30s 内缓存可见。"
            if n_hist <= 0
            else f"共 **{n_hist}** 条缓存记录可供选择。"
        )
        pick_opts = list(range(max(1, n_hist)))

        def _dr_hist_fmt(i: int) -> str:
            if n_hist <= 0:
                return "（暂无保存的报告）"
            rr = records[i]
            return f"#{int(rr['id'])} — " f"{str(rr.get('question') or '')[:60]}"

        pick_ix = int(
            st.selectbox(
                "选择一条查看",
                pick_opts,
                format_func=_dr_hist_fmt,
                disabled=bool(n_hist <= 0),
                key="dr_hist_pick",
            )
        )
        if st.button("载入所选报告", key="dr_hist_load", disabled=bool(n_hist <= 0)):
            hid = int(records[pick_ix]["id"])
            try:
                row = get_research_report_by_id(hid)
                if row and row.get("report_markdown"):
                    st.markdown(str(row["report_markdown"]))
                    if row.get("sources"):
                        with st.expander("引用行（research_report_sources）"):
                            st.dataframe(
                                pd.DataFrame(row["sources"]),
                                use_container_width=True,
                                hide_index=True,
                            )
                else:
                    st.warning("未找到该报告。")
            except Exception as e:
                st.error(f"加载失败：{type(e).__name__}: {e}")

    elif page == _NAV_MAIN[4]:
        sc1, sc2 = st.columns(2)

        kw_sys = _cached_keywords()
        kw_total_sys = len(kw_sys) if not kw_sys.empty else 0

        with sc1:
            st.markdown('<div class="section-header">🔑 API 与服务状态</div>', unsafe_allow_html=True)
            # LLM Key 状态
            if API_KEY and len(API_KEY) > 10:
                st.success("LLM API Key 已加载", icon="✅")
            else:
                st.error("LLM API Key 未配置（DASHSCOPE_API_KEY）", icon="❌")

            # Guardian Key 状态
            if GUARDIAN_API_KEY and len(GUARDIAN_API_KEY) > 5:
                st.success("Guardian API Key 已加载", icon="✅")
            else:
                st.warning("Guardian API Key 未配置（可选）", icon="⚠️")

            # NYT Key 状态
            if NYT_API_KEY and len(NYT_API_KEY) > 5:
                st.success("NYT API Key 已加载", icon="✅")
            else:
                st.warning("NYT API Key 未配置（可选）", icon="⚠️")

            st.markdown("**数据库统计（看板数据源：MySQL）**")
            s1, s2, s3 = _cached_stats()
            st.caption(f"• article_extractions：{s1} 条")
            st.caption(f"• 去重标签（全库）：{s2} 个")
            st.caption(f"• 主域×子域组合：{s3} 种")
            st.caption(f"• 高频词池（展示 Top）：{kw_total_sys} 个")
            st.caption(f"• MySQL：`{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}`")
            st.caption(f"• Agent 本地库（SQLite）：`{DB_PATH}`")

        with sc2:
            st.markdown('<div class="section-header">📡 信源配置</div>', unsafe_allow_html=True)
            st.caption("**卫报 Content API（已集成）**")
            st.caption("• 检索：AI safety / AI governance / AI regulation 等")
            st.caption("• 拉取字段：标题、导语、正文、版块、发布时间")
            st.caption("• 并发抽取：5 篇文章同时调用 LLM，串行入库")
            st.caption("**NYT Article Search API（已集成）**")
            st.caption("• 检索：artificial intelligence safety governance regulation 等")
            st.caption("• 拉取字段：标题、摘要（abstract）、版块、发布时间")
            st.caption("• 并发抽取：5 篇文章同时调用 LLM，串行入库")
            st.caption("**新华网科技频道（已集成）**")
            st.caption(f"• 列表页抓取 [{XINHUA_TECH_URL}]({XINHUA_TECH_URL})，解析正文后并发 LLM 抽取入库")
            st.caption("**新浪科技频道（已集成）**")
            st.caption(f"• 列表页抓取 [{SINA_TECH_URL}]({SINA_TECH_URL})，解析正文后并发 LLM 抽取入库")
            st.caption("**微信公众号 RSS（wechat2rss，已集成）**")
            st.caption("• 配置池内公众号 RSS；拉取标题与正文摘要；后台同步可走 SQLite 任务队列")
            st.caption("**Crawl4AI（已集成，按 URL 侦察）**")
            st.caption("• 支持任意 URL：CSET、斯坦福 AI Index、OpenAI 博客等")
            st.caption("• 通过浏览器引擎渲染 JS 页面后提取结构化情报")

        st.divider()

        # --- 受密码保护的演示操作区 ---
        st.markdown('<div class="section-header">🔐 演示操作区（需验证）</div>', unsafe_allow_html=True)

        required_pwd = os.getenv("DEMO_PASSWORD", "").strip()
        if required_pwd:
            st.text_input(
                "演示密码",
                type="password",
                key="demo_pwd",
                placeholder="输入演示密码后解锁操作",
            )

        if _demo_unlocked():
            if not required_pwd:
                st.caption("（未设置 DEMO_PASSWORD 环境变量，操作区默认开放）")

            st.caption(
                "**卫报 / NYT / 新华网 / 新浪 / 微信 RSS / Agent 侦察**在**后台线程**执行，队列记在 SQLite "
                "`ui_background_jobs`。点按钮提交后，在下方卡片中「刷新任务状态」跟进进度。"
            )

            op1, op2 = st.columns(2)

            # ---- 卫报一键同步 ----
            with op1:
                st.markdown("**📡 卫报 AI 治理新闻同步**")
                sync_pages = st.slider("拉取页数", 1, 5, 2, key="sync_pages")
                sync_size = st.slider("每页条数", 3, 20, 8, key="sync_size")
                if st.button(
                    "🚀 后台提交卫报同步", type="primary", use_container_width=True, key="btn_sync"
                ):
                    jid = start_job_thread(
                        "guardian_sync",
                        {
                            "max_pages": int(sync_pages),
                            "page_size": int(sync_size),
                            "rag_enabled": False,
                        },
                    )
                    st.session_state["bg_guardian_job"] = jid
                    st.session_state.pop("_bg_guardian_job_cleared_cache", None)
                    st.rerun()

            # ---- NYT 一键同步 ----
            with op2:
                st.markdown("**📰 NYT AI 治理新闻同步**")
                nyt_pages = st.slider("拉取页数", 1, 5, 2, key="nyt_sync_pages")
                if not NYT_API_KEY:
                    st.caption("⚠️ NYT_API_KEY 未配置，同步将失败")
                if st.button(
                    "🚀 后台提交 NYT 同步", type="primary", use_container_width=True, key="btn_nyt_sync"
                ):
                    jid = start_job_thread(
                        "nyt_sync",
                        {
                            "max_pages": int(nyt_pages),
                            "rag_enabled": False,
                        },
                    )
                    st.session_state["bg_nyt_job"] = jid
                    st.session_state.pop("_bg_nyt_job_cleared_cache", None)
                    st.rerun()

            st.markdown("**📱 微信公众号 RSS（wechat2rss）**")
            wx_keys = sorted(WECHAT_RSS_POOL.keys())
            wx_feeds = st.multiselect(
                "公众号（不选则同步池内全部）",
                wx_keys,
                default=[],
                key="wx_rss_feeds",
            )
            wx_max = st.slider("每公众号最多篇数", 1, 20, 5, key="wx_rss_max")
            if st.button(
                "🚀 后台提交微信 RSS 同步",
                type="secondary",
                use_container_width=True,
                key="btn_wx_rss_sync",
            ):
                jid = start_job_thread(
                    "wechat_rss_sync",
                    {
                        "feed_names": wx_feeds if wx_feeds else None,
                        "max_articles_per_feed": int(wx_max),
                        "rag_enabled": False,
                    },
                )
                st.session_state["bg_wechat_job"] = jid
                st.session_state.pop("_bg_wechat_job_cleared_cache", None)
                st.rerun()

            xh_sn1, xh_sn2 = st.columns(2)

            with xh_sn1:
                st.markdown("**📰 新华网科技同步**")
                st.caption(f"默认：[news.cn 科技]({XINHUA_TECH_URL})")
                xh_max = st.slider("本轮最多抓取文章数", 3, 25, 10, key="xinhua_max_articles")
                with st.expander("自定义列表页 URL（可选）", expanded=False):
                    st.text_area(
                        "每行一个 URL，留空则用默认科技频道",
                        value="",
                        height=72,
                        key="xinhua_page_urls_txt",
                        placeholder=XINHUA_TECH_URL,
                    )
                if st.button(
                    "🚀 后台提交新华网同步",
                    type="secondary",
                    use_container_width=True,
                    key="btn_xinhua_sync",
                ):
                    xh_urls = _textarea_urls_to_list(
                        str(st.session_state.get("xinhua_page_urls_txt", "") or "")
                    )
                    jid = start_job_thread(
                        "xinhua_tech_sync",
                        {
                            "max_articles": int(xh_max),
                            "page_urls": xh_urls,
                            "rag_enabled": False,
                        },
                    )
                    st.session_state["bg_xinhua_job"] = jid
                    st.session_state.pop("_bg_xinhua_job_cleared_cache", None)
                    st.rerun()

            with xh_sn2:
                st.markdown("**📰 新浪科技同步**")
                st.caption(f"默认：[tech.sina.com.cn]({SINA_TECH_URL})")
                sn_max = st.slider("本轮最多抓取文章数", 3, 25, 10, key="sina_max_articles")
                with st.expander("自定义列表页 URL（可选）", expanded=False):
                    st.text_area(
                        "每行一个 URL，留空则用默认新浪科技首页",
                        value="",
                        height=72,
                        key="sina_page_urls_txt",
                        placeholder=SINA_TECH_URL,
                    )
                if st.button(
                    "🚀 后台提交新浪科技同步",
                    type="secondary",
                    use_container_width=True,
                    key="btn_sina_sync",
                ):
                    sn_urls = _textarea_urls_to_list(
                        str(st.session_state.get("sina_page_urls_txt", "") or "")
                    )
                    jid = start_job_thread(
                        "sina_tech_sync",
                        {
                            "max_articles": int(sn_max),
                            "page_urls": sn_urls,
                            "rag_enabled": False,
                        },
                    )
                    st.session_state["bg_sina_job"] = jid
                    st.session_state.pop("_bg_sina_job_cleared_cache", None)
                    st.rerun()

            st.divider()

            # ---- Agent URL 侦察 ----
            st.markdown("**🔍 Agent URL 深度侦察**")
            scout_presets = {
                "CSET 新闻": "https://cset.georgetown.edu/news/",
                "斯坦福 AI Index": "https://aiindex.stanford.edu/",
                "OpenAI 博客": "https://openai.com/news/",
                "EU AI Act": "https://artificialintelligenceact.eu/news/",
            }
            preset_sel = st.selectbox("预设信源", ["自定义"] + list(scout_presets.keys()), key="scout_preset")
            default_url = scout_presets.get(preset_sel, st.session_state.get("scout_url_val", ""))
            scout_url = st.text_input("目标 URL", value=default_url, key="scout_url_val")

            with st.expander("LLM 接口配置", expanded=False):
                tab_api_key = st.text_input("API Key", value=API_KEY, type="password", key="scout_api_key")
                tab_base_url = st.text_input("Base URL", value=BASE_URL, key="scout_base_url")

            if st.button(
                "🕵️ 后台提交 Agent 侦察", type="primary", use_container_width=True, key="btn_scout"
            ):
                su = (scout_url or "").strip()
                if not su:
                    st.warning("请填写目标 URL。")
                else:
                    jid = start_job_thread(
                        "agent_scout",
                        {
                            "url": su,
                            "api_key": (tab_api_key or "").strip(),
                            "base_url": (tab_base_url or "").strip(),
                        },
                    )
                    st.session_state["bg_scout_job"] = jid
                    st.session_state.pop("_bg_scout_job_cleared_cache", None)
                    st.rerun()

            st.divider()
            _background_job_panel("bg_guardian_job", "卫报同步")
            _background_job_panel("bg_nyt_job", "NYT 同步")
            _background_job_panel("bg_wechat_job", "微信 RSS 同步")
            _background_job_panel("bg_xinhua_job", "新华网科技同步")
            _background_job_panel("bg_sina_job", "新浪科技同步")
            _background_job_panel("bg_scout_job", "Agent URL 侦察")
        else:
            st.info("请输入正确的演示密码以解锁操作区。")

    # ================================================================
    # 侧边栏：仅展示项目简介（不放操作按钮）
    # ================================================================
    with st.sidebar:
        st.markdown("### 🛡️ 系统简介")
        st.markdown("""
**全球 AI 治理监测与自增长 Agent 系统**

自动感知全球 AI 安全动态，基于三元意图风险模型结构化分类，持续演化知识体系。

**核心能力**
- 多信源同步（卫报 / NYT / 新华网 / 新浪科技 / 微信 RSS，后台线程 + SQLite 任务状态）
- 任意 URL 深度 Agent 侦察
- 问答式深度调研（混合检索 + 报告留痕）
- LLM 并发抽取（5 路并发）
- RAG 增强风险子域精炼
- 自增长关键词与子域体系

**技术栈**
Python · Streamlit · MySQL  
Crawl4AI · ChromaDB · httpx
        """)
        st.divider()
        st.caption(f"© {datetime.now().year} AI Safety Research")


if __name__ == "__main__":
    main()
