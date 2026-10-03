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
import uuid
from pathlib import Path
from typing import Any, Sequence
SCRIPT_MODULE_DIR = Path(__file__).resolve().parent
if str(SCRIPT_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_MODULE_DIR))

from managed_assets import AssetError, apply_structure, structure_changes
from asset_checks import run_assets_check
from script_hygiene import check_script_line_endings
from structure_check import CODEMAP_SCAFFOLD, check_structure, structure_report
from plan_governance import (
    FAILURE_ATTRIBUTION_CATEGORIES, PLAN_GOVERNANCE_INPUT_SCHEMA, PLAN_SCHEMA_V3, PlanGovernanceError,
    collect_bugfix_plan_errors,
    governance_from_content, legacy_plan_template_fingerprints, new_plan_fields, nonempty_string_list,
    prepare_settlement as prepare_plan_settlement, render_governance_block, update_plan_markdown,
    validate_bugfix_plan_contract, validate_plan as validate_governed_plan,
)
from knowledge_assets import (
    KNOWLEDGE_INPUT_SCHEMA,
    KNOWLEDGE_SPEC,
    check as check_knowledge_assets,
    create as create_knowledge_asset,
    query as query_knowledge_assets,
    settle as settle_knowledge_asset,
    update as update_knowledge_asset,
)
from acceptance_assets import (
    ACCEPTANCE_EVIDENCE_LAYERS,
    ACCEPTANCE_LAYERS,
    ACCEPTANCE_SETTLE_INPUT_SCHEMA,
    ACCEPTANCE_SPEC,
    ACCEPTANCE_TARGET_INPUT_SCHEMA,
    check as check_acceptance_assets,
    collect_create_errors as collect_acceptance_create_errors,
    create as create_acceptance_asset,
    record as record_acceptance_asset,
    settle as settle_acceptance_asset,
)
from adr_assets import (
    ADR_INPUT_SCHEMA,
    ADR_SETTLE_STATUSES,
    ADR_SPEC,
    check as check_adr_assets,
    create as create_adr_asset,
    settle as settle_adr_asset,
)
from usage_log import (
    USAGE_LOG_DEFAULT_ENABLED,
    USAGE_SCHEMA_VERSION,
    append_event as append_usage_event,
    is_enabled as usage_log_enabled,
)
from usage_report import USAGE_REPORT_DEFAULT_DAYS, build_report as build_usage_report
VERSION = "2.25.0"
CONFIG_SCHEMA = "docs-harness/project-config/v13"
KNOWN_LEGACY_CONFIG_SCHEMAS = {
    f"docs-harness/project-config/v{version}" for version in range(1, 13)
}
PLAN_TEMPLATE_SCHEMA = "docs-harness/plan-template/v3"
PLAN_SELECTION_SCHEMA = "docs-harness/plan-selection/v2"
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
# 不入库、升级不清理的本地约定目录；共用下方嵌套忽略与 local_only_dir_changes 一份判定。
LOCAL_ONLY_DIRS = (TASK_INPUTS_RELATIVE, TASKS_RELATIVE)
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
    "structure_check.py",
    "structure_ts_functions.cjs",
    "usage_log.py",
    "usage_report.py",
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
    ) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.extra_payload = extra_payload or {}


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
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarnessError(f"无法读取 JSON：{path}", code="invalid_json") from exc


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


