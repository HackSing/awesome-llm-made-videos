"""带证据、修订与冲突检测的 Knowledge 资产生命周期。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

from managed_assets import (
    AssetError,
    AssetSpec,
    archive_asset,
    asset_pair,
    check_assets,
    load_asset,
    output_pair,
    rewrite_links,
    seal_asset,
    write_asset,
)


KNOWLEDGE_INPUT_SCHEMA = "docs-harness/knowledge-input/v1"
KNOWLEDGE_ASSET_SCHEMA = "docs-harness/knowledge-asset/v1"
KNOWLEDGE_STATUS_ACTIVE = "有效（现行事实）"
KNOWLEDGE_STATUS_DEPRECATED = "已废弃"
KNOWLEDGE_STATUS_SUPERSEDED = "已废弃-被替代"
KNOWLEDGE_SETTLE_STATUSES = ("deprecated", "superseded")
FACT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")

KNOWLEDGE_SPEC = AssetSpec(
    kind="knowledge",
    root="docs/knowledge",
    heading="项目知识",
    index_begin="<!-- docs-harness:knowledge-index:start -->",
    index_end="<!-- docs-harness:knowledge-index:end -->",
    marker="<!-- docs-harness:knowledge-document/v1 -->",
    schema=KNOWLEDGE_ASSET_SCHEMA,
    readme="""# 项目知识

本目录保存由当前源码或项目文档证据支持的结构化事实。每项知识由同名 JSON 保存
可审计事实与修订记录，Markdown 提供可读投影，并登记到 `docs/INDEX.md`。

