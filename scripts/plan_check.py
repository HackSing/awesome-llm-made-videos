"""Plan 文档卫生检查的常量与纯函数。"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
from pathlib import Path
from typing import Any

from structure_check import symbol_hit


def _controller():
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "build_parser"):
        return main
    import harness
    return harness


PLAN_CHECK_BANNER_MARKER = "状态："
PLAN_CHECK_BANNER_STATES = ("有效", "已实施-仅追溯", "已废弃")
# 符号全命中 WARN 的第三出口：部分交付仍在推进的方案以此横幅登记核对日，时效内不再提示。
PLAN_PARTIAL_DELIVERY_BANNER = "有效-部分交付"
PLAN_PARTIAL_DELIVERY_RECHECK_DAYS = 30
PLAN_CHECK_ARCHIVE_EXEMPTION = "已归档"
PLAN_CHECK_EXCLUDED_DIRS = {"node_modules", ".worktrees", "deliverables", "output", "artifacts"}
PLAN_CHECK_ARTIFACT_DIRS = {"dist", "build", "dist-electron", "release", "zbuddy-output", "test-results", "coverage", "软著"}
PLAN_CHECK_SOURCE_SUFFIXES = {
    ".dart", ".go", ".ts", ".tsx", ".js", ".jsx", ".cjs", ".mjs", ".py",
    ".ps1", ".psm1", ".bat", ".cmd", ".sh", ".json", ".toml", ".yaml", ".yml",
}
PLAN_CHECK_STALE_DAYS = 90
PLAN_CHECK_SYMBOL_MAX_FILE_BYTES = 2_000_000
def plan_status_from_banner(banner: str | None) -> str:
    if not banner:
        return "unknown"
    if "已废弃" in banner:
        return "deprecated"
    if "已实施-仅追溯" in banner:
        return "implemented"
    if "有效" in banner:
        return "active"
    return "unknown"


def plan_check_banner(path: Path) -> str | None:
    """返回文档前 3 行内的状态横幅行；没有横幅返回 None。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[:3]
    except (OSError, UnicodeDecodeError) as exc:
        raise _controller().HarnessError(f"无法读取文档：{path}", code="plan_check_unreadable") from exc
    for line in lines:
        if PLAN_CHECK_BANNER_MARKER in line:
            return line
    return None


def canonical_index_entry(entries: list[str], basename: str) -> str:
    """返回方案的正式 INDEX 条目行。

    优先行首 Markdown 链接，其次行首表格路径；都没有时才回退首次提及。
    引用句与跨方案链接出现在正式条目之前时不得被当成方案条目。
    """
    if not entries:
        return ""
    escaped = re.escape(basename)
    bullet = re.compile(r"^- \[[^\]]*\]\(plans/" + escaped + r"\)")
    table = re.compile(r"^\|\s*`plans/" + escaped + r"`")
    for entry in entries:
        if bullet.match(entry):
            return entry
    for entry in entries:
        if table.match(entry):
            return entry
    return entries[0]


def partial_delivery_checked_recently(banner: str, today: dt.date) -> bool:
    """横幅为「有效-部分交付（YYYY-MM-DD 核对）」且核对日在时效内时返回 True。

    未来日期与非法日期不豁免：写一个远期日期不能永久静默符号全命中告警。
    """
    match = re.search(
        re.escape(PLAN_PARTIAL_DELIVERY_BANNER) + r"（(\d{4}-\d{2}-\d{2}) 核对", banner
    )
    if not match:
        return False
    try:
        checked = dt.date.fromisoformat(match.group(1))
    except ValueError:
        return False
    return 0 <= (today - checked).days <= PLAN_PARTIAL_DELIVERY_RECHECK_DAYS


def plan_check_walk_files(target: Path, prune_dirs: set[str]) -> list[Path]:
    """剪枝遍历：不进入隐藏目录、符号链接目录与指定目录，避免枚举 node_modules 等巨大子树。"""
    files: list[Path] = []
    for root, dirs, names in os.walk(target):
        dirs[:] = [
            name for name in dirs
            if not name.startswith(".") and name not in prune_dirs
            and not (Path(root) / name).is_symlink()
        ]
        files.extend(
            Path(root) / name for name in names if not name.startswith(".")
        )
    return sorted(files)