_GENERIC_STANDARDS = """
## 协作准则

- **按意图交付**：动手前用一句话说出用户拿这个要去做什么，按那个目标交付，而不是按字面。只影响交付完整度的歧义按更完整的理解做，并写明假设；会改变目标、方案或验收的歧义按工作流规则第 1 条先确认。常见映射：要"给 X 的文档"→能直接转交的自包含形态（文档+数据+代码+验证方式）；要"方案"→结论+依据+代价+明确推荐，不是选项清单；要"改一下"→改动+测试+受影响文档同步；要"跑一下"→结果+是否符合预期+异常处解释；问"对不对"→结论+核对方式+不一致处具体差在哪；要"看看"→判断+理由+建议的下一步。
- **前提有误先纠正**：用户的问题里带着错误前提时，第一句先说清哪部分对、哪部分不对，再按正确的前提交付，不顺着错误前提干活。被指出做错时，先确认错在哪、影响到哪些产出，把受影响的产出改回来再继续，不一边认错一边保留错误结果。
- **结论标明证据等级**：每个判断要么附证据（命令与退出码、文件:行、实测值），要么标明「推测」并写出验证方式；推测不放在结论位置，也不用确定语气。否定结论（没有、不存在、全都）门槛最高：写明查了哪些范围，空结果先排除命令报错、写法错误和范围遗漏，能加正对照就加（先查一个已知存在的对象，确认查询本身有效）。数值与行为预测先跑最小实验，来不及就标「估计」并给出依据；从样本推到全体时写明样本数。
- **完整不等于扩大范围**：同一交付物缺的部分、验证、依赖说明、文档同步自己补上；重构没让改的模块、改没提过的配置、优化没人抱怨的性能、删文件、提交代码等范围扩张先问用户。修改面约束见"不顺手加固"。
- **可开工自检**：交付前自问：接收方拿到后，能不能不回来追问就开工？不能就把缺的部分一起补上，不等追问。

## 工作流规则

每条规则自带触发条件；不满足触发条件的部分不启用，无需另行豁免。需要停下来等用户的只有三种情况：本文写明的确认点、用户另有要求、原生授权提示。其余步骤不需要用户输入时直接继续，进度说明与下一步动作放在同一条消息里，不以阶段汇报、"是否继续"的征询或不阻塞工作的选项清单收尾。

1. **验收先行**：动手前先把验收条件转写为可执行的验证方式（测试、命令或复现步骤），完成与否以此为准。验收标准明确时直接执行，验证结果随收尾报告交付；仅当验收标准缺失或有歧义、且不同理解会改变方案时，先向用户确认。
2. **根因优先**：修复 bug 前先定位根因并列出影响面（含同根因可能导致的其他表现）。根因清楚且修复局部、可逆时直接修，根因分析随收尾报告交付；根因跨模块、修复不可逆或存在代价不同的多个方案时，先经用户确认再改代码。定位读够、修改最少：找根因与影响面时上溯到状态所有者、下查到全部消费者，调用方用符号检索确认、不凭 CODEMAP 推断；"最小改动"只约束修改面，不得借此缩小阅读面。方案交给用户前（bug 修复、需用户确认的方案、Plan）逐项自检：① 修复是否落在状态所有者层，而非在消费者处打补丁；② 影响面清单每一条是否都被消除；③ 每处改动删掉后修复是否仍成立，仍成立即多余，删掉；④ 是否新增兜底、重试或兼容分支且拿不出状态可达的证据。任一项不过先改方案再输出；输出附一行自检结论，方案被修正过时写明改了什么。
3. **回归必跑**：交付代码改动前，跑受影响模块的回归验证并附输出（模块级，非仓库级全量；全量测试的触发条件见"测试与验收范围"）。涉及工具 handler/状态机/workflow 的改动不因任务小而豁免：须逐段给出消费链确认证据——改了生产者不查消费者，是隐性回归的首要来源；消费者跨两个以上模块时按第 5 条分头并行确认。
4. **分批交付**：改动跨模块数据流或预计 >3 个文件时分批执行。批次划分写入进度清单并随首批一并报告，每批标注依赖（`B2 ← B1` 或 `独立`）与文件范围；有依赖的批次串行走"改完 → 验证 → 锁定 → 下一批"（锁定指验证通过后该批不再回改，是验证门，不是停下汇报的点），互相独立且文件范围不相交的批次按第 5 条并行，各自验证后由主 agent 统一集成验证再锁定。仅当某批含不可逆或高风险动作时，先经用户确认。
5. **并行优先**：任务拆出多个互不依赖的分支时，按分量选执行方式，不默认串行。单点任务直接做；轻量独立子任务（同时读几个文件、几个独立检索、几条独立命令）用同一条消息内的并行工具调用，不开子智能体；分支各自够重（需多步调研、评审，或文件范围不相交的实施）才同消息并行开子智能体，分支不重则 spawn 开销净亏。子智能体任务书必须带明确目标、验收条件、路径范围与文件白名单，汇报只回结论与证据路径，不回传文件内容；主 agent 核对证据支持结论后才采纳，不直接转述子智能体的结论。以下保持串行：修改同一文件、更新 CODEMAP/CHANGELOG/TODO/Knowledge 等受管公共文件（由主 agent 收尾统一写）、存在依赖的步骤、每批的验证门。并行不豁免第 3 条：分支回流后主 agent 仍跑一次集成验证。

## 编码质量规范

写代码时以下规则与任务要求同级，违反任何一条都算任务未完成：

1. **先复用，后新写。** 动手前必须搜索仓库里是否已有相同或相近的实现；存在就复用或扩展，不得平行再写一份。无法复用时，在收尾报告里说明搜过什么、为何不能复用。
2. **重复即抽象。** 同一段逻辑出现第 3 次时必须抽成独立函数或模块，并让原有调用点改用它；不允许复制粘贴后微调。
3. **分层隔离。** 界面/渲染代码、业务逻辑、外部 IO（文件、网络、进程间通信、系统调用）不得混在同一个函数里。
4. **接口先行。** 新增模块必须先定义对外接口和数据类型，再写实现；对外暴露的东西越少越好，默认不导出。
5. **体量预警。** 单个函数超过 60 行、单个文件超过 600 行时必须进行结构评估。存在多重职责、跨层混合、独立变化方向或难以独立测试时，应按职责拆分；职责单一且拆分只会增加跳转、耦合或样板代码时可以保留，并在收尾报告中说明理由。不得仅为满足行数阈值机械切割。
6. **错误不许吞。** 每个错误要么正确处理，要么明确向上传递；不得静默忽略或仅打印日志后继续。
7. **不擅自加依赖。** 新增第三方库前必须说明：解决什么问题、为何标准库或现有依赖不够。未经用户确认不引入。
8. **收尾自查。** 报告改动时额外回答两个问题：本次是否引入了重复逻辑（答"没有"要说明依据）；本次新增的抽象各自的职责是什么。答不出视为未完成。
9. **业务默认值单一来源。** 所有业务默认值必须定义为具名常量并在唯一位置赋值，不得在 fallback、反序列化、条件分支中硬编码。修改默认值时只改一处，不会因遗漏产生隐性分歧。
10. **抽象同样要证据。** 抽公共模块或加抽象层需要真实证据：第 3 次重复出现，或已存在的第二个实现；不为尚未出现的复用场景预留抽象层。与第 2 条互为边界：两次重复时保持原样，三次必抽。

## 结构护栏

编码质量规范的机械支撑面；五条各自独立触发。

1. **动手前查 CODEMAP。** 写代码前先查 `docs/CODEMAP.md` 定位可复用模块与其公开接口，命中即复用；新增代码文件或公开接口变化时，同一批次内更新对应条目（格式：`模块路径` — 职责：一句话；公开接口：`符号`）。测试文件不必登记。
2. **骨架先行（复杂任务）。** Full Plan 的 `module_interfaces` 字段冻结模块划分与接口骨架；实施先落文件与接口签名（空实现），再分批填充逻辑，不得绕开骨架直接堆代码。
3. **增量检查随批次跑。** `assets-check` 内置 Structure 增量检查（WARN 级）；分批交付的每批验证点可用 `structure check` 单独快跑，WARN 按收尾规则转达，确实拆不动的说明理由即可。
4. **存量债走定期整理。** 既有超红线文件/函数不在功能任务里顺手重构（见"不顺手加固"）；需要偿还时运行 `structure report` 拿存量清单，以报告开专门整理任务。
5. **搜索面收敛。** 禁止无界递归检索——不得从仓库根对 `.` 做递归搜索，也不得让工具自己决定范围；路径必须落到本次任务相关的具体目录或文件，够用即止，并排除 `node_modules`、`.git`、构建产物（`dist`/`build`/`out`/`coverage`/`target`/`__pycache__`）、依赖缓存与生成物目录，大目录写宽了扫不出结果还拖慢任务。委派给子智能体的检索同样受此约束：任务书里的路径范围就是它的搜索边界，不得让子智能体自行决定范围。

## 防御代码准入

修复落在状态或数据的所有者层，优先恢复不变量；不得在各消费者层层加兜底。

1. **证据准入**：新增 fallback、retry、兼容分支、catch-and-continue、数据修复或重复校验前，必须有当前接口契约或可复现运行证据证明该状态确实可达；拿不出证据的潜在风险只写进收尾报告，不转化为代码。
2. **边界一次校验**：数据在边界完成校验与规范化后，下游信任其类型与不变量，不再重复防御。
3. **不顺手加固**：不以"更保险""顺手加固"为理由扩大修改面；顺手发现的问题走报告，不走代码。

## 提示词与代码流程同步

提示词是代码流程的投影，两者必须同步。错位一处，弱模型就卡一处。

1. **代码加了门禁，提示词就要写明前置步骤。** 如果代码要求先调用工具 A 解锁工具 B，提示词里就必须写"先调用 A，再执行后续步骤"。不能让模型自己推理出这层依赖关系。
2. **代码自动注入数据，提示词就要说清模型不用管。** 如果渲染工具会自动从已确认的 Spec 读取文案，提示词要明确写"文案已自动加载，你只需提供映射关系"。否则模型会花大量 token 焦虑"我看不到内容怎么办"。
3. **状态字段必须自解释。** 一个状态值对应一个明确动作。如果模型需要组合多个字段才能判断该做什么，就会在弱模型上出错。拍成单字段线性流转。
4. **提示词只给当前步骤的指令。** 不要把所有阶段的规则堆在一起让模型自己筛选。每个阶段的提示词只描述该阶段该做什么，用编号步骤而非散文段落。
5. **改了代码流程就要同步改提示词，反之亦然。** 每次修改工具解锁顺序、状态迁移逻辑或数据注入方式后，必须检查对应的提示词是否仍然准确。两者不同步是 AI 工作流 bug 的首要来源。

## 测试与验收范围：聚焦优先，按风险扩展

- 修复 Bug 或实现改动时，默认按"最小复现或最小验证 → 改动验证 → 受影响模块测试 → 必要的相邻契约或真实流程"逐层验证，达到最小充分证据后停止。
- "受影响模块测试"覆盖该模块的完整测试集；"仓库级全量测试"指跨越本次影响面的全仓测试、全平台矩阵、全量 race 或完整端到端套件。
- 仅在以下条件至少满足一项时运行仓库级全量测试：
  - 用户明确要求；
  - 修改涉及公共基础设施、共享依赖、构建链、全局配置、协议或跨模块公共契约；
  - 影响边界经调查后仍不能可靠收敛；
  - 聚焦测试发现系统性风险；
  - 合并、发布、打包或正式验收入口明确要求。
- 不得仅以"更保险"或"让证据更充分"为理由运行全量测试，也不得重复运行已通过且输入未变的测试。确需全量时，先说明触发条件、拟执行命令和已知成本。
- 全量测试发现的既有失败、环境失败或 flaky 失败必须单独归因和报告，不得算作本次修复失败，也不得借机修改无关代码。
- 聚焦测试通过不等于安装包、真实设备或外部服务通过——这些层级只在任务需要时分别验收。收尾时应报告实际执行的命令、退出结果、覆盖层级和未覆盖风险。

## 方案、知识与验收资产

- plans 文档卫生（状态横幅、索引符号、归档死链、符号存活与时效）由 `plan check` 把关，pre-commit 与 CI 的 assets-check 已包含；起草期间不跑，提交前或 plan settle 时跑一次，报错即改。判定纪律：代码里找不到符号只能证明概念已死，不能证明方案过期（合法待实施方案同样没有代码）；证据不足标"存疑"，交用户裁决；符号全命中但仍在推进的部分交付方案按 WARN 提示登记核对日，不得为消除 WARN 而 settle。
- WARN 消费：收尾时 assets-check 输出与本任务领域相关的 WARN，必须在收尾报告中转达，不得静默略过。
- Plan：复杂任务先 `plan select` 再 `plan create --output docs/plans/<name>.json` 冻结执行合同（自动生成同名 Markdown 并维护 docs/INDEX.md）；实施完成运行 `plan settle --status implemented`，被取代或废弃用 `--status deprecated`；无伴随 JSON 的手写方案同样直接 settle，不得手工补造冻结 JSON。不手工复制平行方案。Full Plan 声明验收与知识影响，settle 时校验；收尾按 Knowledge → Acceptance → Plan 顺序结算，声明需要验收的先完成 Acceptance 结项。
- Knowledge：只记录有当前源码或项目文档证据支持的可复用事实：按需 `knowledge query`，沉淀 `create`，事实变化 `update`，被替代或废弃 `settle`，收尾 `check`。不得凭模型猜测自动写知识。
- Acceptance：复杂任务在 Plan 后 `acceptance create` 建立目标，真实验证后逐条 `acceptance record`，证据文件必须位于随仓库提交的路径（如 docs/acceptance/evidence/<验收名>/）；失败修复后重新验收，最终 `acceptance settle` 并 `acceptance check`。简单任务直接验证，不强制创建资产。只有收到用户明确确认原话后才能记录 User Acceptance 通过；合同、测试、运行、安装和用户可见层不得相互替代。
- 收尾统一运行 `assets-check`。提交时 pre-commit 钩子执行 `assets-check --fast`，GitHub CI 执行 `assets-check --strict`（新克隆机器先运行 `scripts/githooks/setup.sh` 激活钩子）。项目自定义提交检查写入 `scripts/githooks/pre-commit.local`，不得分叉修改受管 pre-commit。

## 收尾

结论先行，不把推理过程当答案；「先行」只管顺序、不豁免验证，证据不够时先行的结论就是「未查实，下一步查 X」；结论之后先列需要用户决定或确认的事项（含所做的假设、偏离用户要求之处），没有就写"无"；再报告实际改动路径、执行命令与退出结果、验收层、未覆盖项和剩余风险，发现多个问题时按严重性排序。失败、跳过、未完成的部分单独写明，不藏在成功的叙述里。没有证据时不得声称完成。"""


