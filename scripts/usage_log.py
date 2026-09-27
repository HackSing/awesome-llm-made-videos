#!/usr/bin/env python3
"""本地 usage 事件的追加与读取：旁路观察面，不参与任何命令的行为与退出码。

错误处理豁免（编码质量规范第 6 条"错误不许吞"的唯一例外，范围已收窄）：
只有 `append_event` 对 `OSError` 与 `UnicodeEncodeError` 返回 False 而不向上传递。
理由：附属观察路径不得卡断主命令——日志写不进去时，命令本身必须照常完成并保持
原退出码。范围与仓库既有先例 `harness.cache_plan_selection()` 一致（只捕 OSError
返回 False）。除这两类之外的异常一律不捕获，正常向上传递；`read_events` 在真实
命令的读路径上，不做任何豁免。

事件只记结构化枚举值与计数，不记 query 原文、路径、资产名或任何自由文本。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

USAGE_SCHEMA_VERSION = "docs-harness/usage-event/v1"
USAGE_DIR_RELATIVE = ".docs-harness/usage"
# 业务默认值单一来源：harness.v2_config 写 config 时导入本常量，不另行硬编码。
USAGE_LOG_DEFAULT_ENABLED = True

_CONFIG_RELATIVE = ".docs-harness/config.json"
_MONTH_FORMAT = "%Y-%m"
# 嵌套忽略：自包含、零安装面改动、不触碰用户根 .gitignore。
_GITIGNORE_CONTENT = "*\n"


def is_enabled(target: Path) -> bool:
    """当且仅当项目 config 显式声明 `usage_log.enabled` 为 True 时记录。

    没有 config 就是没安装 harness，没安装就没有观测面；不做 fallback 默认。
    不复用 `harness.project_config()`：受管模块不能反向 import 控制器（循环），
    且该函数在配置非对象时抛 HarnessError，与观察路径永不抛出的契约冲突。
    """
    try:
        raw = (target / _CONFIG_RELATIVE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(config, dict):
        return False
    section = config.get("usage_log")
    if not isinstance(section, dict):
        return False
    return section.get("enabled") is True


def append_event(target: Path, event: dict[str, Any]) -> bool:
    """追加一行事件，成功返回 True；写入失败返回 False（见模块 docstring 的豁免说明）。

    单行 `open(..., "a")` 写入，依赖 O_APPEND 对小行的原子性，不加锁。Windows 上该
    原子性不成立，并发写可能产生撕裂行；并发场景仅 pre-commit 钩子与手动调用，
    接受该形态，由 `read_events` 跳过不可解析行兜住。
    """
    directory = target / USAGE_DIR_RELATIVE
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        gitignore = directory / ".gitignore"
        if not gitignore.is_file():
            with open(gitignore, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(_GITIGNORE_CONTENT)
        with open(_month_path(directory, _utc_now()), "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
    except (OSError, UnicodeEncodeError):
        return False
    return True


def read_events(target: Path, days: int) -> list[dict[str, Any]]:
    """窗口内的 `cmd.invoke` 事件，按落盘顺序返回。

    跳过无法解析的行与 schema 不匹配的行：撕裂行是 Windows 非原子追加下有依据的
    可达状态，不是预防性兜底。文件读取错误不豁免，正常向上传递。
    """
    now = _utc_now()
    since = now - dt.timedelta(days=days)
    events: list[dict[str, Any]] = []
    for path in _window_paths(target / USAGE_DIR_RELATIVE, since, now):
        # 按行解码而非整文件解码：撕裂的多字节序列只污染所在行，不应废掉整个月。
        for raw_line in path.read_bytes().splitlines():
            parsed = _parse_event(raw_line.decode("utf-8", errors="replace"))
            if parsed is not None and parsed[1] >= since:
                events.append(parsed[0])
    return events


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _month_path(directory: Path, moment: dt.datetime) -> Path:
    return directory / f"{moment.strftime(_MONTH_FORMAT)}.jsonl"


def _window_paths(directory: Path, since: dt.datetime, now: dt.datetime) -> list[Path]:
    """窗口覆盖到的月文件，按月份升序；不存在的月份直接略过。"""
    paths: list[Path] = []
    cursor = since.replace(day=1)
    while cursor <= now:
        path = _month_path(directory, cursor)
        if path.is_file() and not path.is_symlink():
            paths.append(path)
        cursor = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return paths


def _parse_event(line: str) -> tuple[dict[str, Any], dt.datetime] | None:
    """一行 JSONL →（事件, 时刻）；撕裂、非对象、schema 不匹配或时间戳无效时返回 None。"""
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or value.get("v") != USAGE_SCHEMA_VERSION:
        return None
    moment = _moment(value)
    return None if moment is None else (value, moment)


def _moment(event: dict[str, Any]) -> dt.datetime | None:
    raw = event.get("ts")
    if not isinstance(raw, str):
        return None
    try:
        moment = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=dt.timezone.utc)
