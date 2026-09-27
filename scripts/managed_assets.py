"""Plan/Knowledge/Acceptance 可审计文档资产的通用文件层。"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


INDEX_SCAFFOLD = "# 项目文档索引\n\n项目文档从这里进入；Docs Harness 只维护下方受管区块。\n"


class AssetError(Exception):
    def __init__(self, message: str, code: str = "asset_invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AssetSpec:
    kind: str
    root: str
    heading: str
    index_begin: str
    index_end: str
    marker: str
    schema: str
    readme: str
    # True 时只治理带 projection marker 的 Markdown（目录可能与用户既有文档共存，
    # 如 docs/adr/）；False 时目录内全部 JSON/MD 都必须成对（Harness 专有目录）。
    marker_scoped: bool = False

    @property
    def archive(self) -> str:
        return f"{self.root}/archive"

    @property
    def readme_path(self) -> str:
        return f"{self.root}/README.md"

    @property
    def keep_path(self) -> str:
        return f"{self.archive}/.gitkeep"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: dict[str, Any]) -> str:
    comparable = dict(value)
    comparable.pop("asset_fingerprint", None)
    digest = hashlib.sha256(canonical_json(comparable).encode("utf-8")).hexdigest()
    return "sha256:" + digest


def seal_asset(value: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(value)
    sealed["asset_fingerprint"] = fingerprint(sealed)
    return sealed


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


def validate_markers(text: str, begin: str, end: str) -> None:
    begin_count, end_count = text.count(begin), text.count(end)
    if begin_count == end_count == 0:
        return
    if begin_count != 1 or end_count != 1 or text.index(begin) > text.index(end):
        raise AssetError("受管索引区块不完整或重复", "asset_index_conflict")


def replace_block(text: str, begin: str, end: str, block: str) -> str:
    validate_markers(text, begin, end)
    if begin in text:
        pattern = re.escape(begin) + r".*?" + re.escape(end)
        return re.sub(pattern, block, text, flags=re.DOTALL)
    prefix = text.rstrip()
    return (prefix + "\n\n" if prefix else "") + block + "\n"


def index_entries(text: str, spec: AssetSpec) -> list[str]:
    validate_markers(text, spec.index_begin, spec.index_end)
    if spec.index_begin not in text:
        return []
    body = text.split(spec.index_begin, 1)[1].split(spec.index_end, 1)[0]
    return [line for line in body.splitlines() if line.startswith("- [")]


def render_index_block(entries: Sequence[str], spec: AssetSpec) -> str:
    body = "\n".join(entries)
    suffix = f"\n{body}" if body else ""
    return f"{spec.index_begin}\n## {spec.heading}\n{suffix}\n{spec.index_end}"


def update_index(text: str, spec: AssetSpec, basename: str | None = None, entry: str | None = None) -> str:
    entries = index_entries(text, spec)
    if basename:
        tokens = (f"({spec.root.split('/', 1)[1]}/{basename}.md)", f"({spec.root.split('/', 1)[1]}/archive/{basename}.md)")
        entries = [line for line in entries if not any(token in line for token in tokens)]
    if entry:
        entries.append(entry)
    return replace_block(text, spec.index_begin, spec.index_end, render_index_block(entries, spec))


def _safe_managed_path(target: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise AssetError("受管路径必须位于项目内", "asset_path_conflict")
    cursor = target
    for part in path.parts[:-1]:
        cursor /= part
        if cursor.is_symlink():
            raise AssetError(f"受管路径父目录是符号链接：{relative}", "asset_path_conflict")
    result = target / path
    if result.is_symlink() or (result.exists() and not result.is_file()):
        raise AssetError(f"受管路径不是常规文件：{relative}", "asset_path_conflict")
    return result


def structure_changes(target: Path, spec: AssetSpec) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    for relative in (spec.readme_path, spec.keep_path):
        path = _safe_managed_path(target, relative)
        if not path.is_file():
            changes.append({"path": relative, "action": "create"})
    index = _safe_managed_path(target, "docs/INDEX.md")
    if not index.is_file():
        changes.append({"path": "docs/INDEX.md", "action": "create"})
    else:
        text = index.read_text(encoding="utf-8")
        validate_markers(text, spec.index_begin, spec.index_end)
        if spec.index_begin not in text:
            changes.append({"path": "docs/INDEX.md", "action": f"create_{spec.kind}_index_block"})
    return changes


def apply_structure(target: Path, spec: AssetSpec) -> list[str]:
    expected = {item["path"] for item in structure_changes(target, spec)}
    changed: list[str] = []
    readme = target / spec.readme_path
    if not readme.is_file():
        atomic_write_text(readme, spec.readme)
        changed.append(spec.readme_path)
    keep = target / spec.keep_path
    if not keep.is_file():
        atomic_write_text(keep, "")
        changed.append(spec.keep_path)
    index = target / "docs/INDEX.md"
    current = index.read_text(encoding="utf-8") if index.is_file() else INDEX_SCAFFOLD
    updated = update_index(current, spec)
    if updated != current or not index.is_file():
        atomic_write_text(index, updated)
        changed.append("docs/INDEX.md")
    if not expected.issubset(set(changed)):
        raise AssetError("资产结构初始化结果与预览不一致", "asset_structure_conflict")
    return changed


def output_pair(target: Path, raw: str, spec: AssetSpec) -> tuple[Path, Path]:
    relative = Path(raw)
    if relative.parent.as_posix() != spec.root or relative.suffix != ".json":
        raise AssetError(f"输出必须是 {spec.root}/<name>.json", "asset_output_invalid")
    output = _safe_managed_path(target, relative.as_posix())
    document = _safe_managed_path(target, relative.with_suffix(".md").as_posix())
    return output, document


def asset_pair(target: Path, raw: str, spec: AssetSpec) -> tuple[Path, Path, bool]:
    relative = Path(raw)
    allowed = {spec.root, spec.archive}
    if relative.parent.as_posix() not in allowed or relative.suffix != ".json":
        raise AssetError(f"资产必须位于 {spec.root}/ 或其 archive/", "asset_path_invalid")
    source = _safe_managed_path(target, relative.as_posix())
    document = _safe_managed_path(target, relative.with_suffix(".md").as_posix())
    if not source.is_file() or not document.is_file():
        raise AssetError("资产 JSON/Markdown 不完整", "asset_missing")
    return source, document, relative.parent.as_posix() == spec.archive


def load_asset(path: Path, spec: AssetSpec) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetError(f"无法读取资产：{path}", "asset_invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != spec.schema:
        raise AssetError("资产 Schema 无效", "asset_invalid")
    if value.get("asset_fingerprint") != fingerprint(value):
        raise AssetError("资产指纹无效，文件可能被手工篡改", "asset_fingerprint_invalid")
    return value


def render_index_entry(spec: AssetSpec, basename: str, title: str, symbols: Sequence[str], status: str) -> str:
    symbol_text = "、".join(f"`{symbol}`" for symbol in symbols)
    relative_root = spec.root.split("/", 1)[1]
    return f"- [{title}]({relative_root}/{basename}.md) — 状态：{status}；关键符号：{symbol_text}"


def write_asset(target: Path, spec: AssetSpec, output: Path, document: Path, asset: dict[str, Any], markdown: str, status: str) -> None:
    apply_structure(target, spec)
    atomic_write_json(output, asset)
    atomic_write_text(document, markdown)
    index = target / "docs/INDEX.md"
    current = index.read_text(encoding="utf-8")
    entry = render_index_entry(spec, output.stem, asset["title"], asset["key_symbols"], status)
    atomic_write_text(index, update_index(current, spec, output.stem, entry))


def archive_asset(target: Path, spec: AssetSpec, source: Path, document: Path) -> tuple[Path, Path]:
    archive = target / spec.archive
    archive.mkdir(parents=True, exist_ok=True)
    archived_source, archived_document = archive / source.name, archive / document.name
    if archived_source.exists() or archived_document.exists():
        raise AssetError("归档目标已存在", "asset_archive_conflict")
    os.replace(source, archived_source)
    os.replace(document, archived_document)
    index = target / "docs/INDEX.md"
    atomic_write_text(index, update_index(index.read_text(encoding="utf-8"), spec, source.stem))
    return archived_source, archived_document


def rewrite_links(target: Path, spec: AssetSpec, basename: str, markdown_files: Sequence[Path]) -> list[str]:
    old = f"{spec.root}/{basename}.md"
    new = f"{spec.archive}/{basename}.md"
    changed: list[str] = []
    for path in markdown_files:
        if path.is_symlink() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        updated = text.replace(old, new)
        if updated != text:
            atomic_write_text(path, updated)
            changed.append(path.relative_to(target).as_posix())
    return changed


def check_assets(target: Path, spec: AssetSpec, validate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    index = target / "docs/INDEX.md"
    try:
        missing = structure_changes(target, spec)
    except AssetError as exc:
        return {"status": "failed", "failures": [str(exc)], "warnings": warnings, "checked": 0}
    if missing:
        return {
            "status": "failed",
            "failures": ["资产结构不完整"],
            "warnings": warnings,
            "missing": missing,
            "checked": 0,
        }
    lines = index_entries(index.read_text(encoding="utf-8"), spec)
    checked = 0
    roots = ((target / spec.root, False), (target / spec.archive, True))
    for root, archived in roots:
        json_files = {path.stem: path for path in root.glob("*.json")}
        markdown_files = {}
        for path in root.glob("*.md"):
            if path.name == "README.md":
                continue
            if spec.marker_scoped:
                try:
                    head = path.read_text(encoding="utf-8").splitlines()[:3]
                except (OSError, UnicodeDecodeError):
                    continue
                if spec.marker not in head:
                    continue
            markdown_files[path.stem] = path
        for basename in sorted(set(json_files) | set(markdown_files)):
            path = json_files.get(basename)
            document = markdown_files.get(basename)
            if path is None or document is None:
                missing_kind = "JSON" if path is None else "Markdown"
                failures.append(f"{root.relative_to(target)}/{basename}: 缺少 {missing_kind} 伴随文件")
                continue
            checked += 1
            _check_asset_pair(
                target, spec, validate, lines, path, document, archived, failures
            )
    relative_root = spec.root.split("/", 1)[1]
    for line in lines:
        match = re.search(r"\(" + re.escape(relative_root) + r"/([\w.-]+)\.md\)", line)
        if not match:
            failures.append(f"docs/INDEX.md: {spec.kind} 条目路径无效")
            continue
        basename = match.group(1)
        if not (target / spec.root / f"{basename}.json").is_file():
            failures.append(f"docs/INDEX.md: {basename}.md 条目没有对应活资产")
    status = "failed" if failures else "passed"
    return {
        "status": status,
        "failures": failures,
        "warnings": warnings,
        "checked": checked,
    }


def _check_asset_pair(
    target: Path,
    spec: AssetSpec,
    validate: Callable[[dict[str, Any]], None],
    index_lines: Sequence[str],
    path: Path,
    document: Path,
    archived: bool,
    failures: list[str],
) -> None:
    try:
        asset = load_asset(path, spec)
        validate(asset)
    except AssetError as exc:
        failures.append(f"{path.relative_to(target)}: {exc}")
        return
    if spec.marker not in document.read_text(encoding="utf-8").splitlines()[:3]:
        failures.append(f"{document.relative_to(target)}: Markdown 投影标记无效")
    indexed = any(document.name in line for line in index_lines)
    if archived and indexed:
        failures.append(f"docs/INDEX.md: 归档资产 {document.name} 仍在活索引")
    if not archived and not indexed:
        failures.append(f"docs/INDEX.md: 缺少 {document.name} 条目")
