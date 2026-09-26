#!/usr/bin/env python3
"""Turn triage labels into works / appearances and refresh the generated README tables.

Inputs
  data/candidates/*.csv                   what the collectors found
  data/triage/*.labels.jsonl              triage output (scripts/triage/PROMPT.md)
  data/review/*.jsonl                     second-pass notes (method, frames seen, code checked) laid over triage
  data/triage/manual.labels.jsonl         hand-written labels; loaded last, so they win
  data/work_links.csv                     hand-curated merges: appearance_id -> work_id, with basis

Outputs
  data/works.csv, data/appearances.csv    one row per work / per appearance
  data/candidates/*.csv                   review_status + triage note filled in
  README.md, launches/*.md                tables between <!-- works:... --> markers

A labelled row becomes a work when category is work/comparison and llm_made is
yes/likely. Each such row is its own work unless work_links.csv says it is an
appearance of another one.
"""

import glob
import json
import re
from collections import defaultdict

from _common import CANDIDATE_FIELDS, CANDIDATES, DATA, ROOT, iso, read_csv, utc_now, write_csv

WORK_FIELDS = [
    "work_id", "kind", "title", "creator", "creator_url", "original_url", "first_published_utc", "models",
    "primary_path", "evidence_level", "evidence_links", "creator_claims", "observed", "method", "review_note",
    "reviewed_utc",
]
APPEARANCE_FIELDS = [
    "appearance_id", "work_id", "platform", "url", "uploader", "uploader_id", "published_utc",
    "duration_s", "views_at_capture", "captured_utc", "repost_status", "credit_in_post", "note",
]
MODEL_NAMES = {
    "claude-opus-5-5": "Opus 5.5", "gpt-6-sol": "GPT-6 Sol", "gpt-6-luna": "GPT-6 Luna",
    "gpt-6-astra": "GPT-6 Astra", "gpt-6": "GPT-6", "gpt-6-pro": "GPT-6 Pro",
    "claude": "Claude（版本未说明）", "claude-fable-5-1": "Fable 5.1", "grok-4-7": "Grok 4.7",
    "gemini-4-pro": "Gemini 4 Pro", "gemini-3-pro": "Gemini 3 Pro", "gemini-3-8": "Gemini 3.8",
    "gpt-5-6-luna": "GPT-5.6 Luna", "gpt-5-6-sol": "GPT-5.6 Sol", "gpt-5-6": "GPT-5.6（型号未说明）",
    "claude-opus-5": "Opus 5", "gpt": "GPT（版本未说明）", "openai-codex": "Codex（模型未说明）",
    "gemini": "Gemini（版本未说明）", "deepseek": "DeepSeek（版本未说明）", "deepseek-v4-1": "DeepSeek V4.1",
    "deepseek-v4-1-flash": "DeepSeek V4.1 Flash", "doubao": "豆包（版本未说明）", "doubao-2-1-lite": "豆包 2.1 Lite",
    "kimi-k3": "Kimi K3", "glm-5-3": "GLM-5.3", "step-5-preview": "Step 5 Preview", "workbuddy-hy4": "WorkBuddy HY4",
}
# image models are credited in notes, not as the language model that made the video
NON_LLM = {"gpt-image", "gpt-image-2-5"}
PATH_NAMES = {
    "procedural_2d": "代码绘制的 2D 动画",
    "educational_explainer": "教学讲解",
    "3d_or_realtime_graphics": "3D 与实时图形",
    "existing_source_transformation": "改编现有素材",
    "external_video_model": "调度外部视频模型",
    "app_or_game_capture": "程序与游戏录屏",
    "mixed_or_not_established": "混合或未说明",
}
PLATFORM_NAMES = {"x": "X", "bilibili": "B站", "youtube": "YouTube"}
# A* = a code link is present but nobody has checked it matches the video (automatic triage cannot)
EVIDENCE_ORDER = {"A": 0, "A*": 1, "B": 2, "C": 3, "D": 4}
INCLUDED = {"work", "comparison"}
# triage vocabulary -> appearances.repost_status (docs/methodology.md)
REPOST_STATUS = {
    "original_claimed": "original_claimed", "repost_declared": "declared_repost",
    "repost_suspected": "suspected_repost", "unknown": "unknown",
}
REPOSTS = {"declared_repost", "uncredited_repost", "suspected_repost"}
# a suspected repost is a text-only guess by triage, so it is never shown as a plain "repost"
REPOST_TAGS = {"declared_repost": "（转载）", "uncredited_repost": "（未署名转载）", "suspected_repost": "（疑似转载）"}


