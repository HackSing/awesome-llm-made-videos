"""结构护栏检查：增量体量预警与 CODEMAP 能力索引一致性。

设计要点（与 plan docs-harness-structure-guardrails 一致）：
- 增量优先：体量检查只对"本次改动"（工作区+暂存区+未跟踪 vs HEAD）归责，
  不扫存量，避免遗留大文件造成 WARN 疲劳；存量结构债由 structure_report 按需报告。
- 全部 WARN 级：行数只触发结构评估；是否拆分取决于职责、分层和可测试性，
  处方权留给人。CI --strict 下增量天然为空，仅 CODEMAP 存量一致性生效。
- git 语义：文件清单全部来自 git（untracked 遵守 .gitignore），不建目录排除清单；
  非 git 目标 checked=0 跳过，与 ScriptHygiene 同口径。
- 函数级检查覆盖三类语言：Python 用标准库 ast；Go 按 gofmt 约定（`func` 起于行首、
  `}` 收于行首）做行级匹配，闭包计入外层函数；TS/JS 系列经 scripts/structure_ts_functions.cjs
  调用目标项目已有的 typescript 编译器（node_modules 或 */node_modules，或 DOCS_HARNESS_TS_MODULE_DIR
  指定目录），harness 不引入依赖。node 或 typescript 缺失时降级为文件级检查：改动含 .ts/.tsx 时输出 WARN
  （TS 项目必然自带 typescript，缺失即环境不完整），只含纯 JS 时记入 payload.notes 不出 WARN。
  HEAD 版本或当前版本解析失败时跳过该文件的函数级判定，文件级仍执行。
- 测试文件不做函数级判定（describe/it 回调天然超长），文件级净增检查照常执行。
- 作为调用实参的匿名函数按 `callee#cb` 命名（如 `ipcMain.handle#cb`），同名取最大行数，
  增长比对按该键进行，属于尽力而为的粒度。
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

FILE_RED_LINE = 600
FUNC_RED_LINE = 60
OVERSIZE_FILE_GROWTH_ALERT = 50
FUNC_GROWTH_ALERT = 10
CODEMAP_RELATIVE = "docs/CODEMAP.md"
CODE_SUFFIXES = frozenset({
    ".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".go", ".rs",
    ".java", ".kt", ".swift", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb",
    ".php", ".lua", ".dart",
})
_TEST_FILE_HINTS = ("test_", "_test.", ".test.", ".spec.")
TS_FUNCTION_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".cjs", ".mjs"})
TS_PARSER_SCRIPT = "structure_ts_functions.cjs"
# typescript 查找顺序：环境变量指定目录 → <target>/node_modules → <target>/*/node_modules；harness 不自带 node 依赖。
TS_MODULE_DIR_ENV = "DOCS_HARNESS_TS_MODULE_DIR"
TS_PARSER_UNAVAILABLE_WARNING = (
    "TS 函数级检查不可用：{reason}；本次仅执行文件级检查，在目标项目安装 typescript"
    f"或设置 {TS_MODULE_DIR_ENV} 后重跑 structure check"
)
# 只有 TypeScript 源文件缺解析器才升为 WARN：TS 项目必然自带 typescript，缺失说明环境不完整；
# 纯 JS 文件（.js/.cjs/.mjs/.jsx）没有该前提，缺失时保持 2.11 的文件级口径，仅在 payload.notes 记录。
TS_STRICT_SUFFIXES = frozenset({".ts", ".tsx"})
# 接收者形如 (a *App)、(App)、(s *Store[T])：可选变量名 + 可选 * + 类型名，类型参数忽略。
_GO_FUNC_PATTERN = re.compile(r"^func\s+(?:\(\s*(?:\w+\s+)?\*?\s*(\w+)[^)]*\)\s*)?(\w+)")
_CODEMAP_ENTRY_PATTERN = re.compile(r"^\s*-\s*`([^`]+)`")

CODEMAP_SCAFFOLD = """# CODEMAP：代码能力索引

动手写代码前先查本索引定位可复用模块；新增代码文件或公开接口变化时同步更新条目。
每行一个模块，格式如下（登记时去掉行首的"示例："）：

示例：- `src/example/module.py` — 职责：一句话说明；公开接口：`main_function`、`ExampleClass`