def _managed_content() -> str:
    """Harness 运行模式 + 通用规范：AGENTS.md 受管区块的正文；CLAUDE.md 经 CLAUDE_IMPORT 读到它。"""
    return f"""## Docs Harness {VERSION}：默认直跑，能力按需

- 普通问答、只读检查、代码修改、构建和测试默认不经 Harness 流程直接执行；Harness 不作为任务入口，也不创建任务控制状态。"直接"指不走 Harness，不指主 agent 亲自串行完成，任务如何拆分与委派见工作流规则第 5 条。
- 用户明确说“不使用 Harness”时必须直接执行，不得暗中恢复旧流程。
- 只有缺少的项目事实会改变目标、范围、方案或验收时才运行 knowledge query；需要长期维护的事实才进入 Knowledge 资产生命周期。
- 简单任务不生成方案；复杂、跨模块、高风险或用户明确要求时才走 Plan 与 Acceptance 资产流程，命令与结算顺序见"方案、知识与验收资产"。
- 验收以真实功能为中心：能运行聚焦测试、接口、页面、应用、构建或安装流程时运行最小充分流程；改动产生运行态行为（页面、接口、应用、命令或安装流程）的任务完成后，agent 必须自己走一遍详细的运行态验证（模拟器/本地联调，可用 mock 数据），确认功能流程正常、视觉与交互对用户友好，发现不友好之处直接重新优化并复验，不把功能、视觉或交互体验的验证推给用户；纯文档、只读或不改变行为的任务只做与改动对应的验证，非运行态交付物按接收方的用法验证：方案沿执行路径推演一遍，找第一个卡住的步骤及其代价；核对换一条独立路径重算，看是否收敛到同一答案；文档以零基础读者身份从头读一遍，找出用到但没给的前提；仅真实硬件、系统权限等本地确实无法运行的层准备最低成本环境交用户最短确认。
- 高风险动作使用原生授权与沙箱，不建立第二套 Harness Gate 或授权协议。
- Plan/Knowledge/Acceptance/ADR 的输入 JSON 形状、必填字段与 --dry-run 预检见 python3 scripts/harness.py <cmd> --help；校验失败的报错直接附期望形状；一次性输入 JSON 写入 `{TASK_INPUTS_RELATIVE}/`（不入库、升级不清理）。
- 预计跨多批次或可能经历上下文压缩的长任务，把进度清单写入 `{TASKS_RELATIVE}/<任务名>.md`（不入库、升级不清理）：完成一项勾一项，新发现的事项随时补进去，查进度以这个文件为准，不以对话记录为准。有 Plan 的任务在清单开头写明 Plan 路径，条目按 Plan 的步骤或批次列出、只记进度，不复述 Plan 内容；Plan 是冻结合同，进度不写回 Plan。
- 需要项目架构或历史事实时，先查当前源码与符号；仍缺关键事实再显式运行 knowledge query，不得全量加载 docs/。
- 不在没有证据或没有明确维护任务时自动更新 Knowledge、Changelog、TODO 或质量账本。架构决策由主 agent 通过 adr create 登记；决策失效时用 adr settle 废弃或标记被替代。
- 改动涉及用户可见行为、对外接口或命令契约、版本发布时同步更新 CHANGELOG；任务产生待跟进事项时登记 TODO；不满足触发条件则不更新。
{_GENERIC_STANDARDS}"""


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
    candidates: list[Path] = []
    docs = target / "docs"
    if docs.is_dir() and not docs.is_symlink():
        for path in docs.rglob("*"):
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
        start = max(0, position - 160)
        end = min(len(content), position + 360)
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
    return HarnessError(str(exc), code=exc.code, exit_code=1)


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
    document_facts, omitted = document_knowledge_query(
        target, scopes, tokens, remaining_limit, remaining_chars
    )
    facts = [*managed["facts"], *document_facts]
    refs = [item["ref"] for item in facts]
    return 0, {
        "mode": "knowledge_assist",
        "facts": facts,
        "refs": refs,
        "constraints": [],
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


def build_plan_selection(
    *,
    level: str,
    profile: str,
    secondary_profiles: Sequence[str],
    reason: str,
) -> dict[str, Any]:
    registry = template_registry(SCRIPT_ROOT / PLAN_TEMPLATES_RELATIVE)
    if not registry:
        raise HarnessError(f"{VERSION} 方案模板不完整", code="invalid_plan_registry")
    fields: list[dict[str, Any]] = []
    versions: list[str] = []
    if level != "none":
        level_key = f"levels/{level}.json"
        fields.extend(registry[level_key]["fields"])
        versions.append(f"level/{level}@{VERSION}")
    if level == "full":
        for current in (profile, *secondary_profiles):
            value = registry[PROFILE_FILES[current]]
            fields.extend(value["fields"])
            versions.append(f"profile/{current}@{VERSION}")
    selection: dict[str, Any] = {
        "schema_version": PLAN_SELECTION_SCHEMA,
        "plan_level": level,
        "plan_profile": profile,
        "secondary_profiles": list(secondary_profiles),
        "reason": reason,
        "template_versions": versions,
        "fields": unique_fields(fields),
    }
    selection["selection_fingerprint"] = sha256_json(selection)
    return selection


PLAN_SELECTION_CACHE_RELATIVE = "docs-harness/plan-selections"
PLAN_SELECT_KNOWLEDGE_LIMIT = 5
PLAN_SELECT_KNOWLEDGE_MAX_CHARS = 2000


def plan_selection_cache_dir(target: Path) -> Path | None:
    resolved = git_dir(target)
    if resolved is None:
        return None
    return resolved / PLAN_SELECTION_CACHE_RELATIVE


def cache_plan_selection(target: Path, selection: dict[str, Any]) -> bool:
    """把 plan select 结果缓存进 git 运行态目录，供 plan create --selection sha256:<fp> 引用。

    缓存只是便利性副本（真源仍是 selection 内容与其指纹校验），写入失败不阻断 select。"""
    directory = plan_selection_cache_dir(target)
    if directory is None:
        return False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        name = selection["selection_fingerprint"].removeprefix("sha256:") + ".json"
        atomic_write_json(directory / name, selection)
    except OSError:
        return False
    return True


def resolve_plan_selection_path(target: Path, raw: str) -> Path:
    if not raw.startswith("sha256:"):
        return project_input_path(target, raw, code="invalid_plan_selection")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", raw):
        raise HarnessError(
            "--selection 的 sha256 引用格式无效，应为 plan select 输出里的 selection_ref",
            code="invalid_plan_selection",
        )
    directory = plan_selection_cache_dir(target)
    candidate = (
        directory / (raw.removeprefix("sha256:") + ".json") if directory is not None else None
    )
    if candidate is None or not candidate.is_file() or candidate.is_symlink():
        raise HarnessError(
            f"未找到 {raw} 对应的 plan select 缓存。下一步：在本项目重新运行 plan select"
            "（其输出的 selection_ref 即为该指纹），或把 plan select 输出保存为项目内 JSON 文件后传文件路径",
            code="invalid_plan_selection",
        )
    return candidate


def plan_select_knowledge_payload(target: Path, task: str) -> dict[str, Any]:
    """plan select 的知识被动注入：--task 给任务描述时附相关事实，否则只给存在性提示。"""
    task = task.strip()
    if task:
        tokens = query_tokens(task)
        if tokens:
            try:
                managed = query_knowledge_assets(
                    target, tokens, PLAN_SELECT_KNOWLEDGE_LIMIT, PLAN_SELECT_KNOWLEDGE_MAX_CHARS
                )
            except AssetError as exc:
                return {"knowledge_hits": [], "knowledge_hits_error": str(exc)}
            return {
                "knowledge_hits": managed["facts"],
                "knowledge_conflicts": managed["conflicts"],
            }
    root = target / KNOWLEDGE_SPEC.root
    count = 0
    if root.is_dir() and not root.is_symlink():
        count = sum(1 for path in root.glob("*.json") if path.is_file() and not path.is_symlink())
    if count:
        return {
            "knowledge_hint": (
                f"项目存在 {count} 个活跃 Knowledge；plan select 加 --task <任务描述> 可自动附带相关事实，"
                "或用 knowledge query --query <关键词> 按需查询"
            )
        }
    return {}


def plan_select(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = safe_target(args.target)
    if getattr(args, "query", None):
        raise HarnessError(
            "plan select 不支持 --query；查询项目事实请使用 knowledge query --query <关键词>。"
            "下一步：去掉 --query 重跑 plan select（可加 --task <任务描述> 让结果自动附带相关 Knowledge）",
            code="invalid_plan_selection",
        )
    if args.level:
        level = args.level
        level_reason = "user_or_host_explicit"
    elif args.user_requested_plan or args.high_risk or args.cross_module or args.complexity == "complex":
        level = "full"
        level_reason = "effect_requires_full"
    elif args.complexity == "moderate":
        level = "brief"
        level_reason = "effect_requires_brief"
    else:
        level = "none"
        level_reason = "simple_direct_execution"
    profile = args.profile or args.surface or "general"
    secondary = list(dict.fromkeys(args.secondary_profile or []))
    secondary = [item for item in secondary if item != profile]
    if secondary and level != "full":
        raise HarnessError(
            "次级 Profile 只适用于 full 方案",
            code="invalid_plan_selection",
        )
    if len(secondary) > 2:
        raise HarnessError("次级 Profile 最多两个", code="invalid_plan_selection")
    reason = level_reason + ("; profile_explicit" if args.profile else "; profile_from_surface")
    selection = build_plan_selection(
        level=level,
        profile=profile,
        secondary_profiles=secondary,
        reason=reason,
    )
    payload: dict[str, Any] = dict(selection)
    payload["selection_ref"] = selection["selection_fingerprint"]
    payload["selection_cached"] = cache_plan_selection(target, selection)
    payload.update(plan_select_knowledge_payload(target, getattr(args, "task", None) or ""))
    return 0, payload


# plan select 输出的展示性附加键（不是选择合同的一部分）；agent 常把整份输出存成文件
# 传给 plan create --selection，校验前必须剥离，否则全等比较必然失败。
SELECTION_AUXILIARY_KEYS = (
    "selection_ref",
    "selection_cached",
    "knowledge_hits",
    "knowledge_conflicts",
    "knowledge_hint",
    "knowledge_hits_error",
)


def validate_selection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HarnessError("方案选择文件无效", code="invalid_plan_selection")
    value = {key: item for key, item in value.items() if key not in SELECTION_AUXILIARY_KEYS}
    if value.get("schema_version") != PLAN_SELECTION_SCHEMA:
        raise HarnessError("方案选择文件无效", code="invalid_plan_selection")
    level = value.get("plan_level")
    profile = value.get("plan_profile")
    secondary = value.get("secondary_profiles", [])
    reason = value.get("reason")
    if level not in PLAN_LEVELS or profile not in PLAN_PROFILES:
        raise HarnessError("方案 Level/Profile 未注册", code="invalid_plan_selection")
    if not isinstance(secondary, list) or any(item not in PLAN_PROFILES for item in secondary):
        raise HarnessError("次级 Profile 未注册", code="invalid_plan_selection")
    if not isinstance(reason, str) or not reason:
        raise HarnessError("方案选择缺少原因", code="invalid_plan_selection")
    expected = build_plan_selection(
        level=level,
        profile=profile,
        secondary_profiles=secondary,
        reason=reason,
    )
    if value != expected:
        raise HarnessError("方案选择与注册模板不一致", code="invalid_plan_selection")
    return expected


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
    selection: dict[str, Any], content: dict[str, Any], frozen: dict[str, Any]
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
    return (
        f"> 状态：{PLAN_STATUS_ACTIVE}\n{PLAN_DOCUMENT_MARKER}\n\n# {title}\n\n"
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
    "required=true 的字段必须非空，且不得出现未在 fields 中注册的字段。",
    "--selection 可直接传 plan select 输出的 selection_ref（sha256:<指纹>，缓存在本机 .git 运行态目录），"
    "或把 plan select 输出保存为项目内 JSON 文件后传文件路径。",
    "正式冻结前建议先跑一次 plan create --dry-run（同参数）：一次性返回全部字段错误，不写任何文件。",
)


def inspect_plan_create(
    target: Path, args: argparse.Namespace
) -> tuple[list[HarnessError], dict[str, Any] | None]:
    """收集 plan create 的全部输入错误（--dry-run 用）；无错误时返回各阶段产物。

    依赖失败的阶段会被跳过（与原逐字段抛错的不可达路径一致）；错误追加顺序与原
    抛错顺序一致，真实创建路径抛首个错误即与原行为等价。"""
    if not args.selection or not args.content or not args.output:
        return [HarnessError(
            "plan create 必须提供 --selection、--content 和 --output",
            code="missing_plan_input",
        )], None
    errors: list[HarnessError] = []
    selection: dict[str, Any] | None = None
    try:
        selection = validate_selection(
            read_json(resolve_plan_selection_path(target, args.selection))
        )
        if selection["plan_level"] == "none":
            errors.append(
                HarnessError("plan_level=none 不创建方案文件", code="plan_not_required")
            )
    except HarnessError as exc:
        errors.append(exc)
    content: dict[str, Any] | None = None
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
        fields = selection["fields"]
        allowed = {item["id"] for item in fields}
        required = {item["id"] for item in fields if item["required"]}
        unknown = sorted(set(content) - allowed)
        if unknown:
            errors.append(HarnessError(
                "方案包含未注册字段：" + ", ".join(unknown), code="invalid_plan_content"
            ))
        missing = sorted(
            field for field in required if not nonempty_plan_value(content.get(field))
        )
        if missing:
            errors.append(HarnessError(
                "方案缺少必填字段：" + ", ".join(missing), code="invalid_plan_content"
            ))
        try:
            title, symbols = plan_title_and_symbols(content)
        except HarnessError as exc:
            errors.append(exc)
        errors.extend(
            HarnessError(str(exc), code=exc.code)
            for exc in collect_bugfix_plan_errors(selection, content)
        )
        try:
            governance_from_content(selection["plan_level"], content)
        except PlanGovernanceError as exc:
            errors.append(HarnessError(str(exc), code=exc.code))
    output: Path | None = None
    document: Path | None = None
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
        "content": content,
        "output": output,
        "document": document,
        "title": title,
        "symbols": symbols,
    }


def validate_plan_create_payload(
    target: Path, args: argparse.Namespace
) -> tuple[dict[str, Any], dict[str, Any], Path, Path, str, list[str]]:
    errors, parts = inspect_plan_create(target, args)
    if errors:
        raise errors[0]
    assert parts is not None
    return (
        parts["selection"], parts["content"], parts["output"],
        parts["document"], parts["title"], parts["symbols"],
    )


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
    selection, content, output, document, title, symbols = validate_plan_create_payload(
        target, args
    )
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
    projection_keys = {
        "objective", "steps", "acceptance", "success_criteria", "implementation",
        "acceptance_plan", "affected_modules", "verification_scope",
        "full_regression_trigger", "failure_attribution",
    }
    projection = {key: value for key, value in content.items() if key in projection_keys}
    return 0, {
        "status": "frozen",
        "plan_ref": output.relative_to(target).as_posix(),
        "document_ref": document.relative_to(target).as_posix(),
        "index_ref": PLAN_INDEX_RELATIVE,
        "plan_fingerprint": frozen["plan_fingerprint"],
        "execution_projection": projection,
        "next_action": "execute_plan_then_plan_settle",
    }


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


def acceptance_record(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = safe_target(args.target)
    if not args.input:
        raise HarnessError("acceptance record 必须提供 --input", code="missing_acceptance_input")
    input_path = project_input_path(target, args.input, code="invalid_acceptance_input")
    value = read_json(input_path)
    if not isinstance(value, dict) or value.get("schema_version") != ACCEPTANCE_INPUT_SCHEMA:
        raise HarnessError(f"验收输入 Schema 无效，必须为 {ACCEPTANCE_INPUT_SCHEMA}", code="invalid_acceptance_input")
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
            )
        except AssetError as exc:
            raise translate_asset_error(exc) from exc
        return 0 if result["status"] == "passed" else 3, {
            **result,
            "record_id": record_id,
            "accepted_layer": layer if status == "passed" else None,
            "evidence_layer": evidence_layer,
        }
    atomic_write_json(v2_acceptance_root(target) / f"{record_id}.json", stored)
    if acceptance_type == "contract_check" and status == "passed":
        return 0, {
            "status": "contract_checked",
            "accepted_layer": None,
            "behavior_verified": False,
        }
    if status == "passed":
        return 0, {
            "status": "passed",
            "accepted_layer": layer,
            "evidence_layer": evidence_layer,
            "claim": "behavior_accepted",
        }
    if status == "failed":
        return 3, {
            "status": "failed",
            "layer": ACCEPTANCE_LAYERS[layer],
            "evidence_layer": evidence_layer,
            "reason": stored["reason"],
            "next_action": stored["next_action"],
            "failure_attributions": stored["failure_attributions"],
        }
    if status == "user_pending":
        return 3, {
            "status": "user_pending",
            "accepted_layer": None,
            "user_handoff": stored["user_handoff"],
        }
    return 3, {
        "status": "unverified",
        "accepted_layer": None,
        "next_action": stored["next_action"] or "run_minimum_sufficient_acceptance",
    }


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
)


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
    tasks/（2.19.0）：长任务的进度清单，上下文压缩后以文件为准。两者都不在该元组内，升级不清理。

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
            notices = usage_enabled_notices(existing, USAGE_LOG_DEFAULT_ENABLED)
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
        notices = usage_enabled_notices(existing, project_config(target)["usage_log"]["enabled"])
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