def read_jsonl(path):
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def load_labels():
    """Triage labels, then manual labels (they replace), then review overlays (data/review/*.jsonl).

    A review overlay adds what a second pass found (method_zh, observed_zh, checked
    code links) to an existing label. It may lower a triage label's evidence, never
    raise it to A, and never touches the evidence of a hand-written label.
    """
    labels = {}
    paths = sorted(glob.glob(str(DATA / "triage" / "*.labels.jsonl")))
    manual = [p for p in paths if p.endswith("manual.labels.jsonl")]
    for path in [p for p in paths if p not in manual] + manual:
        for lab in read_jsonl(path):
            labels[lab["appearance_id"]] = dict(lab, manual=path in manual)
    for path in sorted(glob.glob(str(DATA / "review" / "*.jsonl"))):
        for extra in read_jsonl(path):
            lab = labels.get(extra["appearance_id"])
            if not lab:
                continue
            extra = {k: v for k, v in extra.items() if v not in ("", None)}
            if lab["manual"] or extra.get("evidence") == "A":
                extra.pop("evidence", None)
            lab.update(extra)
    for lab in labels.values():
        # A needs someone to have checked the code against the video: us by hand,
        # or athemeroy in a reviewed case they say they verified
        if lab["evidence"] == "A" and not lab["manual"] and not lab.get("verified_by"):
            lab["evidence"] = "A*"
    return labels


def as_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def is_work(lab):
    return lab["category"] in INCLUDED and lab["llm_made"] in ("yes", "likely")


