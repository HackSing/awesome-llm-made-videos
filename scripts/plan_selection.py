"""Plan 选择构建、合同指纹、缓存与校验。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Sequence


def _controller():
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "build_parser"):
        return main
    import harness
    return harness


class _ControllerProxy:
    def __getattr__(self, name):
        return getattr(_controller(), name)


h = _ControllerProxy()


def plan_selection_contract(selection: dict[str, Any]) -> dict[str, Any]:
    """选择合同：只保留会改变冻结内容或必填规则的部分。

    来源说明、标签与 guidance 属于展示信息；模板只改文案时不应让旧 selection 失效。
    """
    return {
        "schema_version": selection.get("schema_version"),
        "plan_level": selection.get("plan_level"),
        "plan_profile": selection.get("plan_profile"),
        "secondary_profiles": selection.get("secondary_profiles", []),
        "template_versions": selection.get("template_versions", []),
        "fields": [
            {"id": item.get("id"), "required": bool(item.get("required"))}
            for item in selection.get("fields", [])
            if isinstance(item, dict)
        ],
    }


def plan_selection_contract_fingerprint(selection: dict[str, Any]) -> str:
    return h.sha256_json(plan_selection_contract(selection))


def plan_selection_diff(actual: dict[str, Any], expected: dict[str, Any]) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    for key in ("schema_version", "plan_level", "plan_profile", "secondary_profiles", "template_versions"):
        if actual.get(key) != expected.get(key):
            differences.append({"field": key, "expected": expected.get(key), "actual": actual.get(key)})
    actual_fields = {
        item.get("id"): bool(item.get("required"))
        for item in actual.get("fields", [])
        if isinstance(item, dict)
    }
    expected_fields = {
        item.get("id"): bool(item.get("required"))
        for item in expected.get("fields", [])
        if isinstance(item, dict)
    }
    for field_id in sorted(set(actual_fields) | set(expected_fields)):
        if actual_fields.get(field_id) != expected_fields.get(field_id):
            differences.append(
                {
                    "field": f"fields.{field_id}",
                    "expected": expected_fields.get(field_id),
                    "actual": actual_fields.get(field_id),
                }
            )
    if not differences:
        differences.append(
            {
                "field": "contract_fingerprint",
                "expected": expected.get("contract_fingerprint"),
                "actual": actual.get("contract_fingerprint"),
            }
        )
    return differences


def plan_content_template(fields: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """给 agent 的填空骨架：键就是允许的 content 字段，值留空等填。"""
    return {
        item["id"]: ([] if item["id"] == "key_symbols" else None)
        for item in fields
        if isinstance(item.get("id"), str)
    }


def build_plan_selection(
    *,
    level: str,
    profile: str,
    secondary_profiles: Sequence[str],
    reason: str,
) -> dict[str, Any]:
    registry = h.template_registry(h.SCRIPT_ROOT / h.PLAN_TEMPLATES_RELATIVE)
    if not registry:
        raise h.HarnessError(f"{h.VERSION} 方案模板不完整", code="invalid_plan_registry")
    fields: list[dict[str, Any]] = []
    versions: list[str] = []
    if level != "none":
        level_key = f"levels/{level}.json"
        fields.extend(registry[level_key]["fields"])
        versions.append(f"level/{level}@{h.VERSION}")
    if level == "full":
        fields.extend(registry[h.PROFILE_FILES[profile]]["fields"])
        versions.append(f"profile/{profile}@{h.VERSION}")
        # 次级 Profile 只补可能影响执行或验收的字段；是否填写由 agent 按任务判断。
        for current in secondary_profiles:
            fields.extend(
                {**item, "required": False}
                for item in registry[h.PROFILE_FILES[current]]["fields"]
            )
            versions.append(f"profile/{current}@{h.VERSION}")
    selection: dict[str, Any] = {
        "schema_version": h.PLAN_SELECTION_SCHEMA,
        "plan_level": level,
        "plan_profile": profile,
        "secondary_profiles": list(secondary_profiles),
        "reason": reason,
        "template_versions": versions,
        "fields": h.unique_fields(fields),
    }
    selection["contract_fingerprint"] = plan_selection_contract_fingerprint(selection)
    selection["selection_fingerprint"] = h.sha256_json(selection)
    return selection


def resolve_plan_choice(args: argparse.Namespace) -> tuple[str, str, list[str], str]:
    """把 CLI 信号归一成 (level, profile, secondary_profiles, reason)。"""
    if args.level:
        level = args.level
        level_reason = "user_or_host_explicit"
    elif args.user_requested_plan or args.high_risk or args.cross_module or args.complexity == "complex":
        level = "full"
        level_reason = "effect_requires_full"
    elif args.complexity == "moderate":
        level = "brief"
        level_reason = "effect_requires_brief"
    elif (args.profile or args.surface or "general") != "general":
        # 只给 profile 不给 level 时，none 会丢失模板语义；按 domain profile 视为 full。
        level = "full"
        level_reason = "profile_implies_full"
    else:
        level = "none"
        level_reason = "simple_direct_execution"
    profile = args.profile or args.surface or "general"
    secondary = list(dict.fromkeys(args.secondary_profile or []))
    secondary = [item for item in secondary if item != profile]
    if secondary and level != "full":
        raise h.HarnessError(
            "次级 Profile 只适用于 full 方案",
            code="invalid_plan_selection",
        )
    if len(secondary) > 2:
        raise h.HarnessError("次级 Profile 最多两个", code="invalid_plan_selection")
    reason = level_reason + ("; profile_explicit" if args.profile else "; profile_from_surface")
    return level, profile, secondary, reason


h.PLAN_SELECTION_CACHE_RELATIVE = "docs-harness/plan-selections"
h.PLAN_SELECT_KNOWLEDGE_LIMIT = 5
h.PLAN_SELECT_KNOWLEDGE_MAX_CHARS = 2000


def plan_selection_cache_dir(target: Path) -> Path | None:
    resolved = h.git_common_dir(target) if hasattr(h, "git_common_dir") else h.git_dir(target)
    if resolved is None:
        return None
    return resolved / h.PLAN_SELECTION_CACHE_RELATIVE


def cache_plan_selection(target: Path, selection: dict[str, Any]) -> bool:
    """把 plan select 结果缓存进 git 运行态目录，供 plan create --selection sha256:<fp> 引用。

    缓存只是便利性副本（真源仍是 selection 内容与其指纹校验），写入失败不阻断 select。"""
    directory = plan_selection_cache_dir(target)
    if directory is None:
        return False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        name = selection["selection_fingerprint"].removeprefix("sha256:") + ".json"
        h.atomic_write_json(directory / name, selection)
    except OSError:
        return False
    return True


def resolve_plan_selection_path(target: Path, raw: str) -> Path:
    if not raw.startswith("sha256:"):
        return h.project_input_path(target, raw, code="invalid_plan_selection")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", raw):
        raise h.HarnessError(
            "--selection 的 sha256 引用格式无效，应为 plan select 输出里的 selection_ref",
            code="invalid_plan_selection",
        )
    directory = plan_selection_cache_dir(target)
    candidate = (
        directory / (raw.removeprefix("sha256:") + ".json") if directory is not None else None
    )
    if candidate is None or not candidate.is_file() or candidate.is_symlink():
        raise h.HarnessError(
            f"未找到 {raw} 对应的 plan select 缓存。下一步：在本项目重新运行 plan select"
            "（其输出的 selection_ref 即为该指纹），或把 plan select 输出保存为项目内 JSON 文件后传文件路径",
            code="invalid_plan_selection",
        )
    return candidate


def plan_select_knowledge_payload(target: Path, task: str) -> dict[str, Any]:
    """plan select 的知识被动注入：--task 给任务描述时附相关事实，否则只给存在性提示。"""
    task = task.strip()
    if task:
        tokens = h.query_tokens(task)
        if tokens:
            try:
                managed = h.query_knowledge_assets(
                    target, tokens, h.PLAN_SELECT_KNOWLEDGE_LIMIT, h.PLAN_SELECT_KNOWLEDGE_MAX_CHARS
                )
            except h.AssetError as exc:
                return {"knowledge_hits": [], "knowledge_hits_error": str(exc)}
            return {
                "knowledge_hits": managed["facts"],
                "knowledge_conflicts": managed["conflicts"],
            }
    root = target / h.KNOWLEDGE_SPEC.root
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


SELECTION_AUXILIARY_KEYS = (
    "selection_ref",
    "selection_cached",
    "content_template",
    "selection_warnings",
    "knowledge_hits",
    "knowledge_conflicts",
    "knowledge_hint",
    "knowledge_hits_error",
)


def validate_selection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise h.HarnessError("方案选择文件无效", code="invalid_plan_selection")
    value = {key: item for key, item in value.items() if key not in SELECTION_AUXILIARY_KEYS}
    if value.get("schema_version") == h.LEGACY_PLAN_SELECTION_SCHEMA:
        raise h.HarnessError(
            "方案选择文件是旧版 v2；请重新运行 plan select 生成 v3 选择",
            code="invalid_plan_selection",
            extra_payload={"next_action": "重新运行 plan select，并把新的输出保存为 selection 文件"},
        )
    if value.get("schema_version") != h.PLAN_SELECTION_SCHEMA:
        raise h.HarnessError(
            f"方案选择文件无效，Schema 必须为 {h.PLAN_SELECTION_SCHEMA}",
            code="invalid_plan_selection",
            extra_payload={"expected": h.PLAN_SELECTION_SCHEMA, "next_action": "重新运行 plan select"},
        )
    level = value.get("plan_level")
    profile = value.get("plan_profile")
    secondary = value.get("secondary_profiles", [])
    reason = value.get("reason")
    if level not in h.PLAN_LEVELS or profile not in h.PLAN_PROFILES:
        raise h.HarnessError("方案 Level/Profile 未注册", code="invalid_plan_selection")
    if not isinstance(secondary, list) or any(item not in h.PLAN_PROFILES for item in secondary):
        raise h.HarnessError("次级 Profile 未注册", code="invalid_plan_selection")
    if not isinstance(reason, str) or not reason:
        raise h.HarnessError("方案选择缺少原因", code="invalid_plan_selection")
    expected = build_plan_selection(
        level=level,
        profile=profile,
        secondary_profiles=secondary,
        reason=reason,
    )
    if value.get("contract_fingerprint") != expected["contract_fingerprint"]:
        raise h.HarnessError(
            "方案选择与当前模板不一致",
            code="invalid_plan_selection",
            extra_payload={
                "differences": plan_selection_diff(value, expected),
                "next_action": "重新运行 plan select 生成当前模板的 selection；若只是模板文案变化，可直接重跑后复用 content",
            },
        )
    unsigned = {key: item for key, item in value.items() if key != "selection_fingerprint"}
    if value.get("selection_fingerprint") != h.sha256_json(unsigned):
        raise h.HarnessError(
            "方案选择指纹无效，文件可能被手工编辑；下一步：重新运行 plan select",
            code="invalid_plan_selection",
        )
    return value


