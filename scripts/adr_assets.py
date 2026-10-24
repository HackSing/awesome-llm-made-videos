"""架构决策记录（ADR）资产生命周期：定稿不可改，只能废弃或被取代。"""

from __future__ import annotations

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
from knowledge_assets import follow_archived_refs


ADR_INPUT_SCHEMA = "docs-harness/adr-input/v1"
ADR_ASSET_SCHEMA = "docs-harness/adr-asset/v1"
ADR_STATUS_ACTIVE = "有效（现行决策）"
ADR_STATUS_DEPRECATED = "已废弃"
ADR_STATUS_SUPERSEDED = "已废弃-被替代"
ADR_SETTLE_STATUSES = ("deprecated", "superseded")
ADR_CONSTRAINT_LIMIT = 3
ADR_CONSTRAINT_MAX_CHARS = 300

ADR_SPEC = AssetSpec(
    kind="adr",
    root="docs/adr",
    heading="架构决策",
    index_begin="<!-- docs-harness:adr-index:start -->",
    index_end="<!-- docs-harness:adr-index:end -->",
    marker="<!-- docs-harness:adr-document/v1 -->",
    schema=ADR_ASSET_SCHEMA,
    marker_scoped=True,
    status_labels={
        "active": ADR_STATUS_ACTIVE,
        "deprecated": ADR_STATUS_DEPRECATED,
        "superseded": ADR_STATUS_SUPERSEDED,
    },
    readme="""# 架构决策记录（ADR）

本目录保存已定稿的架构决策。每项决策由同名 JSON 保存可审计内容，Markdown 提供
可读投影，并登记到 `docs/INDEX.md`。

定稿后不得手改 JSON/Markdown；内容修正用 `adr amend`（须 `--reason`），
失效时用 `adr settle` 废弃或标记被替代，资产移入 `archive/`。
新决策取代旧决策时通过 `supersedes` 记录取代关系。
""",
)


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssetError(f"{label} 必须是非空字符串", "adr_input_invalid")
    return value.strip()


def _symbols(value: Any) -> list[str]:
    if not isinstance(value, list) or not 2 <= len(value) <= 4:
        raise AssetError("key_symbols 必须包含 2-4 项", "adr_input_invalid")
    symbols = [_string(item, "key_symbols 项") for item in value]
    if len(symbols) != len(set(symbols)) or any("`" in item for item in symbols):
        raise AssetError("key_symbols 必须唯一且不能包含反引号", "adr_input_invalid")
    return symbols


def _load_adr_ref(target: Path, raw: str) -> None:
    relative = Path(raw)
    candidates = [raw]
    if relative.parent.as_posix() == ADR_SPEC.root:
        candidates.append(f"{ADR_SPEC.archive}/{relative.name}")
    for candidate in candidates:
        try:
            source, _, _ = asset_pair(target, candidate, ADR_SPEC)
            asset = load_asset(source, ADR_SPEC)
            validate_asset(asset)
            return
        except AssetError:
            continue
    raise AssetError(f"ADR 引用无效：{raw}", "adr_ref_invalid")


