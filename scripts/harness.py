#!/usr/bin/env python3
"""Docs Harness：默认直跑，按需管理四类资产生命周期。"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Sequence
SCRIPT_MODULE_DIR = Path(__file__).resolve().parent
if str(SCRIPT_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_MODULE_DIR))

from managed_assets import AssetError, AssetSpec, apply_structure, asset_pair, load_asset, structure_changes
from asset_checks import preview_warnings, run_assets_check, summarize_warnings
from report_check import check_report
from script_hygiene import check_script_line_endings
from structure_check import CODEMAP_SCAFFOLD, check_structure, structure_report
from symbol_check import symbol_hits
from plan_governance import (
    FAILURE_ATTRIBUTION_CATEGORIES, PLAN_GOVERNANCE_INPUT_SCHEMA, PLAN_SCHEMA_V3, PlanGovernanceError,
    collect_bugfix_plan_errors,
    governance_from_content, legacy_plan_template_fingerprints, new_plan_fields, nonempty_string_list,
    plan_amend_notice_line,
    prepare_settlement as prepare_plan_settlement, render_governance_block, update_plan_markdown,
    validate_bugfix_plan_contract, validate_plan as validate_governed_plan,
)
from knowledge_assets import (
    KNOWLEDGE_INPUT_SCHEMA,
    KNOWLEDGE_SPEC,
    check as check_knowledge_assets,
    create as create_knowledge_asset,
    follow_archived_refs,
    query as query_knowledge_assets,
    settle as settle_knowledge_asset,
    update as update_knowledge_asset,
    validate_asset as validate_knowledge_asset,
)
from acceptance_assets import (
    ACCEPTANCE_EVIDENCE_LAYERS,
    ACCEPTANCE_LAYERS,
    ACCEPTANCE_SETTLE_INPUT_SCHEMA,
    ACCEPTANCE_SETTLE_STATUS_NOTES,
    ACCEPTANCE_SETTLE_STATUSES,
    ACCEPTANCE_SPEC,
    ACCEPTANCE_TARGET_INPUT_SCHEMA,
    check as check_acceptance_assets,
    collect_create_errors as collect_acceptance_create_errors,
    create as create_acceptance_asset,
    record as record_acceptance_asset,
    settle as settle_acceptance_asset,
    validate_asset as validate_acceptance_asset,
)
from adr_assets import (
    ADR_INPUT_SCHEMA,
    ADR_SETTLE_STATUSES,
    ADR_SPEC,
    active_constraints as active_adr_constraints,
    check as check_adr_assets,
    amend as amend_adr_asset,
    create as create_adr_asset,
    settle as settle_adr_asset,
    validate_asset as validate_adr_asset,
)
from usage_log import (
    USAGE_LOG_DEFAULT_ENABLED,
    USAGE_SCHEMA_VERSION,
    append_event as append_usage_event,
    is_enabled as usage_log_enabled,
)
from usage_report import USAGE_REPORT_DEFAULT_DAYS, build_report as build_usage_report
from diagram_view import VIEWS_RELATIVE, open_in_browser, write_view
from acceptance_amend import amend as amend_acceptance_asset
from asset_query import (
    acceptance_list,
    acceptance_show,
    adr_show,
    knowledge_show,
    plan_list,
    plan_show,
)
from cli_support import validate_action_flags
from plan_amend import collect_plan_content_errors
from plan_check import (
    PLAN_CHECK_ARCHIVE_EXEMPTION,
    PLAN_CHECK_ARTIFACT_DIRS,
    PLAN_CHECK_BANNER_MARKER,
    PLAN_CHECK_BANNER_STATES,
    PLAN_CHECK_EXCLUDED_DIRS,
    PLAN_CHECK_SOURCE_SUFFIXES,
    PLAN_CHECK_STALE_DAYS,
    PLAN_CHECK_SYMBOL_MAX_FILE_BYTES,
    PLAN_PARTIAL_DELIVERY_BANNER,
    PLAN_PARTIAL_DELIVERY_RECHECK_DAYS,
    canonical_index_entry,
    load_plan_projection,
    partial_delivery_checked_recently,
    plan_check_banner,
    plan_check_markdown_files,
    plan_check_walk_files,
    plan_has_remaining_steps,
    plan_projection_warnings,
    plan_status_from_banner,
)
from plan_selection import (
    SELECTION_AUXILIARY_KEYS,
    build_plan_selection,
    cache_plan_selection,
    plan_content_template,
    plan_select_knowledge_payload,
    resolve_plan_choice,
    resolve_plan_selection_path,
    validate_selection,
)

VERSION = "2.32.1"
CONFIG_SCHEMA = "docs-harness/project-config/v13"
KNOWN_LEGACY_CONFIG_SCHEMAS = {
    f"docs-harness/project-config/v{version}" for version in range(1, 13)
}
PLAN_TEMPLATE_SCHEMA = "docs-harness/plan-template/v3"
PLAN_SELECTION_SCHEMA = "docs-harness/plan-selection/v3"
LEGACY_PLAN_SELECTION_SCHEMA = "docs-harness/plan-selection/v2"
PLAN_SCHEMA = PLAN_SCHEMA_V3
ACCEPTANCE_INPUT_SCHEMA = "docs-harness/acceptance-input/v3"
ACCEPTANCE_RECORD_SCHEMA = "docs-harness/acceptance-record/v3"
SCRIPT_ROOT = Path(__file__).resolve().parents[1]
PLAN_TEMPLATES_RELATIVE = "plan-templates"
PLAN_TEMPLATE_RELATIVE_FILES = (
    "levels/brief.json",
    "levels/full.json",
    "profiles/general.json",
    "profiles/frontend-ui.json",
    "profiles/backend-service.json",
    "profiles/bugfix.json",
    "profiles/architecture.json",
    "profiles/migration-release.json",
)
TASK_INPUTS_RELATIVE = ".docs-harness/inputs"
TASKS_RELATIVE = ".docs-harness/tasks"
REPORTS_RELATIVE = ".docs-harness/reports"
# 不入库、升级不清理的本地约定目录；共用下方嵌套忽略与 local_only_dir_changes 一份判定。
LOCAL_ONLY_DIRS = (TASK_INPUTS_RELATIVE, TASKS_RELATIVE, REPORTS_RELATIVE, VIEWS_RELATIVE)
# 与 usage_log._GITIGNORE_CONTENT 同口径的嵌套忽略（该写法的第 2 次出现，第 3 次再抽）。
LOCAL_ONLY_GITIGNORE_CONTENT = "*\n"
GIT_HOOKS_RELATIVE = "scripts/githooks"
GIT_HOOK_RELATIVE_FILES = ("pre-commit", "setup.sh")
MANAGED_MODULE_RELATIVE_FILES = (
    "managed_assets.py",
    "asset_checks.py",
    "plan_governance.py",
    "knowledge_assets.py",
    "acceptance_assets.py",
    "adr_assets.py",
    "script_hygiene.py",
    "codemap_check.py",
    "structure_check.py",
    "structure_ts_functions.cjs",
    "usage_log.py",
    "usage_report.py",
    "report_check.py",
    "symbol_check.py",
    "acceptance_diagnostics.py",
    "diagram_view.py",
    "plan_amend.py",
    "plan_selection.py",
    "plan_check.py",
    "acceptance_amend.py",
    "acceptance_render.py",
    "asset_query.py",
    "cli_support.py",
    "agent_prompt.md",
)
PLAN_DOCS_RELATIVE = "docs/plans"
PLAN_ARCHIVE_RELATIVE = "docs/plans/archive"
PLAN_INDEX_RELATIVE = "docs/INDEX.md"
PLAN_README_RELATIVE = "docs/plans/README.md"
PLAN_ARCHIVE_KEEP_RELATIVE = "docs/plans/archive/.gitkeep"
PLAN_LEVELS = ("none", "brief", "full")
PLAN_PROFILES = (
    "general",
    "frontend_ui",
    "backend_service",
    "bugfix",
    "architecture",
    "migration_release",
)
PROFILE_FILES = {
    "general": "profiles/general.json",
    "frontend_ui": "profiles/frontend-ui.json",
    "backend_service": "profiles/backend-service.json",
    "bugfix": "profiles/bugfix.json",
    "architecture": "profiles/architecture.json",
    "migration_release": "profiles/migration-release.json",
}
MANAGED_BEGIN = "<!-- docs-harness:managed-entry:start -->"
MANAGED_END = "<!-- docs-harness:managed-entry:end -->"
CLAUDE_BEGIN = "<!-- docs-harness:claude-bridge:start -->"
CLAUDE_END = "<!-- docs-harness:claude-bridge:end -->"
# CLAUDE.md 受管区块只放这一行导入：两个文件都在时 Claude Code 只读 CLAUDE.md，经导入读到整份
# AGENTS.md（含块外的项目规则），规约只有一份真源。旧版在这里复制受管正文，块外的项目规则 Claude 看不到，
# 自己再导入 AGENTS.md 的项目则加载两遍。
CLAUDE_IMPORT = "@AGENTS.md"
MANAGED_VERSION_BEGIN = "<!-- docs-harness:managed-version:start -->"
MANAGED_VERSION_END = "<!-- docs-harness:managed-version:end -->"
PLAN_INDEX_BEGIN = "<!-- docs-harness:plans-index:start -->"
PLAN_INDEX_END = "<!-- docs-harness:plans-index:end -->"
PLAN_DOCUMENT_MARKER = "<!-- docs-harness:plan-document/v1 -->"
PLAN_STATUS_ACTIVE = "有效（实施中）"
PLAN_STATUS_IMPLEMENTED = "已实施-仅追溯"
PLAN_STATUS_DEPRECATED = "已废弃"
PLAN_SETTLE_STATUSES = ("implemented", "deprecated")
LEGACY_INDEX_PATHS = ("docs/INDEX.md", "docs/modules/INDEX.md")
LEGACY_RULES_RELATIVE = ".docs-harness/harness-home/rules"
LEGACY_RUNTIME_NAMES = (
    "runs",
    "knowledge",
    "knowledge-jobs",
    "background",
    "task-inputs",
)
KNOWLEDGE_MAP_RELATIVE = "docs/knowledge-map.json"
SEMVER_PATTERN = r"[0-9]+\.[0-9]+\.[0-9]+"
PLAN_README_CONTENT = """# 任务方案

本目录保存需要长期审查和追溯的复杂任务方案。Harness 生成的方案由同名 JSON
冻结执行合同、Markdown 提供可读正文，并通过 `docs/INDEX.md` 维护状态和关键符号。

已废弃方案移入 `archive/`；已实施方案保留在根目录并标记为仅追溯。
"""
PLAN_INDEX_SCAFFOLD = """# 项目文档索引

项目文档从这里进入；Docs Harness 只维护下方任务方案区块。
"""
PROJECT_CHANGELOG_SCAFFOLD = """# Changelog

本项目所有显著变更记录于此；版本号遵循语义化版本，新条目置顶。
"""
PROJECT_TODO_SCAFFOLD = """# TODO

条目格式：`- [ ] 事项（owner，YYYY-MM-DD）`；完成后改为 `- [x]` 并保留在「已完成」。

## 待办

