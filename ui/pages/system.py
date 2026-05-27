"""
功能：「⚙️ 系统状态」——API 环境、数据库摘要、受密码保护的演示同步/侦察操作与多任务卡片。

输入：环境变量与 `core.config`；`demo_pwd` 会话字段。
输出：无；多处 `start_job_thread` + `session_state bg_*`。
上下游：`services.dashboard_service`、`services.demo_urls`、`ui.demo_password`、`ui.components.job_panel`、`core.ui_jobs`、`crawler.*`。
"""

from __future__ import annotations

import os

import streamlit as st

from core.config import (
    API_KEY,
    BASE_URL,
    DB_PATH,
    GUARDIAN_API_KEY,
    MYSQL_DATABASE,
    MYSQL_HOST,
    MYSQL_PORT,
    NYT_API_KEY,
    SCOPUS_API_KEY,
)
from core.ui_jobs import start_job_thread
from crawler.sources import SINA_TECH_URL, XINHUA_TECH_URL
from crawler.sources.wechat2rss import WECHAT_RSS_POOL
from services import dashboard_service as dash
from services.demo_urls import textarea_urls_to_list
from ui.components.job_panel import render_background_job_panel
from ui.demo_password import demo_unlocked


def render_system_page() -> None:
    """功能：系统状态 + 演示操作区单页入口。"""
    sc1, sc2 = st.columns(2)

    kw_sys = dash.cached_keywords()
    kw_total_sys = len(kw_sys) if not kw_sys.empty else 0

    with sc1:
        st.markdown("#### 🔑 API 与服务状态")
        if API_KEY and len(API_KEY) > 10:
            st.success("LLM API Key 已加载", icon="✅")
        else:
            st.error("LLM API Key 未配置（DASHSCOPE_API_KEY）", icon="❌")

        if GUARDIAN_API_KEY and len(GUARDIAN_API_KEY) > 5:
            st.success("Guardian API Key 已加载", icon="✅")
        else:
            st.warning("Guardian API Key 未配置（可选）", icon="⚠️")

        if NYT_API_KEY and len(NYT_API_KEY) > 5:
            st.success("NYT API Key 已加载", icon="✅")
        else:
            st.warning("NYT API Key 未配置（可选）", icon="⚠️")

        st.markdown("**数据库统计（看板数据源：MySQL）**")
        s1, s2, s3 = dash.cached_stats()
        st.caption(f"• article_extractions：{s1} 条")
        st.caption(f"• 去重标签（全库）：{s2} 个")
        st.caption(f"• 主域×子域组合：{s3} 种")
        st.caption(f"• 高频词池（展示 Top）：{kw_total_sys} 个")
        st.caption(f"• MySQL：`{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}`")
        st.caption(f"• Agent 本地库（SQLite）：`{DB_PATH}`")

    with sc2:
        st.markdown("#### 📡 信源配置")
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
        st.caption("**政策/法规源（policy，已集成）**")
        st.caption("• US/UK/EU/IN/BR 多国政策 RSS/HTML → LLM 抽取 → articles（source=policy:XX）")
        st.caption("**文献库（arxiv/scopus/springer，已集成）**")
        st.caption("• arXiv / Scopus / Springer → literature_items 表，供专项监测展示，不跑 LLM")
        st.caption("**Crawl4AI（已集成，按 URL 侦察）**")
        st.caption("• 支持任意 URL：CSET、斯坦福 AI Index、OpenAI 博客等")
        st.caption("• 通过浏览器引擎渲染 JS 页面后提取结构化情报")

    st.divider()

    st.markdown("#### 🔐 演示操作区（需验证）")

    required_pwd = os.getenv("DEMO_PASSWORD", "").strip()
    if required_pwd:
        st.text_input(
            "演示密码",
            type="password",
            key="demo_pwd",
            placeholder="输入演示密码后解锁操作",
        )

    if demo_unlocked():
        if not required_pwd:
            st.caption("（未设置 DEMO_PASSWORD 环境变量，操作区默认开放）")

        st.caption(
            "**卫报 / NYT / 政策 / 文献 / 新华网 / 新浪 / 微信 RSS / Agent 侦察**在**后台线程**执行，"
            "队列记在 SQLite `ui_background_jobs`。点按钮提交后，在下方卡片中「刷新任务状态」跟进进度。"
        )

        op1, op2 = st.columns(2)

        with op1:
            st.markdown("**📡 卫报 AI 治理新闻同步**")
            sync_pages = st.slider("拉取页数", 1, 5, 2, key="sync_pages")
            sync_size = st.slider("每页条数", 3, 20, 8, key="sync_size")
            if st.button("🚀 后台提交卫报同步", type="primary", use_container_width=True, key="btn_sync"):
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

        with op2:
            st.markdown("**📰 NYT AI 治理新闻同步**")
            nyt_pages = st.slider("拉取页数", 1, 5, 2, key="nyt_sync_pages")
            if not NYT_API_KEY:
                st.caption("⚠️ NYT_API_KEY 未配置，同步将失败")
            if st.button("🚀 后台提交 NYT 同步", type="primary", use_container_width=True, key="btn_nyt_sync"):
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
                xh_urls = textarea_urls_to_list(str(st.session_state.get("xinhua_page_urls_txt", "") or ""))
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
                sn_urls = textarea_urls_to_list(str(st.session_state.get("sina_page_urls_txt", "") or ""))
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

        pol_sn1, pol_sn2 = st.columns(2)

        with pol_sn1:
            st.markdown("**🏛 政策/法规同步（→ articles）**")
            pol_countries = st.multiselect(
                "国家/地区",
                ["US", "UK", "EU", "IN", "BR"],
                default=["US", "EU"],
                key="policy_countries",
            )
            pol_max = st.slider("每国最多条数", 3, 30, 10, key="policy_max")
            if st.button(
                "🚀 后台提交政策同步",
                type="secondary",
                use_container_width=True,
                key="btn_policy_sync",
            ):
                jid = start_job_thread(
                    "policy_sync",
                    {
                        "countries": pol_countries or None,
                        "max_articles_per_country": int(pol_max),
                        "rag_enabled": False,
                    },
                )
                st.session_state["bg_policy_job"] = jid
                st.session_state.pop("_bg_policy_job_cleared_cache", None)
                st.rerun()

        with pol_sn2:
            st.markdown("**📚 文献库同步（→ literature_items）**")
            lit_sources = st.multiselect(
                "文献源",
                ["arxiv", "springer", "scopus"],
                default=["arxiv"],
                key="lit_sources",
            )
            lit_max = st.slider("每源/分类上限", 1, 10, 3, key="lit_max")
            if SCOPUS_API_KEY:
                st.caption("Scopus API Key 已配置")
            else:
                st.caption("⚠️ SCOPUS_API_KEY 未配置时 Scopus 同步将失败")
            if st.button(
                "🚀 后台提交文献同步",
                type="secondary",
                use_container_width=True,
                key="btn_literature_sync",
            ):
                jid = start_job_thread(
                    "literature_sync",
                    {
                        "sources": lit_sources or ["arxiv"],
                        "max_arxiv_per_category": int(lit_max),
                        "max_springer_per_domain": int(lit_max),
                        "scopus_max_results": int(lit_max) * 5,
                        "rag_enabled": False,
                    },
                )
                st.session_state["bg_literature_job"] = jid
                st.session_state.pop("_bg_literature_job_cleared_cache", None)
                st.rerun()

        st.divider()

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

        if st.button("🕵️ 后台提交 Agent 侦察", type="primary", use_container_width=True, key="btn_scout"):
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
        render_background_job_panel("bg_guardian_job", "卫报同步")
        render_background_job_panel("bg_nyt_job", "NYT 同步")
        render_background_job_panel("bg_wechat_job", "微信 RSS 同步")
        render_background_job_panel("bg_xinhua_job", "新华网科技同步")
        render_background_job_panel("bg_sina_job", "新浪科技同步")
        render_background_job_panel("bg_policy_job", "政策/法规同步")
        render_background_job_panel("bg_literature_job", "文献库同步")
        render_background_job_panel("bg_scout_job", "Agent URL 侦察")
    else:
        st.info("请输入正确的演示密码以解锁操作区。")
