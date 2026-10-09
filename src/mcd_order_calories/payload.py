"""从麦当劳 MCP 的返回文本里取出真实 JSON。

服务端返回的不是裸 JSON，而是「字段说明 + JSON」混排的文本：

    # API Response Information
    ...
    ## Response Structure
    - **data[].storeName**: 门店名称        ← 注意这里含 `data[]`
    ...
    ## Original Response

    {"success":true,"code":200,...,"data":{...}}

踩过的两个坑：

1. `## Original Response` 段里**仍然可能**先有一段展示说明（`- **门店名称**: data[].storeName`），
   说明里的 `data[]` 含方括号，会让 `\\[.*\\]` 这类简单正则抢先匹配、解析出空结果。
2. 因此必须**从最后一个 `{"success"` 起**截取到最后一个 `}`，而不是从头匹配。
"""

from __future__ import annotations

import json
import re
from typing import Any

_MARKER = "## Original Response"
_SUCCESS = re.compile(r'\{"success"\s*:')


class PayloadError(ValueError):
    """返回文本里找不到可解析的 JSON。"""


def extract_payload(text: str) -> Any:
    """从 MCP 返回文本中提取 JSON 对象。

    优先定位 ``{"success"`` 锚点；找不到时退回「从最后一个 ``{`` 开始尝试解析」。
    """
    segment = text
    marker = text.find(_MARKER)
    if marker >= 0:
        segment = text[marker + len(_MARKER):]

    anchors = [m.start() for m in _SUCCESS.finditer(segment)]
    if not anchors:
        anchors = [m.start() for m in re.finditer(r"\{", segment)]

    for start in reversed(anchors):
        end = segment.rfind("}")
        if end <= start:
            continue
        try:
            return json.loads(segment[start:end + 1])
        except json.JSONDecodeError:
            continue
    raise PayloadError(f"无法从返回文本中解析 JSON（前 120 字符：{text[:120]!r}）")


def extract_data(text: str) -> Any:
    """提取并返回 ``payload["data"]``。"""
    payload = extract_payload(text)
    if not isinstance(payload, dict):
        raise PayloadError(f"顶层不是 JSON 对象：{type(payload).__name__}")
    return payload.get("data")
