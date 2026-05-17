"""
功能：监测看板 Plotly 环形图（与原 go.Pie(hole=0.54) 视觉一致）。

输入：labels / values；可选空状态占位文案。
输出：`plotly.graph_objects.Figure`。
上下游：`ui.pages.dashboard` → `st.plotly_chart`。
"""

from __future__ import annotations

from typing import Any, Optional

import plotly.graph_objects as go

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
_DONUT_HOLE_RATIO = 0.54


def donut_color_list(n: int) -> list[str]:
    """功能：为扇区序列循环分配颜色。"""
    base = list(_DONUT_COLORS)
    out: list[str] = []
    while len(out) < n:
        out.extend(base)
    return out[:n]


def fig_taxonomy_donut(
    labels: list[str],
    values: list[int],
    *,
    height: int = 360,
    taxonomy_empty_banner: Optional[str] = None,
) -> Any:
    """功能：主域或子域环形图；taxonomy_empty_banner 非 None 时渲染占位环。"""
    if taxonomy_empty_banner is not None or not labels:
        fig = go.Figure(
            go.Pie(
                labels=[taxonomy_empty_banner or "暂无数据"],
                values=[1],
                hole=_DONUT_HOLE_RATIO,
                marker=dict(colors=["#30405c"], line=dict(color="#0f1424", width=1.5)),
                textinfo="none",
                hoverinfo="none",
                showlegend=False,
            )
        )
    else:
        rows = [(lb.strip(), int(vv or 0)) for lb, vv in zip(labels, values) if int(vv or 0) > 0]
        if not rows:
            return fig_taxonomy_donut([], [], height=height, taxonomy_empty_banner="暂无有效数据。")
        lbs, vls = zip(*rows)
        colors = donut_color_list(len(lbs))
        fig = go.Figure(
            go.Pie(
                labels=list(lbs),
                values=list(vls),
                hole=_DONUT_HOLE_RATIO,
                marker=dict(colors=colors, line=dict(color="#0f1424", width=1.5)),
                textinfo="percent",
                hoverinfo="label+value+percent",
                sort=True,
                direction="clockwise",
            )
        )

    fig.update_layout(
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        font=dict(color="#c7d0e8"),
        showlegend=True,
        legend=dict(font=dict(size=11, color="#94a3b8"), bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=10, r=10, t=20, b=10),
        height=height,
    )
    return fig
