"""
功能：DataFrame 展示相关的轻量封装（统一空表结构，减少页面内样板代码）。

输入：列名序列。
输出：空壳 `pandas.DataFrame`。
上下游：`ui.pages.tracks` 等在无数据时使用，保持 dataframe 挂载点组件类型一致。
"""

from __future__ import annotations

import pandas as pd


def empty_dataframe(columns: list[str]) -> pd.DataFrame:
    """
    功能：构造指定列名、零行的占位表。
    输入：列名列表（中文列名）。
    输出：空 DataFrame。
    """
    return pd.DataFrame(columns=columns)
