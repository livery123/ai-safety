"""
功能：演示区「每行 URL」文本解析。

输入：多行原始字符串。
输出：去空白后的 URL 列表；无有效行时 None。
上下游：`ui.pages.system` 中新华网/新浪同步提交参数。
"""

from __future__ import annotations

from typing import List, Optional


def textarea_urls_to_list(raw: str) -> Optional[List[str]]:
    """
    功能：将「每行一个列表页 URL」转为后台任务 payload 的 page_urls。
    输入：用户粘贴的多行字符串。
    输出：非空则为 list[str]，否则 None 表示用适配器默认频道。
    """
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    return lines or None
