"""Plan amend 命令：受控修订活方案 content。"""

from __future__ import annotations

import argparse
import re
import sys

from managed_assets import atomic_write_json, atomic_write_text
from plan_governance import (
    PlanGovernanceError,
    _plan_pair,
    apply_plan_amend,
    collect_bugfix_plan_errors,
    governance_from_content,
)


def collect_plan_content_errors(selection, content):
    controller = _harness_controller()
    errors = []
    fields = selection["fields"]
    allowed = {item["id"] for item in fields}
    required = {item["id"] for item in fields if item["required"]}
    unknown = sorted(set(content) - allowed)
    if unknown:
        errors.append(controller.HarnessError(
            "方案包含未注册字段：" + ", ".join(unknown), code="invalid_plan_content"
        ))
    missing = sorted(
        field for field in required if not controller.nonempty_plan_value(content.get(field))
    )
    if missing:
        errors.append(controller.HarnessError(
            "方案缺少必填字段：" + ", ".join(missing), code="invalid_plan_content"
        ))
    try:
        controller.plan_title_and_symbols(content)
    except controller.HarnessError as exc:
        errors.append(exc)
    errors.extend(
        controller.HarnessError(str(exc), code=exc.code)
        for exc in collect_bugfix_plan_errors(selection, content)
    )
    try:
        governance_from_content(selection["plan_level"], content)
    except PlanGovernanceError as exc:
        errors.append(controller.HarnessError(str(exc), code=exc.code))
    return errors


def selection_for_frozen_plan(frozen: dict[str, Any], build_plan_selection) -> dict[str, Any]:
    return build_plan_selection(
        level=frozen["plan_level"],
        profile=frozen["plan_profile"],
        secondary_profiles=frozen["secondary_profiles"],
        reason="frozen_plan_amend",
    )


def _banner_status_from_markdown(markdown: str, banner_marker: str, error_type) -> str:
    for line in markdown.splitlines()[:3]:
        if banner_marker in line:
            match = re.search(r"状态：([^｜\n]+)", line)
            if match:
                return match.group(1).strip()
            break
    raise error_type("方案缺少状态横幅", code="invalid_plan_document")


def _status_allows_amend(status: str, implemented_marker: str, deprecated_prefix: str) -> bool:
    if implemented_marker in status or status.startswith(deprecated_prefix):
        return False
    return status.startswith("有效")


def _require_amend_reason(args: argparse.Namespace, error_type) -> str:
    if not args.plan or not args.reason or bool(args.content) == bool(getattr(args, "patch", None)):
        raise error_type(
            "plan amend 必须提供 --plan、--reason，以及 --content 或 --patch 之一",
            code="missing_plan_amend_input",
        )
    reason = args.reason.strip() if isinstance(args.reason, str) else ""
    if not reason or "\n" in reason:
        raise error_type(
            "plan amend 的 --reason 必须是单行非空字符串",
            code="plan_amend_reason_required",
        )
    return reason


def _sync_plan_index(controller, target, source, title, symbols, banner_status) -> None:
    index_path = target / controller.PLAN_INDEX_RELATIVE
    if not index_path.is_file():
        return
    index = index_path.read_text(encoding="utf-8")
    entry = controller.render_plan_index_entry(
        basename=source.stem, title=title, symbols=symbols, status=banner_status
    )
    updated_index = controller.update_plan_index_text(index, basename=source.stem, entry=entry)
    if updated_index != index:
        atomic_write_text(index_path, updated_index)


def _harness_controller():
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "HarnessError"):
        return main
    import harness as controller

    return controller


def _load_amend_content(controller, target, frozen, args) -> tuple[dict[str, Any], str]:
    current_content = frozen.get("content")
    if not isinstance(current_content, dict):
        raise controller.HarnessError("方案内容无效", code="invalid_plan_ref")
    patch_input = getattr(args, "patch", None)
    if patch_input:
        patch = controller.read_json(
            controller.project_input_path(target, patch_input, code="invalid_plan_content")
        )
        if not isinstance(patch, dict):
            raise controller.HarnessError("--patch 必须是 JSON 对象", code="invalid_plan_content")
        return {**current_content, **patch}, "patch"
    raw_content = controller.read_json(
        controller.project_input_path(target, args.content, code="invalid_plan_content")
    )
    if not isinstance(raw_content, dict):
        raise controller.HarnessError("方案内容必须是 JSON 对象", code="invalid_plan_content")
    return raw_content, "replace"