Structure 检查会校验登记路径存在、公开接口符号存活，并提醒未登记的新增代码文件；
测试文件不必登记。
"""


def _git(target: Path, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=target, capture_output=True, check=False
        )
    except OSError:
        return None


def _is_code_file(relative: str) -> bool:
    return Path(relative).suffix.lower() in CODE_SUFFIXES


def _is_test_file(relative: str) -> bool:
    parts = [part.lower() for part in Path(relative).parts]
    if any(part in ("tests", "test", "__tests__") for part in parts[:-1]):
        return True
    return any(hint in parts[-1] for hint in _TEST_FILE_HINTS)


def _zsplit(stdout: bytes) -> list[str]:
    return [raw.decode("utf-8", errors="replace") for raw in stdout.split(b"\0") if raw]


def _untracked_files(target: Path) -> list[str]:
    result = _git(target, "ls-files", "--others", "--exclude-standard", "-z")
    if result is None or result.returncode != 0:
        return []
    return _zsplit(result.stdout)


def _changed_code_files(target: Path) -> dict[str, str] | None:
    """本次改动的代码文件：{相对路径: "A"|"M"}；非 git 仓库返回 None。

    对比基线是 HEAD（含暂存与未暂存），未跟踪文件按新增计；空仓（无 HEAD）时
    全部 tracked+untracked 代码文件按新增处理。
    """
    probe = _git(target, "rev-parse", "--is-inside-work-tree")
    if probe is None or probe.returncode != 0:
        return None
    changed: dict[str, str] = {}
    head = _git(target, "rev-parse", "--verify", "HEAD")
    if head is not None and head.returncode == 0:
        diff = _git(target, "diff", "HEAD", "--name-status", "--no-renames", "-z")
        if diff is not None and diff.returncode == 0:
            tokens = _zsplit(diff.stdout)
            for status, relative in zip(tokens[::2], tokens[1::2]):
                if status.startswith("D"):
                    continue
                changed[relative] = "A" if status.startswith("A") else "M"
    else:
        tracked = _git(target, "ls-files", "-z")
        if tracked is not None and tracked.returncode == 0:
            changed.update({relative: "A" for relative in _zsplit(tracked.stdout)})
    changed.update({relative: "A" for relative in _untracked_files(target)})
    return {
        relative: status
        for relative, status in changed.items()
        if _is_code_file(relative) and (target / relative).is_file()
    }


def _line_count(text: str) -> int:
    return len(text.splitlines())


def _read_current(target: Path, relative: str) -> str:
    return (target / relative).read_text(encoding="utf-8", errors="replace")


def _read_head(target: Path, relative: str) -> str | None:
    result = _git(target, "show", f"HEAD:{relative}")
    if result is None or result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def _python_functions(source: str) -> dict[str, int] | None:
    """函数限定名 -> 行数（def 行到结尾）；语法错误返回 None。同名取最大值。"""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    spans: dict[str, int] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                span = (child.end_lineno or child.lineno) - child.lineno + 1
                spans[name] = max(spans.get(name, 0), span)
                visit(child, f"{name}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return spans


def _go_functions(source: str) -> dict[str, int]:
    """Go 函数限定名 -> 行数。按 gofmt 约定：`func` 起于行首、函数体 `}` 收于行首；
    方法名记为 `Recv.Method`；闭包计入外层函数。未 gofmt 的文件会把跨度算到下一个行首 `}`，
    只影响预警粒度，不产生崩溃。"""
    spans: dict[str, int] = {}
    lines = source.splitlines()
    index = 0
    while index < len(lines):
        match = _GO_FUNC_PATTERN.match(lines[index])
        if not match:
            index += 1
            continue
        receiver, name = match.group(1), match.group(2)
        qualified = f"{receiver}.{name}" if receiver else name
        if lines[index].rstrip().endswith("}"):
            span = 1
            end = index
        else:
            end = index + 1
            while end < len(lines) and not lines[end].startswith("}"):
                end += 1
            span = min(end, len(lines) - 1) - index + 1
        spans[qualified] = max(spans.get(qualified, 0), span)
        index = end + 1
    return spans


def _ts_module_dirs(target: Path) -> list[Path]:
    """含 typescript 包的 node_modules 目录候选，按优先级排列。"""
    candidates: list[Path] = []
    env_dir = os.environ.get(TS_MODULE_DIR_ENV, "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(target / "node_modules")
    candidates.extend(sorted(path for path in target.glob("*/node_modules") if path.is_dir()))
    return [path for path in candidates if (path / "typescript").is_dir()]


def _ts_parser_command(target: Path) -> tuple[list[str], str | None]:
    """返回 (命令, 不可用原因)；命令为空时原因非空。"""
    script = Path(__file__).resolve().parent / TS_PARSER_SCRIPT
    node = shutil.which("node")
    if node is None:
        return [], "未找到 node"
    if not script.is_file():
        return [], f"缺少 {TS_PARSER_SCRIPT}"
    module_dirs = [str(path) for path in _ts_module_dirs(target)]
    if not module_dirs:
        return [], (
            f"未找到 typescript 模块（已查找 node_modules、*/node_modules，可用 {TS_MODULE_DIR_ENV} 指定目录）"
        )
    return [node, str(script), *module_dirs], None


def _ts_function_spans(
    target: Path, items: list[tuple[str, str, str]]
) -> tuple[dict[str, dict[str, int] | None], str | None]:
    """批量解析 TS/JS 源码：items 为 (id, 文件名, 源码)；返回 ({id: spans|None}, 不可用原因)。
    解析器不可用或子进程失败时 spans 映射为空、原因非空，由调用方转为 WARN。"""
    if not items:
        return {}, None
    command, reason = _ts_parser_command(target)
    if reason is not None:
        return {}, reason
    payload = json.dumps([{"id": item_id, "fileName": name, "text": text} for item_id, name, text in items])
    try:
        completed = subprocess.run(
            command, cwd=target, input=payload.encode("utf-8"), capture_output=True, check=False, timeout=120
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {}, f"解析器执行失败（{error.__class__.__name__}）"
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
        return {}, "解析器退出码 " + str(completed.returncode) + (f"：{detail[-1][:160]}" if detail else "")
    try:
        parsed = json.loads(completed.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}, "解析器输出不是合法 JSON"
    return {str(key): value for key, value in parsed.items()}, None


def function_language(relative: str) -> str | None:
    """返回文件参与函数级检查的语言标签；不覆盖的后缀返回 None。"""
    suffix = Path(relative).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix == ".go":
        return "go"
    if suffix in TS_FUNCTION_SUFFIXES:
        return "ts"
    return None


def _local_function_spans(language: str, source: str) -> dict[str, int] | None:
    if language == "python":
        return _python_functions(source)
    if language == "go":
        return _go_functions(source)
    raise ValueError(f"unsupported local language: {language}")


def _file_size_warnings(relative: str, status: str, current: str, head: str | None) -> list[str]:
    lines = _line_count(current)
    if status == "A" or head is None:
        if lines > FILE_RED_LINE:
            return [
                f"新增文件 {relative} 共 {lines} 行，超过 {FILE_RED_LINE} 行结构评估阈值，"
                "检查职责是否混合；职责单一可保留并说明理由"
            ]
        return []
    old_lines = _line_count(head)
    growth = lines - old_lines
    if old_lines <= FILE_RED_LINE < lines:
        return [
            f"{relative} 本次净增 {growth} 行（{old_lines}→{lines}），"
            f"突破 {FILE_RED_LINE} 行结构评估阈值，检查职责是否混合；"
            "职责单一可保留并说明理由"
        ]
    if old_lines > FILE_RED_LINE and growth >= OVERSIZE_FILE_GROWTH_ALERT:
        return [
            f"{relative} 已超 {FILE_RED_LINE} 行红线仍净增 {growth} 行"
            f"（{old_lines}→{lines}），复核新增逻辑是否形成独立职责；"
            "没有独立职责时可保留并说明理由"
        ]
    return []


def _function_warnings(
    relative: str, spans: dict[str, int] | None, old_spans: dict[str, int] | None, has_head: bool
) -> list[str]:
    """spans/old_spans 为 None 表示该版本解析失败，跳过函数级判定。"""
    if spans is None or (has_head and old_spans is None):
        return []
    previous = old_spans or {}
    warnings: list[str] = []
    for name, span in sorted(spans.items()):
        if span <= FUNC_RED_LINE:
            continue
        old_span = previous.get(name)
        if old_span is None:
            warnings.append(
                f"{relative} 新增函数 {name} 共 {span} 行，"
                f"超过 {FUNC_RED_LINE} 行结构评估阈值，检查是否含可独立测试的子步骤；"
                "职责单一可保留并说明理由"
            )
        elif span - old_span >= FUNC_GROWTH_ALERT:
            warnings.append(
                f"{relative} 函数 {name} 本次增长 {span - old_span} 行"
                f"（{old_span}→{span}），已超 {FUNC_RED_LINE} 行结构评估阈值，"
                "复核是否产生可独立测试的子步骤"
            )
    return warnings


def _collect_function_spans(
    target: Path, sources: dict[str, tuple[str, str | None]]
) -> tuple[dict[str, tuple[dict[str, int] | None, dict[str, int] | None]], str | None]:
    """为每个文件计算 (当前 spans, HEAD spans)；TS/JS 一次子进程批量解析。
    返回 (结果, TS 解析器不可用原因)；不可用时 TS/JS 文件不出现在结果里。"""
    result: dict[str, tuple[dict[str, int] | None, dict[str, int] | None]] = {}
    ts_items: list[tuple[str, str, str]] = []
    for relative, (current, head) in sources.items():
        language = function_language(relative)
        if language is None or _is_test_file(relative):
            continue
        if language == "ts":
            ts_items.append((f"{relative}\0current", relative, current))
            if head is not None:
                ts_items.append((f"{relative}\0head", relative, head))
            continue
        result[relative] = (
            _local_function_spans(language, current),
            _local_function_spans(language, head) if head is not None else None,
        )
    ts_spans, ts_reason = _ts_function_spans(target, ts_items)
    if ts_reason is None:
        for relative, (_, head) in sources.items():
            if function_language(relative) != "ts" or _is_test_file(relative):
                continue
            result[relative] = (
                ts_spans.get(f"{relative}\0current"),
                ts_spans.get(f"{relative}\0head") if head is not None else None,
            )
    return result, ts_reason


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


def _codemap_consistency_warnings(target: Path, entries: list[tuple[str, list[str]]]) -> list[str]:
    warnings: list[str] = []
    for module_path, symbols in entries:
        path = target / module_path
        if not path.is_file():
            warnings.append(
                f"CODEMAP 登记的模块不存在：{module_path}，更新或移除该条目"
            )
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for symbol in symbols:
            if symbol not in content:
                warnings.append(
                    f"CODEMAP 中 {module_path} 的公开接口 `{symbol}` "
                    "在源码中不存在，索引已失活，同步更新条目"
                )
    return warnings


def _codemap_registration_warnings(
    changed: dict[str, str], entries: list[tuple[str, list[str]]]
) -> list[str]:
    registered = {module_path for module_path, _ in entries}
    return [
        f"新增代码文件 {relative} 未登记 {CODEMAP_RELATIVE}"
        "（模块路径 — 职责 — 公开接口），登记后复用才可被发现"
        for relative, status in sorted(changed.items())
        if status == "A" and not _is_test_file(relative) and relative not in registered
    ]


def check_structure(
    target: Path, *, exempt: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """assets-check 第六 checker：增量体量 + CODEMAP 一致性，全部 WARN 级。

    exempt 是仓库根相对 POSIX 路径集合，在判定前从改动集合里整体剔除：下游项目里
    harness.py 与受管模块由安装器写入，体量与登记都不归下游处置。排除集由调用方
    （harness.py）单点构造并保证口径，本模块不复制安装清单，也不在此重算路径。
    _codemap_consistency_warnings 不受 exempt 影响：它校验的是 CODEMAP 里已登记条目
    的存活性，登记与否是项目自己的选择。
    """
    changed = _changed_code_files(target)
    if changed is None:
        return {"status": "passed", "failures": [], "warnings": [], "checked": 0}
    changed = {
        relative: status
        for relative, status in changed.items()
        if relative not in exempt
    }
    warnings: list[str] = []
    sources: dict[str, tuple[str, str | None]] = {}
    for relative, status in sorted(changed.items()):
        current = _read_current(target, relative)
        head = _read_head(target, relative) if status == "M" else None
        sources[relative] = (current, head)
        warnings.extend(_file_size_warnings(relative, status, current, head))
    spans_by_file, ts_reason = _collect_function_spans(target, sources)
    for relative in sorted(spans_by_file):
        spans, old_spans = spans_by_file[relative]
        warnings.extend(_function_warnings(relative, spans, old_spans, sources[relative][1] is not None))
    notes: list[str] = []
    if ts_reason is not None:
        affected = [
            relative for relative in sources
            if function_language(relative) == "ts" and not _is_test_file(relative)
        ]
        if any(Path(relative).suffix.lower() in TS_STRICT_SUFFIXES for relative in affected):
            warnings.append(TS_PARSER_UNAVAILABLE_WARNING.format(reason=ts_reason))
        elif affected:
            notes.append(f"纯 JS 文件未做函数级检查（{ts_reason}）：" + "、".join(sorted(affected)))
    checked = len(changed)
    codemap_path = target / CODEMAP_RELATIVE
    if codemap_path.is_file():
        entries = parse_codemap(codemap_path.read_text(encoding="utf-8", errors="replace"))
        warnings.extend(_codemap_consistency_warnings(target, entries))
        warnings.extend(_codemap_registration_warnings(changed, entries))
        checked += len(entries)
    payload: dict[str, Any] = {"status": "passed", "failures": [], "warnings": warnings, "checked": checked}
    if notes:
        payload["notes"] = notes
    return payload


def _stock_code_files(target: Path) -> list[str] | None:
    tracked = _git(target, "ls-files", "-z")
    if tracked is None or tracked.returncode != 0:
        return None
    files = _zsplit(tracked.stdout) + _untracked_files(target)
    return sorted(
        relative
        for relative in dict.fromkeys(files)
        if _is_code_file(relative) and (target / relative).is_file()
    )


def structure_report(
    target: Path, *, exempt: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """存量结构债报告：超红线文件/函数 + CODEMAP 覆盖缺口，供定期整理任务使用。

    exempt 与 check_structure 同口径、同过滤位置（枚举之后、判定之前），两处不各写一份判定。
    """
    files = _stock_code_files(target)
    if files is None:
        return {"status": "skipped", "reason": "目标不是 git 仓库，无法枚举代码文件"}
    files = [relative for relative in files if relative not in exempt]
    oversized_files: list[dict[str, Any]] = []
    oversized_functions: list[dict[str, Any]] = []
    sources: dict[str, tuple[str, str | None]] = {}
    for relative in files:
        content = _read_current(target, relative)
        lines = _line_count(content)
        if lines > FILE_RED_LINE:
            oversized_files.append({"path": relative, "lines": lines})
        if function_language(relative) is not None:
            sources[relative] = (content, None)
    spans_by_file, ts_reason = _collect_function_spans(target, sources)
    for relative in sorted(spans_by_file):
        spans, _ = spans_by_file[relative]
        for name, span in sorted((spans or {}).items()):
            if span > FUNC_RED_LINE:
                oversized_functions.append({"path": relative, "function": name, "lines": span})
    codemap_path = target / CODEMAP_RELATIVE
    entries = (
        parse_codemap(codemap_path.read_text(encoding="utf-8", errors="replace"))
        if codemap_path.is_file()
        else []
    )
    registered = {module_path for module_path, _ in entries}
    unregistered = [
        relative for relative in files
        if not _is_test_file(relative) and relative not in registered
    ]
    return {
        "status": "ok",
        "checked": len(files),
        "file_red_line": FILE_RED_LINE,
        "func_red_line": FUNC_RED_LINE,
        "files_over_red_line": oversized_files,
        "functions_over_red_line": oversized_functions,
        "function_check_languages": ["python", "go", "ts" if ts_reason is None else f"ts(不可用：{ts_reason})"],
        "codemap": {
            "present": codemap_path.is_file(),
            "entries": len(entries),
            "dead_entry_warnings": _codemap_consistency_warnings(target, entries),
            "unregistered_files": unregistered,
        },
        "summary": (
            f"structure report：代码文件 {len(files)} 个，超红线文件 {len(oversized_files)} 个、"
            f"超红线函数 {len(oversized_functions)} 个、未登记 CODEMAP {len(unregistered)} 个"
        ),
    }
