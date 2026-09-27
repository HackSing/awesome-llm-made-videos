#!/usr/bin/env python3
"""usage 事件的纯聚合：把本地命令调用日志数成计数，不做方向性判断。

输出契约完全沿用 `structure report`：`build_report` 返回 dict，由 harness 既有
`emit()` 呈现——非 json 模式按 `key: value` 逐行输出，`--json` 模式输出整体 JSON。
本模块不带独立文本渲染器：仓库现有命令无一自带渲染器，新加一个是没有先例的抽象
（编码质量规范第 10 条"抽象同样要证据"）。

本模块不直接触文件系统，事件读取委托 `usage_log.read_events`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from usage_log import read_events

# 业务默认值单一来源：harness.build_parser 的 --days default 导入本常量。
USAGE_REPORT_DEFAULT_DAYS = 30

# 以下两个元组镜像 CLI 的枚举，目的是把"从未发生"显示为 0 而不是缺键——
# "plan create 5 次、settle 0 次"正是本报告要暴露的堆积信号，缺键会把它藏起来。
# 受管模块不能反向 import harness，故在此各留一份。
_ASSET_COMMANDS = ("knowledge", "plan", "acceptance", "adr")
_PLAN_SETTLE_STATUSES = ("implemented", "deprecated")
# acceptance record 退 0 表示记录存入且整体验收已通过，退 3 表示记录已存入但整体仍
# pending/failed（harness.acceptance_record: `0 if result["status"] == "passed" else 3`）。
# 两者都是"记录成功存入"，都计入返工率分母；只认 0 会让分母只数到最后一条翻绿的记录。
_ACCEPTANCE_RECORD_STORED_CODES = (0, 3)

_LIMITATIONS = (
    "以上均为本地命令调用计数，不代表能力价值，也不代表 agent 对知识的理解质量；"
    "commands 的 errors 只数 HarnessError（result=error），error_codes 为其错误码分布；"
    "exit_codes 按取值分桶不贴成败标签（3 在 harness 里被多个命令复用，语义各不相同）；"
    "checks 只报每个检查命令最近一次调用的 failures/warnings，跨调用加总会把同一条告警重复计数；"
    "资产 create/settle 的成功判定为退出码 0 且非 dry-run；"
    "acceptance record 退出码 3 表示记录已存入但整体验收未通过，计入 acceptance_rework.records 分母；"
    "跨仓库汇总需维护者自行在各仓库运行 usage report --json 后合并，harness 不外发任何数据。"
)


def build_report(target: Path, days: int) -> dict[str, Any]:
    """窗口内 usage 事件的聚合结果（A-F 六组 + summary + limitations）。"""
    events = read_events(target, days)
    report: dict[str, Any] = {
        "window_days": days,
        "event_count": len(events),
        "commands": _command_adoption(events),
        "asset_lifecycle": _asset_lifecycle(events),
        "acceptance_rework": _acceptance_rework(events),
        "plan_settlement": _plan_settlement(events),
        "knowledge_query": _knowledge_query(events),
        "checks": _check_results(events),
    }
    report["summary"] = _summary(report)
    report["limitations"] = _LIMITATIONS
    return report


def _label(event: dict[str, Any]) -> str:
    action = event.get("action")
    return f"{event.get('command')} {action}" if action else str(event.get("command"))


def _succeeded(event: dict[str, Any]) -> bool:
    """create/settle 类命令的成功：退 0 且不是 dry-run（dry-run 退 0 但不产生资产）。"""
    return event.get("exit_code") == 0 and not event.get("flags", {}).get("dry_run")


def _command_adoption(events: list[dict[str, Any]]) -> dict[str, Any]:
    """A 组：每个 (command, action) 的调用次数、活跃天数、首末时间、退出码分桶与报错归因。

    errors 只数 result=error（HarnessError 路径），不从退出码推断：acceptance record 退 3
    是记录已存入，project upgrade 退 3 是待提交，二者都不是报错。
    """
    adoption: dict[str, dict[str, Any]] = {}
    active: dict[str, set[str]] = {}
    for event in events:
        label = _label(event)
        entry = adoption.setdefault(
            label,
            {
                "calls": 0, "active_days": 0, "first": None, "last": None,
                "exit_codes": {}, "errors": 0, "error_codes": {},
            },
        )
        stamp = str(event.get("ts"))
        entry["calls"] += 1
        entry["first"] = stamp if entry["first"] is None else min(entry["first"], stamp)
        entry["last"] = stamp if entry["last"] is None else max(entry["last"], stamp)
        code = str(event.get("exit_code"))
        entry["exit_codes"][code] = entry["exit_codes"].get(code, 0) + 1
        if event.get("result") == "error":
            entry["errors"] += 1
            error_code = event.get("error_code")
            if isinstance(error_code, str):
                entry["error_codes"][error_code] = entry["error_codes"].get(error_code, 0) + 1
        active.setdefault(label, set()).add(stamp[:10])
    for label, entry in adoption.items():
        entry["active_days"] = len(active[label])
    return adoption


def _asset_lifecycle(events: list[dict[str, Any]]) -> dict[str, Any]:
    """B 组：四类资产各自的 create 成功数与 settle 成功数。"""
    counts = {name: {"create": 0, "settle": 0} for name in _ASSET_COMMANDS}
    for event in events:
        command = str(event.get("command"))
        action = str(event.get("action"))
        if command in counts and action in ("create", "settle") and _succeeded(event):
            counts[command][action] += 1
    return counts


def _acceptance_rework(events: list[dict[str, Any]]) -> dict[str, Any]:
    """C 组：acceptance record 的存入总数，及其中返工与用户确认各自的次数。"""
    stored = [
        event.get("flags", {})
        for event in events
        if event.get("command") == "acceptance"
        and event.get("action") == "record"
        and event.get("exit_code") in _ACCEPTANCE_RECORD_STORED_CODES
    ]
    return {
        "records": len(stored),
        "reaccept": sum(1 for flags in stored if flags.get("reaccept")),
        "user_confirmed": sum(1 for flags in stored if flags.get("user_confirmed")),
    }


def _plan_settlement(events: list[dict[str, Any]]) -> dict[str, Any]:
    """D 组：plan settle 成功调用中 implemented 与 deprecated 各自的次数。"""
    counts = {status: 0 for status in _PLAN_SETTLE_STATUSES}
    for event in events:
        if (
            event.get("command") == "plan"
            and event.get("action") == "settle"
            and _succeeded(event)
        ):
            status = str(event.get("flags", {}).get("status"))
            if status in counts:
                counts[status] += 1
    return counts


def _knowledge_query(events: list[dict[str, Any]]) -> dict[str, Any]:
    """E 组：knowledge query 成功调用总数与其中零命中的次数。"""
    hits = [
        event["hits"]
        for event in events
        if event.get("command") == "knowledge"
        and event.get("action") == "query"
        and event.get("exit_code") == 0
        and isinstance(event.get("hits"), int)
    ]
    return {"queries": len(hits), "zero_hit": sum(1 for value in hits if value == 0)}


def _check_results(events: list[dict[str, Any]]) -> dict[str, Any]:
    """F 组：凡带 failures/warnings 计数的调用，按命令数出调用次数、零失败次数与最近一次的计数。

    不硬编码检查类命令清单：带这两个计数的事件就是检查类调用，命令面增删自动跟随。
    只报最近一次而不跨调用加总：pre-commit 每次提交都跑一遍，未处置的同一条 WARN
    会被反复计入（zbuddy-desktop 47 次检查累计 518 条，实为 12 条方案告警重复计数）。
    """
    results: dict[str, dict[str, int]] = {}
    latest: dict[str, str] = {}
    for event in events:
        failures = event.get("failures")
        warnings = event.get("warnings")
        if not isinstance(failures, int) or not isinstance(warnings, int):
            continue
        label = _label(event)
        entry = results.setdefault(
            label, {"calls": 0, "clean": 0, "last_failures": 0, "last_warnings": 0}
        )
        entry["calls"] += 1
        entry["clean"] += 1 if failures == 0 else 0
        stamp = str(event.get("ts"))
        if stamp >= latest.get(label, ""):
            latest[label] = stamp
            entry["last_failures"] = failures
            entry["last_warnings"] = warnings
    return results


def _summary(report: dict[str, Any]) -> str:
    checks = sum(entry["calls"] for entry in report["checks"].values())
    return (
        f"usage report {report['window_days']} 天窗口：事件 {report['event_count']} 条、"
        f"命令面 {len(report['commands'])} 个、检查类调用 {checks} 次、"
        f"acceptance 记录 {report['acceptance_rework']['records']} 条、"
        f"knowledge 查询 {report['knowledge_query']['queries']} 次"
    )
