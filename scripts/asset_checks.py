"""Plan、Knowledge、Acceptance 检查结果的统一编排层。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Callable

from acceptance_assets import ACCEPTANCE_SPEC, validate_asset as validate_acceptance
from knowledge_assets import KNOWLEDGE_SPEC, validate_asset as validate_knowledge
from managed_assets import AssetError, load_asset
from plan_governance import (
    PLAN_SCHEMA_V2,
    PLAN_SCHEMA_V3,
    PlanGovernanceError,
    validate_plan,
)


AssetChecker = Callable[[Path], dict[str, Any]]
PlanChecker = Callable[[Path, bool], dict[str, Any]]
ASSET_STALE_DAYS = 90


def _run_asset_checker(target: Path, label: str, checker: AssetChecker) -> dict[str, Any]:
    try:
        return checker(target)
    except AssetError as exc:
        return {
            "status": "failed",
            "failures": [f"{label}：{exc}"],
            "warnings": [],
            "checked": 0,
        }


def _load_plan_path(target: Path, raw: str) -> tuple[Path, dict[str, Any]]:
    relative = Path(raw)
    path = target / relative
    if not path.is_file() and relative.parent.as_posix() == "docs/plans":
        path = target / "docs/plans/archive" / relative.name
    if not path.is_file() or path.is_symlink():
        raise AssetError(f"Plan 引用不存在：{raw}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return path, validate_plan(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetError(f"Plan 引用无法读取：{raw}") from exc


def _is_stale(raw: Any, now: dt.datetime) -> bool:
    if not isinstance(raw, str):
        return False
    try:
        moment = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    return now - moment > dt.timedelta(days=ASSET_STALE_DAYS)


def _check_plan_relations(
    target: Path,
    path: Path,
    plan: dict[str, Any],
    now: dt.datetime,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    if plan["schema_version"] == PLAN_SCHEMA_V2:
        return failures, warnings
    relative_plan = path.relative_to(target)
    plan_ref = (
        f"docs/plans/{path.name}"
        if relative_plan.parent.as_posix() == "docs/plans/archive"
        else relative_plan.as_posix()
    )
    refs = plan.get("acceptance_refs", [])
    governance = plan.get("governance")
    if isinstance(governance, dict) and not governance["acceptance_required"] and refs:
        warnings.append(f"WARN: {plan_ref}: acceptance_required=false 但存在关联 Acceptance")
    linked: list[dict[str, Any]] = []
    for ref in refs:
        try:
            acceptance = load_asset(target / ref, ACCEPTANCE_SPEC)
            validate_acceptance(acceptance)
        except AssetError as exc:
            failures.append(f"FAIL: {plan_ref}: 关联 Acceptance 无效：{ref}：{exc}")
            continue
        linked.append(acceptance)
        if acceptance.get("plan_ref") != plan_ref:
            failures.append(f"FAIL: {plan_ref}: 与 {ref} 的正反向引用不一致")
    # 结算泄漏预警：关联验收已全部结项但 Plan 仍挂着未 settle，活索引会随之淤积。
    if (
        linked
        and len(linked) == len(refs)
        and all(item.get("settled_at") for item in linked)
        and isinstance(governance, dict)
        and not governance.get("governance_settled_at")
        and relative_plan.parent.as_posix() != "docs/plans/archive"
    ):
        warnings.append(
            f"WARN: {plan_ref}: 关联 Acceptance 已全部结项，Plan 仍未 settle"
            "（plan settle --status implemented|deprecated）"
        )
    # 结算泄漏预警（超期形态）：方案冻结后长期不 settle，活索引随之淤积。
    if (
        relative_plan.parent.as_posix() != "docs/plans/archive"
        and isinstance(governance, dict)
        and not governance.get("governance_settled_at")
        and _is_stale(plan.get("frozen_at"), now)
    ):
        warnings.append(
            f"WARN: {plan_ref}: 冻结超过 {ASSET_STALE_DAYS} 天仍未 settle"
            "（plan settle --status implemented|deprecated）"
        )
    if not isinstance(governance, dict) or not governance.get("governance_settled_at"):
        return failures, warnings
    if governance.get("knowledge_impact") != "updated":
        return failures, warnings
    for ref in governance.get("updated_knowledge_refs", []):
        try:
            knowledge = load_asset(target / ref, KNOWLEDGE_SPEC)
            validate_knowledge(knowledge)
        except AssetError as exc:
            failures.append(f"FAIL: {plan_ref}: 结算 Knowledge 无效：{ref}：{exc}")
            continue
        if knowledge.get("status") != "active":
            failures.append(f"FAIL: {plan_ref}: 结算 Knowledge 非 active：{ref}")
    return failures, warnings


def check_cross_asset_relations(target: Path) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    plan_paths = sorted((target / "docs/plans").glob("*.json"))
    plan_paths.extend(sorted((target / "docs/plans/archive").glob("*.json")))
    now = dt.datetime.now(dt.timezone.utc)
    for path in plan_paths:
        try:
            plan = validate_plan(json.loads(path.read_text(encoding="utf-8")))
            plan_failures, plan_warnings = _check_plan_relations(target, path, plan, now)
            failures.extend(plan_failures)
            warnings.extend(plan_warnings)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            AssetError,
            PlanGovernanceError,
        ) as exc:
            failures.append(f"FAIL: {path.relative_to(target)}: {exc}")
    acceptance_paths = sorted((target / ACCEPTANCE_SPEC.root).glob("*.json"))
    for path in acceptance_paths:
        try:
            acceptance = load_asset(path, ACCEPTANCE_SPEC)
            validate_acceptance(acceptance)
        except AssetError:
            continue
        ref = acceptance.get("plan_ref")
        if not ref:
            continue
        relative = path.relative_to(target).as_posix()
        try:
            plan_path, plan = _load_plan_path(target, ref)
        except (AssetError, PlanGovernanceError) as exc:
            failures.append(f"FAIL: {relative}: {exc}")
            continue
        if plan["schema_version"] == PLAN_SCHEMA_V3:
            if relative not in plan.get("acceptance_refs", []):
                failures.append(f"FAIL: {relative}: 未登记到 Plan.acceptance_refs")
        elif acceptance.get("status") == "pending":
            warnings.append(f"WARN: {relative}: 关联 v2 Plan，无法建立反向登记")
        if acceptance.get("status") != "pending":
            continue
        if "archive" in plan_path.relative_to(target).parts:
            warnings.append(f"WARN: {relative}: pending Acceptance 指向已归档 Plan")
        if _is_stale(acceptance.get("updated_at"), now):
            warnings.append(
                f"WARN: {relative}: pending 超过 {ASSET_STALE_DAYS} 天仍未结项"
            )
    return {
        "status": "failed" if failures else "passed",
        "failures": failures,
        "warnings": warnings,
        "checked": len(plan_paths) + len(acceptance_paths),
    }


def run_assets_check(
    target: Path,
    *,
    fast: bool,
    strict: bool,
    plan_checker: PlanChecker,
    knowledge_checker: AssetChecker,
    acceptance_checker: AssetChecker,
    adr_checker: AssetChecker,
    script_hygiene_checker: AssetChecker,
    structure_checker: AssetChecker,
) -> dict[str, Any]:
    plans = plan_checker(target, fast)
    knowledge = _run_asset_checker(target, "Knowledge", knowledge_checker)
    acceptance = _run_asset_checker(target, "Acceptance", acceptance_checker)
    adr = _run_asset_checker(target, "ADR", adr_checker)
    script_hygiene = _run_asset_checker(target, "ScriptHygiene", script_hygiene_checker)
    structure = _run_asset_checker(target, "Structure", structure_checker)
    cross = check_cross_asset_relations(target)
    failures = list(plans.get("failures", []))
    warnings = list(plans.get("warnings", []))
    for label, payload in (
        ("Knowledge", knowledge),
        ("Acceptance", acceptance),
        ("ADR", adr),
        ("ScriptHygiene", script_hygiene),
        ("Structure", structure),
    ):
        failures.extend(f"FAIL: {label}: {item}" for item in payload.get("failures", []))
        warnings.extend(f"WARN: {label}: {item}" for item in payload.get("warnings", []))
    failures.extend(cross["failures"])
    warnings.extend(cross["warnings"])
    failed = bool(failures) or (strict and bool(warnings))
    status = "failed" if failed else "passed"
    return {
        "status": status,
        "strict": strict,
        "fast": fast,
        "checked": {
            "plans": plans.get("checked", {}),
            "knowledge": knowledge.get("checked", 0),
            "acceptance": acceptance.get("checked", 0),
            "adr": adr.get("checked", 0),
            "script_hygiene": script_hygiene.get("checked", 0),
            "structure": structure.get("checked", 0),
            "cross": cross.get("checked", 0),
        },
        "failures": failures,
        "warnings": warnings,
        "summary": (
            f"assets-check {status}：Plan/Knowledge/Acceptance/ADR/ScriptHygiene/Structure 已检查，"
            f"违规 {len(failures)} 条、警告 {len(warnings)} 条"
        ),
    }
