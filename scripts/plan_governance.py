"""Plan v3 治理合同、bugfix 校验合同、反向引用与结算校验。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from managed_assets import atomic_write_json, atomic_write_text, canonical_json, fingerprint


PLAN_SCHEMA_V2 = "docs-harness/plan/v2"
PLAN_SCHEMA_V3 = "docs-harness/plan/v3"
PLAN_SCHEMAS = (PLAN_SCHEMA_V2, PLAN_SCHEMA_V3)
PLAN_GOVERNANCE_INPUT_SCHEMA = "docs-harness/plan-governance-input/v1"
ACCEPTANCE_ASSET_SCHEMA = "docs-harness/acceptance-asset/v1"
KNOWLEDGE_ASSET_SCHEMA = "docs-harness/knowledge-asset/v1"
GOVERNANCE_BEGIN = "<!-- docs-harness:plan-governance:start -->"
GOVERNANCE_END = "<!-- docs-harness:plan-governance:end -->"
LEGACY_PLAN_TEMPLATE_FINGERPRINTS = {
    "2.4.1": {
        "levels/brief.json": "sha256:93d23569e7561d1f6295b20d46a0e212974c8d1009211b7885176dc4ca5fd329",
        "levels/full.json": "sha256:a2c6129588f1a5a2c906afead465fae64635a84f6f918868dbc14f9bc3d42fbe",
        "profiles/general.json": "sha256:4e7f07d94617a1aeda29085040eb705f56abfbfec3b08f4dd25710d8811462d1",
        "profiles/frontend-ui.json": "sha256:ac9e8ba805fac007a1e629c86e1d7c60d5368791700f2b84ce2e7505b7beb298",
        "profiles/backend-service.json": "sha256:8510133adf7a78fc0f5759fa90fdc555e39020487439042cfda57259871d40f2",
        "profiles/bugfix.json": "sha256:1bd546f481dedba942663ed45013250f3d5d8bb406fffff66704fa0ebff145b1",
        "profiles/architecture.json": "sha256:1dcad086f31fcb409fca18a935794de20f17e11c151e444fbb8f45a3e35cd557",
        "profiles/migration-release.json": "sha256:1595b0cc7e04b132bcb8d9eff6245498735af4bf5df3c36a0d1ea844dafa6e73",
    }
}


def legacy_plan_template_fingerprints(version: Any) -> dict[str, str]:
    return LEGACY_PLAN_TEMPLATE_FINGERPRINTS.get(version, {})


class PlanGovernanceError(Exception):
    def __init__(self, message: str, code: str = "invalid_plan_governance") -> None:
        super().__init__(message)
        self.code = code


FULL_REGRESSION_REASON_CODES = {
    "cross_module_change",
    "public_contract_change",
    "shared_infrastructure_change",
    "dependency_or_shared_fixture_change",
    "release_gate",
}
FAILURE_ATTRIBUTION_CATEGORIES = {
    "change_related",
    "unrelated",
    "pre_existing",
    "environment",
    "flaky",
}
FAILURE_ATTRIBUTION_SHAPE = json.dumps(
    {"categories": sorted(FAILURE_ATTRIBUTION_CATEGORIES),
     "separate_non_change_failures": True, "evidence_required": True},
    ensure_ascii=False,
)


def nonempty_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def collect_bugfix_plan_errors(
    selection: dict[str, Any], content: dict[str, Any]
) -> list[PlanGovernanceError]:
    """收集 bugfix 校验合同的全部错误（--dry-run 用）；各字段独立判定，顺序与逐字段抛错一致。"""
    profiles = {selection.get("plan_profile"), *selection.get("secondary_profiles", [])}
    if selection.get("plan_level") != "full" or "bugfix" not in profiles:
        return []
    errors: list[PlanGovernanceError] = []
    if not nonempty_string_list(content.get("affected_modules")):
        errors.append(PlanGovernanceError(
            'Bugfix 方案的 affected_modules 必须是非空字符串数组，如 ["service/session"]', "invalid_plan_content"))
    scope = content.get("verification_scope")
    scope_mode: str | None = None
    if not isinstance(scope, dict):
        errors.append(PlanGovernanceError(
            'Bugfix 方案的 verification_scope 必须是对象，期望形状 {"mode": "affected_modules|repository_full", '
            '"commands": [实际命令字符串], "reused_passed_evidence": [可复用已通过证据，可空数组]}',
            "invalid_plan_content",
        ))
    else:
        mode = scope.get("mode")
        if mode not in {"affected_modules", "repository_full"}:
            errors.append(PlanGovernanceError(
                "verification_scope.mode 必须是 affected_modules 或 repository_full", "invalid_plan_content"))
        else:
            scope_mode = mode
        if not nonempty_string_list(scope.get("commands")):
            errors.append(PlanGovernanceError(
                "verification_scope.commands 必须是非空字符串数组", "invalid_plan_content"))
        reused = scope.get("reused_passed_evidence")
        if not isinstance(reused, list) or any(not isinstance(item, str) or not item.strip() for item in reused):
            errors.append(PlanGovernanceError(
                "verification_scope.reused_passed_evidence 必须是字符串数组（可空数组）", "invalid_plan_content"))
    trigger = content.get("full_regression_trigger")
    trigger_required: bool | None = None
    trigger_codes: list[Any] | None = None
    if not isinstance(trigger, dict) or not isinstance(trigger.get("required"), bool):
        errors.append(PlanGovernanceError(
            'full_regression_trigger 必须是对象，期望形状 {"required": true|false, "reason_codes": [原因码，可空数组], "rationale": "依据"}',
            "invalid_plan_content",
        ))
    else:
        trigger_required = trigger["required"]
        reason_codes = trigger.get("reason_codes")
        rationale = trigger.get("rationale")
        if not isinstance(reason_codes, list) or any(
            not isinstance(item, str) or item not in FULL_REGRESSION_REASON_CODES for item in reason_codes
        ):
            errors.append(PlanGovernanceError(
                "full_regression_trigger.reason_codes 仅接受：" + "、".join(sorted(FULL_REGRESSION_REASON_CODES)),
                "invalid_plan_content",
            ))
        elif len(reason_codes) != len(set(reason_codes)):
            errors.append(PlanGovernanceError("全量测试触发原因码不得重复", "invalid_plan_content"))
        else:
            trigger_codes = reason_codes
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(PlanGovernanceError("full_regression_trigger.rationale 不能为空", "invalid_plan_content"))
    if scope_mode is not None and trigger_required is not None and trigger_codes is not None:
        if scope_mode == "repository_full" and (trigger_required is not True or not trigger_codes):
            errors.append(PlanGovernanceError(
                "mode=repository_full 时 full_regression_trigger.required 必须为 true 且 reason_codes 非空", "invalid_plan_content"))
        if scope_mode == "affected_modules" and (trigger_required is not False or trigger_codes):
            errors.append(PlanGovernanceError(
                "mode=affected_modules 时 full_regression_trigger.required 必须为 false 且 reason_codes 必须为空数组", "invalid_plan_content"))
    attribution = content.get("failure_attribution")
    if not isinstance(attribution, dict):
        errors.append(PlanGovernanceError(
            "Bugfix 方案的 failure_attribution 必须是对象，期望形状 " + FAILURE_ATTRIBUTION_SHAPE, "invalid_plan_content"))
    else:
        categories = attribution.get("categories")
        if (
            not isinstance(categories, list)
            or any(not isinstance(item, str) for item in categories)
            or len(categories) != len(set(categories))
            or set(categories) != FAILURE_ATTRIBUTION_CATEGORIES
            or attribution.get("separate_non_change_failures") is not True
            or attribution.get("evidence_required") is not True
        ):
            errors.append(PlanGovernanceError(
                "failure_attribution.categories 必须恰好声明 " + "、".join(sorted(FAILURE_ATTRIBUTION_CATEGORIES))
                + "，且 separate_non_change_failures 与 evidence_required 均为字面 true",
                "invalid_plan_content",
            ))
    return errors


def validate_bugfix_plan_contract(selection: dict[str, Any], content: dict[str, Any]) -> None:
    errors = collect_bugfix_plan_errors(selection, content)
    if errors:
        raise errors[0]


def plan_fingerprint(value: dict[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("plan_fingerprint", None)
    digest = hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()
    return "sha256:" + digest


def seal_plan(value: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(value)
    sealed["plan_fingerprint"] = plan_fingerprint(sealed)
    return sealed


def _single_line(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value:
        raise PlanGovernanceError(f"{label} 必须是单行非空字符串")
    return value.strip()


def governance_from_content(level: str, content: dict[str, Any]) -> dict[str, Any] | None:
    if level != "full":
        return None
    acceptance_required = content.get("acceptance_required")
    knowledge_impact = content.get("knowledge_impact")
    if not isinstance(acceptance_required, bool):
        raise PlanGovernanceError("Full Plan 的 acceptance_required 必须是布尔值")
    if knowledge_impact not in {"updated", "unchanged"}:
        raise PlanGovernanceError("Full Plan 的 knowledge_impact 必须是 updated 或 unchanged")
    return {
        "acceptance_required": acceptance_required,
        "knowledge_impact": knowledge_impact,
        "updated_knowledge_refs": [],
        "unchanged_reason": None,
        "governance_settled_at": None,
    }


def _validate_governance_state(governance: dict[str, Any]) -> None:
    refs = governance.get("updated_knowledge_refs")
    reason = governance.get("unchanged_reason")
    settled_at = governance.get("governance_settled_at")
    if not isinstance(refs, list) or any(
        not isinstance(item, str) or not re.fullmatch(r"docs/knowledge/[\w.-]+\.json", item)
        for item in refs
    ) or len(refs) != len(set(refs)):
        raise PlanGovernanceError("Plan updated_knowledge_refs 无效", "invalid_plan_ref")
    if settled_at is not None and not isinstance(settled_at, str):
        raise PlanGovernanceError("Plan governance_settled_at 无效", "invalid_plan_ref")
    if governance["knowledge_impact"] == "updated":
        if settled_at and (not refs or reason is not None):
            raise PlanGovernanceError("已结算 updated 治理状态无效", "invalid_plan_ref")
        if not settled_at and (refs or reason is not None):
            raise PlanGovernanceError("未结算 updated 治理状态无效", "invalid_plan_ref")
    elif settled_at:
        _single_line(reason, "unchanged_reason")
        if refs:
            raise PlanGovernanceError("unchanged 治理不得登记 Knowledge 引用", "invalid_plan_ref")
    elif refs or reason is not None:
        raise PlanGovernanceError("未结算 unchanged 治理状态无效", "invalid_plan_ref")


def validate_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") not in PLAN_SCHEMAS:
        raise PlanGovernanceError("方案冻结合同无效", "invalid_plan_ref")
    if value.get("plan_fingerprint") != plan_fingerprint(value):
        raise PlanGovernanceError("方案冻结合同指纹无效", "invalid_plan_ref")
    if value["schema_version"] == PLAN_SCHEMA_V2:
        return value
    refs = value.get("acceptance_refs")
    if not isinstance(refs, list) or any(
        not isinstance(item, str) or not re.fullmatch(r"docs/acceptance/[\w.-]+\.json", item)
        for item in refs
    ) or len(refs) != len(set(refs)):
        raise PlanGovernanceError("Plan acceptance_refs 无效", "invalid_plan_ref")
    expected = governance_from_content(value.get("plan_level", ""), value.get("content", {}))
    governance = value.get("governance")
    if expected is None:
        if governance is not None:
            raise PlanGovernanceError("非 Full Plan 不得包含 governance", "invalid_plan_ref")
    elif not isinstance(governance, dict) or any(
        governance.get(key) != expected[key]
        for key in ("acceptance_required", "knowledge_impact")
    ):
        raise PlanGovernanceError("Plan governance 与 content 声明不一致", "invalid_plan_ref")
    else:
        _validate_governance_state(governance)
    return value


def new_plan_fields(level: str, content: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {"acceptance_refs": []}
    governance = governance_from_content(level, content)
    if governance is not None:
        fields["governance"] = governance
    return fields


def render_governance_block(frozen: dict[str, Any]) -> str:
    refs = frozen.get("acceptance_refs", [])
    ref_text = "、".join(f"`{item}`" for item in refs) if refs else "无"
    lines = [GOVERNANCE_BEGIN, "## 资产治理", "", f"- 关联验收：{ref_text}"]
    governance = frozen.get("governance")
    if isinstance(governance, dict):
        lines.extend([
            f"- 需要 Acceptance：{str(governance['acceptance_required']).lower()}",
            f"- Knowledge 影响：{governance['knowledge_impact']}",
        ])
    lines.append(GOVERNANCE_END)
    return "\n".join(lines)


def update_plan_markdown(markdown: str, frozen: dict[str, Any]) -> str:
    fingerprint_line = f"- 冻结合同：`{frozen['plan_fingerprint']}`"
    if not re.search(r"(?m)^- 冻结合同：`[^`]+`$", markdown):
        raise PlanGovernanceError("Plan Markdown 缺少冻结合同指纹")
    updated = re.sub(r"(?m)^- 冻结合同：`[^`]+`$", fingerprint_line, markdown, count=1)
    block = render_governance_block(frozen)
    begin_count, end_count = updated.count(GOVERNANCE_BEGIN), updated.count(GOVERNANCE_END)
    if begin_count != end_count or begin_count > 1:
        raise PlanGovernanceError("Plan Markdown 治理区块不完整或重复")
    if begin_count == 1:
        pattern = re.escape(GOVERNANCE_BEGIN) + r".*?" + re.escape(GOVERNANCE_END)
        return re.sub(pattern, block, updated, count=1, flags=re.DOTALL)
    return updated.rstrip() + "\n\n" + block + "\n"


def _pair_exists(source: Path, document: Path) -> bool:
    return (
        source.is_file() and not source.is_symlink()
        and document.is_file() and not document.is_symlink()
    )


def _plan_pair(target: Path, raw: str, *, allow_archived: bool = False) -> tuple[Path, Path]:
    relative = Path(raw)
    if relative.is_absolute() or relative.parent.as_posix() not in {
        "docs/plans", "docs/plans/archive",
    } or relative.suffix != ".json":
        raise PlanGovernanceError("plan_ref 必须指向项目内 Plan JSON")
    source = target / relative
    document = source.with_suffix(".md")
    if _pair_exists(source, document):
        return source, document
    # allow_archived：引用登记时的 plan_ref 指向活路径,方案随后被 settle deprecated
    # 移入 archive/;反向登记维护(如 acceptance settle 退出登记)按归档位置回退解析
    if allow_archived and relative.parent.as_posix() == "docs/plans":
        archived_source = target / "docs/plans/archive" / relative.name
        archived_document = archived_source.with_suffix(".md")
        if _pair_exists(archived_source, archived_document):
            return archived_source, archived_document
    raise PlanGovernanceError(f"Plan 引用不存在：{raw}")


def _update_acceptance_ref(
    target: Path, plan_ref: str, acceptance_ref: str, add: bool, *, allow_archived: bool = False
) -> bool:
    source, document = _plan_pair(target, plan_ref, allow_archived=allow_archived)
    frozen = validate_plan(json.loads(source.read_text(encoding="utf-8")))
    if frozen["schema_version"] == PLAN_SCHEMA_V2:
        return False
    refs = list(frozen["acceptance_refs"])
    if add and acceptance_ref not in refs:
        refs.append(acceptance_ref)
    elif not add and acceptance_ref in refs:
        refs.remove(acceptance_ref)
    else:
        return False
    frozen["acceptance_refs"] = refs
    sealed = seal_plan(frozen)
    markdown = update_plan_markdown(document.read_text(encoding="utf-8"), sealed)
    atomic_write_json(source, sealed)
    atomic_write_text(document, markdown)
    return True


def add_acceptance_ref(
    target: Path, plan_ref: str, acceptance_ref: str, *, allow_archived_plan: bool = False
) -> bool:
    return _update_acceptance_ref(
        target, plan_ref, acceptance_ref, True, allow_archived=allow_archived_plan
    )


def remove_acceptance_ref(
    target: Path, plan_ref: str, acceptance_ref: str, *, allow_archived_plan: bool = False
) -> bool:
    return _update_acceptance_ref(
        target, plan_ref, acceptance_ref, False, allow_archived=allow_archived_plan
    )


def _load_linked_asset(target: Path, raw: str, schema: str) -> dict[str, Any]:
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise PlanGovernanceError(f"治理引用越出项目：{raw}")
    path = target / relative
    if not path.is_file() or path.is_symlink():
        raise PlanGovernanceError(f"治理引用不存在：{raw}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanGovernanceError(f"治理引用无法读取：{raw}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != schema:
        raise PlanGovernanceError(f"治理引用 Schema 无效：{raw}")
    if value.get("asset_fingerprint") != fingerprint(value):
        raise PlanGovernanceError(f"治理引用指纹无效：{raw}")
    return value


def prepare_settlement(
    target: Path,
    frozen: dict[str, Any],
    governance_input: Any,
    now: str,
) -> tuple[dict[str, Any], list[str]]:
    validate_plan(frozen)
    if frozen["schema_version"] == PLAN_SCHEMA_V2:
        return frozen, []
    governance = frozen.get("governance")
    if not isinstance(governance, dict):
        return frozen, []
    warnings: list[str] = []
    if governance["acceptance_required"]:
        acceptances = [
            _load_linked_asset(target, ref, ACCEPTANCE_ASSET_SCHEMA)
            for ref in frozen["acceptance_refs"]
        ]
        terminal = [item for item in acceptances if item.get("settled_at")]
        if not terminal:
            raise PlanGovernanceError(
                "该方案声明需要 Acceptance，但没有已结项验收。下一步：先执行 acceptance create/record/settle，再重跑 plan settle"
            )
        if any(item.get("status") == "failed" for item in terminal):
            warnings.append("WARN: 关联 Acceptance 以 failed 结项，收尾报告必须说明失败结果")
    if not isinstance(governance_input, dict) or governance_input.get("schema_version") != PLAN_GOVERNANCE_INPUT_SCHEMA:
        raise PlanGovernanceError(
            "Plan v3 缺少治理结算输入。下一步：提供 --governance-input，形状 "
            '{"schema_version": "docs-harness/plan-governance-input/v1", "updated_knowledge_refs": [活跃 Knowledge 引用], "unchanged_reason": "不更新理由"}'
        )
    allowed = {"schema_version", "updated_knowledge_refs", "unchanged_reason"}
    unknown = sorted(set(governance_input) - allowed)
    if unknown:
        raise PlanGovernanceError("治理结算输入包含未注册字段：" + ", ".join(unknown))
    if governance["knowledge_impact"] == "updated":
        refs = governance_input.get("updated_knowledge_refs")
        if (
            not isinstance(refs, list) or not refs
            or any(not isinstance(item, str) for item in refs)
            or len(refs) != len(set(refs))
        ):
            raise PlanGovernanceError("knowledge_impact=updated 必须提供唯一的 updated_knowledge_refs")
        for ref in refs:
            value = _load_linked_asset(target, _single_line(ref, "Knowledge 引用"), KNOWLEDGE_ASSET_SCHEMA)
            if value.get("status") != "active":
                raise PlanGovernanceError(f"更新后的 Knowledge 必须为 active：{ref}")
        governance["updated_knowledge_refs"] = refs
        governance["unchanged_reason"] = None
    else:
        governance["updated_knowledge_refs"] = []
        governance["unchanged_reason"] = _single_line(
            governance_input.get("unchanged_reason"), "unchanged_reason"
        )
    governance["governance_settled_at"] = now
    frozen["governance"] = governance
    return seal_plan(frozen), warnings