def _supersedes(target: Path, value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AssetError("supersedes 必须是数组", "adr_input_invalid")
    refs = [_string(item, "supersedes 项") for item in value]
    if len(refs) != len(set(refs)):
        raise AssetError("supersedes 不得重复", "adr_input_invalid")
    for ref in refs:
        _load_adr_ref(target, ref)
    return refs


def validate_input(target: Path, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != ADR_INPUT_SCHEMA:
        raise AssetError(f"ADR 输入 Schema 无效，必须为 {ADR_INPUT_SCHEMA}，仅允许 title/key_symbols/context/decision/consequences/supersedes", "adr_input_invalid")
    allowed = {"schema_version", "title", "key_symbols", "context", "decision", "consequences", "supersedes"}
    if set(value) - allowed:
        raise AssetError("ADR 输入包含未注册字段", "adr_input_invalid")
    return {
        "title": _string(value.get("title"), "title"),
        "key_symbols": _symbols(value.get("key_symbols")),
        "context": _string(value.get("context"), "context"),
        "decision": _string(value.get("decision"), "decision"),
        "consequences": _string(value.get("consequences"), "consequences"),
        "supersedes": _supersedes(target, value.get("supersedes")),
    }


def _validate_revision_history(value: Any) -> None:
    if not isinstance(value, list):
        raise AssetError("ADR revision_history 无效", "adr_asset_invalid")
    for item in value:
        if not isinstance(item, dict):
            raise AssetError("ADR revision_history 项无效", "adr_asset_invalid")
        if not isinstance(item.get("revision"), int) or item["revision"] < 1:
            raise AssetError("ADR revision_history.revision 无效", "adr_asset_invalid")
        for key in ("asset_fingerprint", "updated_at"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise AssetError(f"ADR revision_history 缺少 {key}", "adr_asset_invalid")
        reason = item.get("amend_reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip() or "\n" in reason):
            raise AssetError("ADR revision_history.amend_reason 无效", "adr_asset_invalid")


def validate_asset(value: dict[str, Any]) -> None:
    if value.get("status") not in {"active", "deprecated", "superseded"}:
        raise AssetError("ADR 状态无效", "adr_asset_invalid")
    if not isinstance(value.get("revision"), int) or value["revision"] < 1:
        raise AssetError("ADR revision 无效", "adr_asset_invalid")
    for field in ("title", "context", "decision", "consequences"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise AssetError(f"ADR {field} 无效", "adr_asset_invalid")
    if not isinstance(value.get("supersedes"), list):
        raise AssetError("ADR supersedes 无效", "adr_asset_invalid")
    _validate_revision_history(value.get("revision_history", []))
    last_reason = value.get("last_amend_reason")
    if last_reason is not None and (not isinstance(last_reason, str) or not last_reason.strip() or "\n" in last_reason):
        raise AssetError("ADR last_amend_reason 无效", "adr_asset_invalid")


def render_markdown(asset: dict[str, Any]) -> str:
    status = {
        "active": ADR_STATUS_ACTIVE,
        "deprecated": ADR_STATUS_DEPRECATED,
        "superseded": ADR_STATUS_SUPERSEDED,
    }[asset["status"]]
    symbols = "、".join(f"`{item}`" for item in asset["key_symbols"])
    replacement = f"\n- 替代资产：`{asset['replacement']}`" if asset.get("replacement") else ""
    amend_line = ""
    if asset.get("last_amend_reason") and asset.get("status") == "active":
        amend_line = f"> 最近修订：{asset['updated_at'][:10]} — {asset['last_amend_reason']}\n"
    supersedes = ""
    if asset["supersedes"]:
        supersedes = "\n\n## 取代\n\n" + "\n".join(f"- `{item}`" for item in asset["supersedes"])
    return (
        f"> 状态：{status}\n{amend_line}{ADR_SPEC.marker}\n\n# {asset['title']}\n\n"
        f"- 修订：{asset['revision']}\n- 关键符号：{symbols}\n- 资产指纹：`{asset['asset_fingerprint']}`{replacement}\n\n"
        f"## 背景\n\n{asset['context']}\n\n## 决策\n\n{asset['decision']}\n\n"
        f"## 影响\n\n{asset['consequences']}\n{supersedes}"
    )


def create(target: Path, value: Any, raw_output: str, now: str) -> dict[str, Any]:
    content = validate_input(target, value)
    output, document = output_pair(target, raw_output, ADR_SPEC)
    if output.exists() or document.exists():
        raise AssetError("ADR 输出已存在", "adr_already_exists")
    asset = seal_asset({
        "schema_version": ADR_ASSET_SCHEMA,
        **content,
        "status": "active",
        "revision": 1,
        "revision_history": [],
        "last_amend_reason": None,
        "created_at": now,
        "updated_at": now,
    })
    write_asset(target, ADR_SPEC, output, document, asset, render_markdown(asset), ADR_STATUS_ACTIVE)
    return {"status": "created", "adr_ref": raw_output, "document_ref": raw_output[:-5] + ".md"}


def amend(
    target: Path,
    raw_asset: str,
    value: Any,
    reason: str,
    now: str,
) -> dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip() or "\n" in reason:
        raise AssetError("adr amend 必须提供单行非空 --reason", "adr_amend_reason_required")
    reason = reason.strip()
    source, document, archived = asset_pair(target, raw_asset, ADR_SPEC)
    if archived:
        raise AssetError("已归档 ADR 不可 amend", "adr_archived")
    current = load_asset(source, ADR_SPEC)
    validate_asset(current)
    if current["status"] != "active":
        raise AssetError("仅 active ADR 可 amend", "adr_amend_inactive")
    content = validate_input(target, value)
    if content["supersedes"] != current["supersedes"]:
        raise AssetError("adr amend 不得改变 supersedes", "adr_amend_supersedes_change")
    history = list(current.get("revision_history", []))
    history.append({
        "revision": current["revision"],
        "asset_fingerprint": current["asset_fingerprint"],
        "updated_at": current["updated_at"],
        "amend_reason": reason,
    })
    asset = seal_asset({
        "schema_version": ADR_ASSET_SCHEMA,
        **content,
        "status": "active",
        "revision": current["revision"] + 1,
        "revision_history": history,
        "last_amend_reason": reason,
        "created_at": current["created_at"],
        "updated_at": now,
    })
    write_asset(target, ADR_SPEC, source, document, asset, render_markdown(asset), ADR_STATUS_ACTIVE)
    return {
        "status": "amended",
        "adr_ref": raw_asset,
        "document_ref": raw_asset[:-5] + ".md",
        "revision": asset["revision"],
        "previous_fingerprint": current["asset_fingerprint"],
        "asset_fingerprint": asset["asset_fingerprint"],
    }


def settle(target: Path, raw_asset: str, status: str, replacement: str | None, now: str, markdown_files: Sequence[Path]) -> dict[str, Any]:
    if status not in ADR_SETTLE_STATUSES:
        raise AssetError("ADR settle 状态无效", "adr_settle_invalid")
    source, document, archived = asset_pair(target, raw_asset, ADR_SPEC)
    if archived:
        raise AssetError("ADR 已归档", "adr_archived")
    if status == "superseded" and not replacement:
        raise AssetError("superseded 必须提供 replacement", "adr_replacement_required")
    if replacement:
        replacement_source, _, replacement_archived = asset_pair(target, replacement, ADR_SPEC)
        if replacement_source == source:
            raise AssetError("replacement 不能指向自身", "adr_replacement_invalid")
        if replacement_archived:
            raise AssetError("replacement 必须是活跃 ADR", "adr_replacement_invalid")
    current = load_asset(source, ADR_SPEC)
    current.update({"status": status, "replacement": replacement, "settled_at": now})
    asset = seal_asset(current)
    write_asset(target, ADR_SPEC, source, document, asset, render_markdown(asset), ADR_STATUS_SUPERSEDED if status == "superseded" else ADR_STATUS_DEPRECATED)
    archived_source, archived_document = archive_asset(target, ADR_SPEC, source, document)
    rewritten = rewrite_links(target, ADR_SPEC, source.stem, markdown_files)
    followed = follow_archived_refs(target, ADR_SPEC.root, source.stem)
    return {"status": status, "adr_ref": archived_source.relative_to(target).as_posix(), "document_ref": archived_document.relative_to(target).as_posix(), "rewritten_links": rewritten, "rewritten_source_refs": followed}


def active_constraints(target: Path) -> list[dict[str, str]]:
    """把活跃 ADR 的决策投影成知识查询的 constraints，限制条数与单条长度。"""
    root = target / ADR_SPEC.root
    if not root.is_dir() or root.is_symlink():
        return []
    constraints: list[dict[str, str]] = []
    for path in sorted(root.glob("*.json")):
        try:
            asset = load_asset(path, ADR_SPEC)
            validate_asset(asset)
        except AssetError:
            continue
        if asset.get("status") != "active":
            continue
        decision = asset.get("decision")
        if not isinstance(decision, str) or not decision.strip():
            continue
        text = f"{asset['title']}：{decision.strip()}"
        if len(text) > ADR_CONSTRAINT_MAX_CHARS:
            text = text[: ADR_CONSTRAINT_MAX_CHARS - 1] + "…"
        constraints.append({
            "text": text,
            "ref": path.relative_to(target).as_posix(),
        })
        if len(constraints) >= ADR_CONSTRAINT_LIMIT:
            break
    return constraints


def check(target: Path) -> dict[str, Any]:
    def validate(value: dict[str, Any]) -> None:
        validate_asset(value)
        for ref in value["supersedes"]:
            _load_adr_ref(target, ref)

    return check_assets(target, ADR_SPEC, validate)