def plan_check_banner(path: Path) -> str | None:
    """返回文档前 3 行内的状态横幅行；没有横幅返回 None。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[:3]
    except (OSError, UnicodeDecodeError) as exc:
        raise HarnessError(f"无法读取文档：{path}", code="plan_check_unreadable") from exc
    for line in lines:
        if PLAN_CHECK_BANNER_MARKER in line:
            return line
    return None


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
    """全仓 .md 文件，排除 VCS 元数据、依赖、构建产物与隐藏目录。"""
    return [
        path for path in plan_check_walk_files(target, PLAN_CHECK_EXCLUDED_DIRS)
        if path.suffix == ".md" and not path.is_symlink()
    ]


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
    for basename in archived_names:
        # 同名活文档存在时 docs/plans/<basename> 指向活文档（新文档同名取代归档草稿），不构成死链。
        if any(path.name == basename for path in live_docs):
            continue
        stale = re.compile(r"docs/plans/" + re.escape(basename) + r"(?![\w.-])")
        for path in markdown_files:
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
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
            elif basename in active_basenames:
                active_symbols[basename] = symbols
    if not fast and (trace_pending or active_symbols):
        landed: dict[str, set[str]] = {basename: set() for basename in active_symbols}
        prune = PLAN_CHECK_EXCLUDED_DIRS | PLAN_CHECK_ARTIFACT_DIRS
        for path in plan_check_walk_files(target, prune):
            if not trace_pending and all(
                len(landed[basename]) == len(symbols)
                for basename, symbols in active_symbols.items()
            ):
                break
            if not path.is_file() or path.is_symlink():
                continue
            parts = path.relative_to(target).parts
            if parts[0] == "docs":
                continue
            if path.suffix.lower() not in PLAN_CHECK_SOURCE_SUFFIXES:
                continue
            try:
                if path.stat().st_size > PLAN_CHECK_SYMBOL_MAX_FILE_BYTES:
                    continue
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for basename, symbols in list(trace_pending.items()):
                if any(symbol in content for symbol in symbols):
                    del trace_pending[basename]
            for basename, symbols in active_symbols.items():
                if len(landed[basename]) == len(symbols):
                    continue
                landed[basename].update(symbol for symbol in symbols if symbol in content)
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
    status = "failed" if failed else "passed"
    return (1 if failed else 0), {
        "status": status,
        "strict": strict,
        "fast": fast,
        "checked": {
            "live_docs": len(live_docs),
            "archived_docs": len(archived_names),
            "markdown_files": len(markdown_files),
        },
        "failures": failures,
        "warnings": warnings,
        "summary": (
            f"plan check {status}：活文档 {len(live_docs)} 份、归档 {len(archived_names)} 份、"
            f"扫描 markdown {len(markdown_files)} 份，违规 {len(failures)} 条、警告 {len(warnings)} 条"
        ),
    }


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
    return (0 if payload["status"] == "passed" else 1), payload


def command_structure(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """structure check/report 的 CLI 投影；规则实现与阈值真源在 structure_check 模块。"""
    target = safe_target(args.target)
    exempt = structure_exempt_paths(target)
    if args.action == "report":
        return 0, structure_report(target, exempt=exempt)
    return 0, check_structure(target, exempt=exempt)


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
        "project_config_v9": (
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
    "plan check [--fast] [--strict]：docs/plans 文档可发现性常驻检查（横幅、索引符号、归档死链、符号存活与时效）。",
    f"部分交付仍在推进的方案横幅写「{PLAN_PARTIAL_DELIVERY_BANNER}（YYYY-MM-DD 核对）」，"
    f"核对日起 {PLAN_PARTIAL_DELIVERY_RECHECK_DAYS} 天内不报符号全命中告警，未来日期不豁免。",
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
))

ADR_EPILOG = _EPILOG_INTRO + "\n\n" + _schema_example_block(
    f"adr create --input（{ADR_INPUT_SCHEMA}）：",
    ADR_INPUT_EXAMPLE,
    (
        "ADR 定稿后不可更新；失效时 adr settle --status deprecated|superseded（superseded 需 --replacement）。",
    ),
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
    knowledge.add_argument("action", choices=("create", "update", "query", "settle", "check"))
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

    plan = commands.add_parser(
        "plan",
        help="选择、冻结并维护任务方案",
        epilog=PLAN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    plan.add_argument("action", choices=("select", "create", "settle", "check"))
    add_target(plan)
    plan.add_argument("--strict", action="store_true", help="WARN 也使退出码非 0（供 CI 使用；仅 check）")
    plan.add_argument("--fast", action="store_true", help="跳过符号存活性与 Git 时效慢检查（仅 check）")
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

    acceptance = commands.add_parser(
        "acceptance",
        help="创建、记录并维护分层验收资产",
        epilog=ACCEPTANCE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    acceptance.add_argument("action", choices=("create", "record", "settle", "check"))
    add_target(acceptance)
    acceptance.add_argument("--input")
    acceptance.add_argument("--output")
    acceptance.add_argument(
        "--dry-run",
        action="store_true",
        help="仅校验（仅 create）：一次性报告全部输入错误与冲突，不写任何文件",
    )
    acceptance.add_argument("--acceptance")
    acceptance.add_argument("--status", choices=("passed", "failed", "superseded"))
    acceptance.add_argument("--replacement")
    acceptance.add_argument("--user-confirmed", action="store_true")
    acceptance.add_argument("--reaccept", action="store_true")

    adr = commands.add_parser(
        "adr",
        help="创建并维护架构决策记录（定稿不可改）",
        epilog=ADR_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    adr.add_argument("action", choices=("create", "settle", "check"))
    add_target(adr)
    adr.add_argument("--input")
    adr.add_argument("--output")
    adr.add_argument("--adr")
    adr.add_argument("--status", choices=ADR_SETTLE_STATUSES)
    adr.add_argument("--replacement")

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

    usage = commands.add_parser(
        "usage", help="本地使用观测：report 聚合 .docs-harness/usage 的调用日志（只读，不外发）"
    )
    usage.add_argument("action", choices=("report",))
    add_target(usage)
    usage.add_argument(
        "--days", type=int, default=USAGE_REPORT_DEFAULT_DAYS, help="统计窗口天数（正整数，默认 %(default)s）"
    )

    self_test = commands.add_parser("self-test", help=f"运行 {VERSION} 内置自检")
    add_target(self_test)

    assets_check = commands.add_parser(
        "assets-check",
        help="统一检查 Plan、Knowledge、Acceptance、ADR、脚本卫生、结构护栏与跨资产关系",
    )
    add_check_options(assets_check)
    return parser


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        print(
            f"{key}: "
            + (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value)
            )
        )


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    started = time.monotonic()
    try:
        if args.command == "knowledge":
            knowledge_handlers = {
                "create": knowledge_create,
                "update": knowledge_update,
                "query": knowledge_query,
                "settle": knowledge_settle,
                "check": knowledge_check,
            }
            code, payload = knowledge_handlers[args.action](args)
        elif args.command == "plan":
            plan_handlers = {
                "select": plan_select,
                "create": plan_create,
                "settle": plan_settle,
                "check": command_plan_check,
            }
            code, payload = plan_handlers[args.action](args)
        elif args.command == "acceptance":
            acceptance_handlers = {
                "create": acceptance_create,
                "record": acceptance_record,
                "settle": acceptance_settle,
                "check": acceptance_check,
            }
            code, payload = acceptance_handlers[args.action](args)
        elif args.command == "adr":
            adr_handlers = {
                "create": adr_create,
                "settle": adr_settle,
                "check": adr_check,
            }
            code, payload = adr_handlers[args.action](args)
        elif args.command == "project":
            code, payload = command_project(args)
        elif args.command == "release":
            code, payload = command_release(args)
        elif args.command == "assets-check":
            code, payload = command_assets_check(args)
        elif args.command == "structure":
            code, payload = command_structure(args)
        elif args.command == "usage":
            code, payload = command_usage(args)
        else:
            code, payload = command_self_test(args)
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


if __name__ == "__main__":
    raise SystemExit(main())
