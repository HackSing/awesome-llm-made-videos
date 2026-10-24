"""CODEMAP 文本解析：当前能力索引行与公开接口字段。

只负责把 CODEMAP 文本解析成条目与接口符号；条目存活与目录模块内容由
structure_check 消费本模块结果后判定。
"""

from __future__ import annotations

import re

_CODEMAP_ENTRY_PATTERN = re.compile(r"^\s*-\s*`([^`]+)`")


def parse_codemap(text: str) -> list[tuple[str, list[str]]]:
    """解析 CODEMAP 条目行：`- `路径` — 职责：…；公开接口：`sym`…`；不匹配的行忽略。"""
    entries: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        match = _CODEMAP_ENTRY_PATTERN.match(line)
        if not match:
            continue
        _, _, interface_part = line.partition("公开接口")
        symbols = re.findall(r"`([^`]+)`", interface_part)
        entries.append((match.group(1), symbols))
    return entries


def _is_codemap_index_entry(line: str) -> bool:
    return "职责：" in line or "职责:" in line


def codemap_interface_symbols(line: str) -> list[str]:
    """提取条目当前声明的公开接口符号。

    只取「公开接口」字段里的符号：括号里的替代说明、历史符号与「公开接口：无」
    后面的解释文字都不是当前接口，不能拿来做存活性判定。
    """
    if "公开接口" not in line:
        return []
    segment = line.split("公开接口", 1)[1].lstrip()
    if segment.startswith("："):
        segment = segment[1:].lstrip()
    while segment.startswith("（"):
        close = segment.find("）")
        first_tick = segment.find("`")
        if close == -1 or (first_tick != -1 and close > first_tick):
            break
        segment = segment[close + 1:].lstrip()
        if segment.startswith(("，", "、", ",")):
            segment = segment[1:].lstrip()
    if segment.startswith("无"):
        return []
    cut = len(segment)
    for marker in ("（", "；", ";"):
        pos = segment.find(marker)
        if pos != -1:
            cut = min(cut, pos)
    return re.findall(r"`([^`]+)`", segment[:cut])


def parse_codemap_index_entries(text: str) -> list[tuple[str, list[str]]]:
    """只返回当前能力索引条目（含「职责」的行），跳过历史「变更」记录。"""
    entries: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        match = _CODEMAP_ENTRY_PATTERN.match(line)
        if not match or not _is_codemap_index_entry(line):
            continue
        entries.append((match.group(1), codemap_interface_symbols(line)))
    return entries
