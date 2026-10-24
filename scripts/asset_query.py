"""只读资产查询：show/list。"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from managed_assets import AssetError, AssetSpec, asset_pair, load_asset
from knowledge_assets import KNOWLEDGE_SPEC, validate_asset as validate_knowledge_asset
from acceptance_assets import ACCEPTANCE_SPEC, validate_asset as validate_acceptance_asset
from adr_assets import ADR_SPEC, validate_asset as validate_adr_asset
from plan_check import plan_check_banner, plan_status_from_banner


def _controller():
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "plan_settle_paths"):
        return main
    import harness
    return harness


class _ControllerProxy:
    def __getattr__(self, name):
        return getattr(_controller(), name)


h = _ControllerProxy()


def show_asset(
    target: Path,
    raw: str,
    spec: AssetSpec,
    validator,
    kind: str,
) -> tuple[int, dict[str, Any]]:
    try:
        source, document, archived = asset_pair(target, raw, spec)
        asset = load_asset(source, spec)
        validator(asset)
    except AssetError as exc:
        raise h.translate_asset_error(exc) from exc
    return 0, {
        "status": "shown",
        "kind": kind,
        "asset_ref": source.relative_to(target).as_posix(),
        "document_ref": document.relative_to(target).as_posix(),
        "archived": archived,
        "asset": asset,
    }



def list_assets(
    target: Path,
    spec: AssetSpec,
    validator,
    kind: str,
) -> tuple[int, dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for root, archived in ((target / spec.root, False), (target / spec.archive, True)):
        if not root.is_dir() or root.is_symlink():
            continue
        for path in sorted(root.glob("*.json")):
            relative = path.relative_to(target).as_posix()
            try:
                asset = load_asset(path, spec)
                validator(asset)
            except AssetError as exc:
                items.append({"asset_ref": relative, "archived": archived, "invalid": True, "error": str(exc)})
                continue
            items.append({
                "asset_ref": relative,
                "document_ref": relative[:-5] + ".md",
                "archived": archived,
                "status": asset.get("status"),
                "title": asset.get("title"),
                "revision": asset.get("revision"),
                "updated_at": asset.get("updated_at"),
            })
    return 0, {"status": "listed", "kind": kind, "count": len(items), "items": items}



def knowledge_show(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.knowledge:
        raise h.HarnessError("knowledge show 必须提供 --knowledge", code="missing_knowledge_input")
    return show_asset(
        h.safe_target(args.target), args.knowledge, KNOWLEDGE_SPEC, validate_knowledge_asset, "knowledge"
    )



def acceptance_show(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.acceptance:
        raise h.HarnessError("acceptance show 必须提供 --acceptance", code="missing_acceptance_target")
    return show_asset(
        h.safe_target(args.target), args.acceptance, ACCEPTANCE_SPEC,
        validate_acceptance_asset, "acceptance",
    )



def acceptance_list(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = h.safe_target(args.target)
    code, payload = list_assets(target, ACCEPTANCE_SPEC, validate_acceptance_asset, "acceptance")
    for item in payload["items"]:
        if item.get("invalid"):
            continue
        try:
            asset = load_asset(target / item["asset_ref"], ACCEPTANCE_SPEC)
        except AssetError:
            continue
        criteria = asset.get("criteria", [])
        pending = [criterion["id"] for criterion in criteria if criterion.get("status") == "pending"]
        item["criteria_total"] = len(criteria)
        item["criteria_pending"] = len(pending)
        item["pending_criteria"] = pending
    return code, payload



def adr_show(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.adr:
        raise h.HarnessError("adr show 必须提供 --adr", code="missing_adr_input")
    return show_asset(
        h.safe_target(args.target), args.adr, ADR_SPEC, validate_adr_asset, "adr"
    )



def plan_show(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """只读返回冻结内容与完整执行投影，供长任务或子 agent 在上下文压缩后恢复。"""
    target = h.safe_target(args.target)
    if not args.plan:
        raise h.HarnessError(
            "plan show 必须提供 --plan",
            code="missing_plan_input",
            extra_payload={"next_action": "传 --plan docs/plans/<name>.json"},
        )
    plan_json, document, archived = h.plan_settle_paths(target, args.plan)
    markdown = document.read_text(encoding="utf-8")
    banner = plan_check_banner(document)
    payload: dict[str, Any] = {
        "status": "shown",
        "plan_ref": plan_json.relative_to(target).as_posix() if plan_json is not None else None,
        "document_ref": document.relative_to(target).as_posix(),
        "archived": archived,
        "handwritten": plan_json is None,
        "plan_status": plan_status_from_banner(banner),
        "banner": banner,
    }
    if plan_json is None:
        payload["content"] = None
        payload["execution_projection"] = None
        payload["markdown"] = markdown
        return 0, payload
    frozen = h.validate_frozen_plan(h.read_json(plan_json))
    content = frozen.get("content")
    if not isinstance(content, dict):
        raise h.HarnessError("方案内容无效", code="invalid_plan_ref")
    payload.update(
        {
            "plan_fingerprint": frozen.get("plan_fingerprint"),
            "template_versions": frozen.get("template_versions", []),
            "acceptance_refs": frozen.get("acceptance_refs", []),
            "governance": frozen.get("governance"),
            "content": content,
            "execution_projection": dict(content),
        }
    )
    return 0, payload



def plan_list(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = h.safe_target(args.target)
    items: list[dict[str, Any]] = []
    managed_stems: set[str] = set()
    for root, archived in ((target / h.PLAN_DOCS_RELATIVE, False), (target / h.PLAN_ARCHIVE_RELATIVE, True)):
        if not root.is_dir() or root.is_symlink():
            continue
        for path in sorted(root.glob("*.json")):
            relative = path.relative_to(target).as_posix()
            try:
                frozen = h.validate_frozen_plan(h.read_json(path))
                content = frozen.get("content")
                if not isinstance(content, dict):
                    raise h.HarnessError("方案内容无效", code="invalid_plan_ref")
                title, symbols = h.plan_title_and_symbols(content)
            except h.HarnessError as exc:
                items.append({"plan_ref": relative, "archived": archived, "invalid": True, "error": str(exc)})
                continue
            managed_stems.add(path.stem)
            document = path.with_suffix(".md")
            banner = plan_check_banner(document) if document.is_file() else None
            items.append({
                "plan_ref": relative,
                "document_ref": document.relative_to(target).as_posix(),
                "archived": archived,
                "handwritten": False,
                "plan_status": plan_status_from_banner(banner),
                "banner": banner,
                "title": title,
                "key_symbols": list(symbols),
                "acceptance_refs": frozen.get("acceptance_refs", []),
                "governance_settled": bool(
                    isinstance(frozen.get("governance"), dict)
                    and frozen["governance"].get("governance_settled_at")
                ),
            })
    for root, archived in ((target / h.PLAN_DOCS_RELATIVE, False), (target / h.PLAN_ARCHIVE_RELATIVE, True)):
        if not root.is_dir() or root.is_symlink():
            continue
        for document in sorted(root.glob("*.md")):
            if document.name == "README.md" or document.stem in managed_stems:
                continue
            banner = plan_check_banner(document)
            if not banner:
                continue
            match = re.search(r"(?m)^#\s+(.+)$", document.read_text(encoding="utf-8"))
            items.append({
                "plan_ref": None,
                "document_ref": document.relative_to(target).as_posix(),
                "archived": archived,
                "handwritten": True,
                "plan_status": plan_status_from_banner(banner),
                "banner": banner,
                "title": match.group(1).strip() if match else document.stem,
            })
    items.sort(key=lambda item: item.get("document_ref") or item.get("plan_ref") or "")
    return 0, {"status": "listed", "kind": "plan", "count": len(items), "items": items}


