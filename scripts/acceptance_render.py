"""Acceptance Markdown 投影渲染。"""

from __future__ import annotations

from typing import Any


def _criterion_markdown(criterion: dict[str, Any]) -> str:
    records = criterion["records"]
    latest = records[-1] if records else None
    evidence = "、".join(f"`{ref}`" for ref in latest.get("evidence_refs", [])) if latest else "尚无"
    return (
        f"### `{criterion['id']}` {criterion['title']}\n\n"
        f"- 状态：{criterion['status']}\n- 类型：{criterion['acceptance_type']}\n"
        f"- 层级：{criterion['layer']}\n- 证据：{evidence}"
    )


def render_markdown(asset: dict[str, Any]) -> str:
    from acceptance_assets import ACCEPTANCE_SPEC, STATUS_LABELS

    symbols = "、".join(f"`{item}`" for item in asset["key_symbols"])
    criteria = "\n\n".join(_criterion_markdown(item) for item in asset["criteria"])
    refs = []
    if asset.get("plan_ref"):
        refs.append(f"- 关联方案：`{asset['plan_ref']}`")
    if asset.get("knowledge_refs"):
        refs.append("- 关联知识：" + "、".join(f"`{item}`" for item in asset["knowledge_refs"]))
    if asset["status"] == "deprecated":
        destination = asset.get("replacement")
        refs.append(f"- 去向：`{destination}`" if destination else "- 去向：未注明")
    elif asset.get("replacement"):
        refs.append(f"- 替代资产：`{asset['replacement']}`")
    return (
        f"> 状态：{STATUS_LABELS[asset['status']]}\n{ACCEPTANCE_SPEC.marker}\n\n# {asset['title']}\n\n"
        f"- 修订：{asset['revision']}\n- 关键符号：{symbols}\n"
        f"- 资产指纹：`{asset['asset_fingerprint']}`\n"
        + (f"- 最近修订：{asset['last_amend_reason']}\n" if asset.get("last_amend_reason") else "")
        + "\n".join(refs) +
        f"\n\n## 验收目标\n\n{asset['objective']}\n\n## 验收标准\n\n{criteria}\n"
    )