Knowledge 不替代当前源码与运行态；冲突必须显式解决。废弃或被取代的资产移入 `archive/`。
""",
)


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value:
        raise AssetError(f"{label} 必须是单行非空字符串", "knowledge_input_invalid")
    return value.strip()


def _symbols(value: Any) -> list[str]:
    if not isinstance(value, list) or not 2 <= len(value) <= 4:
        raise AssetError("key_symbols 必须包含 2-4 项", "knowledge_input_invalid")
    symbols = [_string(item, "key_symbols 项") for item in value]
    if len(symbols) != len(set(symbols)) or any("`" in item for item in symbols):
        raise AssetError("key_symbols 必须唯一且不能包含反引号", "knowledge_input_invalid")
    return symbols


def _source_path(target: Path, raw: str) -> Path:
    relative = raw.rsplit(":", 1)[0] if re.search(r":\d+$", raw) else raw
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise AssetError("source_refs 必须是项目内相对路径", "knowledge_source_invalid")
    resolved = (target / path).resolve()
    try:
        resolved.relative_to(target.resolve())
    except ValueError as exc:
        raise AssetError("source_refs 越出项目目录", "knowledge_source_invalid") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise AssetError(f"知识证据不存在：{raw}", "knowledge_source_missing")
    return resolved


def _facts(target: Path, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise AssetError("facts 必须是非空数组", "knowledge_input_invalid")
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise AssetError("facts 每项必须是对象", "knowledge_input_invalid")
        fact_id = _string(item.get("id"), "fact id")
        if not FACT_ID_PATTERN.fullmatch(fact_id) or fact_id in seen:
            raise AssetError("fact id 格式无效或重复", "knowledge_input_invalid")
        statement = _string(item.get("statement"), "fact statement")
        refs = item.get("source_refs")
        if not isinstance(refs, list) or not refs:
            raise AssetError("每条事实必须包含 source_refs", "knowledge_input_invalid")
        normalized_refs = [_string(ref, "source_ref") for ref in refs]
        for ref in normalized_refs:
            _source_path(target, ref)
        seen.add(fact_id)
        facts.append({"id": fact_id, "statement": statement, "source_refs": normalized_refs})
    return facts


def validate_input(target: Path, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != KNOWLEDGE_INPUT_SCHEMA:
        raise AssetError(f"Knowledge 输入 Schema 无效，必须为 {KNOWLEDGE_INPUT_SCHEMA}，仅允许 title/key_symbols/summary/facts", "knowledge_input_invalid")
    allowed = {"schema_version", "title", "key_symbols", "summary", "facts"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise AssetError("Knowledge 输入包含未注册字段：" + ", ".join(unknown), "knowledge_input_invalid")
    return {
        "title": _string(value.get("title"), "title"),
        "key_symbols": _symbols(value.get("key_symbols")),
        "summary": _string(value.get("summary"), "summary"),
        "facts": _facts(target, value.get("facts")),
    }


def validate_asset(value: dict[str, Any]) -> None:
    if value.get("status") not in {"active", "deprecated", "superseded"}:
        raise AssetError("Knowledge 状态无效", "knowledge_asset_invalid")
    if not isinstance(value.get("revision"), int) or value["revision"] < 1:
        raise AssetError("Knowledge revision 无效", "knowledge_asset_invalid")
    if not isinstance(value.get("facts"), list) or not value["facts"]:
        raise AssetError("Knowledge facts 无效", "knowledge_asset_invalid")


def render_markdown(asset: dict[str, Any]) -> str:
    status = {
        "active": KNOWLEDGE_STATUS_ACTIVE,
        "deprecated": KNOWLEDGE_STATUS_DEPRECATED,
        "superseded": KNOWLEDGE_STATUS_SUPERSEDED,
    }[asset["status"]]
    symbols = "、".join(f"`{item}`" for item in asset["key_symbols"])
    facts = "\n\n".join(
        f"### `{item['id']}`\n\n{item['statement']}\n\n证据："
        + "、".join(f"`{ref}`" for ref in item["source_refs"])
        for item in asset["facts"]
    )
    replacement = f"\n- 替代资产：`{asset['replacement']}`" if asset.get("replacement") else ""
    return (
        f"> 状态：{status}\n{KNOWLEDGE_SPEC.marker}\n\n# {asset['title']}\n\n"
        f"- 修订：{asset['revision']}\n- 关键符号：{symbols}\n"
        f"- 资产指纹：`{asset['asset_fingerprint']}`{replacement}\n\n"
        f"## 摘要\n\n{asset['summary']}\n\n## 事实\n\n{facts}\n"
    )


def create(target: Path, value: Any, raw_output: str, now: str) -> dict[str, Any]:
    content = validate_input(target, value)
    output, document = output_pair(target, raw_output, KNOWLEDGE_SPEC)
    if output.exists() or document.exists():
        raise AssetError("Knowledge 输出已存在", "knowledge_already_exists")
    asset = seal_asset({
        "schema_version": KNOWLEDGE_ASSET_SCHEMA,
        **content,
        "status": "active",
        "revision": 1,
        "revision_history": [],
        "created_at": now,
        "updated_at": now,
    })
    write_asset(target, KNOWLEDGE_SPEC, output, document, asset, render_markdown(asset), KNOWLEDGE_STATUS_ACTIVE)
    return {"status": "created", "knowledge_ref": raw_output, "document_ref": raw_output[:-5] + ".md", "revision": 1}


def update(target: Path, raw_asset: str, value: Any, now: str) -> dict[str, Any]:
    source, document, archived = asset_pair(target, raw_asset, KNOWLEDGE_SPEC)
    if archived:
        raise AssetError("归档 Knowledge 不可更新", "knowledge_archived")
    current = load_asset(source, KNOWLEDGE_SPEC)
    validate_asset(current)
    content = validate_input(target, value)
    history = list(current.get("revision_history", []))
    history.append({"revision": current["revision"], "asset_fingerprint": current["asset_fingerprint"], "updated_at": current["updated_at"]})
    asset = seal_asset({
        "schema_version": KNOWLEDGE_ASSET_SCHEMA,
        **content,
        "status": "active",
        "revision": current["revision"] + 1,
        "revision_history": history,
        "created_at": current["created_at"],
        "updated_at": now,
    })
    write_asset(target, KNOWLEDGE_SPEC, source, document, asset, render_markdown(asset), KNOWLEDGE_STATUS_ACTIVE)
    return {"status": "updated", "knowledge_ref": raw_asset, "revision": asset["revision"], "previous_fingerprint": current["asset_fingerprint"]}


def active_assets(target: Path) -> list[tuple[Path, dict[str, Any]]]:
    result: list[tuple[Path, dict[str, Any]]] = []
    root = target / KNOWLEDGE_SPEC.root
    if not root.is_dir() or root.is_symlink():
        return result
    for path in sorted(root.glob("*.json")):
        asset = load_asset(path, KNOWLEDGE_SPEC)
        validate_asset(asset)
        if asset["status"] == "active":
            result.append((path, asset))
    return result


def conflicts(target: Path) -> list[dict[str, Any]]:
    by_id: dict[str, list[dict[str, str]]] = {}
    for path, asset in active_assets(target):
        for fact in asset["facts"]:
            by_id.setdefault(fact["id"], []).append({"statement": fact["statement"], "asset_ref": path.relative_to(target).as_posix()})
    result: list[dict[str, Any]] = []
    for fact_id, variants in by_id.items():
        if len({item["statement"] for item in variants}) > 1:
            result.append({"fact_id": fact_id, "variants": variants})
    return result


def query(target: Path, tokens: Sequence[str], limit: int, max_chars: int) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    used = 0
    for path, asset in active_assets(target):
        for fact in asset["facts"]:
            haystack = f"{fact['id']} {fact['statement']} {' '.join(asset['key_symbols'])}".casefold()
            if tokens and not any(token in haystack for token in tokens):
                continue
            if len(matches) >= limit or used + len(fact["statement"]) > max_chars:
                continue
            ref = path.relative_to(target).as_posix()
            matches.append({"text": fact["statement"], "ref": ref, "fact_id": fact["id"], "source_refs": fact["source_refs"]})
            used += len(fact["statement"])
    return {"facts": matches, "conflicts": conflicts(target)}


def settle(target: Path, raw_asset: str, status: str, replacement: str | None, now: str, markdown_files: Sequence[Path]) -> dict[str, Any]:
    if status not in KNOWLEDGE_SETTLE_STATUSES:
        raise AssetError("Knowledge settle 状态无效", "knowledge_settle_invalid")
    source, document, archived = asset_pair(target, raw_asset, KNOWLEDGE_SPEC)
    if archived:
        raise AssetError("Knowledge 已归档", "knowledge_archived")
    if status == "superseded" and not replacement:
        raise AssetError("superseded 必须提供 replacement", "knowledge_replacement_required")
    if replacement:
        replacement_source, _, replacement_archived = asset_pair(target, replacement, KNOWLEDGE_SPEC)
        if replacement_source == source:
            raise AssetError("replacement 不能指向自身", "knowledge_replacement_invalid")
        if replacement_archived:
            raise AssetError("replacement 必须是活跃 Knowledge", "knowledge_replacement_invalid")
    current = load_asset(source, KNOWLEDGE_SPEC)
    current.update({"status": status, "replacement": replacement, "settled_at": now})
    asset = seal_asset(current)
    write_asset(target, KNOWLEDGE_SPEC, source, document, asset, render_markdown(asset), KNOWLEDGE_STATUS_SUPERSEDED if status == "superseded" else KNOWLEDGE_STATUS_DEPRECATED)
    archived_source, archived_document = archive_asset(target, KNOWLEDGE_SPEC, source, document)
    rewritten = rewrite_links(target, KNOWLEDGE_SPEC, source.stem, markdown_files)
    return {"status": status, "knowledge_ref": archived_source.relative_to(target).as_posix(), "document_ref": archived_document.relative_to(target).as_posix(), "rewritten_links": rewritten}


def check(target: Path) -> dict[str, Any]:
    def validate(value: dict[str, Any]) -> None:
        validate_asset(value)
        for fact in value["facts"]:
            if not isinstance(fact, dict) or not FACT_ID_PATTERN.fullmatch(str(fact.get("id", ""))):
                raise AssetError("Knowledge fact 结构无效", "knowledge_asset_invalid")
            refs = fact.get("source_refs")
            if not isinstance(refs, list) or not refs:
                raise AssetError("Knowledge fact 缺少 source_refs", "knowledge_asset_invalid")
            for ref in refs:
                _source_path(target, ref)

    result = check_assets(target, KNOWLEDGE_SPEC, validate)
    try:
        active_conflicts = conflicts(target)
    except AssetError as exc:
        result["failures"].append(str(exc))
        active_conflicts = []
    if active_conflicts:
        result["failures"].append("存在同 fact id 的活跃冲突")
    result["conflicts"] = active_conflicts
    result["status"] = "failed" if result["failures"] else "passed"
    return result