def main():
    labels = load_labels()
    links = {r["appearance_id"]: r for r in read_csv(DATA / "work_links.csv")}
    previous = {w["work_id"]: w for w in read_csv(DATA / "works.csv")}
    now = iso(utc_now())

    # A hand-linked appearance nobody labelled (e.g. an athemeroy seed post, or an original
    # added by hand) takes its label from another appearance of the same work.
    for aid, link in links.items():
        if aid in labels:
            continue
        sibling = next((labels[a] for a, l in links.items() if l["work_id"] == link["work_id"] and a in labels), None)
        if sibling:
            labels[aid] = dict(sibling, appearance_id=aid, source_hint="", note="继承同一作品其他出现记录的标签")

    # 1. Update candidates with triage outcome
    candidates = {}
    for path in sorted(CANDIDATES.glob("*.csv")):
        rows = read_csv(path)
        for r in rows:
            lab = labels.get(r["appearance_id"])
            if not lab:
                continue
            r["review_status"] = "work" if (is_work(lab) or r["appearance_id"] in links) else f"excluded:{lab['category']}" + (
                "" if lab["category"] not in INCLUDED else f"/llm_made={lab['llm_made']}")
            candidates[r["appearance_id"]] = r
        write_csv(path, rows, CANDIDATE_FIELDS)

    # 2. Appearances and works
    appearances, works = [], {}
    by_work = defaultdict(list)
    for aid, lab in labels.items():
        r = candidates.get(aid)
        link = links.get(aid)
        # a hand-curated link always wins over the automatic label
        if not r or not (is_work(lab) or link):
            continue
        work_id = link["work_id"] if link else aid.replace(":", "-")
        by_work[work_id].append((r, lab))
        appearances.append({
            "appearance_id": aid, "work_id": work_id, "platform": r["platform"], "url": r["url"],
            "uploader": r["uploader"], "uploader_id": r["uploader_id"], "published_utc": r["published_utc"],
            "duration_s": r["duration_s"], "views_at_capture": r["views"], "captured_utc": r["last_captured_utc"],
            "repost_status": link["repost_status"] if link else REPOST_STATUS.get(lab["repost"], "unknown"),
            "credit_in_post": lab.get("source_hint", ""),
            "note": (link["basis"] if link else lab.get("note", "")),
        })

    for work_id, items in by_work.items():
        # The original is the appearance not marked as a repost; otherwise the earliest one
        status = {a["appearance_id"]: a["repost_status"] for a in appearances if a["work_id"] == work_id}
        items.sort(key=lambda it: (status[it[0]["appearance_id"]] in REPOSTS, it[0]["published_utc"]))
        r, lab = items[0]
        best_evidence = min((it[1]["evidence"] for it in items), key=lambda e: EVIDENCE_ORDER.get(e, 9))
        works[work_id] = {
            "work_id": work_id,
            "kind": "comparison" if all(it[1]["category"] == "comparison" for it in items) else "work",
            "title": lab["label_zh"],
            "creator": r["uploader"],
            "creator_url": "",
            "original_url": r["url"],
            "first_published_utc": min(it[0]["published_utc"] for it in items),
            "models": ";".join(sorted({m for it in items for m in it[1]["models"] if m not in NON_LLM})),
            "primary_path": lab.get("primary_path") or "mixed_or_not_established",
            "evidence_level": best_evidence,
            "evidence_links": " ".join(sorted(filter(None, {it[0]["desc_links"] for it in items}))),
            "creator_claims": lab.get("note", ""),
            "observed": "; ".join(filter(None, [lab.get("observed_zh", ""), f"duration_s={r['duration_s']}" if r["duration_s"] else ""])),
            "method": next((it[1]["method_zh"] for it in items if it[1].get("method_zh")), ""),
            "review_note": f"triage category={lab['category']} llm_made={lab['llm_made']}"
            + (f"; verified_by={lab['verified_by']}" if lab.get("verified_by") else ""),
            "reviewed_utc": previous.get(work_id, {}).get("reviewed_utc") or now,
        }

    write_csv(DATA / "appearances.csv", sorted(appearances, key=lambda a: (a["work_id"], a["published_utc"])), APPEARANCE_FIELDS)
    write_csv(DATA / "works.csv", sorted(works.values(), key=lambda w: w["first_published_utc"]), WORK_FIELDS)

    # 3. Generated tables
    apps_by_work = defaultdict(list)
    for a in appearances:
        apps_by_work[a["work_id"]].append(a)

    def views(work_id):
        return max(as_int(a["views_at_capture"]) for a in apps_by_work[work_id])

    def row(w):
        models = "、".join(MODEL_NAMES.get(m, m) for m in w["models"].split(";") if m)
        where = " · ".join(
            f"[{PLATFORM_NAMES.get(a['platform'], a['platform'])}{REPOST_TAGS.get(a['repost_status'], '')}]({a['url']})"
            for a in sorted(apps_by_work[w["work_id"]], key=lambda a: a["published_utc"])
        )
        title = w["title"] + ("（对比）" if w["kind"] == "comparison" else "")
        return f"| {title} | {w['creator']} | {models} | {where} | {w['evidence_level']} |"

    def render(limit, data_prefix):
        sections = []
        for path_key, path_name in PATH_NAMES.items():
            ws = [w for w in works.values() if w["primary_path"] == path_key]
            if not ws:
                continue
            # finished works before side-by-side model tests, then evidence, then reach
            ws.sort(key=lambda w: (w["kind"] == "comparison", EVIDENCE_ORDER.get(w["evidence_level"], 9), -views(w["work_id"])))
            lines = [f"### {path_name}（{len(ws)}）", "", "| 作品 | 作者 | 模型 | 出现 | 证据 |", "|---|---|---|---|:-:|"]
            lines += [row(w) for w in ws[:limit]]
            if len(ws) > limit:
                lines.append(f"\n其余 {len(ws) - limit} 件见 [`data/works.csv`]({data_prefix}works.csv)。")
            sections.append("\n".join(lines))
        return "\n\n".join(sections)

    stats = {
        "works": len(works),
        "appearances": len(appearances),
        "triaged": len(labels),
        "by_platform": {p: sum(1 for a in appearances if a["platform"] == p) for p in PLATFORM_NAMES},
        "reposts": sum(1 for a in appearances if a["repost_status"] in REPOSTS),
        "evidence": {e: sum(1 for w in works.values() if w["evidence_level"] == e) for e in EVIDENCE_ORDER},
        "by_repost_status": {k: sum(1 for a in appearances if a["repost_status"] == k) for k in sorted(REPOSTS)},
    }
    (DATA / "build-summary.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", "utf-8")

    # Candidate counts, split so seed-only X rows are not presented as our own coverage
    cand = defaultdict(list)
    for p in sorted(CANDIDATES.glob("*.csv")):
        for r in read_csv(p):
            cand[r["platform"]].append(r)
    seed_only = [r for r in cand.get("x", []) if all(q.startswith("seed:") for q in r["matched_queries"].split(";") if q)]
    seed_unlabelled = sum(1 for r in seed_only if r["appearance_id"] not in labels)
    seen_frames = sum(1 for w in works.values() if any(
        labels[a["appearance_id"]].get("observed_zh") for a in apps_by_work[w["work_id"]] if a["appearance_id"] in labels))
    date = now[:10]
    per_platform = "，".join(f"{PLATFORM_NAMES.get(k, k)} {len(v):,}" for k, v in cand.items())
    summary = (
        f"> **快照 {date}**：候选帖子 {sum(len(v) for v in cand.values()):,} 条（{per_platform}；"
        f"其中 X 的 {len(seed_only):,} 条来自 athemeroy 仓库，他们人工审过的案例已转成本项目的标签，"
        f"其余 {seed_unlabelled:,} 条本项目没有筛选）。"
        f"已筛选 {len(labels):,} 条，确认为大模型参与制作的作品 {len(works)} 件，"
        f"在各平台共出现 {len(appearances)} 次，其中上传者自己声明是转载的 {stats['by_repost_status']['declared_repost']} 次，"
        f"筛选判为疑似转载、尚未找到原作的 {stats['by_repost_status']['suspected_repost']} 次。"
        f"初筛只看文字；{seen_frames} 件作品有人看过抽帧（athemeroy 审核或本项目复核），其余结论有待人工复核。"
    )

    def fill(text, tag, body):
        return re.sub(rf"(<!-- {tag} -->).*?(<!-- /{tag} -->)", lambda m: m.group(1) + body + m.group(2), text, flags=re.S)

    targets = [(ROOT / "README.md", 5, "data/")] + [(p, 12, "../data/") for p in sorted((ROOT / "launches").glob("*.md"))]
    for md, limit, prefix in targets:
        text = md.read_text("utf-8")
        body = render(limit, prefix)
        new = re.sub(r"<!-- works:start -->.*?<!-- works:end -->", lambda m: f"<!-- works:start -->\n{body}\n<!-- works:end -->", text, flags=re.S)
        new = fill(new, "snapshot:summary", "\n" + summary + "\n")
        new = fill(new, "snapshot:date", date)
        if new != text:
            md.write_text(new, "utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