## 已完成
"""
TODO_ENTRY_PATTERN = re.compile(r"^- \[[ x]\] .+（\S+，\d{4}-\d{2}-\d{2}）\s*$")
class HarnessError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_request",
        exit_code: int = 2,
        extra_payload: dict[str, Any] | None = None,
        expected: Any = None,
        next_action: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        payload = dict(extra_payload or {})
        if expected is not None:
            payload.setdefault("expected", expected)
        if next_action is not None:
            payload.setdefault("next_action", next_action)
        if details is not None:
            payload.setdefault("details", details)
        self.extra_payload = payload

def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()

def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))

def file_fingerprint(path: Path) -> str:
    # Windows core.autocrlf=true 会把工作区文本文件检出为 CRLF，按原始字节哈希会把
    # 纯行尾差异误判为"用户修改"并拒绝升级；指纹统一按 LF 归一计算。受管文件均为文本
    # （py/md/json/sh/gitkeep），安装时写入的是源包 LF 字节，历史指纹与归一结果一致。
    return sha256_bytes(path.read_bytes().replace(b"\r\n", b"\n"))

def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise HarnessError(
            f"无法读取 JSON：{path}（第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}）",
            code="invalid_json",
            details={"line": exc.lineno, "column": exc.colno, "reason": exc.msg},
            next_action="修复 JSON 语法后重跑",
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise HarnessError(
            f"无法读取 JSON：{path}（{exc.__class__.__name__}: {exc}）",
            code="invalid_json",
            details={"kind": exc.__class__.__name__, "error": str(exc)},
            next_action="检查文件路径、读取权限与 UTF-8 编码",
        ) from exc

def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()

def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")

def safe_target(raw: str) -> Path:
    target = Path(raw).expanduser().resolve()
    if not target.is_dir():
        raise HarnessError(f"项目目录不存在：{target}", code="invalid_target")
    return target

def ensure_within(target: Path, path: Path, *, code: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(target.resolve())
    except ValueError as exc:
        raise HarnessError("路径越出项目目录", code=code) from exc
    return resolved

def assert_no_symlink_ancestors(
    target: Path,
    relative: str,
    *,
    code: str,
) -> None:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise HarnessError("受管路径必须位于项目内", code=code)
    cursor = target
    for part in path.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise HarnessError(
                f"受管路径父目录是符号链接：{cursor.relative_to(target)}",
                code=code,
            )

def project_input_path(target: Path, raw: str, *, code: str) -> Path:
    source = Path(raw).expanduser()
    path = source if source.is_absolute() else target / source
    resolved = ensure_within(target, path, code=code)
    if not resolved.is_file() or resolved.is_symlink():
        raise HarnessError(f"输入文件不存在或不是常规文件：{raw}", code=code)
    return resolved

def project_output_path(target: Path, raw: str, *, code: str) -> Path:
    relative = Path(raw)
    if relative.is_absolute() or not relative.parts:
        raise HarnessError("输出必须是项目内相对路径", code=code)
    path = ensure_within(target, target / relative, code=code)
    cursor = target
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise HarnessError("输出路径包含符号链接", code=code)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise HarnessError("输出目标不是可写常规文件", code=code)
    return path

def git_command(target: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(target), *args], capture_output=True, check=False)

def git_root(target: Path) -> Path | None:
    result = git_command(target, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    return Path(result.stdout.decode("utf-8", errors="replace").strip()).resolve()

def git_dir(target: Path) -> Path | None:
    result = git_command(target, "rev-parse", "--git-dir")
    if result.returncode != 0:
        return None
    raw = result.stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        return None
    path = Path(raw)
    resolved = (path if path.is_absolute() else target / path).resolve()
    return resolved if resolved.is_dir() else None

def git_ignored_refs(target: Path, refs: list[str]) -> list[str]:
    """挑出 refs 里被 git 忽略的路径。

    非 git 仓库、git 不可用或无匹配时一律返回空列表——本函数只用于加固，
    环境不具备时不应反过来锁死资产登记。
    """
    if not refs or git_root(target) is None:
        return []
    result = git_command(target, "check-ignore", "--", *refs)
    if result.returncode != 0:  # 1=无匹配，128=非仓库/git 不可用
        return []
    lines = result.stdout.decode("utf-8", errors="replace").splitlines()
    return [line.strip() for line in lines if line.strip()]

def git_dir(target: Path) -> Path | None:
    result = git_command(target, "rev-parse", "--git-dir")
    if result.returncode != 0:
        return None
    raw = Path(result.stdout.decode("utf-8", errors="replace").strip())
    return (raw if raw.is_absolute() else target / raw).resolve()

def git_common_dir(target: Path) -> Path | None:
    result = git_command(target, "rev-parse", "--git-common-dir")
    if result.returncode != 0:
        return None
    raw = Path(result.stdout.decode("utf-8", errors="replace").strip())
    resolved = (raw if raw.is_absolute() else target / raw).resolve()
    return resolved if resolved.is_dir() else None

def runtime_parent(target: Path) -> Path:
    metadata = git_dir(target)
    return metadata / "docs-harness" if metadata else target / ".docs-harness"

def migration_runtime_parents(target: Path) -> list[Path]:
    """返回 pre-2.0 曾使用过的 Runtime 根，包括 Git 存储迁移前后位置。"""
    parents = [target / ".docs-harness"]
    metadata = git_dir(target)
    if metadata is not None:
        parents.append(metadata / "docs-harness")
    return list(dict.fromkeys(parents))

def v2_acceptance_root(target: Path) -> Path:
    return runtime_parent(target) / "v2" / "acceptance"

def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    metadata: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata, text[end + 5 :]

AGENT_PROMPT_RELATIVE = "agent_prompt.md"
def _managed_content() -> str:
    """Harness 运行模式 + 通用规范：从受管模板读取，安装后与控制器同目录。"""
    template = (Path(__file__).resolve().parent / AGENT_PROMPT_RELATIVE).read_text(encoding="utf-8")
    replacements = {
        "{{VERSION}}": VERSION,
        "{{TASK_INPUTS_RELATIVE}}": TASK_INPUTS_RELATIVE,
        "{{TASKS_RELATIVE}}": TASKS_RELATIVE,
        "{{REPORTS_RELATIVE}}": REPORTS_RELATIVE,
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template

def managed_agent_block(target: Path) -> str:
    return f"{MANAGED_BEGIN}\n{_managed_content()}\n{MANAGED_END}"

def claude_block(target: Path) -> str:
    return f"{CLAUDE_BEGIN}\n{CLAUDE_IMPORT}\n{CLAUDE_END}"

def validate_managed_markers(text: str, begin: str, end: str) -> None:
    begin_count = text.count(begin)
    end_count = text.count(end)
    if begin_count == end_count == 0:
        return
    if begin_count != 1 or end_count != 1 or text.index(begin) > text.index(end):
        raise HarnessError("Docs Harness 受管区块不完整或重复", code="install_conflict")

def replace_managed_block(text: str, begin: str, end: str, block: str) -> str:
    validate_managed_markers(text, begin, end)
    if begin in text:
        return re.sub(re.escape(begin) + r".*?" + re.escape(end), block, text, flags=re.DOTALL)
    prefix = text.rstrip()
    return (prefix + "\n\n" if prefix else "") + block + "\n"

def remove_managed_block(text: str, begin: str, end: str) -> str:
    validate_managed_markers(text, begin, end)
    if begin not in text:
        return text
    pattern = r"\n*" + re.escape(begin) + r".*?" + re.escape(end) + r"\n*"
    return re.sub(pattern, "\n", text, flags=re.DOTALL).strip() + "\n"

def plan_index_doc_tokens(basename: str) -> tuple[str, ...]:
    """INDEX 条目引用 plans 文档的两种合法形态：Markdown 链接与反引号路径。

    带 plans/ 前缀的 token 防止 acceptance/ 等区块同名文档误伤；反引号形态
    兼容表格式索引的存量项目（如 ZBuddy），二者语义等同。
    """
    return (f"(plans/{basename})", f"`plans/{basename}`")

def plan_index_entry_lines(text: str) -> list[str]:
    """读取受管方案索引条目；区块外项目正文不参与管理。"""
    validate_managed_markers(text, PLAN_INDEX_BEGIN, PLAN_INDEX_END)
    if PLAN_INDEX_BEGIN not in text:
        return []
    body = text.split(PLAN_INDEX_BEGIN, 1)[1].split(PLAN_INDEX_END, 1)[0]
    return [line for line in body.splitlines() if line.startswith("- [")]

def render_plan_index_block(entries: Sequence[str]) -> str:
    body = "\n".join(entries)
    suffix = f"\n{body}" if body else ""
    return (
        f"{PLAN_INDEX_BEGIN}\n## 任务方案\n{suffix}\n"
        f"{PLAN_INDEX_END}"
    )

def update_plan_index_text(
    text: str,
    *,
    basename: str | None = None,
    entry: str | None = None,
) -> str:
    """按文件名替换或删除一个方案条目，并保留项目自己的索引正文。"""
    entries = plan_index_entry_lines(text)
    if basename:
        link_tokens = (f"(plans/{basename}.md)", f"(plans/archive/{basename}.md)")
        entries = [line for line in entries if not any(token in line for token in link_tokens)]
    if entry:
        entries.append(entry)
    block = render_plan_index_block(entries)
    return replace_managed_block(text, PLAN_INDEX_BEGIN, PLAN_INDEX_END, block)

def validate_plan_docs_paths(target: Path) -> None:
    for relative in (
        PLAN_INDEX_RELATIVE,
        PLAN_README_RELATIVE,
        PLAN_ARCHIVE_KEEP_RELATIVE,
    ):
        assert_no_symlink_ancestors(target, relative, code="plan_docs_conflict")
        path = target / relative
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise HarnessError(
                f"方案文档路径不是常规文件：{relative}",
                code="plan_docs_conflict",
            )
    index_path = target / PLAN_INDEX_RELATIVE
    if index_path.is_file():
        validate_managed_markers(
            index_path.read_text(encoding="utf-8"), PLAN_INDEX_BEGIN, PLAN_INDEX_END
        )

def plan_docs_structure_changes(target: Path) -> list[dict[str, str]]:
    validate_plan_docs_paths(target)
    changes: list[dict[str, str]] = []
    if not (target / PLAN_README_RELATIVE).is_file():
        changes.append({"path": PLAN_README_RELATIVE, "action": "create"})
    if not (target / PLAN_ARCHIVE_KEEP_RELATIVE).is_file():
        changes.append({"path": PLAN_ARCHIVE_KEEP_RELATIVE, "action": "create"})
    index_path = target / PLAN_INDEX_RELATIVE
    if not index_path.is_file():
        changes.append({"path": PLAN_INDEX_RELATIVE, "action": "create"})
    elif PLAN_INDEX_BEGIN not in index_path.read_text(encoding="utf-8"):
        changes.append({"path": PLAN_INDEX_RELATIVE, "action": "create_plan_index_block"})
    return changes

def apply_plan_docs_structure(target: Path) -> list[str]:
    """幂等初始化方案目录与受管索引区块，不覆盖现有项目文档。"""
    changes = plan_docs_structure_changes(target)
    changed: list[str] = []
    readme = target / PLAN_README_RELATIVE
    if not readme.is_file():
        atomic_write_text(readme, PLAN_README_CONTENT)
        changed.append(PLAN_README_RELATIVE)
    keep = target / PLAN_ARCHIVE_KEEP_RELATIVE
    if not keep.is_file():
        atomic_write_text(keep, "")
        changed.append(PLAN_ARCHIVE_KEEP_RELATIVE)
    index_path = target / PLAN_INDEX_RELATIVE
    current = (
        index_path.read_text(encoding="utf-8")
        if index_path.is_file()
        else PLAN_INDEX_SCAFFOLD
    )
    updated = update_plan_index_text(current)
    if updated != current or not index_path.is_file():
        atomic_write_text(index_path, updated)
        changed.append(PLAN_INDEX_RELATIVE)
    expected = {item["path"] for item in changes}
    if not expected.issubset(set(changed)):
        raise HarnessError("方案文档初始化结果与预览不一致", code="plan_docs_conflict")
    return changed

def template_registry(root: Path) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    for relative in PLAN_TEMPLATE_RELATIVE_FILES:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            return {}
        value = read_json(path)
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != PLAN_TEMPLATE_SCHEMA
            or value.get("version") != VERSION
            or not isinstance(value.get("fields"), list)
        ):
            return {}
        registry[relative] = value
    return registry

def template_fingerprints(root: Path) -> dict[str, str]:
    registry = template_registry(root)
    if not registry:
        return {}
    return {relative: file_fingerprint(root / relative) for relative in PLAN_TEMPLATE_RELATIVE_FILES}

def githook_fingerprints(root: Path) -> dict[str, str]:
    """git 钩子文件指纹；任一文件缺失或非常规文件时返回空（与方案模板口径一致）。"""
    fingerprints: dict[str, str] = {}
    for relative in GIT_HOOK_RELATIVE_FILES:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            return {}
        fingerprints[relative] = file_fingerprint(path)
    return fingerprints

GITHOOK_SHIM_MARKER = "docs-harness-hook-shim"

def git_hook_directory(target: Path) -> Path | None:
    """真实钩子目录（git worktree 之间共享）；非 git 目标或 git 不可用时返回 None。"""
    root = git_root(target)
    if root is None:
        return None
    result = git_command(root, "rev-parse", "--git-common-dir")
    if result.returncode != 0:
        return None
    common = Path(result.stdout.decode("utf-8", errors="replace").strip())
    if not common.is_absolute():
        common = root / common
    return common / "hooks"

GITHOOK_INDEX_FIX_COMMAND = (
    "git update-index --chmod=+x scripts/githooks/pre-commit scripts/githooks/setup.sh"
)

def register_githook_index_mode(target: Path) -> list[str]:
    """core.filemode=false 的仓库里把受管钩子以 100755 登记进索引，返回登记的路径。

    filemode=false（Windows 默认）时 git 首次登记新文件读不到磁盘可执行位，钩子会以
    100644 入库，类 Unix 克隆上不执行；已入库条目的模式此后被沿用，所以只需在索引缺失
    或非 100755 时登记一次。副作用：这两个文件的内容随之进入暂存区。filemode=true 时
    git 按磁盘可执行位入库（安装器已 chmod +x），被 git 忽略的钩子路径不强行加入索引。
    """
    root = git_root(target)
    if root is None:
        return []
    filemode = git_command(root, "config", "--get", "core.filemode")
    if filemode.stdout.decode("utf-8", errors="replace").strip().lower() != "false":
        return []
    hook_paths = [f"{GIT_HOOKS_RELATIVE}/{relative}" for relative in GIT_HOOK_RELATIVE_FILES]
    ignored = set(git_ignored_install_paths(target, hook_paths))
    registered: list[str] = []
    for relative in hook_paths:
        path = target / relative
        if relative in ignored or not path.is_file():
            continue
        git_relative = path.resolve().relative_to(root).as_posix()
        listed = git_command(root, "ls-files", "-s", "--", git_relative)
        mode = listed.stdout.decode("utf-8", errors="replace").split(None, 1)[:1]
        if mode == ["100755"]:
            continue
        result = git_command(
            root, "update-index", "--add", "--chmod=+x", "--", git_relative
        )
        if result.returncode != 0:
            raise HarnessError(
                f"钩子可执行位登记失败（{relative}）："
                + result.stderr.decode("utf-8", errors="replace").strip()
                + f"；请手工执行：{GITHOOK_INDEX_FIX_COMMAND}",
                code="githook_index_mode_failed",
                exit_code=1,
            )
        registered.append(relative)
    return registered

def check_githook_health(target: Path, findings: list[dict[str, str]]) -> None:
    """钩子健康检查（yellow）：受管钩子入库可执行位 + hooksPath 屏蔽冲突。

    内容指纹比对（githook_drift）检测不到索引模式差异与 hooksPath 替换语义
    导致的静默失效，这两条 finding 覆盖这两类盲区；非 git 目标整体跳过。
    """
    root = git_root(target)
    if root is None:
        return
    result = git_command(
        root,
        "ls-files",
        "-s",
        "--",
        *(f"{GIT_HOOKS_RELATIVE}/{relative}" for relative in GIT_HOOK_RELATIVE_FILES),
    )
    if result.returncode == 0:
        non_executable = []
        for line in result.stdout.decode("utf-8", errors="replace").splitlines():
            meta, _, path = line.partition("\t")
            mode = meta.split(None, 1)[0] if meta.strip() else ""
            if path and mode and mode != "100755":
                non_executable.append(path)
        if non_executable:
            findings.append(
                {
                    "severity": "yellow",
                    "code": "githook_index_mode",
                    "message": "受管钩子入库模式非 100755，类 Unix 克隆上不会执行："
                    + ", ".join(sorted(non_executable))
                    + f"；修复：{GITHOOK_INDEX_FIX_COMMAND} 后提交",
                }
            )
    hooks_dir = git_hook_directory(target)
    if hooks_dir is None or not hooks_dir.is_dir():
        return
    current = git_command(root, "config", "--get", "core.hooksPath")
    hooks_path = current.stdout.decode("utf-8", errors="replace").strip()
    if current.returncode == 0 and hooks_path:
        shadowed = sorted(
            entry.name
            for entry in hooks_dir.iterdir()
            if entry.is_file() and not entry.name.endswith(".sample")
        )
        if shadowed:
            findings.append(
                {
                    "severity": "yellow",
                    "code": "githook_hookspath_conflict",
                    "message": f"core.hooksPath={hooks_path} 已设置，钩子目录内这些钩子不会运行："
                    + ", ".join(shadowed),
                }
            )

def managed_module_fingerprints(root: Path) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for relative in MANAGED_MODULE_RELATIVE_FILES:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            return {}
        fingerprints[relative] = file_fingerprint(path)
    return fingerprints

def asset_structure_changes(target: Path) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    for spec in (KNOWLEDGE_SPEC, ACCEPTANCE_SPEC, ADR_SPEC):
        try:
            changes.extend(structure_changes(target, spec))
        except AssetError as exc:
            raise translate_asset_error(exc) from exc
    return changes

def apply_asset_structures(target: Path) -> list[str]:
    changed: list[str] = []
    for spec in (KNOWLEDGE_SPEC, ACCEPTANCE_SPEC, ADR_SPEC):
        try:
            changed.extend(apply_structure(target, spec))
        except AssetError as exc:
            raise translate_asset_error(exc) from exc
    return list(dict.fromkeys(changed))

def project_doc_scaffolds(target: Path) -> dict[str, str]:
    """项目级文档骨架；README 取目录名，其余为固定模板。"""
    return {
        "CHANGELOG.md": PROJECT_CHANGELOG_SCAFFOLD,
        "TODO.md": PROJECT_TODO_SCAFFOLD,
        "README.md": f"# {target.resolve().name}\n\n（项目简介占位：一句话说明这个项目是什么。）\n",
        "docs/CODEMAP.md": CODEMAP_SCAFFOLD,
    }

def project_doc_changes(target: Path) -> list[dict[str, str]]:
    return [
        {"path": relative, "action": "create"}
        for relative in project_doc_scaffolds(target)
        if not (target / relative).is_file()
    ]

def apply_project_doc_scaffolds(target: Path) -> list[str]:
    changed: list[str] = []
    for relative, content in project_doc_scaffolds(target).items():
        path = target / relative
        if not path.is_file():
            atomic_write_text(path, content)
            changed.append(relative)
    return changed

def validate_project_source(source_root: Path) -> None:
    source_hint = (
        f"若你正从项目内已安装的副本运行 init/upgrade，请改用完整的 Docs Harness {VERSION} 源包，"
        "或先恢复项目内缺失的 plan-templates 文件"
    )
    if not (source_root / "scripts" / "harness.py").is_file():
        raise HarnessError(
            f"来源包缺少 scripts/harness.py（{source_hint}）", code="invalid_source"
        )
    if not managed_module_fingerprints(source_root / "scripts"):
        raise HarnessError(
            f"来源包缺少完整 {VERSION} 资产生命周期模块（{source_hint}）",
            code="invalid_source",
        )
    if not template_fingerprints(source_root / PLAN_TEMPLATES_RELATIVE):
        raise HarnessError(
            f"来源包缺少完整 {VERSION} 方案模板（{source_hint}）", code="invalid_source"
        )
    if not githook_fingerprints(source_root / GIT_HOOKS_RELATIVE):
        raise HarnessError(
            f"来源包缺少完整 {VERSION} git 钩子（{source_hint}）", code="invalid_source"
        )

def source_package_version(source_root: Path) -> str:
    version_file = source_root / "VERSION"
    if version_file.is_file():
        return version_file.read_text(encoding="utf-8").strip()
    match = re.search(
        r'^VERSION\s*=\s*"([^"]+)"',
        (source_root / "scripts" / "harness.py").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise HarnessError(
            "来源包缺少 VERSION 文件，且无法从 scripts/harness.py 读取版本",
            code="invalid_source",
        )
    return match.group(1)

def resolve_source_root(raw: str, target: Path) -> Path:
    source_root = Path(raw).expanduser().resolve()
    if not source_root.is_dir() or source_root.is_symlink():
        raise HarnessError(
            f"来源包必须是真实目录：{source_root}", code="invalid_source"
        )
    validate_project_source(source_root)
    source_version = source_package_version(source_root)
    if source_version != VERSION:
        raise HarnessError(
            f"来源包版本 {source_version} 与当前控制器版本 {VERSION} 不同；"
            "跨版本升级请直接运行源包内的 scripts/harness.py project upgrade",
            code="source_version_mismatch",
        )
    return source_root

def project_config(target: Path) -> dict[str, Any] | None:
    assert_no_symlink_ancestors(
        target,
        ".docs-harness/config.json",
        code="invalid_config_path",
    )
    path = target / ".docs-harness" / "config.json"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise HarnessError("项目配置路径不是常规文件", code="invalid_config_path")
    if not path.is_file():
        return None
    value = read_json(path)
    if not isinstance(value, dict):
        raise HarnessError("项目配置必须是 JSON 对象", code="invalid_config")
    return value

def portable_install_paths() -> list[str]:
    return [
        "scripts/harness.py",
        *(f"scripts/{relative}" for relative in MANAGED_MODULE_RELATIVE_FILES),
        "AGENTS.md",
        "CLAUDE.md",
        ".docs-harness/config.json",
        PLAN_INDEX_RELATIVE,
        PLAN_README_RELATIVE,
        PLAN_ARCHIVE_KEEP_RELATIVE,
        KNOWLEDGE_SPEC.readme_path,
        KNOWLEDGE_SPEC.keep_path,
        ACCEPTANCE_SPEC.readme_path,
        ACCEPTANCE_SPEC.keep_path,
        *(f"{PLAN_TEMPLATES_RELATIVE}/{relative}" for relative in PLAN_TEMPLATE_RELATIVE_FILES),
        *(f"{GIT_HOOKS_RELATIVE}/{relative}" for relative in GIT_HOOK_RELATIVE_FILES),
    ]

def git_ignored_install_paths(target: Path, relative_paths: Sequence[str]) -> list[str]:
    root = git_root(target)
    if root is None:
        return []
    ignored: list[str] = []
    for relative in relative_paths:
        path = target / relative
        try:
            git_relative = path.resolve(strict=False).relative_to(root).as_posix()
        except ValueError:
            ignored.append(relative)
            continue
        result = git_command(root, "check-ignore", "--no-index", "-q", "--", git_relative)
        if result.returncode == 0:
            ignored.append(relative)
    return ignored

def knowledge_candidates(target: Path, scopes: Sequence[str]) -> list[Path]:
    docs = target / "docs"
    if not docs.is_dir() or docs.is_symlink():
        return []
    candidates: list[Path] = []
    paths: list[Path] = []
    if git_root(target) is not None:
        result = git_command(
            target, "ls-files", "--cached", "--others", "--exclude-standard", "-z", "docs"
        )
        if result.returncode == 0:
            for raw in result.stdout.split(bytes([0])):
                if raw:
                    paths.append(target / raw.decode("utf-8", errors="replace"))
    if not paths:
        paths = list(docs.rglob("*"))
    for path in paths:
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in {".md", ".txt", ".json"}:
            continue
        try:
            path.resolve().relative_to(target.resolve())
        except ValueError:
            continue
        relative = path.relative_to(target).as_posix()
        if relative == PLAN_INDEX_RELATIVE or relative.startswith(
            (
                "docs/history/",
                "docs/plans/",
                "docs/knowledge/",
                "docs/acceptance/",
                "docs/reviews/",
            )
        ):
            continue
        if scopes and not any(fnmatch.fnmatch(relative, pattern) for pattern in scopes):
            continue
        candidates.append(path)
    return sorted(set(candidates))

def query_tokens(query: str) -> list[str]:
    raw = re.findall(r"[A-Za-z0-9_./:-]+|[\u4e00-\u9fff]{2,}", query.casefold())
    tokens: list[str] = []
    for token in raw:
        tokens.append(token)
        if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) > 4:
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
    return list(dict.fromkeys(item for item in tokens if item.strip()))

def validate_knowledge_query_args(args: argparse.Namespace) -> tuple[str, list[str], list[str]]:
    query = (args.query or "").strip()
    if not query:
        raise HarnessError("knowledge query 必须提供具体 --query", code="missing_knowledge_query")
    if not 1 <= args.limit <= 10:
        raise HarnessError("--limit 必须在 1-10", code="invalid_knowledge_limit")
    if not 500 <= args.max_chars <= 12000:
        raise HarnessError("--max-chars 必须在 500-12000", code="invalid_knowledge_budget")
    scopes = list(dict.fromkeys(args.scope or []))
    if any(Path(scope).is_absolute() or ".." in Path(scope).parts for scope in scopes):
        raise HarnessError("--scope 必须是项目内模式", code="invalid_knowledge_scope")
    return query, scopes, query_tokens(query)

def document_knowledge_query(
    target: Path,
    scopes: Sequence[str],
    tokens: Sequence[str],
    limit: int,
    max_chars: int,
) -> tuple[list[dict[str, str]], int]:
    ranked: list[tuple[int, str, str]] = []
    for path in knowledge_candidates(target, scopes):
        try:
            if path.stat().st_size > 512_000:
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lowered = content.casefold()
        score = sum(lowered.count(token) * max(1, len(token)) for token in tokens)
        if score > 0:
            ranked.append((score, path.relative_to(target).as_posix(), content))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    facts: list[dict[str, str]] = []
    refs: list[str] = []
    used = 0
    omitted = 0
    for _, relative, content in ranked:
        if len(facts) >= limit:
            omitted += 1
            continue
        lowered = content.casefold()
        positions = [lowered.find(token) for token in tokens if lowered.find(token) >= 0]
        position = min(positions) if positions else 0
        heading = content.rfind("\n#", 0, position)
        start = heading + 1 if heading >= 0 and position - heading <= 1200 else max(0, position - 160)
        next_heading = re.search(r"(?m)^#{1,6}\s", content[position:])
        end = position + next_heading.start() if next_heading and next_heading.start() <= 1200 else min(len(content), position + 360)
        end = min(end, start + 900)
        snippet = content[start:end].strip()
        if start:
            snippet = "…" + snippet
        if end < len(content):
            snippet += "…"
        remaining = max_chars - used
        if remaining <= 0:
            omitted += 1
            continue
        if len(snippet) > remaining:
            snippet = snippet[: max(0, remaining - 1)] + "…"
        if not snippet:
            omitted += 1
            continue
        line = content.count("\n", 0, position) + 1
        ref = f"{relative}:{line}"
        facts.append({"text": snippet, "ref": ref})
        used += len(snippet)
    return facts, omitted

def translate_asset_error(exc: AssetError) -> HarnessError:
    extra: dict[str, Any] = {}
    if exc.details is not None:
        extra["details"] = exc.details
    if exc.next_action:
        extra["next_action"] = exc.next_action
    return HarnessError(str(exc), code=exc.code, exit_code=1, extra_payload=extra)

def raise_aggregated_errors(
    errors: Sequence[HarnessError], *, summary: str
) -> None:
    if not errors:
        return
    first = errors[0]
    rendered: list[dict[str, Any]] = []
    for exc in errors:
        entry: dict[str, Any] = {"code": exc.code, "message": str(exc)}
        for key in ("expected", "next_action", "details", "differences"):
            if key in exc.extra_payload:
                entry[key] = exc.extra_payload[key]
        rendered.append(entry)
    raise HarnessError(
        str(first),
        code=first.code,
        exit_code=first.exit_code,
        extra_payload={"errors": rendered, "error_count": len(errors)},
        next_action="逐条修复 errors 后重跑；不确定时先加 --dry-run 预检",
    )

def knowledge_query(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = safe_target(args.target)
    _, scopes, tokens = validate_knowledge_query_args(args)
    try:
        managed = query_knowledge_assets(target, tokens, args.limit, args.max_chars)
    except AssetError as exc:
        raise translate_asset_error(exc) from exc
    used = sum(len(item["text"]) for item in managed["facts"])
    remaining_limit = max(0, args.limit - len(managed["facts"]))
    remaining_chars = max(0, args.max_chars - used)
    document_facts, document_omitted = document_knowledge_query(
        target, scopes, tokens, remaining_limit, remaining_chars
    )
    omitted = managed.get("omitted", 0) + document_omitted
    facts = [*managed["facts"], *document_facts]
    refs = [item["ref"] for item in facts]
    return 0, {
        "mode": "knowledge_assist",
        "facts": facts,
        "refs": refs,
        "constraints": active_adr_constraints(target),
        "conflicts": managed["conflicts"],
        "conflict_check": "managed_knowledge_assets_evaluated",
        "omitted": {"count": omitted, "reason": "limit_or_character_budget" if omitted else None},
        "source_priority": "current_source_and_runtime_remain_authoritative",
    }

# knowledge create|update --input 的 --help 示例（校验在 knowledge_assets；改 schema 同步此处）。
KNOWLEDGE_INPUT_EXAMPLE = {
    "schema_version": KNOWLEDGE_INPUT_SCHEMA,
    "title": "单行标题",
    "key_symbols": ["2-4 个唯一符号，不含反引号"],
    "summary": "单行摘要",
    "facts": [
        {
            "id": "小写fact.id",
            "statement": "单行事实",
            "source_refs": ["项目内已存在的相对路径"],
        }
    ],
}

def knowledge_create(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.input or not args.output:
        raise HarnessError("knowledge create 必须提供 --input 与 --output", code="missing_knowledge_input")
    target = safe_target(args.target)
    value = read_json(project_input_path(target, args.input, code="invalid_knowledge_input"))
    try:
        return 0, create_knowledge_asset(target, value, args.output, utc_now())
    except AssetError as exc:
        raise translate_asset_error(exc) from exc

def knowledge_update(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.input or not args.knowledge:
        raise HarnessError("knowledge update 必须提供 --input 与 --knowledge", code="missing_knowledge_input")
    target = safe_target(args.target)
    value = read_json(project_input_path(target, args.input, code="invalid_knowledge_input"))
    try:
        return 0, update_knowledge_asset(target, args.knowledge, value, utc_now())
    except AssetError as exc:
        raise translate_asset_error(exc) from exc

def knowledge_settle(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.knowledge or not args.status:
        raise HarnessError("knowledge settle 必须提供 --knowledge 与 --status", code="missing_knowledge_input")
    target = safe_target(args.target)
    try:
        payload = settle_knowledge_asset(
            target, args.knowledge, args.status, args.replacement, utc_now(), plan_check_markdown_files(target)
        )
    except AssetError as exc:
        raise translate_asset_error(exc) from exc
    return 0, payload

def knowledge_check(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = safe_target(args.target)
    try:
        payload = check_knowledge_assets(target)
    except AssetError as exc:
        raise translate_asset_error(exc) from exc
    return (0 if payload["status"] == "passed" else 1), payload

def unique_fields(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        field_id = item.get("id")
        if isinstance(field_id, str) and field_id not in seen:
            seen.add(field_id)
            normalized = {
                "id": field_id,
                "label": item.get("label", field_id),
                "required": bool(item.get("required")),
            }
            guidance = item.get("guidance")
            if isinstance(guidance, str) and guidance.strip():
                normalized["guidance"] = guidance.strip()
            result.append(normalized)
    return result

def plan_select(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = safe_target(args.target)
    if getattr(args, "query", None):
        raise HarnessError(
            "plan select 不支持 --query；查询项目事实请使用 knowledge query --query <关键词>。"
            "下一步：去掉 --query 重跑 plan select（可加 --task <任务描述> 让结果自动附带相关 Knowledge）",
            code="invalid_plan_selection",
        )
    level, profile, secondary, reason = resolve_plan_choice(args)
    selection = build_plan_selection(
        level=level,
        profile=profile,
        secondary_profiles=secondary,
        reason=reason,
    )
    payload: dict[str, Any] = dict(selection)
    payload["selection_ref"] = selection["selection_fingerprint"]
    payload["selection_cached"] = cache_plan_selection(target, selection)
    payload["content_template"] = plan_content_template(selection["fields"])
    if args.profile and args.surface != "general" and args.profile != args.surface:
        payload["selection_warnings"] = ["--profile 与 --surface 不一致，以 --profile 为准"]
    payload.update(plan_select_knowledge_payload(target, getattr(args, "task", None) or ""))
    return 0, payload

# plan select 输出的展示性附加键（不是选择合同的一部分）；agent 常把整份输出存成文件
# 传给 plan create --selection，校验前必须剥离，否则全等比较必然失败。
def nonempty_plan_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None

def plan_title_and_symbols(content: dict[str, Any]) -> tuple[str, list[str]]:
    title = content.get("title")
    symbols = content.get("key_symbols")
    if not isinstance(title, str) or not title.strip() or "\n" in title:
        raise HarnessError("方案 title 必须是单行非空字符串", code="invalid_plan_content")
    if (
        not isinstance(symbols, list)
        or not 2 <= len(symbols) <= 4
        or len(symbols) != len(set(symbols))
        or any(
            not isinstance(item, str)
            or not item.strip()
            or "`" in item
            or "\n" in item
            for item in symbols
        )
    ):
        raise HarnessError("方案 key_symbols 必须是 2-4 个唯一单行字符串", code="invalid_plan_content")
    return title.strip(), [item.strip() for item in symbols]

def plan_output_pair(target: Path, raw: str) -> tuple[Path, Path]:
    output = project_output_path(target, raw, code="invalid_plan_output")
    relative = output.relative_to(target)
    if relative.parent.as_posix() != PLAN_DOCS_RELATIVE or output.suffix != ".json":
        raise HarnessError(
            "方案输出必须是 docs/plans/<name>.json",
            code="invalid_plan_output",
        )
    document = project_output_path(
        target,
        f"{PLAN_DOCS_RELATIVE}/{output.stem}.md",
        code="invalid_plan_output",
    )
    return output, document

def render_plan_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"

def render_plan_markdown(
    selection: dict[str, Any],
    content: dict[str, Any],
    frozen: dict[str, Any],
    *,
    banner_status: str = PLAN_STATUS_ACTIVE,
) -> str:
    title, symbols = plan_title_and_symbols(content)
    labels = {item["id"]: item["label"] for item in selection["fields"]}
    sections = []
    for field in selection["fields"]:
        field_id = field["id"]
        if field_id in {"title", "key_symbols"} or field_id not in content:
            continue
        sections.append(f"## {labels[field_id]}\n\n{render_plan_value(content[field_id])}")
    symbol_text = "、".join(f"`{symbol}`" for symbol in symbols)
    notice = plan_amend_notice_line(frozen)
    return (
        f"> 状态：{banner_status}\n{notice}{PLAN_DOCUMENT_MARKER}\n\n# {title}\n\n"
        f"- 冻结合同：`{frozen['plan_fingerprint']}`\n"
        f"- 关键符号：{symbol_text}\n\n" + "\n\n".join(sections)
        + "\n\n" + render_governance_block(frozen) + "\n"
    )

def render_plan_index_entry(
    *, basename: str, title: str, symbols: Sequence[str], status: str
) -> str:
    symbol_text = "、".join(f"`{symbol}`" for symbol in symbols)
    return (
        f"- [{title}](plans/{basename}.md) — 状态：{status}；"
        f"关键符号：{symbol_text}"
    )

def comparable_plan(value: dict[str, Any]) -> dict[str, Any]:
    comparable = dict(value)
    comparable.pop("frozen_at", None)
    comparable.pop("plan_fingerprint", None)
    return comparable

def preflight_plan_artifacts(
    output: Path, frozen: dict[str, Any]
) -> dict[str, Any]:
    if output.is_file():
        existing = validate_frozen_plan(read_json(output))
        if comparable_plan(existing) != comparable_plan(frozen):
            raise HarnessError(
                "方案输出已存在且内容不同",
                code="plan_already_frozen",
                exit_code=3,
            )
        frozen = existing
    return frozen

# plan create --content 字段动态（由 plan select 的 fields 决定，校验在 validate_plan_create_payload）；
# 下面是 brief 级最小 --help 示例。
PLAN_CONTENT_EXAMPLE = {
    "title": "方案标题",
    "key_symbols": ["2-4 个唯一符号"],
    "objective": "目标",
    "scope": "范围",
    "steps": "关键步骤",
    "acceptance": "验收方案",
}
PLAN_CONTENT_NOTES = (
    "content 字段以 plan select 输出的 fields 为准；上面是 brief 级最小示例。",
    "required=true 的字段必须非空，且不得出现未在 fields 中注册的字段；次级 Profile 字段默认 optional。",
    "不传 --selection 时，可直接用 --level/--profile/--complexity/--surface 让 plan create 生成选择；"
    "也可以传 plan select 输出的 selection_ref（sha256:<指纹>）或保存后的 selection JSON。",
    "plan select 输出的 content_template 是可直接填写的骨架；正式冻结前建议先跑 plan create --dry-run。",
)

def plan_create_selection_requested(args: argparse.Namespace) -> bool:
    return bool(
        args.level
        or args.profile
        or (args.surface and args.surface != "general")
        or args.secondary_profile
        or args.cross_module
        or args.high_risk
        or args.user_requested_plan
        or args.complexity != "simple"
    )

def resolve_plan_create_selection(
    target: Path, args: argparse.Namespace
) -> tuple[dict[str, Any], str, bool]:
    if args.selection:
        return (
            validate_selection(read_json(resolve_plan_selection_path(target, args.selection))),
            "file",
            False,
        )
    if not plan_create_selection_requested(args):
        raise HarnessError(
            "plan create 需要 --selection，或直接提供 --level/--profile/--complexity 选择模板",
            code="missing_plan_selection",
            extra_payload={
                "next_action": "加 --selection <plan select 输出>，或加 --level/--profile/--complexity 直接创建",
            },
        )
    level, profile, secondary, reason = resolve_plan_choice(args)
    selection = build_plan_selection(
        level=level,
        profile=profile,
        secondary_profiles=secondary,
        reason=reason,
    )
    return selection, "direct", cache_plan_selection(target, selection)

def inspect_plan_create(
    target: Path, args: argparse.Namespace
) -> tuple[list[HarnessError], dict[str, Any] | None]:
    """收集 plan create 的全部输入错误（--dry-run 用）；无错误时返回各阶段产物。

    依赖失败的阶段会被跳过；错误追加顺序与原逐字段抛错的不可达路径一致。"""
    errors: list[HarnessError] = []
    selection: dict[str, Any] | None = None
    selection_source: str | None = None
    selection_cached = False
    try:
        selection, selection_source, selection_cached = resolve_plan_create_selection(target, args)
        if selection["plan_level"] == "none":
            errors.append(
                HarnessError("plan_level=none 不创建方案文件", code="plan_not_required")
            )
    except HarnessError as exc:
        errors.append(exc)
    content: dict[str, Any] | None = None
    if not args.content:
        errors.append(
            HarnessError(
                "plan create 缺少 --content",
                code="missing_plan_input",
                extra_payload={"next_action": "把 content JSON 写入 .docs-harness/inputs/ 后传 --content"},
            )
        )
    else:
        try:
            raw_content = read_json(
                project_input_path(target, args.content, code="invalid_plan_content")
            )
            if not isinstance(raw_content, dict):
                raise HarnessError("方案内容必须是 JSON 对象", code="invalid_plan_content")
            content = raw_content
        except HarnessError as exc:
            errors.append(exc)
    title: str | None = None
    symbols: list[str] | None = None
    if selection is not None and content is not None:
        errors.extend(collect_plan_content_errors(selection, content))
        try:
            title, symbols = plan_title_and_symbols(content)
        except HarnessError:
            title, symbols = None, None
    output: Path | None = None
    document: Path | None = None
    if not args.output:
        errors.append(
            HarnessError(
                "plan create 缺少 --output",
                code="missing_plan_input",
                extra_payload={"next_action": "传 --output docs/plans/<name>.json"},
            )
        )
    else:
        try:
            output, document = plan_output_pair(target, args.output)
        except HarnessError as exc:
            errors.append(exc)
    if (
        errors
        or selection is None or content is None
        or output is None or document is None
        or title is None or symbols is None
    ):
        return errors, None
    return [], {
        "selection": selection,
        "selection_source": selection_source,
        "selection_cached": selection_cached,
        "content": content,
        "output": output,
        "document": document,
        "title": title,
        "symbols": symbols,
    }

def validate_plan_create_payload(
    target: Path, args: argparse.Namespace
) -> dict[str, Any]:
    errors, parts = inspect_plan_create(target, args)
    if errors:
        raise_aggregated_errors(errors, summary="plan create 校验失败")
    assert parts is not None
    return parts

def dry_run_plan_create(
    target: Path, args: argparse.Namespace
) -> tuple[int, dict[str, Any]]:
    """plan create --dry-run：整体校验并报告全部错误，含已冻结冲突，不落任何文件。"""
    errors, parts = inspect_plan_create(target, args)
    if parts is not None:
        frozen = build_frozen_plan(parts["selection"], parts["content"])
        try:
            preflight_plan_artifacts(parts["output"], frozen)
        except HarnessError as exc:
            errors.append(exc)
        markdown = render_plan_markdown(parts["selection"], parts["content"], frozen)
        document = parts["document"]
        if document.is_file() and document.read_text(encoding="utf-8") != markdown:
            errors.append(HarnessError(
                "方案 Markdown 已存在且内容不同",
                code="plan_document_conflict",
                exit_code=3,
            ))
    payload = {
        "status": "dry_run_invalid" if errors else "dry_run_valid",
        "errors": [{"code": exc.code, "message": str(exc)} for exc in errors],
        "writes": "none",
    }
    return (2 if errors else 0), payload

def build_frozen_plan(selection: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    frozen: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA,
        "plan_level": selection["plan_level"],
        "plan_profile": selection["plan_profile"],
        "secondary_profiles": selection["secondary_profiles"],
        "template_versions": selection["template_versions"],
        "selection_fingerprint": selection["selection_fingerprint"],
        "content": content,
        **new_plan_fields(selection["plan_level"], content),
        "frozen_at": utc_now(),
    }
    frozen["plan_fingerprint"] = sha256_json(frozen)
    return frozen

def write_plan_artifacts(
    target: Path,
    output: Path,
    document: Path,
    frozen: dict[str, Any],
    markdown: str,
    entry: str,
) -> None:
    apply_plan_docs_structure(target)
    if not output.is_file():
        atomic_write_json(output, frozen)
    if not document.is_file():
        atomic_write_text(document, markdown)
    index_path = target / PLAN_INDEX_RELATIVE
    index = index_path.read_text(encoding="utf-8")
    updated_index = update_plan_index_text(index, basename=output.stem, entry=entry)
    if updated_index != index:
        atomic_write_text(index_path, updated_index)

def plan_create(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = safe_target(args.target)
    if getattr(args, "dry_run", False):
        return dry_run_plan_create(target, args)
    parts = validate_plan_create_payload(target, args)
    selection = parts["selection"]
    content = parts["content"]
    output = parts["output"]
    document = parts["document"]
    title = parts["title"]
    symbols = parts["symbols"]
    frozen = preflight_plan_artifacts(output, build_frozen_plan(selection, content))
    markdown = render_plan_markdown(selection, content, frozen)
    if document.is_file() and document.read_text(encoding="utf-8") != markdown:
        raise HarnessError(
            "方案 Markdown 已存在且内容不同",
            code="plan_document_conflict",
            exit_code=3,
        )
    entry = render_plan_index_entry(
        basename=output.stem, title=title, symbols=symbols, status=PLAN_STATUS_ACTIVE
    )
    write_plan_artifacts(target, output, document, frozen, markdown, entry)
    return 0, {
        "status": "frozen",
        "plan_ref": output.relative_to(target).as_posix(),
        "document_ref": document.relative_to(target).as_posix(),
        "index_ref": PLAN_INDEX_RELATIVE,
        "plan_fingerprint": frozen["plan_fingerprint"],
        "selection_ref": frozen.get("selection_fingerprint"),
        "contract_ref": selection.get("contract_fingerprint"),
        "selection_source": parts["selection_source"],
        "selection_cached": parts["selection_cached"],
        "execution_projection": dict(content),
        "next_action": "execute_plan_then_plan_settle",
    }

def plan_amend(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    from plan_amend import run_plan_amend  # noqa: PLC0415

    return run_plan_amend(args)

def plan_settle_paths(target: Path, raw: str) -> tuple[Path | None, Path, bool]:
    relative = Path(raw)
    if relative.is_absolute() or relative.suffix not in {".json", ".md"}:
        raise HarnessError("--plan 必须指向项目内方案 JSON 或 Markdown", code="invalid_plan_ref")
    basename = relative.stem
    if not basename or "/" in basename:
        raise HarnessError("方案文件名无效", code="invalid_plan_ref")
    root_json = target / PLAN_DOCS_RELATIVE / f"{basename}.json"
    root_markdown = target / PLAN_DOCS_RELATIVE / f"{basename}.md"
    archive_json = target / PLAN_ARCHIVE_RELATIVE / f"{basename}.json"
    archive_markdown = target / PLAN_ARCHIVE_RELATIVE / f"{basename}.md"
    if root_json.is_file() and root_markdown.is_file():
        return root_json, root_markdown, False
    if archive_json.is_file() and archive_markdown.is_file():
        return archive_json, archive_markdown, True
    # 手写方案：无伴随 JSON、无 Harness 文档标记、前 3 行有状态横幅；JSON 位返回 None。
    for markdown, archived in ((root_markdown, False), (archive_markdown, True)):
        if markdown.is_file() and not markdown.with_suffix(".json").exists() and plan_check_banner(markdown):
            if PLAN_DOCUMENT_MARKER not in markdown.read_text(encoding="utf-8").splitlines()[:3]:
                return None, markdown, archived
    raise HarnessError(
        "方案 JSON 与 Markdown 伴随文件不完整或不存在；手写方案须无 Harness 文档标记且前 3 行有状态横幅",
        code="invalid_plan_ref",
    )

def replace_plan_status_banner(text: str, status: str, *, managed: bool = True) -> str:
    """改写前 3 行内的状态横幅；首个「｜」之后的附注（架构决策、真源链接等）原样保留。"""
    lines = text.splitlines()
    if managed and PLAN_DOCUMENT_MARKER not in lines[:3]:
        raise HarnessError("方案缺少 Harness 文档标记", code="invalid_plan_document")
    for index, line in enumerate(lines[:3]):
        if PLAN_CHECK_BANNER_MARKER in line:
            _, bar, notes = line.partition("｜")
            lines[index] = f"> 状态：{status}{bar}{notes}"
            return "\n".join(lines) + "\n"
    raise HarnessError("方案缺少状态横幅", code="invalid_plan_document")

def validate_frozen_plan(value: Any) -> dict[str, Any]:
    try:
        return validate_governed_plan(value)
    except PlanGovernanceError as exc:
        raise HarnessError(str(exc), code=exc.code) from exc

def settled_plan_identity(
    content: dict[str, Any], markdown: str, index: str, basename: str
) -> tuple[str, list[str]]:
    """2.5 新合同优先；旧冻结方案从可审查文档与索引迁移读取身份。"""
    try:
        return plan_title_and_symbols(content)
    except HarnessError:
        title_match = re.search(r"(?m)^#\s+(.+)$", markdown)
        entries = [
            line for line in plan_index_entry_lines(index)
            if f"(plans/{basename}.md)" in line
        ]
        symbols = (
            re.findall(r"`([^`]+)`", entries[0].split("关键符号", 1)[1])
            if entries and "关键符号" in entries[0]
            else []
        )
        if title_match and 2 <= len(symbols) <= 4:
            return title_match.group(1).strip(), symbols
        raise HarnessError(
            "旧方案缺少可迁移的标题或 2-4 个关键符号",
            code="invalid_plan_ref",
        )

def rewrite_archived_plan_links(target: Path, basename: str) -> list[str]:
    replacements = (
        (f"docs/plans/{basename}.md", f"docs/plans/archive/{basename}.md"),
        (f"plans/{basename}.md", f"plans/archive/{basename}.md"),
    )
    changed: list[str] = []
    for path in plan_check_markdown_files(target):
        before = path.read_text(encoding="utf-8")
        after = before
        for old, new in replacements:
            after = after.replace(old, new)
        if after != before:
            atomic_write_text(path, after)
            changed.append(path.relative_to(target).as_posix())
    return changed

def settle_implemented_plan(
    target: Path,
    plan_json: Path,
    document: Path,
    markdown: str,
    index: str,
    title: str,
    symbols: Sequence[str],
    frozen: dict[str, Any],
) -> list[str]:
    basename = plan_json.stem
    today = dt.date.today().isoformat()
    status = f"{PLAN_STATUS_IMPLEMENTED}（代码已是真源，{today} 核对）"
    projected = update_plan_markdown(markdown, frozen)
    updated = replace_plan_status_banner(projected, status)
    entry = render_plan_index_entry(
        basename=basename, title=title, symbols=symbols, status=status
    )
    index_path = target / PLAN_INDEX_RELATIVE
    updated_index = update_plan_index_text(index, basename=basename, entry=entry)
    changed: list[str] = []
    before_frozen = read_json(plan_json)
    if before_frozen != frozen:
        atomic_write_json(plan_json, frozen)
        changed.append(plan_json.relative_to(target).as_posix())
    for path, before, after in ((document, markdown, updated), (index_path, index, updated_index)):
        if before != after:
            atomic_write_text(path, after)
            changed.append(path.relative_to(target).as_posix())
    return changed

def settle_deprecated_plan(
    target: Path,
    plan_json: Path | None,
    document: Path,
    archived: bool,
    markdown: str,
    index: str,
    replacement: str,
) -> tuple[Path | None, Path, list[str]]:
    if "\n" in replacement:
        raise HarnessError("--replacement 必须是单行方案引用", code="invalid_plan_transition")
    basename = document.stem
    today = dt.date.today().isoformat()
    status = (
        f"{PLAN_STATUS_DEPRECATED}-被 {replacement} 取代（{today} 核对）"
        if replacement
        else f"{PLAN_STATUS_DEPRECATED}（{today} 核对，无替代方案）"
    )
    updated = replace_plan_status_banner(markdown, status, managed=plan_json is not None)
    updated_index = update_plan_index_text(index, basename=basename)
    changed: list[str] = []
    if archived:
        if updated != markdown:
            atomic_write_text(document, updated)
            changed.append(document.relative_to(target).as_posix())
    else:
        archive_json = target / PLAN_ARCHIVE_RELATIVE / f"{basename}.json"
        archive_document = target / PLAN_ARCHIVE_RELATIVE / document.name
        if archive_json.exists() or archive_document.exists():
            raise HarnessError("归档目标已存在", code="plan_archive_conflict", exit_code=3)
        atomic_write_text(document, updated)
        if plan_json is not None:
            plan_json = plan_json.replace(archive_json)
            changed.append(plan_json.relative_to(target).as_posix())
        document = document.replace(archive_document)
        changed.append(document.relative_to(target).as_posix())
    if updated_index != index:
        index_path = target / PLAN_INDEX_RELATIVE
        atomic_write_text(index_path, updated_index)
        changed.append(PLAN_INDEX_RELATIVE)
    changed.extend(rewrite_archived_plan_links(target, basename))
    try:
        changed.extend(follow_archived_refs(target, PLAN_DOCS_RELATIVE, basename))
    except AssetError as exc:
        raise translate_asset_error(exc) from exc
    return plan_json, document, list(dict.fromkeys(changed))

def settle_handwritten_plan(
    target: Path, args: argparse.Namespace, document: Path, archived: bool
) -> tuple[int, dict[str, Any]]:
    """无冻结 JSON 的手写方案结算：只改横幅，deprecated 另归档并改写链接，不做治理终验。

    受管方案区块外的 INDEX 条目属项目正文，不改写，只在 warnings 提示手工同步。
    """
    if args.governance_input:
        raise HarnessError(
            "手写方案没有冻结治理合同，不接受 --governance-input", code="invalid_plan_transition"
        )
    apply_plan_docs_structure(target)
    index = (target / PLAN_INDEX_RELATIVE).read_text(encoding="utf-8")
    markdown = document.read_text(encoding="utf-8")
    basename = document.stem
    in_block = any(f"(plans/{basename}.md)" in line for line in plan_index_entry_lines(index))
    warnings = [] if in_block else [
        f"docs/INDEX.md 中 {document.name} 的条目不在受管方案区块内，状态文字与路径需手工同步"
    ]
    replacement = args.replacement.strip() if isinstance(args.replacement, str) else ""
    if args.status == "deprecated":
        _, document, changed = settle_deprecated_plan(
            target, None, document, archived, markdown, index, replacement
        )
    elif archived:
        raise HarnessError("已归档方案不能重新标记为已实施", code="invalid_plan_transition")
    else:
        status = f"{PLAN_STATUS_IMPLEMENTED}（代码已是真源，{dt.date.today().isoformat()} 核对）"
        identity = settled_plan_identity({}, markdown, index, basename) if in_block else None
        atomic_write_text(document, replace_plan_status_banner(markdown, status, managed=False))
        changed = [document.relative_to(target).as_posix()]
        if identity:
            entry = render_plan_index_entry(
                basename=basename, title=identity[0], symbols=identity[1], status=status
            )
            atomic_write_text(
                target / PLAN_INDEX_RELATIVE,
                update_plan_index_text(index, basename=basename, entry=entry),
            )
            changed.append(PLAN_INDEX_RELATIVE)
    return 0, {
        "status": args.status, "plan_ref": None, "handwritten": True,
        "document_ref": document.relative_to(target).as_posix(),
        "replacement": replacement or None, "changed": changed, "warnings": warnings,
    }

# plan settle --governance-input 的 --help 示例（校验在 plan_governance；改 schema 同步此处）。
PLAN_GOVERNANCE_INPUT_EXAMPLE = {
    "schema_version": PLAN_GOVERNANCE_INPUT_SCHEMA,
    "updated_knowledge_refs": ["knowledge_impact=updated 时必填非空"],
    "unchanged_reason": "unchanged 时必填",
}

def plan_settle(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = safe_target(args.target)
    if not args.plan or args.status not in PLAN_SETTLE_STATUSES:
        raise HarnessError(
            "plan settle 必须提供 --plan 与 --status implemented|deprecated",
            code="missing_plan_input",
        )
    plan_json, document, archived = plan_settle_paths(target, args.plan)
    if plan_json is None:
        return settle_handwritten_plan(target, args, document, archived)
    frozen = validate_frozen_plan(read_json(plan_json))
    content = frozen.get("content")
    if not isinstance(content, dict):
        raise HarnessError("方案内容无效", code="invalid_plan_ref")
    apply_plan_docs_structure(target)
    index = (target / PLAN_INDEX_RELATIVE).read_text(encoding="utf-8")
    markdown = document.read_text(encoding="utf-8")
    title, symbols = settled_plan_identity(content, markdown, index, plan_json.stem)
    if args.status == "implemented":
        if archived:
            raise HarnessError("已归档方案不能重新标记为已实施", code="invalid_plan_transition")
        governance_input = None
        if args.governance_input:
            governance_input = read_json(
                project_input_path(
                    target, args.governance_input, code="invalid_plan_governance"
                )
            )
        try:
            frozen, warnings = prepare_plan_settlement(
                target, frozen, governance_input, utc_now()
            )
        except PlanGovernanceError as exc:
            raise HarnessError(str(exc), code=exc.code) from exc
        changed = settle_implemented_plan(
            target, plan_json, document, markdown, index, title, symbols, frozen
        )
        return 0, {
            "status": "implemented",
            "plan_ref": plan_json.relative_to(target).as_posix(),
            "document_ref": document.relative_to(target).as_posix(),
            "changed": changed,
            "warnings": warnings,
        }
    replacement = args.replacement.strip() if isinstance(args.replacement, str) else ""
    plan_json, document, changed = settle_deprecated_plan(
        target, plan_json, document, archived, markdown, index, replacement
    )
    return 0, {
        "status": "deprecated",
        "plan_ref": plan_json.relative_to(target).as_posix(),
        "document_ref": document.relative_to(target).as_posix(),
        "replacement": replacement or None,
        "changed": changed,
    }

def evidence_path(target: Path, raw: str) -> Path:
    source = Path(raw)
    path = source if source.is_absolute() else target / source
    return ensure_within(target, path, code="acceptance_evidence_outside_project")

def assert_evidence_usable(target: Path, refs: list[str], what: str) -> None:
    """证据必须此刻存在，且不能落在 git 忽略路径。

    只查"存在"是不够的：写在 .gitignore 覆盖目录（如 build/）里的证据登记时
    照样通过，却不会进仓库，本地一清理引用就永久失效，assets-check 只能在
    失效之后报警。这里在登记入口直接拒绝，把事后尸检换成事前疫苗。
    """
    for ref in refs:
        if not evidence_path(target, ref).is_file():
            raise HarnessError(f"{what}不存在：{ref}", code="acceptance_evidence_missing")
    ignored = git_ignored_refs(target, refs)
    if ignored:
        raise HarnessError(
            f"{what}落在 git 忽略路径，提交后必然失效："
            + "、".join(ignored)
            + "；请改存到随仓库提交的位置，例如 docs/acceptance/evidence/<验收名>/",
            code="acceptance_evidence_ignored",
        )

def normalize_failure_attributions(target: Path, raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise HarnessError(
            "失败验收必须提供非空 failure_attributions 数组",
            code="invalid_acceptance_input",
        )
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise HarnessError("failure_attributions 每项必须是对象", code="invalid_acceptance_input")
        category = item.get("category")
        summary = item.get("summary")
        blocking = item.get("blocking")
        refs = item.get("evidence_refs")
        if category not in FAILURE_ATTRIBUTION_CATEGORIES:
            raise HarnessError("失败归因类别无效，仅接受：" + "、".join(sorted(FAILURE_ATTRIBUTION_CATEGORIES)), code="invalid_acceptance_input")
        if not isinstance(summary, str) or not summary.strip():
            raise HarnessError("失败归因 summary 不能为空", code="invalid_acceptance_input")
        if not isinstance(blocking, bool):
            raise HarnessError("失败归因 blocking 必须是布尔值", code="invalid_acceptance_input")
        if not nonempty_string_list(refs):
            raise HarnessError(
                "失败归因 evidence_refs 必须是非空字符串数组",
                code="invalid_acceptance_input",
            )
        key = (category, summary.strip())
        if key in seen:
            raise HarnessError("失败归因不得重复", code="invalid_acceptance_input")
        seen.add(key)
        assert_evidence_usable(target, refs, "失败归因证据")
        normalized.append(
            {
                "category": category,
                "summary": summary.strip(),
                "blocking": blocking,
                "evidence_refs": list(refs),
            }
        )
    return normalized

# acceptance record --input 的 --help 示例（校验就在下方本函数内；改 schema 同步此处）。
ACCEPTANCE_INPUT_EXAMPLE = {
    "schema_version": ACCEPTANCE_INPUT_SCHEMA,
    "objective": "与目标一致",
    "criterion_id": "关联 --acceptance 时必填",
    "acceptance_type": "contract_check|behavior_acceptance|user_acceptance",
    "status": "passed|failed|unverified|user_pending",
    "layer": "L1-L5",
    "evidence_layer": "仅 behavior_acceptance 可填；与 criterion 同 L 层即可，不要求字面一致",
    "method": "passed 必填",
    "evidence_refs": ["已存在的证据文件"],
    "confirmation": "用户确认原话，配合 --user-confirmed",
}
ACCEPTANCE_INPUT_NOTES = (
    "按状态必填：",
    "  passed（behavior/contract）→ method + 非空 evidence_refs；",
    "  failed → reason + next_action + failure_attributions[]"
    "（每项 {category, summary, blocking, evidence_refs}）；",
    "  user_pending → automatically_verified / user_checks / steps（≤5）"
    "三个非空字符串数组 + environment_ready=true。",
    "记录的 evidence_layer 与 criterion 同 L 层即接受，不要求字面一致"
    "（映射仍为 focused_test/repository_full_test→L2、local_runtime→L3、"
    "package_or_install→L4、real_device→L5）。",
)

def build_stored_acceptance_record(
    target: Path,
    value: dict[str, Any],
    objective: str,
    *,
    associated: bool,
    user_confirmed: bool,
) -> dict[str, Any]:
    """校验单条验收记录字段（形状 + 按状态必填 + 证据存在 + 用户确认规则）并构造 stored 记录。

    供 acceptance record 单条路径与 acceptance settle --input 批量路径共用。
    schema_version / criterion_id / objective 是否存在由各自入口负责；
    objective 已由调用方校验为非空并原样用于 objective_fingerprint。
    associated=是否关联 Acceptance 资产（对应单条路径的 args.acceptance）。"""
    acceptance_type = value.get("acceptance_type")
    status = value.get("status")
    layer = value.get("layer")
    evidence_layer = value.get("evidence_layer")
    if acceptance_type not in {"contract_check", "behavior_acceptance", "user_acceptance"}:
        raise HarnessError("验收类型无效，仅接受 contract_check|behavior_acceptance|user_acceptance", code="invalid_acceptance_input")
    if status not in {"passed", "failed", "unverified", "user_pending"} or layer not in ACCEPTANCE_LAYERS:
        raise HarnessError("验收状态或层级无效，status 仅接受 passed|failed|unverified|user_pending，layer 仅接受 L1-L5", code="invalid_acceptance_input")
    if acceptance_type == "behavior_acceptance":
        expected_layer = ACCEPTANCE_EVIDENCE_LAYERS.get(evidence_layer)
        if expected_layer is None:
            raise HarnessError("行为验收必须声明有效 evidence_layer", code="invalid_acceptance_input")
        if layer != expected_layer:
            raise HarnessError(
                f"evidence_layer={evidence_layer} 必须记录在 {expected_layer}",
                code="invalid_acceptance_input",
            )
    elif evidence_layer is not None:
        raise HarnessError(
            "只有 behavior_acceptance 可以声明 evidence_layer",
            code="invalid_acceptance_input",
        )
    refs = value.get("evidence_refs", [])
    if not isinstance(refs, list) or any(
        not isinstance(item, str) or not item for item in refs
    ):
        raise HarnessError("evidence_refs 必须是字符串数组", code="invalid_acceptance_input")
    if acceptance_type == "behavior_acceptance" and status == "passed":
        if layer not in {"L2", "L3", "L4", "L5"}:
            raise HarnessError("行为验收通过必须位于 L2-L5", code="invalid_acceptance_input")
        if (
            not isinstance(value.get("method"), str)
            or not value["method"].strip()
            or not refs
        ):
            raise HarnessError("行为验收通过必须提供方法和证据", code="invalid_acceptance_input")
    if acceptance_type == "contract_check" and status == "passed" and layer != "L1":
        raise HarnessError("Contract Check 通过必须是 L1", code="invalid_acceptance_input")
    if acceptance_type == "contract_check" and status == "passed":
        if (
            not isinstance(value.get("method"), str)
            or not value["method"].strip()
            or not refs
        ):
            raise HarnessError(
                "Contract Check 通过必须提供方法和证据",
                code="invalid_acceptance_input",
            )
    user_confirmation: dict[str, str] | None = None
    if acceptance_type == "user_acceptance" and status == "passed":
        confirmation = value.get("confirmation")
        if not (
            associated
            and user_confirmed
            and isinstance(confirmation, str)
            and confirmation.strip()
        ):
            raise HarnessError(
                "用户验收通过必须关联 Acceptance，并在收到用户明确确认后使用 --user-confirmed",
                code="user_confirmation_required",
                exit_code=3,
            )
        user_confirmation = {
            "confirmed_by": "user",
            "confirmation": confirmation.strip(),
        }
    if status == "user_pending":
        required_lists = ("automatically_verified", "user_checks", "steps")
        if (
            acceptance_type != "user_acceptance"
            or layer != "L5"
            or value.get("environment_ready") is not True
            or any(
                not isinstance(value.get(key), list)
                or not value[key]
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in value[key]
                )
                for key in required_lists
            )
        ):
            raise HarnessError("用户验收交接不完整，须 acceptance_type=user_acceptance、layer=L5、environment_ready=true 且 automatically_verified/user_checks/steps 三个非空字符串数组", code="invalid_acceptance_input")
        if len(value["steps"]) > 5:
            raise HarnessError("用户验收步骤必须保持最短，最多 5 步", code="invalid_acceptance_input")
    if status == "passed":
        assert_evidence_usable(target, refs, "验收证据")
    failure_attributions: list[dict[str, Any]] = []
    if status == "failed":
        reason = value.get("reason")
        next_action = value.get("next_action")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(next_action, str)
            or not next_action.strip()
        ):
            raise HarnessError("失败验收必须给出原因和下一步", code="invalid_acceptance_input")
        failure_attributions = normalize_failure_attributions(
            target, value.get("failure_attributions")
        )
    elif value.get("failure_attributions") is not None:
        raise HarnessError(
            "只有 failed 状态可以声明 failure_attributions",
            code="invalid_acceptance_input",
        )
    record_id = (
        "acc-"
        + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:10]
    )
    stored: dict[str, Any] = {
        "schema_version": ACCEPTANCE_RECORD_SCHEMA,
        "record_id": record_id,
        "objective_fingerprint": sha256_bytes(objective.encode("utf-8")),
        "acceptance_type": acceptance_type,
        "status": status,
        "layer": layer,
        "evidence_layer": evidence_layer,
        "method": value.get("method"),
        "evidence_refs": refs,
        "reason": value.get("reason"),
        "next_action": value.get("next_action"),
        "failure_attributions": failure_attributions,
        "recorded_at": utc_now(),
    }
    if user_confirmation:
        stored["user_confirmation"] = user_confirmation
    if status == "user_pending":
        stored["user_handoff"] = {
            "automatically_verified": value["automatically_verified"],
            "user_checks": value["user_checks"],
            "steps": value["steps"],
            "environment_ready": True,
        }
    return stored

def unlinked_nonpassing_payload(
    stored: dict[str, Any],
    status: str,
    layer: str,
    evidence_layer: str | None,
    unlinked: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    if status == "failed":
        return 3, {
            "status": "failed",
            "layer": ACCEPTANCE_LAYERS[layer],
            "evidence_layer": evidence_layer,
            "reason": stored["reason"],
            "next_action": stored["next_action"],
            "failure_attributions": stored["failure_attributions"],
            **unlinked,
        }
    if status == "user_pending":
        return 3, {
            "status": "user_pending",
            "accepted_layer": None,
            "user_handoff": stored["user_handoff"],
            **unlinked,
            "next_action": "完成用户确认后带 --acceptance 重新记录",
        }
    return 3, {
        "status": "unverified",
        "accepted_layer": None,
        "next_action": stored["next_action"] or "run_minimum_sufficient_acceptance",
        **unlinked,
    }

def record_unlinked_acceptance(
    target: Path,
    value: dict[str, Any],
    stored: dict[str, Any],
    record_id: str,
    status: str,
    layer: str,
    evidence_layer: str | None,
    acceptance_type: str,
    dry_run: bool = False,
) -> tuple[int, dict[str, Any]]:
    if value.get("criterion_id") is not None:
        raise HarnessError(
            "验收记录带 criterion_id 但没有 --acceptance，无法关联到验收资产；"
            "下一步：先用 acceptance create 建立资产，再用 --acceptance <path> 重跑；"
            "若确实只要独立记录，去掉 criterion_id",
            code="acceptance_link_required",
            extra_payload={
                "next_action": "acceptance create 后用 --acceptance 重跑，或去掉 criterion_id",
            },
        )
    if dry_run:
        return 0, {
            "status": "dry_run_valid",
            "persisted_to": "runtime",
            "linked": False,
            "record_id": record_id,
            "writes": "none",
            "next_action": "去掉 --dry-run 后重跑以写入",
        }
    atomic_write_json(v2_acceptance_root(target) / f"{record_id}.json", stored)
    unlinked = {
        "persisted_to": "runtime",
        "linked": False,
        "record_id": record_id,
        "warning": "该记录写入本地 Runtime，不进入 docs/acceptance，也不计入 Plan 的验收；需要资产闭环请用 --acceptance",
    }
    if acceptance_type == "contract_check" and status == "passed":
        return 0, {
            "status": "contract_checked",
            "accepted_layer": None,
            "behavior_verified": False,
            **unlinked,
            "next_action": "acceptance create 后带 --acceptance 重新记录",
        }
    if status == "passed":
        return 0, {
            "status": "passed",
            "accepted_layer": layer,
            "evidence_layer": evidence_layer,
            "claim": "behavior_accepted",
            **unlinked,
            "next_action": "acceptance create 后带 --acceptance 重新记录",
        }
    return unlinked_nonpassing_payload(stored, status, layer, evidence_layer, unlinked)

def acceptance_record(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = safe_target(args.target)
    if not args.input:
        raise HarnessError("acceptance record 必须提供 --input", code="missing_acceptance_input")
    input_path = project_input_path(target, args.input, code="invalid_acceptance_input")
    value = read_json(input_path)
    if not isinstance(value, dict) or value.get("schema_version") != ACCEPTANCE_INPUT_SCHEMA:
        raise HarnessError(
            f"验收输入 Schema 无效，必须为 {ACCEPTANCE_INPUT_SCHEMA}",
            code="invalid_acceptance_input",
        )
    objective = value.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise HarnessError("验收目标不能为空", code="invalid_acceptance_input")
    stored = build_stored_acceptance_record(
        target,
        value,
        objective.strip(),
        associated=bool(args.acceptance),
        user_confirmed=bool(args.user_confirmed),
    )
    status = stored["status"]
    layer = stored["layer"]
    evidence_layer = stored["evidence_layer"]
    acceptance_type = stored["acceptance_type"]
    record_id = stored["record_id"]
    if args.acceptance:
        criterion_id = value.get("criterion_id")
        if not isinstance(criterion_id, str) or not criterion_id.strip():
            raise HarnessError(
                "关联 Acceptance 的记录必须提供 criterion_id",
                code="acceptance_criterion_missing",
            )
        try:
            result = record_acceptance_asset(
                target,
                args.acceptance,
                criterion_id.strip(),
                stored,
                utc_now(),
                bool(args.reaccept),
                dry_run=bool(args.dry_run),
            )
        except AssetError as exc:
            raise translate_asset_error(exc) from exc
        if args.dry_run:
            return 0, {
                **result,
                "record_id": record_id,
                "persisted_to": "asset",
                "linked": True,
            }
        return 0 if result["status"] == "passed" else 3, {
            **result,
            "record_id": record_id,
            "accepted_layer": layer if status == "passed" else None,
            "evidence_layer": evidence_layer,
            "persisted_to": "asset",
            "linked": True,
        }
    return record_unlinked_acceptance(
        target, value, stored, record_id, status, layer, evidence_layer, acceptance_type,
        dry_run=bool(args.dry_run),
    )

def acceptance_amend(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.acceptance or not args.input or not args.reason:
        raise HarnessError(
            "acceptance amend 必须提供 --acceptance、--input 与 --reason",
            code="missing_acceptance_amend_input",
            extra_payload={"next_action": "用与 acceptance create 相同的输入形状，并加 --reason"},
        )
    target = safe_target(args.target)
    value = read_json(project_input_path(target, args.input, code="invalid_acceptance_target"))
    try:
        return 0, amend_acceptance_asset(
            target, args.acceptance, value, args.reason, utc_now(),
            dry_run=bool(args.dry_run),
        )
    except AssetError as exc:
        raise translate_asset_error(exc) from exc

# acceptance create --input 的 --help 示例（校验在 acceptance_assets；改 schema 同步此处）。
ACCEPTANCE_TARGET_INPUT_EXAMPLE = {
    "schema_version": ACCEPTANCE_TARGET_INPUT_SCHEMA,
    "title": "…",
    "key_symbols": ["…"],
    "objective": "…",
    "plan_ref": "docs/plans/x.json（可选）",
    "knowledge_refs": [],
    "criteria": [
        {
            "id": "c1",
            "title": "…",
            "acceptance_type": "contract_check|behavior_acceptance|user_acceptance",
            "layer": "L1-L5",
            "evidence_layer": "仅 behavior_acceptance 填写，且映射必须等于 layer",
        }
    ],
}
ACCEPTANCE_TARGET_INPUT_NOTES = (
    "耦合：contract_check=L1 且无 evidence_layer；user_acceptance=L5 且无 evidence_layer；",
    "behavior_acceptance 的 evidence_layer 映射 focused_test/repository_full_test→L2、"
    "local_runtime→L3、package_or_install→L4、real_device→L5。",
    "正式创建前建议先跑 acceptance create --dry-run（同参数）：一次性返回全部字段错误，不写任何文件。",
)

def acceptance_create(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.input or not args.output:
        raise HarnessError(
            "acceptance create 必须提供 --input 与 --output",
            code="missing_acceptance_target",
        )
    target = safe_target(args.target)
    value = read_json(project_input_path(target, args.input, code="invalid_acceptance_target"))
    if getattr(args, "dry_run", False):
        try:
            errors = collect_acceptance_create_errors(target, value, args.output)
        except AssetError as exc:
            raise translate_asset_error(exc) from exc
        payload = {
            "status": "dry_run_invalid" if errors else "dry_run_valid",
            "errors": [{"code": exc.code, "message": str(exc)} for exc in errors],
            "writes": "none",
        }
        return (1 if errors else 0), payload
    try:
        errors = collect_acceptance_create_errors(target, value, args.output)
    except AssetError as exc:
        raise translate_asset_error(exc) from exc
    if errors:
        raise_aggregated_errors(
            [translate_asset_error(exc) for exc in errors],
            summary="acceptance create 校验失败",
        )
    try:
        return 0, create_acceptance_asset(target, value, args.output, utc_now())
    except AssetError as exc:
        raise translate_asset_error(exc) from exc

# acceptance settle --input 的 --help 示例（顶层形状校验在 read_settle_input，逐条记录复用
# build_stored_acceptance_record；改 schema 同步此处）。
ACCEPTANCE_SETTLE_INPUT_EXAMPLE = {
    "schema_version": ACCEPTANCE_SETTLE_INPUT_SCHEMA,
    "objective": "必须与资产 objective 一致",
    "records": [
        {
            "criterion_id": "c1（必须当前 pending；已验收改写走 --reaccept）",
            "acceptance_type": "contract_check|behavior_acceptance|user_acceptance",
            "status": "passed|failed|user_pending",
            "layer": "L1-L5",
            "evidence_layer": "仅 behavior_acceptance 填写，与 criterion 同 L 层",
            "method": "passed 必填",
            "evidence_refs": ["已存在的证据文件"],
            "confirmation": "user_acceptance passed 配合 --user-confirmed",
        }
    ],
}
ACCEPTANCE_SETTLE_INPUT_NOTES = (
    "records 每项 = acceptance record 单条输入去掉 objective（objective 提到顶层，必须与资产一致）；",
    "records 为空数组等价于不传 --input；单条记录按状态必填规则与 acceptance record 完全一致；",
    "records 内 criterion_id 不得重复，且目标 criterion 必须当前 pending；",
    "成功时返回 payload 追加 recorded/record_ids 便于核对。",
) + ACCEPTANCE_SETTLE_STATUS_NOTES

def read_settle_input(
    target: Path, args: argparse.Namespace
) -> tuple[str | None, list[tuple[str, dict[str, Any]]]]:
    """读取并校验 settle --input 顶层形状，逐条记录复用单条 record 输入级校验。

    返回 (objective, records)；未传 --input 时返回 (None, [])。
    records 为 (criterion_id, stored) 列表，供资产层 settle 原子批量落盘。"""
    if not args.input:
        return None, []
    input_path = project_input_path(target, args.input, code="invalid_acceptance_settle_input")
    value = read_json(input_path)
    if not isinstance(value, dict) or value.get("schema_version") != ACCEPTANCE_SETTLE_INPUT_SCHEMA:
        raise HarnessError(
            f"settle 输入 Schema 无效，必须为 {ACCEPTANCE_SETTLE_INPUT_SCHEMA}",
            code="invalid_acceptance_settle_input",
        )
    unknown = sorted(set(value) - {"schema_version", "objective", "records"})
    if unknown:
        raise HarnessError(
            "settle 输入包含未注册字段：" + ", ".join(unknown),
            code="invalid_acceptance_settle_input",
        )
    objective = value.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise HarnessError("settle 输入 objective 不能为空", code="invalid_acceptance_settle_input")
    raw_records = value.get("records")
    if not isinstance(raw_records, list):
        raise HarnessError("settle 输入 records 必须是数组", code="invalid_acceptance_settle_input")
    objective = objective.strip()
    records: list[tuple[str, dict[str, Any]]] = []
    for item in raw_records:
        if not isinstance(item, dict):
            raise HarnessError("settle records 每项必须是对象", code="invalid_acceptance_settle_input")
        criterion_id = item.get("criterion_id")
        if not isinstance(criterion_id, str) or not criterion_id.strip():
            raise HarnessError("settle 记录必须提供 criterion_id", code="acceptance_criterion_missing")
        stored = build_stored_acceptance_record(
            target,
            item,
            objective,
            associated=True,
            user_confirmed=bool(args.user_confirmed),
        )
        records.append((criterion_id.strip(), stored))
    return objective, records

def acceptance_settle(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.acceptance or not args.status:
        raise HarnessError(
            "acceptance settle 必须提供 --acceptance 与 --status",
            code="missing_acceptance_target",
        )
    target = safe_target(args.target)
    objective, records = read_settle_input(target, args)
    try:
        payload = settle_acceptance_asset(
            target,
            args.acceptance,
            args.status,
            args.replacement,
            utc_now(),
            plan_check_markdown_files(target),
            records=records,
            objective=objective,
            dry_run=bool(args.dry_run),
        )
    except AssetError as exc:
        raise translate_asset_error(exc) from exc
    return 0, payload

def acceptance_check(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = safe_target(args.target)
    try:
        payload = check_acceptance_assets(target)
    except AssetError as exc:
        raise translate_asset_error(exc) from exc
    return (0 if payload["status"] == "passed" else 1), payload

# adr create --input 的 --help 示例（校验在 adr_assets；改 schema 同步此处）。
ADR_INPUT_EXAMPLE = {
    "schema_version": ADR_INPUT_SCHEMA,
    "title": "决策标题",
    "key_symbols": ["2-4 个唯一符号，不含反引号"],
    "context": "决策背景与约束",
    "decision": "采取的方案",
    "consequences": "影响与代价",
    "supersedes": ["可选：被取代的既有 ADR 路径 docs/adr/<name>.json"],
}

def adr_amend(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.adr or not args.input or not args.reason:
        raise HarnessError(
            "adr amend 必须提供 --adr、--input 与 --reason",
            code="missing_adr_amend_input",
        )
    target = safe_target(args.target)
    value = read_json(project_input_path(target, args.input, code="invalid_adr_input"))
    try:
        return 0, amend_adr_asset(target, args.adr, value, args.reason, utc_now())
    except AssetError as exc:
        raise translate_asset_error(exc) from exc

def adr_create(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.input or not args.output:
        raise HarnessError("adr create 必须提供 --input 与 --output", code="missing_adr_input")
    target = safe_target(args.target)
    value = read_json(project_input_path(target, args.input, code="invalid_adr_input"))
    try:
        return 0, create_adr_asset(target, value, args.output, utc_now())
    except AssetError as exc:
        raise translate_asset_error(exc) from exc

def adr_settle(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.adr or not args.status:
        raise HarnessError("adr settle 必须提供 --adr 与 --status", code="missing_adr_input")
    target = safe_target(args.target)
    try:
        payload = settle_adr_asset(
            target, args.adr, args.status, args.replacement, utc_now(), plan_check_markdown_files(target)
        )
    except AssetError as exc:
        raise translate_asset_error(exc) from exc
    return 0, payload

def adr_check(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = safe_target(args.target)
    try:
        payload = check_adr_assets(target)
    except AssetError as exc:
        raise translate_asset_error(exc) from exc
    return (0 if payload["status"] == "passed" else 1), payload

def migration_display_path(target: Path, path: Path) -> str:
    with contextlib.suppress(ValueError):
        return path.resolve(strict=False).relative_to(target.resolve()).as_posix()
    return str(path.resolve(strict=False))

def legacy_cleanup_plan(target: Path) -> dict[str, Any]:
    config = project_config(target)
    remove_files: list[str] = []
    remove_directories: list[str] = []
    strip_blocks: list[str] = []
    preserved: list[str] = []
    conflicts: list[dict[str, str]] = []
    remove_file_fingerprints: dict[str, str] = {}

    if config and config.get("schema_version") not in (
        *KNOWN_LEGACY_CONFIG_SCHEMAS,
        CONFIG_SCHEMA,
    ):
        conflicts.append(
            {
                "path": ".docs-harness/config.json",
                "reason_code": "unknown_config_schema",
            }
        )

    rules_root = target / LEGACY_RULES_RELATIVE
    installed = (
        config.get("installed_rule_fingerprints", {})
        if config and config.get("schema_version") in KNOWN_LEGACY_CONFIG_SCHEMAS
        else {}
    )
    installed = installed if isinstance(installed, dict) else {}
    if rules_root.exists() or rules_root.is_symlink():
        if rules_root.is_symlink() or not rules_root.is_dir():
            conflicts.append(
                {"path": LEGACY_RULES_RELATIVE, "reason_code": "legacy_rules_root_unsafe"}
            )
        else:
            for path in sorted(rules_root.rglob("*")):
                relative = migration_display_path(target, path)
                if path.is_symlink():
                    conflicts.append(
                        {"path": relative, "reason_code": "legacy_rule_symlink"}
                    )
                elif path.is_dir():
                    continue
                elif path.parent != rules_root:
                    preserved.append(relative)
                else:
                    expected = installed.get(path.name)
                    if isinstance(expected, str) and file_fingerprint(path) == expected:
                        remove_files.append(relative)
                        remove_file_fingerprints[relative] = expected
                    else:
                        preserved.append(relative)
            if not conflicts and not preserved:
                remove_directories.append(LEGACY_RULES_RELATIVE)

    knowledge_map = target / KNOWLEDGE_MAP_RELATIVE
    if knowledge_map.exists() or knowledge_map.is_symlink():
        if knowledge_map.is_symlink() or not knowledge_map.is_file():
            conflicts.append(
                {
                    "path": KNOWLEDGE_MAP_RELATIVE,
                    "reason_code": "legacy_knowledge_map_unsafe",
                }
            )
        else:
            try:
                value = read_json(knowledge_map)
            except HarnessError:
                preserved.append(KNOWLEDGE_MAP_RELATIVE)
            else:
                if isinstance(value, dict) and str(
                    value.get("schema_version", "")
                ).startswith("docs-harness/knowledge-map/"):
                    remove_files.append(KNOWLEDGE_MAP_RELATIVE)
                    remove_file_fingerprints[KNOWLEDGE_MAP_RELATIVE] = file_fingerprint(
                        knowledge_map
                    )
                else:
                    preserved.append(KNOWLEDGE_MAP_RELATIVE)

    for relative in LEGACY_INDEX_PATHS:
        path = target / relative
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file():
            conflicts.append(
                {"path": relative, "reason_code": "legacy_managed_block_unsafe"}
            )
            continue
        text = path.read_text(encoding="utf-8")
        begin_count = text.count(MANAGED_VERSION_BEGIN)
        end_count = text.count(MANAGED_VERSION_END)
        if begin_count or end_count:
            if (
                begin_count == end_count == 1
                and text.index(MANAGED_VERSION_BEGIN) < text.index(MANAGED_VERSION_END)
            ):
                strip_blocks.append(relative)
            else:
                conflicts.append(
                    {"path": relative, "reason_code": "legacy_managed_block_invalid"}
                )

    for parent in migration_runtime_parents(target):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            conflicts.append(
                {
                    "path": migration_display_path(target, parent),
                    "reason_code": "legacy_runtime_root_unsafe",
                }
            )
            continue
        for name in LEGACY_RUNTIME_NAMES:
            path = parent / name
            if not path.exists() and not path.is_symlink():
                continue
            display = migration_display_path(target, path)
            if path.is_symlink() or not path.is_dir():
                conflicts.append(
                    {"path": display, "reason_code": "legacy_runtime_path_unsafe"}
                )
            else:
                remove_directories.append(display)

    return {
        "schema_version": "docs-harness/legacy-document-cleanup/v1",
        "remove_files": sorted(set(remove_files)),
        "remove_directories": sorted(set(remove_directories)),
        "strip_managed_blocks": sorted(set(strip_blocks)),
        "remove_file_fingerprints": dict(sorted(remove_file_fingerprints.items())),
        "preserved_paths": sorted(set(preserved)),
        "conflicts": conflicts,
        "cleanup_possible": not conflicts,
    }

def resolve_migration_path(target: Path, display: str) -> Path:
    path = Path(display)
    return path if path.is_absolute() else target / path

def apply_legacy_cleanup(target: Path, plan: dict[str, Any]) -> list[str]:
    if plan["conflicts"]:
        raise HarnessError(
            "旧文档系统存在不安全路径，升级未写入",
            code="legacy_document_cleanup_conflict",
            exit_code=3,
            extra_payload={"legacy_document_cleanup": plan},
        )
    changed: list[str] = []
    for relative in plan["strip_managed_blocks"]:
        path = resolve_migration_path(target, relative)
        if path.is_symlink() or not path.is_file():
            raise HarnessError(
                "受管索引在应用前发生变化",
                code="legacy_document_cleanup_conflict",
                exit_code=3,
            )
        before = path.read_text(encoding="utf-8")
        after = remove_managed_block(before, MANAGED_VERSION_BEGIN, MANAGED_VERSION_END)
        if after != before:
            atomic_write_text(path, after)
            changed.append(f"{relative}:remove_legacy_managed_block")
    for relative in plan["remove_files"]:
        path = resolve_migration_path(target, relative)
        if path.is_symlink():
            raise HarnessError(
                "旧文件在应用前变为符号链接",
                code="legacy_document_cleanup_conflict",
                exit_code=3,
            )
        expected = plan.get("remove_file_fingerprints", {}).get(relative)
        if not path.is_file() or not isinstance(expected, str):
            raise HarnessError(
                "待清理旧文件已发生变化",
                code="legacy_document_cleanup_conflict",
                exit_code=3,
            )
        if file_fingerprint(path) != expected:
            raise HarnessError(
                "待清理旧文件指纹已变化",
                code="legacy_document_cleanup_conflict",
                exit_code=3,
            )
        if path.is_file():
            path.unlink()
            changed.append(relative)
    for relative in sorted(plan["remove_directories"], key=len, reverse=True):
        path = resolve_migration_path(target, relative)
        if relative == LEGACY_RULES_RELATIVE:
            assert_no_symlink_ancestors(
                target,
                relative,
                code="legacy_document_cleanup_conflict",
            )
        else:
            runtime_parents = migration_runtime_parents(target)
            if (
                path.parent not in runtime_parents
                or path.name not in LEGACY_RUNTIME_NAMES
                or path.parent.is_symlink()
                or (path.parent.exists() and not path.parent.is_dir())
            ):
                raise HarnessError(
                    "待清理旧 Runtime 越出受管目录",
                    code="legacy_document_cleanup_conflict",
                    exit_code=3,
                )
        if path.is_symlink():
            raise HarnessError(
                "旧目录在应用前变为符号链接",
                code="legacy_document_cleanup_conflict",
                exit_code=3,
            )
        if path.is_dir():
            if relative == LEGACY_RULES_RELATIVE:
                remaining = [
                    item
                    for item in path.rglob("*")
                    if item.is_symlink() or item.is_file()
                ]
                if remaining:
                    raise HarnessError(
                        "旧规则目录在应用前出现新增文件",
                        code="legacy_document_cleanup_conflict",
                        exit_code=3,
                    )
            shutil.rmtree(path)
            changed.append(relative)
            if relative == LEGACY_RULES_RELATIVE:
                for parent in (path.parent, path.parent.parent):
                    with contextlib.suppress(OSError):
                        parent.rmdir()
    return changed

USAGE_ENABLED_NOTICE = (
    "本次升级开启了本地使用观测：harness 自身的命令调用会追加到 "
    ".docs-harness/usage/YYYY-MM.jsonl（按月分文件）。日志只留在本地，"
    "自带嵌套 .gitignore 不入库、不外发、不遥测，也不改变任何命令的行为与退出码。"
    "不需要时把 .docs-harness/config.json 的 usage_log.enabled 改为 false 即可关闭。"
)

HARNESS_PLAN_V230_NOTICE = (
    "【2.30.0 必读】口语「走 plan / 方案模式」指 Harness Plan"
    "（python3 scripts/harness.py plan select|create），不是 IDE 自带的 Plan 聊天模式；"
    "不得用切换 IDE Plan 代替 plan create 冻结合同。"
    "Full 级方案新建时必填 process_flowchart（Mermaid 流程图）与 ux_journey（体验或操作者路径）；brief 级不变。"
)

def parse_semver(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.strip().split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts or (0,))

def semver_lt(left: str, right: str) -> bool:
    a, b = parse_semver(left), parse_semver(right)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) < b + (0,) * (width - len(b))

def usage_enabled_notices(existing: dict[str, Any] | None, enabled: bool) -> list[str]:
    """首次开启 usage 观测时的一次性告知；其余情况返回空列表。

    三个条件缺一不可：existing 是非空 dict（fresh init 的 existing 为 None，
    init 与 upgrade --apply 共用同一条返回路径，靠这一条把 init 排除在外）；
    existing 缺 usage_log 键（把范围锁死在 v12 及更早 → v13 这一次迁移，
    已是 v13 的项目该键必在，再升级不再提示）；新配置确实开着。

    两个调用点的 enabled 取值：upgrade 预览侧尚未写 config，取 USAGE_LOG_DEFAULT_ENABLED
    ——existing 缺 usage_log 时 v2_config 必然回落该常量，取值确定；apply 侧 config 刚由
    v2_config 写完，usage_log.enabled 是保证存在的 bool，直接按键取（边界已校验，
    下游不再重复防御）。
    """
    if not isinstance(existing, dict) or not existing:
        return []
    if "usage_log" in existing or not enabled:
        return []
    return [USAGE_ENABLED_NOTICE]

def project_upgrade_notices(
    *,
    action: str,
    from_version: str | None,
    existing: dict[str, Any] | None,
    usage_enabled: bool,
) -> list[str]:
    notices: list[str] = []
    if action == "upgrade" and from_version and semver_lt(from_version, "2.30.0"):
        notices.append(HARNESS_PLAN_V230_NOTICE)
    notices.extend(usage_enabled_notices(existing, usage_enabled))
    return notices

def v2_config(
    *,
    source_script: Path,
    source_modules: dict[str, str],
    source_templates: dict[str, str],
    source_githooks: dict[str, str],
    existing: dict[str, Any] | None,
    docs_preexisted: bool,
    cleanup: dict[str, Any],
) -> dict[str, Any]:
    removed = sorted(
        set(cleanup["remove_files"])
        | set(cleanup["remove_directories"])
        | set(cleanup["strip_managed_blocks"])
    )
    if (
        existing
        and existing.get("schema_version") == CONFIG_SCHEMA
        and isinstance(existing.get("migration"), dict)
    ):
        migration = dict(existing["migration"])
        migration["preserved_paths"] = sorted(
            set(migration.get("preserved_paths", []))
            | set(cleanup["preserved_paths"])
        )
        if removed:
            migration["legacy_document_system"] = "removed"
            migration["removed_paths"] = sorted(
                set(migration.get("removed_paths", [])) | set(removed)
            )
    else:
        migration = {
            "source_version": existing.get("version") if existing else None,
            "legacy_document_system": "removed" if existing else "not_present",
            "removed_paths": removed,
            "preserved_paths": cleanup["preserved_paths"],
        }
    existing_knowledge = existing.get("knowledge") if existing else None
    docs_flag = (
        existing_knowledge.get("docs_preexisting_at_install", docs_preexisted)
        if isinstance(existing_knowledge, dict)
        else docs_preexisted
    )
    # 升级沿用用户已显式关闭的开关；缺失或被改坏时回落唯一默认常量。
    existing_usage = existing.get("usage_log") if existing else None
    usage_enabled = (
        existing_usage["enabled"]
        if isinstance(existing_usage, dict) and isinstance(existing_usage.get("enabled"), bool)
        else USAGE_LOG_DEFAULT_ENABLED
    )
    return {
        "schema_version": CONFIG_SCHEMA,
        "version": VERSION,
        "installed_script_fingerprint": file_fingerprint(source_script),
        "installed_module_fingerprints": source_modules,
        "installed_plan_template_fingerprints": source_templates,
        "installed_githook_fingerprints": source_githooks,
        "direct_mode": {"default": True},
        "knowledge": {
            "mode": "asset_lifecycle",
            "query": "on_demand",
            "docs_preexisting_at_install": bool(docs_flag),
        },
        "usage_log": {"enabled": usage_enabled},
        "migration": migration,
        "installed_at": (
            existing.get("installed_at")
            if existing and existing.get("installed_at")
            else utc_now()
        ),
    }

def preflight_owned_files(
    target: Path,
    install_relative: str,
    source_fingerprints: dict[str, str],
    installed_fingerprints: Any,
    conflicts: list[dict[str, Any]],
    *,
    label: str,
    compatible_fingerprints: dict[str, str] | None = None,
) -> None:
    """指纹归属文件的接管预检：方案模板与 git 钩子共用同一口径。

    指纹偏离（用户修改/归属不明）只向 conflicts 收集，由调用方统一报错；
    symlink、非常规文件、安装指纹无效等结构性错误仍即时抛出。
    """
    if not isinstance(installed_fingerprints, dict):
        raise HarnessError(f"{label}安装指纹无效", code="install_conflict")
    for relative, source_fingerprint in source_fingerprints.items():
        path = target / install_relative / relative
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise HarnessError(
                f"{label} {relative} 不是可接管常规文件",
                code="install_conflict",
            )
        if path.is_file():
            allowed = {source_fingerprint}
            old = installed_fingerprints.get(relative)
            if isinstance(old, str):
                allowed.add(old)
            compatible = (compatible_fingerprints or {}).get(relative)
            allowed.update([compatible] if isinstance(compatible, str) else [])
            actual = file_fingerprint(path)
            if actual not in allowed:
                conflicts.append(
                    {
                        "path": f"{install_relative}/{relative}",
                        "reason": "modified",
                        "actual_fingerprint": actual,
                        "allowed_fingerprints": sorted(allowed),
                    }
                )

def install_preflight(
    target: Path,
    source_root: Path,
    existing: dict[str, Any] | None,
    cleanup: dict[str, Any],
) -> tuple[Path, dict[str, str], dict[str, str], dict[str, str]]:
    if cleanup["conflicts"]:
        raise HarnessError(
            "旧文档系统存在不安全路径，升级未写入",
            code="legacy_document_cleanup_conflict",
            exit_code=3,
            extra_payload={"legacy_document_cleanup": cleanup},
        )
    for relative, expected in cleanup.get("remove_file_fingerprints", {}).items():
        path = resolve_migration_path(target, relative)
        if (
            path.is_symlink()
            or not path.is_file()
            or file_fingerprint(path) != expected
        ):
            raise HarnessError(
                "待清理旧文件在安装前发生变化",
                code="legacy_document_cleanup_conflict",
                exit_code=3,
                extra_payload={"legacy_document_cleanup": cleanup},
            )
    validate_project_source(source_root)
    source_script = source_root / "scripts" / "harness.py"
    source_modules = managed_module_fingerprints(source_root / "scripts")
    source_templates = template_fingerprints(source_root / PLAN_TEMPLATES_RELATIVE)
    source_githooks = githook_fingerprints(source_root / GIT_HOOKS_RELATIVE)
    for relative in portable_install_paths():
        assert_no_symlink_ancestors(target, relative, code="install_conflict")
    conflicts: list[dict[str, Any]] = []
    target_script = target / "scripts" / "harness.py"
    if target_script.is_symlink() or (
        target_script.exists() and not target_script.is_file()
    ):
        raise HarnessError(
            "目标 scripts/harness.py 不是可接管常规文件",
            code="install_conflict",
        )
    if target_script.is_file():
        current = file_fingerprint(target_script)
        allowed = {file_fingerprint(source_script)}
        if existing and isinstance(existing.get("installed_script_fingerprint"), str):
            allowed.add(existing["installed_script_fingerprint"])
        if current not in allowed:
            conflicts.append(
                {
                    "path": "scripts/harness.py",
                    "reason": "modified",
                    "actual_fingerprint": current,
                    "allowed_fingerprints": sorted(allowed),
                }
            )
    preflight_owned_files(
        target,
        "scripts",
        source_modules,
        existing.get("installed_module_fingerprints", {}) if existing else {},
        conflicts,
        label="资产生命周期模块",
    )
    preflight_owned_files(
        target,
        PLAN_TEMPLATES_RELATIVE,
        source_templates,
        existing.get("installed_plan_template_fingerprints", {}) if existing else {},
        conflicts,
        label="方案模板",
        compatible_fingerprints=legacy_plan_template_fingerprints(existing.get("version") if existing else None),
    )
    preflight_owned_files(
        target,
        GIT_HOOKS_RELATIVE,
        source_githooks,
        existing.get("installed_githook_fingerprints", {}) if existing else {},
        conflicts,
        label="git 钩子",
    )
    if conflicts:
        paths = "、".join(item["path"] for item in conflicts)
        raise HarnessError(
            f"受管文件存在本地修改，升级未写入：{paths}。"
            "恢复安装版本后重试升级；确需保留的修改请合入 docs-harness 随新版本升级；保持分叉则跳过升级。",
            code="install_conflict",
            extra_payload={"install_conflicts": conflicts},
        )
    plan_docs_structure_changes(target)
    asset_structure_changes(target)
    for relative, begin, end in (
        ("AGENTS.md", MANAGED_BEGIN, MANAGED_END),
        ("CLAUDE.md", CLAUDE_BEGIN, CLAUDE_END),
    ):
        path = target / relative
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise HarnessError(f"{relative} 不是可合并常规文件", code="install_conflict")
        if path.is_file():
            validate_managed_markers(path.read_text(encoding="utf-8"), begin, end)
    config_path = target / ".docs-harness" / "config.json"
    if config_path.is_symlink() or (
        config_path.exists() and not config_path.is_file()
    ):
        raise HarnessError(".docs-harness/config.json 不安全", code="install_conflict")
    ignored = git_ignored_install_paths(target, portable_install_paths())
    if ignored:
        raise HarnessError(
            f"Git 忽略了 Docs Harness {VERSION} 必需安装文件：" + ", ".join(ignored),
            code="git_delivery_ignored",
            exit_code=3,
        )
    return source_script, source_modules, source_templates, source_githooks

def project_changes(target: Path, source_root: Path) -> list[dict[str, Any]]:
    validate_project_source(source_root)
    changes: list[dict[str, Any]] = []
    source_script = source_root / "scripts" / "harness.py"
    target_script = target / "scripts" / "harness.py"
    if not target_script.is_file():
        changes.append({"path": "scripts/harness.py", "action": "create"})
    elif file_fingerprint(target_script) != file_fingerprint(source_script):
        changes.append({"path": "scripts/harness.py", "action": "update"})
    source_modules = managed_module_fingerprints(source_root / "scripts")
    for relative, fingerprint in source_modules.items():
        path = target / "scripts" / relative
        if not path.is_file():
            changes.append({"path": f"scripts/{relative}", "action": "create"})
        elif file_fingerprint(path) != fingerprint:
            changes.append({"path": f"scripts/{relative}", "action": "update"})
    for relative, begin, end, block in (
        ("AGENTS.md", MANAGED_BEGIN, MANAGED_END, managed_agent_block(target)),
        ("CLAUDE.md", CLAUDE_BEGIN, CLAUDE_END, claude_block(target)),
    ):
        path = target / relative
        if not path.is_file():
            changes.append({"path": relative, "action": "create_or_merge"})
        else:
            current = path.read_text(encoding="utf-8")
            try:
                expected = replace_managed_block(current, begin, end, block)
            except HarnessError:
                changes.append(
                    {
                        "path": relative,
                        "action": "needs_manual_cleanup",
                        "reason_code": "managed_block_invalid",
                    }
                )
            else:
                if current != expected:
                    changes.append(
                        {
                            "path": relative,
                            "action": (
                                "update_managed_block"
                                if begin in current
                                else "create_or_merge"
                            ),
                        }
                    )
    source_templates = template_fingerprints(source_root / PLAN_TEMPLATES_RELATIVE)
    for relative, fingerprint in source_templates.items():
        path = target / PLAN_TEMPLATES_RELATIVE / relative
        if not path.is_file():
            changes.append(
                {"path": f"{PLAN_TEMPLATES_RELATIVE}/{relative}", "action": "create"}
            )
        elif file_fingerprint(path) != fingerprint:
            changes.append(
                {"path": f"{PLAN_TEMPLATES_RELATIVE}/{relative}", "action": "update"}
            )
    source_githooks = githook_fingerprints(source_root / GIT_HOOKS_RELATIVE)
    for relative, fingerprint in source_githooks.items():
        path = target / GIT_HOOKS_RELATIVE / relative
        if not path.is_file():
            changes.append(
                {"path": f"{GIT_HOOKS_RELATIVE}/{relative}", "action": "create"}
            )
        elif file_fingerprint(path) != fingerprint:
            changes.append(
                {"path": f"{GIT_HOOKS_RELATIVE}/{relative}", "action": "update"}
            )
    changes.extend(plan_docs_structure_changes(target))
    changes.extend(asset_structure_changes(target))
    changes.extend(project_doc_changes(target))
    changes.extend(local_only_dir_changes(target))
    cleanup = legacy_cleanup_plan(target)
    changes.extend(
        {"path": path, "action": "remove_owned_legacy"}
        for path in cleanup["remove_files"]
    )
    changes.extend(
        {"path": path, "action": "remove_legacy_runtime"}
        for path in cleanup["remove_directories"]
    )
    changes.extend(
        {"path": path, "action": "remove_legacy_managed_block"}
        for path in cleanup["strip_managed_blocks"]
    )
    changes.extend(
        {
            "path": item["path"],
            "action": "needs_manual_cleanup",
            "reason_code": item["reason_code"],
        }
        for item in cleanup["conflicts"]
    )
    existing = project_config(target)
    expected_config = v2_config(
        source_script=source_script,
        source_modules=source_modules,
        source_templates=source_templates,
        source_githooks=source_githooks,
        existing=existing,
        docs_preexisted=(target / "docs").is_dir(),
        cleanup=cleanup,
    )
    config_path = target / ".docs-harness" / "config.json"
    if not config_path.is_file():
        changes.append({"path": ".docs-harness/config.json", "action": "create"})
    elif existing != expected_config:
        changes.append({"path": ".docs-harness/config.json", "action": "update"})
    # docs/INDEX.md 承载四类资产各自的索引区块，各结构检查会分别登记同一条变更；
    # 清单描述的是文件级动作，完全相同的条目只保留首次出现。
    unique: list[dict[str, Any]] = []
    for item in changes:
        if item not in unique:
            unique.append(item)
    return unique

def apply_project_install(
    target: Path, source_root: Path
) -> tuple[list[str], dict[str, Any]]:
    existing = project_config(target)
    cleanup = legacy_cleanup_plan(target)
    source_script, source_modules, source_templates, source_githooks = install_preflight(
        target, source_root, existing, cleanup
    )
    config_value = v2_config(
        source_script=source_script,
        source_modules=source_modules,
        source_templates=source_templates,
        source_githooks=source_githooks,
        existing=existing,
        docs_preexisted=(target / "docs").is_dir(),
        cleanup=cleanup,
    )
    changed: list[str] = []
    changed.extend(apply_plan_docs_structure(target))
    changed.extend(apply_asset_structures(target))
    changed.extend(apply_project_doc_scaffolds(target))
    target_script = target / "scripts" / "harness.py"
    target_script.parent.mkdir(parents=True, exist_ok=True)
    if (
        not target_script.is_file()
        or file_fingerprint(target_script) != file_fingerprint(source_script)
    ):
        shutil.copy2(source_script, target_script)
        target_script.chmod(target_script.stat().st_mode | 0o111)
        changed.append("scripts/harness.py")
    for relative, fingerprint in source_modules.items():
        path = target / "scripts" / relative
        if not path.is_file() or file_fingerprint(path) != fingerprint:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / "scripts" / relative, path)
            changed.append(f"scripts/{relative}")
    for relative, begin, end, block, heading in (
        (
            "AGENTS.md",
            MANAGED_BEGIN,
            MANAGED_END,
            managed_agent_block(target),
            "# AGENTS.md\n",
        ),
        (
            "CLAUDE.md",
            CLAUDE_BEGIN,
            CLAUDE_END,
            claude_block(target),
            "# CLAUDE.md\n",
        ),
    ):
        path = target / relative
        current = path.read_text(encoding="utf-8") if path.is_file() else heading
        updated = replace_managed_block(current, begin, end, block)
        if updated != current:
            atomic_write_text(path, updated)
            changed.append(relative)
    source_template_root = source_root / PLAN_TEMPLATES_RELATIVE
    for relative, fingerprint in source_templates.items():
        path = target / PLAN_TEMPLATES_RELATIVE / relative
        if not path.is_file() or file_fingerprint(path) != fingerprint:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_template_root / relative, path)
            changed.append(f"{PLAN_TEMPLATES_RELATIVE}/{relative}")
    source_githook_root = source_root / GIT_HOOKS_RELATIVE
    for relative, fingerprint in source_githooks.items():
        path = target / GIT_HOOKS_RELATIVE / relative
        if not path.is_file() or file_fingerprint(path) != fingerprint:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_githook_root / relative, path)
            path.chmod(path.stat().st_mode | 0o111)
            changed.append(f"{GIT_HOOKS_RELATIVE}/{relative}")
    config_path = target / ".docs-harness" / "config.json"
    if existing != config_value:
        atomic_write_json(config_path, config_value)
        changed.append(".docs-harness/config.json")
    changed.extend(apply_local_only_dirs(target))
    changed.extend(apply_legacy_cleanup(target, cleanup))
    return list(dict.fromkeys(changed)), cleanup

def local_only_dir_changes(target: Path) -> list[dict[str, str]]:
    """本地约定目录的预览判定：每个缺嵌套 .gitignore 的目录报一条 create，否则为空。

    与 plan_docs_structure_changes / asset_structure_changes 同形：project_changes（升级预览
    与 project diff 的共同来源）汇总它，apply_local_only_dirs 据它写入，三处同一份判定。
    2.16.1 只有 apply 一侧，升级预览列 13 项而实际写入 14 项，diff 也看不到它。
    """
    return [
        {"path": f"{relative}/.gitignore", "action": "create"}
        for relative in LOCAL_ONLY_DIRS
        if not (target / relative / ".gitignore").is_file()
    ]

def apply_local_only_dirs(target: Path) -> list[str]:
    """确保本地约定目录存在且不入库；返回本次实际写入的相对路径。

    inputs/（2.16.1）：plan create --content、acceptance record --input 等要求输入文件位于
    项目内，此前没有约定位置；1.x 运行态目录 .docs-harness/task-inputs/ 在
    LEGACY_RUNTIME_NAMES 内，project upgrade 必清，用它会反复丢文件。
    tasks/（2.19.0）：长任务的进度清单，上下文压缩后以文件为准。
    reports/（2.26.0）：收尾记录，回复只给路径，证据留给日后追查。
    views/：view 命令写出的 Mermaid 页面（渲染产物；常量真源在 diagram_view）。四者都不在该元组内，升级不清理。

    嵌套 .gitignore 写法与 usage_log._GITIGNORE_CONTENT 同口径，这是它的第 2 次出现：
    新增目录只进 LOCAL_ONLY_DIRS，不另写一份；第 3 次出现时抽公共函数（编码质量规范第 2、10 条）。
    已存在的 .gitignore 一律不覆盖——用户可能改过。写入失败不吞：安装器的写入必须炸，
    与 usage_log.append_event 的 best-effort 豁免是两条性质不同的路径。
    """
    changes = local_only_dir_changes(target)
    for change in changes:
        gitignore = target / change["path"]
        gitignore.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(gitignore, LOCAL_ONLY_GITIGNORE_CONTENT)
    return [change["path"] for change in changes]

def project_findings(target: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    config = project_config(target)
    if not config or config.get("schema_version") != CONFIG_SCHEMA:
        return [
            {
                "severity": "red",
                "code": "missing_config",
                "message": f"缺少 {CONFIG_SCHEMA}",
            }
        ]
    if config.get("version") != VERSION:
        findings.append(
            {
                "severity": "red",
                "code": "version_mismatch",
                "message": "配置版本与控制器不一致",
            }
        )
    legacy_keys = sorted(
        {
            "rules_root",
            "installed_rule_fingerprints",
            "background_governance",
            "gate_path_rules",
            "verification",
        }
        & set(config)
    )
    if legacy_keys:
        findings.append(
            {
                "severity": "red",
                "code": "legacy_config_not_removed",
                "message": ", ".join(legacy_keys),
            }
        )
    direct = config.get("direct_mode")
    if direct != {"default": True}:
        findings.append(
            {
                "severity": "red",
                "code": "direct_mode_invalid",
                "message": "默认执行模式配置无效",
            }
        )
    knowledge = config.get("knowledge")
    expected_knowledge_keys = {"mode", "query", "docs_preexisting_at_install"}
    if not isinstance(knowledge, dict) or not (
        set(knowledge) == expected_knowledge_keys
        and knowledge.get("mode") == "asset_lifecycle"
        and knowledge.get("query") == "on_demand"
        and isinstance(knowledge.get("docs_preexisting_at_install"), bool)
    ):
        findings.append(
            {
                "severity": "red",
                "code": "knowledge_mode_invalid",
                "message": "知识资产生命周期配置无效",
            }
        )
    usage_log = config.get("usage_log")
    if not isinstance(usage_log, dict) or set(usage_log) != {"enabled"} or not isinstance(
        usage_log.get("enabled"), bool
    ):
        findings.append(
            {"severity": "red", "code": "usage_log_invalid", "message": "usage 观测开关配置无效"}
        )
    script = target / "scripts" / "harness.py"
    if (
        not script.is_file()
        or config.get("installed_script_fingerprint") != file_fingerprint(script)
    ):
        findings.append(
            {
                "severity": "red",
                "code": "script_drift",
                "message": "控制器缺失或发生漂移",
            }
        )
    live_modules = managed_module_fingerprints(target / "scripts")
    if live_modules != config.get("installed_module_fingerprints"):
        findings.append(
            {
                "severity": "red",
                "code": "asset_module_drift",
                "message": "资产生命周期模块缺失或漂移",
            }
        )
    for relative, begin, end in (
        ("AGENTS.md", MANAGED_BEGIN, MANAGED_END),
        ("CLAUDE.md", CLAUDE_BEGIN, CLAUDE_END),
    ):
        path = target / relative
        if not path.is_file():
            findings.append(
                {
                    "severity": "red",
                    "code": "missing_entry_chain",
                    "message": f"{relative} 缺失",
                }
            )
            continue
        text = path.read_text(encoding="utf-8")
        try:
            validate_managed_markers(text, begin, end)
        except HarnessError:
            findings.append(
                {
                    "severity": "red",
                    "code": "managed_block_invalid",
                    "message": f"{relative} 受管区块损坏",
                }
            )
        else:
            if begin not in text:
                findings.append(
                    {
                        "severity": "red",
                        "code": "missing_entry_chain",
                        "message": f"{relative} 缺少受管区块",
                    }
                )
    live_templates = template_fingerprints(target / PLAN_TEMPLATES_RELATIVE)
    if live_templates != config.get("installed_plan_template_fingerprints"):
        findings.append(
            {
                "severity": "red",
                "code": "plan_template_drift",
                "message": "方案模板缺失或漂移",
            }
        )
    live_githooks = githook_fingerprints(target / GIT_HOOKS_RELATIVE)
    if live_githooks != config.get("installed_githook_fingerprints"):
        findings.append(
            {
                "severity": "red",
                "code": "githook_drift",
                "message": "git 钩子缺失或漂移",
            }
        )
    check_githook_health(target, findings)
    try:
        structure_changes = plan_docs_structure_changes(target)
    except HarnessError as exc:
        findings.append(
            {
                "severity": "red",
                "code": exc.code,
                "message": str(exc),
            }
        )
    else:
        if structure_changes:
            findings.append(
                {
                    "severity": "red",
                    "code": "plan_docs_structure_missing",
                    "message": ", ".join(item["path"] for item in structure_changes),
                }
            )
    try:
        managed_asset_changes = asset_structure_changes(target)
    except HarnessError as exc:
        findings.append(
            {"severity": "red", "code": exc.code, "message": str(exc)}
        )
    else:
        if managed_asset_changes:
            findings.append(
                {
                    "severity": "red",
                    "code": "asset_docs_structure_missing",
                    "message": ", ".join(item["path"] for item in managed_asset_changes),
                }
            )
        else:
            for code, label, checker in (
                ("knowledge_assets_invalid", "Knowledge", check_knowledge_assets),
                ("acceptance_assets_invalid", "Acceptance", check_acceptance_assets),
                ("adr_assets_invalid", "ADR", check_adr_assets),
            ):
                try:
                    result = checker(target)
                except AssetError as exc:
                    result = {"status": "failed", "failures": [str(exc)]}
                if result["status"] != "passed":
                    findings.append(
                        {
                            "severity": "red",
                            "code": code,
                            "message": f"{label} 资产检查失败：" + "; ".join(result["failures"]),
                        }
                    )
    for relative, code, label in (
        ("CHANGELOG.md", "project_changelog_missing", "CHANGELOG.md 缺失"),
        ("TODO.md", "project_todo_missing", "TODO.md 缺失"),
    ):
        if not (target / relative).is_file():
            findings.append(
                {"severity": "red", "code": code, "message": f"{label}（init/upgrade 会生成骨架）"}
            )
    todo_path = target / "TODO.md"
    if todo_path.is_file():
        try:
            todo_lines = todo_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            todo_lines = []
        malformed = [
            line.strip()
            for line in todo_lines
            if line.strip().startswith("- [") and not TODO_ENTRY_PATTERN.match(line.strip())
        ]
        if malformed:
            findings.append(
                {
                    "severity": "yellow",
                    "code": "project_todo_format",
                    "message": f"TODO.md 有 {len(malformed)} 条格式不符（应为 `- [ ] 事项（owner，YYYY-MM-DD）`）",
                }
            )
    cleanup = legacy_cleanup_plan(target)
    if cleanup["conflicts"]:
        findings.append(
            {
                "severity": "red",
                "code": "legacy_document_cleanup_conflict",
                "message": "存在不安全旧路径",
            }
        )
    if (
        cleanup["remove_files"]
        or cleanup["remove_directories"]
        or cleanup["strip_managed_blocks"]
    ):
        findings.append(
            {
                "severity": "red",
                "code": "legacy_document_system_active",
                "message": "仍存在受管 1.x 工件",
            }
        )
    recorded = (
        set(config.get("migration", {}).get("preserved_paths", []))
        if isinstance(config.get("migration"), dict)
        else set()
    )
    unrecorded = sorted(set(cleanup["preserved_paths"]) - recorded)
    if unrecorded:
        findings.append(
            {
                "severity": "yellow",
                "code": "unrecorded_preserved_paths",
                "message": ", ".join(unrecorded),
            }
        )
    ignored = git_ignored_install_paths(target, portable_install_paths())
    if ignored:
        findings.append(
            {
                "severity": "red",
                "code": "git_delivery_ignored",
                "message": ", ".join(ignored),
            }
        )
    return findings

def project_delivery(target: Path) -> dict[str, Any]:
    root = git_root(target)
    if root is None:
        return {
            "delivery_status": "not_applicable",
            "clone_ready": False,
            "required_commit_paths": [],
            "ignored_paths": [],
        }
    ignored = git_ignored_install_paths(target, portable_install_paths())
    pending: list[str] = []
    for relative in portable_install_paths():
        path = target / relative
        try:
            git_relative = path.resolve(strict=False).relative_to(root).as_posix()
        except ValueError:
            pending.append(relative)
            continue
        if git_command(root, "cat-file", "-e", f"HEAD:{git_relative}").returncode != 0:
            pending.append(relative)
            continue
        if git_command(root, "diff", "--quiet", "HEAD", "--", git_relative).returncode != 0:
            pending.append(relative)
    status = "blocked" if ignored else ("pending_commit" if pending else "in_head")
    return {
        "delivery_status": status,
        "clone_ready": status == "in_head",
        "required_commit_paths": sorted(set(pending)),
        "ignored_paths": ignored,
    }

def project_knowledge_summary(target: Path) -> dict[str, Any]:
    managed_root = target / KNOWLEDGE_SPEC.root
    if managed_root.is_dir() and not managed_root.is_symlink():
        return {
            "status": "available",
            "source": "managed_knowledge_assets",
            "managed_by_harness": True,
            "active_assets": len(list(managed_root.glob("*.json"))),
        }
    if (target / "docs").is_dir():
        return {
            "status": "available",
            "source": "project_docs",
            "managed_by_harness": False,
        }
    return {"status": "absent", "source": None, "managed_by_harness": False}

def project_read_only_command(
    args: argparse.Namespace, target: Path, source_root: Path
) -> tuple[int, dict[str, Any]] | None:
    if args.action == "diff":
        cleanup = legacy_cleanup_plan(target)
        return 0, {
            "action": "diff",
            "target": str(target),
            "changes": project_changes(target, source_root),
            "legacy_document_cleanup": cleanup,
        }
    if args.action == "check":
        findings = project_findings(target)
        red = [item for item in findings if item["severity"] == "red"]
        yellow = [item for item in findings if item["severity"] == "yellow"]
        delivery = project_delivery(target)
        pending = not red and delivery["delivery_status"] == "pending_commit"
        return (1 if red else 3 if pending else 0), {
            "action": "check",
            "target": str(target),
            "status": "failed" if red else ("needs_delivery" if pending else "passed"),
            **delivery,
            "red": len(red),
            "yellow": len(yellow),
            "findings": findings,
            "knowledge": project_knowledge_summary(target),
        }
    return None

def command_project(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = safe_target(args.target)
    raw_source = getattr(args, "source", None)
    if raw_source and args.action not in {"init", "upgrade"}:
        raise HarnessError("--source 仅支持 init 与 upgrade", code="invalid_request")
    source_root = (
        resolve_source_root(raw_source, target) if raw_source else SCRIPT_ROOT
    )
    if args.action in {"init", "upgrade"}:
        existing = project_config(target)
        from_version = existing.get("version") if existing else None
        cleanup = legacy_cleanup_plan(target)
        changes = project_changes(target, source_root)
        if not args.apply:
            notices = project_upgrade_notices(
                action=args.action,
                from_version=from_version,
                existing=existing,
                usage_enabled=USAGE_LOG_DEFAULT_ENABLED,
            )
            return 0, {
                "action": args.action,
                "mode": "preview",
                "target": str(target),
                "source": str(source_root),
                "source_is_target": source_root == target,
                "from_version": from_version,
                "to_version": VERSION,
                "changes": changes,
                "legacy_document_cleanup": cleanup,
                "manual_migrations": cleanup["conflicts"],
                "apply_completion_possible": cleanup["cleanup_possible"],
                "write_performed": False,
                "knowledge": project_knowledge_summary(target),
                **({"notices": notices} if notices else {}),
            }
        changed, cleanup_applied = apply_project_install(target, source_root)
        githook_index_registered = register_githook_index_mode(target)
        findings = project_findings(target)
        red = [item for item in findings if item["severity"] == "red"]
        delivery = project_delivery(target)
        pending = not red and delivery["delivery_status"] == "pending_commit"
        notices = project_upgrade_notices(
            action=args.action,
            from_version=from_version,
            existing=existing,
            usage_enabled=project_config(target)["usage_log"]["enabled"],
        )
        status = (
            "failed"
            if red
            else (
                "needs_delivery"
                if pending
                else ("installed" if args.action == "init" else "upgraded")
            )
        )
        return (1 if red else 3 if pending else 0), {
            "action": args.action,
            "mode": "apply",
            "target": str(target),
            "source": str(source_root),
            "source_is_target": source_root == target,
            "from_version": from_version,
            "to_version": VERSION,
            "version": VERSION,
            "status": status,
            **delivery,
            "planned_changes": changes,
            "changed": changed,
            "githook_activation_hint": (
                "git 钩子已安装到 scripts/githooks/（不自动修改 git 配置）；"
                "如需启用入库 pre-commit（提交时执行 assets-check --fast），"
                "请执行一次 scripts/githooks/setup.sh"
            ),
            # core.filemode=false 时已把这些钩子以 100755 登记进暂存区（内容一并暂存）。
            "githook_index_mode_registered": githook_index_registered,
            "findings": findings,
            "legacy_document_cleanup": cleanup_applied,
            "preserved_existing_docs": True,
            "knowledge": project_knowledge_summary(target),
            **({"notices": notices} if notices else {}),
        }
    read_only = project_read_only_command(args, target, source_root)
    if read_only is not None:
        return read_only
    cleanup = legacy_cleanup_plan(target)
    if not args.apply:
        return 0, {
            "action": "uninstall",
            "mode": "preview",
            "target": str(target),
            "would_remove": [
                "Docs Harness managed AGENTS.md/CLAUDE.md blocks",
                ".docs-harness/config.json",
                "owned scripts/harness.py",
                "owned asset lifecycle modules",
                "owned plan-templates files",
                "owned scripts/githooks files",
                "installed pre-commit shim in git hook directory",
                *cleanup["remove_files"],
                *cleanup["remove_directories"],
            ],
            "would_preserve": cleanup["preserved_paths"],
            "purge_runtime": bool(args.purge_runtime),
        }
    if cleanup["conflicts"]:
        raise HarnessError(
            "卸载前发现不安全旧路径，未写入",
            code="legacy_document_cleanup_conflict",
            exit_code=3,
            extra_payload={"legacy_document_cleanup": cleanup},
        )
    config = project_config(target)
    removed = apply_legacy_cleanup(target, cleanup)
    for relative, begin, end in (
        ("AGENTS.md", MANAGED_BEGIN, MANAGED_END),
        ("CLAUDE.md", CLAUDE_BEGIN, CLAUDE_END),
    ):
        path = target / relative
        if path.is_file():
            before = path.read_text(encoding="utf-8")
            after = remove_managed_block(before, begin, end)
            if after != before:
                atomic_write_text(path, after)
                removed.append(f"{relative}:managed_block")
    script = target / "scripts" / "harness.py"
    if (
        script.is_file()
        and config
        and config.get("installed_script_fingerprint") == file_fingerprint(script)
    ):
        script.unlink()
        removed.append("scripts/harness.py")
    installed_modules = config.get("installed_module_fingerprints", {}) if config else {}
    if isinstance(installed_modules, dict):
        for relative, fingerprint in installed_modules.items():
            path = target / "scripts" / str(relative)
            if (
                relative in MANAGED_MODULE_RELATIVE_FILES
                and path.is_file()
                and file_fingerprint(path) == fingerprint
            ):
                path.unlink()
                removed.append(f"scripts/{relative}")
    installed_templates = (
        config.get("installed_plan_template_fingerprints", {}) if config else {}
    )
    if isinstance(installed_templates, dict):
        for relative, fingerprint in installed_templates.items():
            path = target / PLAN_TEMPLATES_RELATIVE / str(relative)
            if (
                relative in PLAN_TEMPLATE_RELATIVE_FILES
                and path.is_file()
                and file_fingerprint(path) == fingerprint
            ):
                path.unlink()
                removed.append(f"{PLAN_TEMPLATES_RELATIVE}/{relative}")
        for directory in (
            target / PLAN_TEMPLATES_RELATIVE / "levels",
            target / PLAN_TEMPLATES_RELATIVE / "profiles",
            target / PLAN_TEMPLATES_RELATIVE,
        ):
            with contextlib.suppress(OSError):
                directory.rmdir()
    installed_githooks = (
        config.get("installed_githook_fingerprints", {}) if config else {}
    )
    if isinstance(installed_githooks, dict):
        for relative, fingerprint in installed_githooks.items():
            path = target / GIT_HOOKS_RELATIVE / str(relative)
            if (
                relative in GIT_HOOK_RELATIVE_FILES
                and path.is_file()
                and file_fingerprint(path) == fingerprint
            ):
                path.unlink()
                removed.append(f"{GIT_HOOKS_RELATIVE}/{relative}")
        with contextlib.suppress(OSError):
            (target / GIT_HOOKS_RELATIVE).rmdir()
    # 清理 setup.sh 装进真实钩子目录的转发 shim（按标记识别，不动他人钩子）。
    hooks_dir = git_hook_directory(target)
    if hooks_dir is not None:
        shim = hooks_dir / "pre-commit"
        try:
            shim_owned = shim.is_file() and GITHOOK_SHIM_MARKER in shim.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            shim_owned = False
        if shim_owned:
            shim.unlink()
            removed.append(str(shim))
    config_path = target / ".docs-harness" / "config.json"
    if config_path.is_file():
        config_path.unlink()
        removed.append(".docs-harness/config.json")
    if args.purge_runtime:
        for parent in migration_runtime_parents(target):
            for name in (*LEGACY_RUNTIME_NAMES, "v2"):
                path = parent / name
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                    removed.append(migration_display_path(target, path))
    return 0, {
        "action": "uninstall",
        "mode": "apply",
        "target": str(target),
        "removed": list(dict.fromkeys(removed)),
        "project_docs_preserved": True,
        "preserved_paths": cleanup["preserved_paths"],
        "quality_ledger_preserved": True,
    }

def read_version_sources(root: Path) -> dict[str, str | None]:
    sources: dict[str, str | None] = {
        "VERSION": (
            root.joinpath("VERSION").read_text(encoding="utf-8").strip()
            if root.joinpath("VERSION").is_file()
            else None
        ),
        "controller": None,
        "skill": None,
        "package": None,
        "templates": None,
        "evals": None,
    }
    script = root / "scripts" / "harness.py"
    if script.is_file():
        match = re.search(
            rf"(?m)^VERSION\s*=\s*[\"']({SEMVER_PATTERN})[\"']\s*$",
            script.read_text(encoding="utf-8"),
        )
        sources["controller"] = match.group(1) if match else None
    sources["skill"] = _skill_version(root)
    package = root / "package.json"
    if package.is_file():
        value = read_json(package)
        if isinstance(value, dict) and isinstance(value.get("version"), str):
            sources["package"] = value["version"]
    sources["templates"] = read_template_versions(root)
    sources["evals"] = _evals_version(root)
    return sources

def _skill_version(root: Path) -> str | None:
    """SKILL.md frontmatter 的 version；文件缺失或字段缺失返回 None。

    抛出面按搬移前保留：read_text 对非 UTF-8 内容抛 UnicodeDecodeError、IO 故障抛
    OSError（注意都不是 HarnessError——这一段不走 read_json）。parse_frontmatter 是
    纯字符串处理，对任何输入都只返回 ({}, text)，畸形 frontmatter 经 get 自然得 None。
    """
    skill = root / "SKILL.md"
    if not skill.is_file():
        return None
    metadata, _ = parse_frontmatter(skill.read_text(encoding="utf-8"))
    return metadata.get("version")

def _evals_version(root: Path) -> str | None:
    """evals/evals.json 顶层 version 字符串；文件缺失或字段非字符串返回 None。

    抛出面按搬移前保留：经 read_json，不可读或非法 JSON 抛 HarnessError(invalid_json)。
    """
    evals_file = root / "evals" / "evals.json"
    if not evals_file.is_file():
        return None
    value = read_json(evals_file)
    if isinstance(value, dict) and isinstance(value.get("version"), str):
        return value["version"]
    return None

def is_source_package(target: Path) -> bool:
    """target 是不是 docs-harness 源包本身（而不是装了 harness 的下游项目）。

    判据是源包独有的两个标记文件同时带版本：SKILL.md 的 frontmatter version 与
    evals/evals.json 的 version。安装器只写 scripts/、plan-templates/、
    scripts/githooks/、AGENTS.md、CLAUDE.md、.docs-harness/config.json 与 docs/ 骨架
    （见 portable_install_paths），永不写这两个文件。不看 package.json 与 VERSION：
    任意 npm 或前端下游都可能有它们，作为判别式过弱；也不看 plan-templates：那本就是
    被安装的。

    只读这两个文件、不复用 read_version_sources 整体，是为了不把 package.json 与 8 个
    模板 JSON 的 read_json 崩溃面带进结构检查——结构检查此前根本不读 package.json。

    读取失败一律判为"不是源包"。这不是吞错：本函数是分类器，标记文件不可读在语义上
    就等于没有源包标记；release sync 那条链路上同样的损坏必须炸，两个调用方两份契约。
    被视为不可读的三种形态（已实测）：(a) evals/evals.json 非法 JSON 或不可读 →
    HarnessError(invalid_json)；(b) SKILL.md 非 UTF-8 字节 → UnicodeDecodeError；
    (c) 两个文件的权限或 IO 故障 → OSError。捕获清单按实际抛出面逐项列出，不写裸 Exception。
    """
    try:
        return _skill_version(target) is not None and _evals_version(target) is not None
    except (HarnessError, OSError, UnicodeDecodeError):
        return False

def structure_exempt_paths(target: Path) -> frozenset[str]:
    """Structure 检查对 target 应排除的路径：下游排除 harness 自带文件，源包不排除。

    源包不排除是守卫所在：排除了就丢掉"新增受管模块未登记 CODEMAP"这道检查。
    清单在此单点构造——受管模块不能反向 import harness.py（循环依赖），
    structure_check 不复制安装清单。githooks 两项当前是空集贡献（pre-commit 无后缀、
    setup.sh 的 .sh 都不在 structure_check.CODE_SUFFIXES），仍列入以保持与安装面同源。
    """
    if is_source_package(target):
        return frozenset()
    return frozenset(
        [
            "scripts/harness.py",
            *(f"scripts/{relative}" for relative in MANAGED_MODULE_RELATIVE_FILES),
            *(f"{GIT_HOOKS_RELATIVE}/{relative}" for relative in GIT_HOOK_RELATIVE_FILES),
        ]
    )

def read_template_versions(root: Path) -> str | None:
    """读取全部方案模板的 version 字段：任一缺失/非法返回 None；不一致返回排序拼接串。"""
    versions: set[str] = set()
    for relative in PLAN_TEMPLATE_RELATIVE_FILES:
        path = root / PLAN_TEMPLATES_RELATIVE / relative
        if not path.is_file():
            return None
        value = read_json(path)
        version = value.get("version") if isinstance(value, dict) else None
        if not isinstance(version, str):
            return None
        versions.add(version)
    if len(versions) != 1:
        return "+".join(sorted(versions)) or None
    return next(iter(versions))

def changelog_top_version(root: Path) -> str | None:
    path = root / "CHANGELOG.md"
    if not path.is_file():
        return None
    match = re.search(
        rf"(?m)^##\s+\[?v?({SEMVER_PATTERN})\]?",
        path.read_text(encoding="utf-8"),
    )
    return match.group(1) if match else None

def update_json_version(raw: str, version: str) -> str:
    pattern = re.compile(
        r'(?m)^([ \t]*"version"[ \t]*:[ \t]*")' + SEMVER_PATTERN + r'(")'
    )
    if len(pattern.findall(raw)) != 1:
        raise HarnessError(
            "JSON version 字段不唯一",
            code="release_managed_file_unrecognized",
        )
    return pattern.sub(rf"\g<1>{version}\g<2>", raw, count=1)

def update_skill_version(raw: str, version: str) -> str:
    if not raw.startswith("---\n"):
        raise HarnessError(
            "SKILL.md 缺少 frontmatter",
            code="release_managed_file_unrecognized",
        )
    end = raw.find("\n---\n", 4)
    if end < 0:
        raise HarnessError(
            "SKILL.md frontmatter 不完整",
            code="release_managed_file_unrecognized",
        )
    front = raw[4:end]
    pattern = re.compile(
        rf"(?m)^([ \t]*version[ \t]*:[ \t]*)([\"']?){SEMVER_PATTERN}([\"']?)[ \t]*$"
    )
    matches = list(pattern.finditer(front))
    if len(matches) != 1 or matches[0].group(2) != matches[0].group(3):
        raise HarnessError(
            "SKILL.md version 字段不唯一",
            code="release_managed_file_unrecognized",
        )
    match = matches[0]
    new_front = (
        front[: match.start()]
        + f"{match.group(1)}{match.group(2)}{version}{match.group(3)}"
        + front[match.end() :]
    )
    return raw[:4] + new_front + raw[end:]

def apply_release_writes(writes: Sequence[tuple[Path, str]]) -> None:
    staged: list[tuple[Path, Path, bytes]] = []
    try:
        for path, content in writes:
            if not path.is_file() or path.is_symlink():
                raise HarnessError(
                    f"版本真源不是可写常规文件：{path.name}",
                    code="release_write_failed",
                    exit_code=1,
                )
            original = path.read_bytes()
            fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            temp = Path(raw)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                with contextlib.suppress(FileNotFoundError):
                    temp.unlink()
                raise
            if temp.read_bytes() != content.encode("utf-8"):
                raise HarnessError(
                    f"版本真源临时文件校验失败：{path.name}",
                    code="release_write_failed",
                    exit_code=1,
                )
            staged.append((path, temp, original))
        replaced: list[tuple[Path, bytes]] = []
        try:
            for path, temp, original in staged:
                os.replace(temp, path)
                replaced.append((path, original))
        except OSError as exc:
            for path, original in reversed(replaced):
                with contextlib.suppress(OSError):
                    path.write_bytes(original)
            raise HarnessError(
                "版本真源写入失败，已回滚",
                code="release_write_failed",
                exit_code=1,
            ) from exc
    finally:
        for _, temp, _ in staged:
            with contextlib.suppress(FileNotFoundError):
                temp.unlink()

def command_release(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = safe_target(args.target)
    sources = read_version_sources(target)
    truth = sources["controller"]
    if truth is None:
        raise HarnessError(
            "无法读取控制器 VERSION",
            code="release_source_unreadable",
            exit_code=1,
        )
    if args.target_version and args.target_version != truth:
        raise HarnessError(
            "--target-version 与控制器不一致",
            code="release_version_conflict",
        )
    missing = [name for name, value in sources.items() if value is None]
    diffs = [
        {"source": name, "expected": truth, "actual": value}
        for name, value in sources.items()
        if value is not None and value != truth
    ]
    payload: dict[str, Any] = {
        "action": "sync",
        "target": str(target),
        "version_truth": truth,
        "sources": sources,
        "diffs": diffs,
        "changelog_top_version": changelog_top_version(target),
    }
    if not args.apply:
        changelog_top = payload["changelog_top_version"]
        strict_changelog_fail = (
            bool(getattr(args, "strict", False)) and changelog_top != truth
        )
        payload.update(
            {
                "mode": "check",
                "status": (
                    "unreadable"
                    if missing
                    else "inconsistent"
                    if diffs or strict_changelog_fail
                    else "consistent"
                ),
            }
        )
        if missing:
            payload["missing_sources"] = missing
        if strict_changelog_fail:
            payload["strict_failures"] = [
                f"CHANGELOG 顶部版本 {changelog_top or '(缺失)'} 与 VERSION {truth} 不一致"
            ]
        return (1 if missing or strict_changelog_fail else 2 if diffs else 0), payload
    if missing:
        raise HarnessError(
            "版本真源缺失",
            code="release_source_unreadable",
            exit_code=1,
        )
    changed: list[str] = []
    writes: list[tuple[Path, str]] = []
    builders = {
        "VERSION": lambda raw: f"{truth}\n",
        "package.json": lambda raw: update_json_version(raw, truth),
        "SKILL.md": lambda raw: update_skill_version(raw, truth),
    }
    source_names = {
        "VERSION": "VERSION",
        "package.json": "package",
        "SKILL.md": "skill",
    }
    for relative, builder in builders.items():
        if sources[source_names[relative]] == truth:
            continue
        path = target / relative
        raw = path.read_text(encoding="utf-8")
        updated = builder(raw)
        if updated != raw:
            writes.append((path, updated))
            changed.append(relative)
    if sources["templates"] != truth:
        for template_relative in PLAN_TEMPLATE_RELATIVE_FILES:
            relative = f"{PLAN_TEMPLATES_RELATIVE}/{template_relative}"
            raw = target.joinpath(relative).read_text(encoding="utf-8")
            updated = update_json_version(raw, truth)
            if updated != raw:
                writes.append((target / relative, updated))
                changed.append(relative)
    if sources["evals"] != truth:
        relative = "evals/evals.json"
        raw = target.joinpath(relative).read_text(encoding="utf-8")
        updated = update_json_version(raw, truth)
        if updated != raw:
            writes.append((target / relative, updated))
            changed.append(relative)
    if writes:
        apply_release_writes(writes)
    strict_changelog_fail = (
        bool(getattr(args, "strict", False))
        and payload["changelog_top_version"] != truth
    )
    if strict_changelog_fail:
        payload["strict_failures"] = [
            f"CHANGELOG 顶部版本 {payload['changelog_top_version'] or '(缺失)'} 与 VERSION {truth} 不一致"
        ]
    payload.update(
        {
            "mode": "apply",
            "status": "synced" if changed else "already_consistent",
            "changed": changed,
        }
    )
    return (1 if strict_changelog_fail else 0), payload

def _plan_check_result(
    *,
    status: str,
    strict: bool,
    fast: bool,
    live_count: int,
    archived_count: int,
    markdown_count: int,
    failures: list[str],
    warnings: list[str],
    show_preview: bool,
) -> tuple[int, dict[str, Any]]:
    payload: dict[str, Any] = {
        "status": status,
        "strict": strict,
        "fast": fast,
        "checked": {
            "live_docs": live_count,
            "archived_docs": archived_count,
            "markdown_files": markdown_count,
        },
        "failures": failures,
        "warnings": warnings,
        "warning_summary": summarize_warnings(warnings),
        "summary": (
            f"plan check {status}：活文档 {live_count} 份、归档 {archived_count} 份、"
            f"扫描 markdown {markdown_count} 份，违规 {len(failures)} 条、警告 {len(warnings)} 条"
        ),
    }
    if show_preview:
        preview = preview_warnings(warnings)
        if preview:
            payload["warnings_preview"] = preview
            omitted = len(warnings) - len(preview)
            if omitted:
                payload["warnings_omitted"] = omitted
    return (0 if status == "passed" else 1), payload


def command_plan_check(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """docs/plans 文档卫生常驻检查：横幅、索引闭环、死链、取值、符号存活性与时效。"""
    target = safe_target(args.target)
    plans_dir = target / "docs" / "plans"
    index_path = target / "docs" / "INDEX.md"
    missing: list[str] = []
    if not plans_dir.is_dir():
        missing.append("docs/plans/")
    if not index_path.is_file():
        missing.append("docs/INDEX.md")
    if missing:
        reason = "方案文档体系不完整：" + "、".join(missing) + " 不存在"
        return 1, {
            "status": "failed",
            "reason": reason,
            "failures": ["FAIL: " + reason + "；请先运行 project init/upgrade --apply"],
            "warnings": [],
        }
    live_docs = sorted(
        path for path in plans_dir.glob("*.md")
        if path.name != "README.md" and path.is_file() and not path.is_symlink()
    )
    archive_dir = plans_dir / "archive"
    archived_names = (
        sorted(
            path.name
            for path in archive_dir.glob("*.md")
            if path.is_file() and not path.is_symlink()
        )
        if archive_dir.is_dir()
        else []
    )
    failures: list[str] = []
    warnings: list[str] = []

    # C1/C4：活文档前 3 行必须有状态横幅，且横幅取值合法。
    banners: dict[str, str] = {}
    for path in live_docs:
        relative = path.relative_to(target).as_posix()
        banner = plan_check_banner(path)
        if banner is None:
            failures.append(f"FAIL: {relative}: 前 3 行内缺少状态横幅（状态：）")
            continue
        banners[relative] = banner
        if not any(state in banner for state in PLAN_CHECK_BANNER_STATES):
            failures.append(
                f"FAIL: {relative}: 横幅取值非法，须含 有效/已实施-仅追溯/已废弃 之一"
            )
        elif "已废弃" in banner:
            warnings.append(
                f"WARN: {relative}: 活文档横幅含已废弃但仍在 docs/plans/ 根目录，应移入 archive/"
            )

    # C2：活文档必须进 INDEX.md 且条目带关键符号；归档文档必须退出活索引。
    # 匹配带 plans/ 前缀的 token 而非裸子串：acceptance/ 等其他区块存在同名
    # 文档时不得误伤；同时接受 Markdown 链接与反引号路径两种存量条目形态。
    index_lines = index_path.read_text(encoding="utf-8").splitlines()
    for path in live_docs:
        relative = path.relative_to(target).as_posix()
        basename = path.name
        tokens = plan_index_doc_tokens(basename)
        entries = [
            line for line in index_lines
            if any(token in line for token in tokens)
        ]
        if not entries:
            failures.append(f"FAIL: docs/INDEX.md: 缺少 {relative} 的条目")
        else:
            symbol_sets = [
                re.findall(r"`([^`]+)`", line.split("关键符号", 1)[1])
                for line in entries if "关键符号" in line
            ]
            if not any(2 <= len(symbols) <= 4 for symbols in symbol_sets):
                failures.append(
                    f"FAIL: docs/INDEX.md: {basename} 条目须包含 2-4 个关键符号"
                )
            entry = canonical_index_entry(entries, basename)
            warnings.extend(plan_projection_warnings(relative, entry, banners.get(relative), *load_plan_projection(path.with_suffix(".json"))))
    for basename in archived_names:
        # 同名活文档存在时，活索引条目指向的是活文档（plans/<basename>），不构成归档泄漏；
        # 「新文档同名取代归档草稿」是受支持的工作流。
        if any(path.name == basename for path in live_docs):
            continue
        tokens = plan_index_doc_tokens(basename)
        leaked = [
            line for line in index_lines
            if any(token in line for token in tokens)
            and PLAN_CHECK_ARCHIVE_EXEMPTION not in line
        ]
        if leaked:
            failures.append(
                f"FAIL: docs/INDEX.md: 归档文档 {basename} 仍出现在活索引条目中"
            )

    # C7：Harness 生成的 Markdown 与冻结 JSON 必须成对存在。
    for path in (*live_docs, *(archive_dir / name for name in archived_names)):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if PLAN_DOCUMENT_MARKER not in content.splitlines()[:3]:
            continue
        companion = path.with_suffix(".json")
        if not companion.is_file() or companion.is_symlink():
            failures.append(
                f"FAIL: {path.relative_to(target).as_posix()}: 缺少同名冻结 JSON"
            )
            continue
        try:
            validate_frozen_plan(read_json(companion))
        except HarnessError:
            failures.append(
                f"FAIL: {companion.relative_to(target).as_posix()}: 冻结合同无效"
            )

    # C3：全仓 .md 不得引用已归档文档的旧路径 docs/plans/<basename>。
    markdown_files = plan_check_markdown_files(target)
    markdown_contents: list[tuple[Path, str]] = []
    for path in markdown_files:
        try:
            markdown_contents.append((path, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    for basename in archived_names:
        # 同名活文档存在时 docs/plans/<basename> 指向活文档（新文档同名取代归档草稿），不构成死链。
        if any(path.name == basename for path in live_docs):
            continue
        stale = re.compile(r"docs/plans/" + re.escape(basename) + r"(?![\w.-])")
        for path, content in markdown_contents:
            if stale.search(content):
                relative = path.relative_to(target).as_posix()
                failures.append(
                    f"FAIL: {relative}: 引用已归档文档旧路径 docs/plans/{basename}"
                    f"（正确路径 docs/plans/archive/{basename}）"
                )

    fast = bool(getattr(args, "fast", False))

    # C5：镜像为已实施-仅追溯的条目，其关键符号必须在 docs/ 之外的源码内容中至少命中 1 处。
    # 源码白名单遍历：构建产物里的旧符号不算「代码仍是真源」的证据，二进制/资产文件也无须读入。
    # C8（同一次遍历的反向预警）：横幅为有效的条目，关键符号全部命中源码说明代码很可能
    # 已交付而 plan 未 settle——下游实证过的结算泄漏形态（assets-check --fast 不跑本检查）。
    # 时效内登记为部分交付的方案不参与反向预警，也就不必为它扫描符号。
    trace_pending: dict[str, list[str]] = {}
    active_symbols: dict[str, list[str]] = {}
    today = dt.date.today()
    if not fast:
        active_basenames = {
            Path(relative).name
            for relative, banner in banners.items()
            if "有效" in banner and not partial_delivery_checked_recently(banner, today)
        }
        for line in index_lines:
            if "关键符号" not in line:
                continue
            match = re.search(r"plans/([\w.-]+\.md)", line)
            basename = match.group(1) if match else "(未知条目)"
            symbols = re.findall(r"`([^`]+)`", line.split("关键符号", 1)[1])
            if not symbols:
                continue
            if "已实施-仅追溯" in line:
                trace_pending[basename] = symbols
            elif basename in active_basenames and not plan_has_remaining_steps(target, basename):
                active_symbols[basename] = symbols
    if not fast and (trace_pending or active_symbols):
        landed: dict[str, set[str]] = {basename: set() for basename in active_symbols}
        needed_symbols = {
            symbol
            for symbols in (*trace_pending.values(), *active_symbols.values())
            for symbol in symbols
        }
        prune = PLAN_CHECK_EXCLUDED_DIRS | PLAN_CHECK_ARTIFACT_DIRS
        for path in plan_check_walk_files(target, prune):
            if not trace_pending and all(
                len(landed[basename]) == len(symbols)
                for basename, symbols in active_symbols.items()
            ):
                break
            if path.suffix.lower() not in PLAN_CHECK_SOURCE_SUFFIXES:
                continue
            parts = path.relative_to(target).parts
            if parts[0] == "docs":
                continue
            if not path.is_file() or path.is_symlink():
                continue
            try:
                if path.stat().st_size > PLAN_CHECK_SYMBOL_MAX_FILE_BYTES:
                    continue
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            hits = symbol_hits(content, needed_symbols, path.suffix)
            for basename, symbols in list(trace_pending.items()):
                if any(symbol in hits for symbol in symbols):
                    del trace_pending[basename]
            for basename, symbols in active_symbols.items():
                if len(landed[basename]) == len(symbols):
                    continue
                landed[basename].update(symbol for symbol in symbols if symbol in hits)
        for basename in trace_pending:
            warnings.append(
                f"WARN: docs/INDEX.md: 条目 {basename} 镜像为已实施-仅追溯"
                "但关键符号在 docs/ 之外零命中，需复核代码是否仍是真源"
            )
        for basename, symbols in active_symbols.items():
            if landed[basename] == set(symbols):
                warnings.append(
                    f"WARN: docs/plans/{basename}: 横幅为有效但关键符号已全部在源码命中，"
                    "代码很可能已交付；已交付请 plan settle --status implemented，不再推进请 "
                    "--status deprecated；仍在推进的部分交付方案把横幅改为"
                    f"「> 状态：{PLAN_PARTIAL_DELIVERY_BANNER}（{today.isoformat()} 核对）」，"
                    f"{PLAN_PARTIAL_DELIVERY_RECHECK_DAYS} 天内不再提示"
                )

    # C6：横幅为有效的活文档长期未触碰告警；无 git 历史或 git 不可用时静默跳过。
    if not fast:
        now = dt.datetime.now(dt.timezone.utc)
        for relative, banner in banners.items():
            if "有效" not in banner:
                continue
            if partial_delivery_checked_recently(banner, today):
                continue
            result = git_command(target, "log", "-1", "--format=%ci", "--", relative)
            if result.returncode != 0 or not result.stdout.strip():
                continue
            try:
                touched = dt.datetime.strptime(
                    result.stdout.decode("utf-8", errors="replace").strip(),
                    "%Y-%m-%d %H:%M:%S %z",
                )
            except ValueError:
                continue
            if (now - touched) > dt.timedelta(days=PLAN_CHECK_STALE_DAYS):
                warnings.append(
                    f"WARN: {relative}: 横幅为有效但超过 {PLAN_CHECK_STALE_DAYS} 天未触碰"
                    f"（最后提交 {touched.date().isoformat()}），需确认是否仍然有效"
                )

    strict = bool(getattr(args, "strict", False))
    failed = bool(failures) or (strict and bool(warnings))
    return _plan_check_result(
        status="failed" if failed else "passed",
        strict=strict,
        fast=fast,
        live_count=len(live_docs),
        archived_count=len(archived_names),
        markdown_count=len(markdown_files),
        failures=failures,
        warnings=warnings,
        show_preview=not getattr(args, "json", False) and not getattr(args, "verbose", False),
    )

def command_assets_check(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """统一编排四类资产与脚本卫生检查；是否启用资产不由本命令推断。"""
    target = safe_target(args.target)
    payload = run_assets_check(
        target,
        fast=bool(getattr(args, "fast", False)),
        strict=bool(getattr(args, "strict", False)),
        plan_checker=lambda current, quick: command_plan_check(argparse.Namespace(
            target=str(current), strict=False, fast=quick
        ))[1],
        knowledge_checker=check_knowledge_assets,
        acceptance_checker=check_acceptance_assets,
        adr_checker=check_adr_assets,
        script_hygiene_checker=check_script_line_endings,
        structure_checker=lambda current: check_structure(
            current, exempt=structure_exempt_paths(current)
        ),
    )
    if not getattr(args, "json", False) and not getattr(args, "verbose", False):
        preview = preview_warnings(payload["warnings"])
        if preview:
            payload["warnings_preview"] = preview
            omitted = len(payload["warnings"]) - len(preview)
            if omitted:
                payload["warnings_omitted"] = omitted
    return (0 if payload["status"] == "passed" else 1), payload

def command_report(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """收尾报告字段校验；路径相对 target 解析，绝对路径直接使用。"""
    target = safe_target(args.target)
    raw = Path(args.path)
    path = raw if raw.is_absolute() else target / raw
    if not path.is_file():
        raise HarnessError(
            f"收尾报告不存在：{args.path}",
            code="report_missing",
            next_action="传 --path .docs-harness/reports/<YYYY-MM-DD>-<任务名>.md",
        )
    try:
        payload = check_report(path)
    except ValueError as exc:
        raise HarnessError(
            f"收尾报告无法读取：{args.path}：{exc}",
            code="report_unreadable",
        ) from exc
    payload["report_ref"] = (
        path.relative_to(target).as_posix() if path.is_relative_to(target) else str(path)
    )
    if payload["missing"]:
        payload["next_action"] = "补齐 missing 字段后重跑 report check；字段名见 FIELD_LABELS"
    return (0 if payload["status"] == "passed" else 1), payload

def command_structure(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """structure check/report 的 CLI 投影；规则实现与阈值真源在 structure_check 模块。"""
    target = safe_target(args.target)
    exempt = structure_exempt_paths(target)
    if args.action == "report":
        if getattr(args, "base", "HEAD") != "HEAD":
            raise HarnessError(
                "structure report 不支持 --base；该参数仅用于 structure check",
                code="unsupported_flag",
            )
        return 0, structure_report(target, exempt=exempt)
    base = getattr(args, "base", "HEAD")
    if base != "HEAD":
        probe = git_command(target, "rev-parse", "--verify", base)
        if probe.returncode != 0:
            raise HarnessError(
                f"structure check --base 引用不存在：{base}",
                code="invalid_base",
                extra_payload={"next_action": "传一个存在的 commit/ref，例如 HEAD~1"},
            )
    return 0, check_structure(target, exempt=exempt, base=base)

def command_usage(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """usage report 的 CLI 投影；聚合规则真源在 usage_report 模块。

    报告内容本身永不触发非零退出（旁路观察不做门禁）；只有参数非法与日志不可读
    两种真实错误走 HarnessError，与其余命令的错误面一致。
    """
    target = safe_target(args.target)
    if args.days <= 0:
        raise HarnessError("usage report 的 --days 必须是正整数", code="invalid_request")
    try:
        return 0, build_usage_report(target, args.days)
    except OSError as exc:
        raise HarnessError(
            f"无法读取 usage 日志：{exc}", code="usage_log_unreadable"
        ) from exc

def command_view(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """view 的 CLI 投影：渲染、落盘与打开在 diagram_view，这里只把异常映射成错误码。

    UnicodeDecodeError 是 ValueError 的子类，必须排在 ValueError 之前捕获。
    """
    target = safe_target(args.target)
    try:
        written = write_view(target, Path(args.source).expanduser().resolve())
    except FileNotFoundError as exc:
        raise HarnessError(str(exc), code="view_source_missing") from exc
    except UnicodeDecodeError as exc:
        raise HarnessError(f"Mermaid 源文件不是 UTF-8：{args.source}", code="view_source_not_utf8") from exc
    except ValueError as exc:
        raise HarnessError(str(exc), code="view_source_empty") from exc
    relative = written.relative_to(target).as_posix()
    if args.open:
        try:
            open_in_browser(written)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise HarnessError(
                f"页面已写入 {relative}，但无法用系统浏览器打开：{exc}",
                code="view_open_failed",
                extra_payload={"path": relative},
            ) from exc
    return 0, {"status": "written", "path": relative, "opened": args.open}

def command_self_test(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = safe_target(args.target)
    config = project_config(target)
    source_distribution = all(
        (SCRIPT_ROOT / relative).is_file()
        for relative in ("VERSION", "SKILL.md", "package.json")
    )
    version_sources = read_version_sources(SCRIPT_ROOT) if source_distribution else {}
    installed_script = target / "scripts" / "harness.py"
    script_version_valid = (
        all(value == VERSION for value in version_sources.values())
        if source_distribution
        else bool(
            config
            and config.get("version") == VERSION
            and config.get("schema_version") == CONFIG_SCHEMA
            and installed_script.is_file()
            and config.get("installed_script_fingerprint")
            == file_fingerprint(installed_script)
        )
    )
    strict_parse_ok = True
    try:
        build_parser().parse_args(["plan", "check", "--strict", "--fast"])
        build_parser().parse_args(["assets-check", "--strict", "--fast"])
    except SystemExit:
        strict_parse_ok = False
    checks = {
        "script_version": script_version_valid,
        "command_parser": all(
            name in build_parser().format_help()
            for name in ("knowledge", "plan", "acceptance", "adr", "project", "release", "assets-check", "structure", "usage", "self-test")
        ),
        "asset_check_flags": strict_parse_ok,
        "direct_mode_default": (
            True
            if source_distribution
            else config.get("direct_mode", {}).get("default") is True
        ),
        "plan_templates_valid": bool(
            template_fingerprints(SCRIPT_ROOT / PLAN_TEMPLATES_RELATIVE)
        ),
        "asset_modules_valid": bool(managed_module_fingerprints(SCRIPT_ROOT / "scripts")) and (
            True
            if source_distribution
            else config.get("installed_module_fingerprints")
            == managed_module_fingerprints(SCRIPT_ROOT / "scripts")
        ),
        "project_config_v13": (
            True
            if source_distribution
            else config.get("schema_version") == CONFIG_SCHEMA
        ),
        "v3_acceptance_contract": (
            ACCEPTANCE_INPUT_SCHEMA.endswith("/v3")
            and ACCEPTANCE_RECORD_SCHEMA.endswith("/v3")
        ),
        "knowledge_lifecycle_v1": KNOWLEDGE_SPEC.schema.endswith("/v1"),
        "acceptance_lifecycle_v1": ACCEPTANCE_SPEC.schema.endswith("/v1"),
        "adr_lifecycle_v1": ADR_SPEC.schema.endswith("/v1"),
    }
    passed = all(checks.values())
    return (0 if passed else 1), {
        "version": VERSION,
        "status": "passed" if passed else "failed",
        "checks": checks,
    }

def add_target(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", default=".")
    parser.add_argument("--json", action="store_true")

def add_check_options(parser: argparse.ArgumentParser) -> None:
    add_target(parser)
    parser.add_argument("--strict", action="store_true", help="WARN 也使退出码非 0（供 CI 使用）")
    parser.add_argument("--fast", action="store_true", help="跳过符号存活性与 Git 时效慢检查")

def _schema_example_block(
    heading: str,
    example: dict[str, Any] | None = None,
    notes: Sequence[str] = (),
) -> str:
    """把某个输入 JSON 的示例常量渲染成一段 --help 文本。"""
    lines = [heading]
    if example is not None:
        lines.append(json.dumps(example, ensure_ascii=False, indent=2))
    lines.extend(notes)
    return "\n".join(lines)

_EPILOG_INTRO = "输入 JSON 形状与示例（示例常量在本文件对应校验函数附近，改 schema 请同步）："

KNOWLEDGE_EPILOG = _EPILOG_INTRO + "\n\n" + _schema_example_block(
    f"knowledge create|update --input（{KNOWLEDGE_INPUT_SCHEMA}）：",
    KNOWLEDGE_INPUT_EXAMPLE,
)

PLAN_EPILOG = _EPILOG_INTRO + "\n\n" + "\n\n".join((
    _schema_example_block(
        "plan create --content（字段动态，以 plan select 输出的 fields 为准）：",
        PLAN_CONTENT_EXAMPLE,
        PLAN_CONTENT_NOTES,
    ),
    _schema_example_block(
        f"plan settle --governance-input（{PLAN_GOVERNANCE_INPUT_SCHEMA}）：",
        PLAN_GOVERNANCE_INPUT_EXAMPLE,
    ),
    "plan settle 也接受无伴随 JSON、无 Harness 文档标记的手写方案 Markdown：只改横幅（deprecated 另归档并改写链接），"
    "不做治理终验、不接受 --governance-input；受管方案区块外的 INDEX 条目不改写，按 warnings 手工同步。",
    "plan show --plan <path>：只读返回冻结 content 与完整 execution_projection；手写方案返回 markdown。",
    "plan check [--fast] [--strict]：docs/plans 文档可发现性常驻检查（横幅、索引符号、归档死链、符号存活与时效）。",
    f"部分交付仍在推进的方案横幅写「{PLAN_PARTIAL_DELIVERY_BANNER}（YYYY-MM-DD 核对）」，"
    f"核对日起 {PLAN_PARTIAL_DELIVERY_RECHECK_DAYS} 天内不报符号全命中告警，未来日期不豁免。",
    "plan amend --content：形状与 plan create --content 相同（整包 content 替换）；"
    "不得改变 acceptance_required/knowledge_impact；已 settle 或归档方案拒绝。",
))

ACCEPTANCE_EPILOG = _EPILOG_INTRO + "\n\n" + "\n\n".join((
    _schema_example_block(
        f"acceptance create --input（{ACCEPTANCE_TARGET_INPUT_SCHEMA}）：",
        ACCEPTANCE_TARGET_INPUT_EXAMPLE,
        ACCEPTANCE_TARGET_INPUT_NOTES,
    ),
    _schema_example_block(
        f"acceptance record --input（{ACCEPTANCE_INPUT_SCHEMA}）：",
        ACCEPTANCE_INPUT_EXAMPLE,
        ACCEPTANCE_INPUT_NOTES,
    ),
    _schema_example_block(
        f"acceptance settle --input（{ACCEPTANCE_SETTLE_INPUT_SCHEMA}）：",
        ACCEPTANCE_SETTLE_INPUT_EXAMPLE,
        ACCEPTANCE_SETTLE_INPUT_NOTES,
    ),
    "acceptance amend --acceptance <path> --input <JSON> --reason <原因>：输入形状同 acceptance create；"
    "仅 pending 且尚无任何 records 的资产可改 criteria，plan_ref 不得改变。",
))

ADR_EPILOG = _EPILOG_INTRO + "\n\n" + _schema_example_block(
    f"adr create --input（{ADR_INPUT_SCHEMA}）：",
    ADR_INPUT_EXAMPLE,
    (
        "定稿后不得手改；内容修正用 adr amend --input（同 create 形状）并必填 --reason。"
        "失效时 adr settle --status deprecated|superseded（superseded 需 --replacement）。",
    ),
)

def add_plan_parser(commands: argparse._SubParsersAction) -> None:
    plan = commands.add_parser(
        "plan",
        help="选择、冻结并维护任务方案",
        epilog=PLAN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    plan.add_argument("action", choices=("select", "create", "show", "list", "amend", "settle", "check"))
    add_target(plan)
    plan.add_argument("--strict", action="store_true", help="WARN 也使退出码非 0（供 CI 使用；仅 check）")
    plan.add_argument("--fast", action="store_true", help="跳过符号存活性与 Git 时效慢检查（仅 check）")
    plan.add_argument("--verbose", action="store_true", help="输出全部 WARN（仅 check；默认显示摘要与预览）")
    plan.add_argument("--level", choices=PLAN_LEVELS)
    plan.add_argument("--profile", choices=PLAN_PROFILES)
    plan.add_argument("--secondary-profile", action="append", choices=PLAN_PROFILES)
    plan.add_argument(
        "--complexity",
        choices=("simple", "moderate", "complex"),
        default="simple",
    )
    plan.add_argument("--surface", choices=PLAN_PROFILES, default="general")
    plan.add_argument("--cross-module", action="store_true")
    plan.add_argument("--high-risk", action="store_true")
    plan.add_argument("--user-requested-plan", action="store_true")
    plan.add_argument(
        "--task",
        help="任务描述（仅 select）：结果自动附带相关 Knowledge 事实",
    )
    plan.add_argument(
        "--query",
        help="误用拦截：plan select 不支持查询，请改用 knowledge query --query",
    )
    plan.add_argument("--selection")
    plan.add_argument("--content")
    plan.add_argument("--patch")
    plan.add_argument("--output")
    plan.add_argument(
        "--dry-run",
        action="store_true",
        help="仅校验（仅 create）：一次性报告全部输入错误与冲突，不写任何文件",
    )
    plan.add_argument("--plan")
    plan.add_argument("--status", choices=PLAN_SETTLE_STATUSES)
    plan.add_argument("--replacement")
    plan.add_argument("--governance-input")
    plan.add_argument(
        "--reason",
        help="修订原因（仅 amend）：单行非空",
    )

def add_acceptance_parser(commands: argparse._SubParsersAction) -> None:
    acceptance = commands.add_parser(
        "acceptance",
        help="创建、记录并维护分层验收资产",
        epilog=ACCEPTANCE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    acceptance.add_argument("action", choices=("create", "amend", "record", "show", "list", "settle", "check"))
    add_target(acceptance)
    acceptance.add_argument("--input")
    acceptance.add_argument("--output")
    acceptance.add_argument(
        "--dry-run",
        action="store_true",
        help="仅校验（仅 create）：一次性报告全部输入错误与冲突，不写任何文件",
    )
    acceptance.add_argument("--acceptance")
    acceptance.add_argument("--status", choices=ACCEPTANCE_SETTLE_STATUSES)
    acceptance.add_argument("--replacement")
    acceptance.add_argument("--user-confirmed", action="store_true")
    acceptance.add_argument("--reaccept", action="store_true")
    acceptance.add_argument(
        "--reason",
        help="修订原因（仅 amend）：单行非空",
    )

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description=f"Docs Harness v{VERSION} 独立辅助控制器",
    )
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)

    knowledge = commands.add_parser(
        "knowledge",
        help="创建、修订、查询并维护项目知识",
        epilog=KNOWLEDGE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    knowledge.add_argument("action", choices=("create", "update", "query", "show", "settle", "check"))
    add_target(knowledge)
    knowledge.add_argument("--query")
    knowledge.add_argument("--scope", action="append")
    knowledge.add_argument("--limit", type=int, default=5)
    knowledge.add_argument("--max-chars", type=int, default=6000)
    knowledge.add_argument("--input")
    knowledge.add_argument("--output")
    knowledge.add_argument("--knowledge")
    knowledge.add_argument("--status", choices=("deprecated", "superseded"))
    knowledge.add_argument("--replacement")

    add_plan_parser(commands)
    add_acceptance_parser(commands)

    adr = commands.add_parser(
        "adr",
        help="创建并维护架构决策记录（定稿不可改）",
        epilog=ADR_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    adr.add_argument("action", choices=("create", "amend", "show", "settle", "check"))
    add_target(adr)
    adr.add_argument("--input")
    adr.add_argument("--output")
    adr.add_argument("--adr")
    adr.add_argument("--status", choices=ADR_SETTLE_STATUSES)
    adr.add_argument("--replacement")
    adr.add_argument(
        "--reason",
        help="修订原因（仅 amend）：单行非空",
    )

    project = commands.add_parser(
        "project",
        help=f"{VERSION} 安装、单向升级、检查和卸载",
    )
    project.add_argument(
        "action",
        choices=("init", "upgrade", "check", "diff", "uninstall"),
    )
    add_target(project)
    project.add_argument(
        "--apply",
        action="store_true",
        help="init/upgrade/uninstall 只有带上它才写入；不带时只预览，不落任何文件",
    )
    project.add_argument("--purge-runtime", action="store_true")
    project.add_argument(
        "--source",
        help="init/upgrade 的来源包目录（默认使用当前控制器所在源包）",
    )

    release = commands.add_parser("release", help="版本真源一致性检查")
    release.add_argument("action", choices=("sync",), nargs="?", default="sync")
    add_target(release)
    release.add_argument("--apply", action="store_true")
    release.add_argument("--target-version")
    release.add_argument(
        "--strict", action="store_true", help="CHANGELOG 顶部版本与 VERSION 不一致时退出码非 0"
    )

    structure = commands.add_parser(
        "structure",
        help="结构护栏：check 增量体量与 CODEMAP 检查，report 存量结构债报告",
    )
    structure.add_argument("action", choices=("check", "report"))
    add_target(structure)
    structure.add_argument(
        "--base",
        default="HEAD",
        help="增量检查的对比基线（仅 check；默认 HEAD，可传任务起始 commit）",
    )

    usage = commands.add_parser(
        "usage", help="本地使用观测：report 聚合 .docs-harness/usage 的调用日志（只读，不外发）"
    )
    usage.add_argument("action", choices=("report",))
    add_target(usage)
    usage.add_argument(
        "--days", type=int, default=USAGE_REPORT_DEFAULT_DAYS, help="统计窗口天数（正整数，默认 %(default)s）"
    )

    report = commands.add_parser("report", help="收尾记录字段校验（改动路径/命令/退出码/验证层级/未覆盖）")
    report.add_argument("action", choices=("check",))
    add_target(report)
    report.add_argument("--path", required=True, help="收尾报告路径，相对 target 解析")

    view = commands.add_parser(
        "view", help=f"把 Mermaid 源码渲染成 {VIEWS_RELATIVE}/ 下的独立 HTML 页面"
    )
    view.add_argument("source", help="Mermaid 源码文件，整个文件是一张图（约定 .mmd；相对路径按当前目录解析）")
    add_target(view)
    view.add_argument("--open", action="store_true", help="写完后用系统默认浏览器打开")

    self_test = commands.add_parser("self-test", help=f"运行 {VERSION} 内置自检")
    add_target(self_test)

    assets_check = commands.add_parser(
        "assets-check",
        help="统一检查 Plan、Knowledge、Acceptance、ADR、脚本卫生、结构护栏与跨资产关系",
    )
    add_check_options(assets_check)
    assets_check.add_argument(
        "--verbose",
        action="store_true",
        help="输出全部 WARN；默认只显示分组摘要与少量预览",
    )
    return parser

def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    notices = payload.get("notices")
    preview = payload.get("warnings_preview")
    for key, value in payload.items():
        if key in ("notices", "warnings_preview"):
            continue
        if key == "warnings" and isinstance(preview, list):
            print(f"warnings: {len(value)} 条（预览 {len(preview)} 条，--verbose 查看全部）")
            for item in preview:
                print(f"- {item[:300]}{'…' if len(item) > 300 else ''}")
            continue
        if key == "warning_summary" and isinstance(value, dict):
            groups = "、".join(
                f"{item['group']} {item['count']}" for item in value.get("groups", [])
            )
            duplicates = value.get("duplicates", 0)
            print(
                f"warning_summary: {value.get('total', 0)} 条"
                f"（可行动 {value.get('actionable', 0)} / 提示 {value.get('advisory', 0)}"
                f"{f' / 重复 {duplicates}' if duplicates else ''}"
                f"{'；' + groups if groups else ''}）"
            )
            continue
        print(
            f"{key}: "
            + (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value)
            )
        )
    if isinstance(notices, list) and notices:
        print("")
        print("=" * 72)
        print("重要提示（请读）")
        print("=" * 72)
        for index, notice in enumerate(notices, start=1):
            prefix = f"[{index}] " if len(notices) > 1 else ""
            print(f"{prefix}{notice}")
        print("=" * 72)

# cmd.invoke 只收录枚举白名单 flag：全部来自 argparse 的 choices 或 store_true，
# 不含任何自由文本；改这里等于改事件契约，须同步 docs/contracts.md。
USAGE_FLAG_KEYS = ("status", "reaccept", "dry_run", "strict", "fast", "user_confirmed")
# payload 列表键 → 事件计数字段；命令 payload 里不存在该键时事件不写对应字段。
USAGE_COUNT_KEYS = (("facts", "hits"), ("failures", "failures"), ("warnings", "warnings"))
# 事件另记 payload["status"] 为 result：退出码不足以表达结果——acceptance record 退 3
# 表示记录已存入但整体验收未通过，而 3 在 project upgrade、plan settle 里语义又各不相同。
# status 取值全部来自命令 payload 的既有枚举（created/frozen/pending/passed/failed/
# error/dry_run_valid/needs_delivery…），不是自由文本。
# result=error 时另记 payload["code"] 为 error_code：HarnessError 的 code 是标识符枚举
# （invalid_plan_ref、acceptance_record_mismatch…），不记 message 自由文本；缺了它首次失败无从归因。

def usage_invoke_event(
    args: argparse.Namespace, code: int, payload: dict[str, Any], duration_ms: int
) -> dict[str, Any]:
    """把一次命令调用投影成 cmd.invoke 事件：只取枚举白名单与计数，不取自由文本。

    推导不出的字段直接不写键（不写 null），读侧统一 .get()，避免缺失与 null 两种空表示。
    """
    event: dict[str, Any] = {
        "v": USAGE_SCHEMA_VERSION,
        "ts": utc_now(),
        "event": "cmd.invoke",
        "command": args.command,
        "exit_code": code,
        "duration_ms": duration_ms,
    }
    action = getattr(args, "action", None)
    if action is not None:
        event["action"] = action
    status = payload.get("status")
    if isinstance(status, str):
        event["result"] = status
    error_code = payload.get("code")
    if status == "error" and isinstance(error_code, str):
        event["error_code"] = error_code
    flags = {key: getattr(args, key) for key in USAGE_FLAG_KEYS if getattr(args, key, None)}
    if flags:
        event["flags"] = flags
    for source, field in USAGE_COUNT_KEYS:
        value = payload.get(source)
        if isinstance(value, list):
            event[field] = len(value)
    return event

def record_usage_invoke(
    args: argparse.Namespace, code: int, payload: dict[str, Any], started: float
) -> None:
    """旁路记录一次命令调用；未安装或开关关闭时不记录，写入失败不影响命令结果。

    target 不经 safe_target：目录不存在时 is_enabled 读不到 config 自然返回 False，
    观察路径因此不需要捕获任何异常。
    """
    target = Path(args.target).expanduser().resolve()
    if not usage_log_enabled(target):
        return
    duration_ms = int((time.monotonic() - started) * 1000)
    append_usage_event(target, usage_invoke_event(args, code, payload, duration_ms))

def dispatch_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.command == "knowledge":
        return {
            "create": knowledge_create,
            "update": knowledge_update,
            "query": knowledge_query,
            "show": knowledge_show,
            "settle": knowledge_settle,
            "check": knowledge_check,
        }[args.action](args)
    if args.command == "plan":
        return {
            "select": plan_select,
            "create": plan_create,
            "show": plan_show,
            "list": plan_list,
            "amend": plan_amend,
            "settle": plan_settle,
            "check": command_plan_check,
        }[args.action](args)
    if args.command == "acceptance":
        return {
            "create": acceptance_create,
            "amend": acceptance_amend,
            "record": acceptance_record,
            "show": acceptance_show,
            "list": acceptance_list,
            "settle": acceptance_settle,
            "check": acceptance_check,
        }[args.action](args)
    if args.command == "adr":
        return {
            "create": adr_create,
            "amend": adr_amend,
            "show": adr_show,
            "settle": adr_settle,
            "check": adr_check,
        }[args.action](args)
    if args.command == "project":
        return command_project(args)
    if args.command == "release":
        return command_release(args)
    if args.command == "assets-check":
        return command_assets_check(args)
    if args.command == "report":
        return command_report(args)
    if args.command == "structure":
        return command_structure(args)
    if args.command == "usage":
        return command_usage(args)
    if args.command == "view":
        return command_view(args)
    return command_self_test(args)

def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(raw)
    started = time.monotonic()
    try:
        dry_run_actions = {
            ("plan", "create"), ("acceptance", "create"),
            ("plan", "amend"), ("acceptance", "amend"),
            ("acceptance", "record"), ("acceptance", "settle"),
        }
        if getattr(args, "dry_run", False) and (
            args.command, getattr(args, "action", None)
        ) not in dry_run_actions:
            raise HarnessError(
                f"{args.command} {args.action} 不支持 --dry-run（仅 create/amend/record/settle 有预检），去掉后重跑",
                code="dry_run_unsupported",
            )
        validate_action_flags(args, raw, HarnessError)
        code, payload = dispatch_command(args)
        emit(payload, args.json)
        record_usage_invoke(args, code, payload, started)
        return code
    except HarnessError as exc:
        payload = {
            "status": "error",
            "code": exc.code,
            "message": str(exc),
            **exc.extra_payload,
        }
        emit(payload, getattr(args, "json", False))
        record_usage_invoke(args, exc.exit_code, payload, started)
        return exc.exit_code
    except Exception as exc:
        payload = {
            "status": "error",
            "code": "internal_error",
            "message": str(exc),
            "details": traceback.format_exc(),
            "next_action": "这是未预期异常；报告时附上 details，便于定位",
        }
        emit(payload, getattr(args, "json", False))
        record_usage_invoke(args, 1, payload, started)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