def _amend_dry_run_payload(source, target, raw_content, mode) -> dict[str, Any]:
    return {
        "status": "dry_run_valid",
        "plan_ref": source.relative_to(target).as_posix(),
        "writes": "none",
        "mode": mode,
        "content": raw_content,
        "next_action": "去掉 --dry-run 后重跑以写入",
    }


def _amend_result_payload(source, target, document, amended, frozen, raw_content, mode) -> dict[str, Any]:
    log = (amended.get("governance") or {}).get("amend_log") or amended.get("amend_log") or []
    return {
        "status": "amended",
        "plan_ref": source.relative_to(target).as_posix(),
        "document_ref": document.relative_to(target).as_posix(),
        "plan_fingerprint": amended["plan_fingerprint"],
        "previous_fingerprint": frozen["plan_fingerprint"],
        "amend_count": len(log),
        "mode": mode,
        "execution_projection": dict(raw_content),
        "next_action": "continue_execution_with_updated_projection",
    }


def run_plan_amend(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    controller = _harness_controller()
    reason = _require_amend_reason(args, controller.HarnessError)
    target = controller.safe_target(args.target)
    try:
        source, document = _plan_pair(target, args.plan)
    except PlanGovernanceError as exc:
        raise controller.HarnessError(str(exc), code=exc.code) from exc
    if source.parent.as_posix().endswith("archive"):
        raise controller.HarnessError("已归档方案不可 amend", code="plan_amend_archived")
    frozen = controller.validate_frozen_plan(controller.read_json(source))
    markdown = document.read_text(encoding="utf-8")
    if controller.PLAN_DOCUMENT_MARKER not in markdown.splitlines()[:3]:
        raise controller.HarnessError("手写方案不支持 amend", code="plan_amend_handwritten")
    banner_status = _banner_status_from_markdown(
        markdown, controller.PLAN_CHECK_BANNER_MARKER, controller.HarnessError
    )
    if not _status_allows_amend(
        banner_status, controller.PLAN_STATUS_IMPLEMENTED, controller.PLAN_STATUS_DEPRECATED
    ):
        raise controller.HarnessError(
            "仅有效（未 settle）的受管方案可 amend",
            code="plan_amend_settled",
        )
    raw_content, mode = _load_amend_content(controller, target, frozen, args)
    selection = selection_for_frozen_plan(frozen, controller.build_plan_selection)
    errors = collect_plan_content_errors(selection, raw_content)
    if errors:
        raise errors[0]
    try:
        amended = apply_plan_amend(frozen, raw_content, reason, controller.utc_now())
    except PlanGovernanceError as exc:
        raise controller.HarnessError(str(exc), code=exc.code) from exc
    if getattr(args, "dry_run", False):
        return 0, _amend_dry_run_payload(source, target, raw_content, mode)
    atomic_write_json(source, amended)
    atomic_write_text(
        document,
        controller.render_plan_markdown(
            selection, raw_content, amended, banner_status=banner_status
        ),
    )
    title, symbols = controller.plan_title_and_symbols(raw_content)
    _sync_plan_index(controller, target, source, title, symbols, banner_status)
    log = (amended.get("governance") or {}).get("amend_log") or amended.get("amend_log") or []
    return 0, {
        "status": "amended",
        "plan_ref": source.relative_to(target).as_posix(),
        "document_ref": document.relative_to(target).as_posix(),
        "plan_fingerprint": amended["plan_fingerprint"],
        "previous_fingerprint": frozen["plan_fingerprint"],
        "amend_count": len(log),
        "mode": mode,
        "execution_projection": dict(raw_content),
        "next_action": "continue_execution_with_updated_projection",
    }
