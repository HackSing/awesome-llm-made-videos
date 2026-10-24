"""收尾报告字段校验：把“证据写进记录”变成可执行检查。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = ("path", "command", "exit", "layer", "uncovered")
FIELD_LABELS = {
    "path": "改动路径",
    "command": "验证命令",
    "exit": "退出结果",
    "layer": "验证层级（验收层）",
    "uncovered": "未覆盖项",
}
_PATH = re.compile(r"`[^`\n]*[./\\][^`\n]*`|(?:^|\s)(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+")
_COMMAND = re.compile(
    r"(?m)(?:^|[\s`])(?:python[0-9]*(?:\.[0-9]+)*|pytest|npm|pnpm|yarn|cargo|go test|dotnet|gradle|mvn|make|bash|node|flutter|dart|git)\b"
)
_EXIT = re.compile(
    r"退出(?:码|状态)?\s*[:：]?\s*\d|exit[_ ]?code|exit\s*[:=]\s*\d|EXIT=|返回码",
    re.IGNORECASE,
)
_LAYER = re.compile(r"验收层|验证层级")
_UNCOVERED = re.compile(r"未覆盖")


def report_fields(text: str) -> dict[str, bool]:
    return {
        "path": bool(_PATH.search(text)),
        "command": bool(_COMMAND.search(text)),
        "exit": bool(_EXIT.search(text)),
        "layer": bool(_LAYER.search(text)),
        "uncovered": bool(_UNCOVERED.search(text)),
    }


def check_report(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(str(exc)) from exc
    found = report_fields(text)
    missing = [FIELD_LABELS[name] for name in REQUIRED_FIELDS if not found[name]]
    return {
        "status": "passed" if not missing else "failed",
        "checked": len(REQUIRED_FIELDS),
        "found": found,
        "missing": missing,
    }
