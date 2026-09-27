"""脚本文件卫生检查：混合行尾机械拦截。

入库内容已被 .gitattributes 的 eol 规则规范化（永远干净），真正的隐患是磁盘上的
混合行尾脚本——它是字节级编辑事故（转义塌陷、锚点漂移、伪 \r 匹配）的首要来源。
本检查对 tracked 脚本做全仓字节级扫描，pre-commit（assets-check --fast）与
CI（assets-check --strict）共用同一真源，不依赖各机器钩子是否激活。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from managed_assets import AssetError

SCRIPT_GLOBS = ("*.sh", "*.iss", "*.bat", "*.cmd", "*.ps1")


def _tracked_script_files(target: Path) -> list[str] | None:
    """返回 tracked 脚本清单；目标不是 git 仓库或 git 不可用时返回 None。"""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", *SCRIPT_GLOBS],
            cwd=target,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return [raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]


def check_script_line_endings(target: Path) -> dict[str, Any]:
    files = _tracked_script_files(target)
    if files is None:
        # 非 git 目标不适用本检查（pre-commit/CI 永远在 git 仓库内运行）；记 checked=0
        # 而不产 WARN，避免 --strict 对环境性跳过误报。
        return {"status": "passed", "failures": [], "warnings": [], "checked": 0}
    failures: list[str] = []
    checked = 0
    for relative in files:
        path = target / relative
        if not path.is_file():
            continue
        data = path.read_bytes()
        crlf = data.count(b"\r\n")
        bare_lf = data.count(b"\n") - crlf
        checked += 1
        if crlf > 0 and bare_lf > 0:
            failures.append(
                f"ScriptHygiene：{relative} 为混合行尾（CRLF {crlf} 行 / 裸 LF {bare_lf} 行），"
                "先按 .gitattributes 约定统一行尾再提交"
            )
    return {"status": "failed" if failures else "passed", "failures": failures, "warnings": [], "checked": checked}
