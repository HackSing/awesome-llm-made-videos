"""Acceptance 目标修订：仅 pending 且尚无记录时可用。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from managed_assets import AssetError, asset_pair, load_asset, seal_asset, write_asset
from acceptance_assets import (
    ACCEPTANCE_SPEC,
    STATUS_LABELS,
    _next_revision,
    render_markdown,
    validate_asset,
    validate_input,
)


def _dry_run_amend_payload(raw_asset: str, revision: int, criteria: int) -> dict[str, Any]:
    return {
        "status": "dry_run_valid",
        "acceptance_ref": raw_asset,
        "writes": "none",
        "revision": revision,
        "criteria": criteria,
        "next_action": "去掉 --dry-run 后重跑以写入",
    }


def amend(
    target: Path,
    raw_asset: str,
    value: Any,
    reason: str,
    now: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """修订尚未记录任何结果的 Acceptance 目标；已有记录时只能新建资产或重验。"""
    if not isinstance(reason, str) or not reason.strip() or "\n" in reason:
        raise AssetError("acceptance amend 必须提供单行非空 --reason", "acceptance_amend_reason_required")
    reason = reason.strip()
    source, document, archived = asset_pair(target, raw_asset, ACCEPTANCE_SPEC)
    if archived:
        raise AssetError("已归档 Acceptance 不可 amend", "acceptance_archived")
    current = load_asset(source, ACCEPTANCE_SPEC)
    validate_asset(current)
    if current.get("settled_at") or current["status"] != "pending":
        raise AssetError("只有 pending Acceptance 可 amend", "acceptance_amend_settled")
    if any(criterion.get("records") for criterion in current["criteria"]):
        raise AssetError(
            "已有验收记录的 Acceptance 不能改 criteria；请新建 Acceptance 或走 settle",
            "acceptance_amend_recorded",
        )
    content = validate_input(target, value)
    if content.get("plan_ref") != current.get("plan_ref"):
        raise AssetError(
            "acceptance amend 不得改变 plan_ref；需要换关联方案请新建 Acceptance",
            "acceptance_amend_plan_ref",
        )
    revision, history = _next_revision(current, now, reason=reason)
    asset = seal_asset({
        **current,
        "title": content["title"],
        "key_symbols": content["key_symbols"],
        "objective": content["objective"],
        "knowledge_refs": content["knowledge_refs"],
        "criteria": content["criteria"],
        "revision": revision,
        "revision_history": history,
        "last_amend_reason": reason,
        "updated_at": now,
    })
    if dry_run:
        return _dry_run_amend_payload(raw_asset, revision, len(asset["criteria"]))
    write_asset(target, ACCEPTANCE_SPEC, source, document, asset, render_markdown(asset), STATUS_LABELS["pending"])
    return {
        "status": "amended",
        "acceptance_ref": raw_asset,
        "document_ref": raw_asset[:-5] + ".md",
        "revision": revision,
        "criteria": len(asset["criteria"]),
        "previous_fingerprint": current["asset_fingerprint"],
        "asset_fingerprint": asset["asset_fingerprint"],
    }