def plan_check_markdown_files(target: Path) -> list[Path]:
    """全仓 .md 文件；有 git 时用索引，避免遍历依赖与构建产物。"""
    controller = _controller()
    if controller.git_root(target) is not None:
        result = controller.git_command(
            target, "ls-files", "--cached", "--others", "--exclude-standard", "-z"
        )
        if result.returncode == 0:
            paths: list[Path] = []
            for raw in result.stdout.split(b"\x00"):
                if not raw:
                    continue
                relative = raw.decode("utf-8", errors="replace")
                path = target / relative
                if path.suffix != ".md" or not path.is_file() or path.is_symlink():
                    continue
                parts = Path(relative).parts
                if any(part.startswith(".") for part in parts):
                    continue
                if any(part in PLAN_CHECK_EXCLUDED_DIRS for part in parts):
                    continue
                paths.append(path)
            return sorted(paths)
    return [
        path for path in plan_check_walk_files(target, PLAN_CHECK_EXCLUDED_DIRS)
        if path.suffix == ".md" and not path.is_symlink()
    ]


def load_plan_projection(companion: Path) -> tuple[dict[str, Any] | None, Any]:
    if not companion.is_file() or companion.is_symlink():
        return None, None
    try:
        frozen = _controller().validate_frozen_plan(_controller().read_json(companion))
    except _controller().HarnessError:
        return None, None
    content = frozen.get("content")
    return (content if isinstance(content, dict) else None), frozen.get("governance")


def plan_has_remaining_steps(target: Path, basename: str) -> bool:
    basename = Path(basename).stem
    path = target / _controller().PLAN_DOCS_RELATIVE / f"{basename}.json"
    if not path.is_file() or path.is_symlink():
        return False
    try:
        frozen = _controller().validate_frozen_plan(_controller().read_json(path))
    except _controller().HarnessError:
        return False
    content = frozen.get("content")
    if not isinstance(content, dict):
        return False
    progress = content.get("progress")
    if not isinstance(progress, dict):
        return False
    remaining = progress.get("remaining_steps")
    return isinstance(remaining, list) and bool(remaining)


def plan_projection_warnings(
    relative: str,
    entry: str,
    banner: str | None,
    frozen_content: dict[str, Any] | None,
    frozen_governance: Any,
) -> list[str]:
    warnings: list[str] = []
    index_status = (
        entry.split("状态：", 1)[1].split("；关键符号", 1)[0].strip()
        if "状态：" in entry else ""
    )
    if banner and index_status and plan_status_from_banner(banner) != plan_status_from_banner(index_status):
        warnings.append(
            f"WARN: {relative}: INDEX 状态「{index_status}」与 Markdown 横幅不一致"
        )
    if frozen_content is not None:
        try:
            expected_title, expected_symbols = _controller().plan_title_and_symbols(frozen_content)
        except _controller().HarnessError:
            expected_title, expected_symbols = "", []
        title_match = re.match(r"^- \[([^\]]+)\]", entry)
        if expected_title and title_match and title_match.group(1) != expected_title:
            warnings.append(
                f"WARN: {relative}: INDEX 标题与冻结方案不一致"
                f"（INDEX「{title_match.group(1)}」，JSON「{expected_title}」）"
            )
        entry_symbols = re.findall(
            r"`([^`]+)`", entry.split("关键符号", 1)[1]
        ) if "关键符号" in entry else []
        if expected_symbols and sorted(set(entry_symbols)) != sorted(set(expected_symbols)):
            warnings.append(
                f"WARN: {relative}: INDEX 关键符号与冻结方案不一致"
                f"（INDEX {entry_symbols}，JSON {list(expected_symbols)}）"
            )
    if isinstance(frozen_governance, dict) and banner:
        expected_status = (
            "implemented"
            if frozen_governance.get("governance_settled_at")
            else "active"
        )
        actual_status = plan_status_from_banner(banner)
        if actual_status != expected_status:
            warnings.append(
                f"WARN: {relative}: 横幅状态与治理记录不一致"
                f"（横幅 {actual_status}，治理记录 {expected_status}）"
            )
    return warnings


