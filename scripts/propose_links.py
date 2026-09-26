#!/usr/bin/env python3
"""Propose cross-platform links from triage `source_hint`s, for a human to review.

For every labelled work whose source hint names an X status, an X handle or a
YouTube id, look for the matching original among the candidates and among other
works. Nothing is written to data/work_links.csv: proposals go to
data/raw/link_proposals.csv and a person copies the ones that hold up, with a basis.

Usage:
  python3 scripts/propose_links.py
"""

import csv
import glob
import json
import re
from collections import defaultdict

from _common import CANDIDATES, DATA, RAW, read_csv

STATUS_RE = re.compile(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)/status/(\d+)")
HANDLE_RE = re.compile(r"@([A-Za-z0-9_]{3,15})\b")
YT_RE = re.compile(r"(?:youtu\.be/|youtube[^\s]*?[ :=/]|YouTube[^\s]*?\s)([A-Za-z0-9_-]{11})\b")


def main():
    labels = {}
    for path in sorted(glob.glob(str(DATA / "triage" / "*.labels.jsonl"))):
        for line in open(path, encoding="utf-8"):
            if line.strip():
                lab = json.loads(line)
                labels[lab["appearance_id"]] = lab
    cands = {r["appearance_id"]: r for p in CANDIDATES.glob("*.csv") for r in read_csv(p)}
    x_by_handle = defaultdict(list)
    for r in cands.values():
        if r["platform"] == "x":
            x_by_handle[r["uploader_id"].lower()].append(r)
    linked = {r["appearance_id"] for r in read_csv(DATA / "work_links.csv")}

    def is_work(lab):
        return lab["category"] in ("work", "comparison") and lab["llm_made"] in ("yes", "likely")

    proposals = []
    yt_groups = defaultdict(list)
    for aid, lab in labels.items():
        if not is_work(lab) or aid in linked:
            continue
        hint = lab.get("source_hint", "")
        for handle, sid in STATUS_RE.findall(hint):
            target = cands.get(f"x:{sid}")
            proposals.append((aid, f"x:{sid}", "x_status", "in candidates" if target else "not collected", hint))
        for handle in HANDLE_RE.findall(hint):
            posts = [r for r in x_by_handle.get(handle.lower(), []) if r["appearance_id"] != aid]
            works = [r for r in posts if (labels.get(r["appearance_id"]) and is_work(labels[r["appearance_id"]])) or "opus_made=yes" in r["notes"]]
            for r in works[:5]:
                proposals.append((aid, r["appearance_id"], "x_handle", f"{len(works)} work-like posts by @{handle}", r["title"][:80]))
        for yid in YT_RE.findall(hint):
            yt_groups[yid].append(aid)

    for yid, aids in yt_groups.items():
        for aid in aids:
            proposals.append((aid, f"youtube:{yid}", "youtube_id", f"{len(aids)} Bilibili works cite it", labels[aid]["source_hint"]))

    out = RAW / "link_proposals.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["appearance_id", "proposed_original", "match", "detail", "evidence_text"])
        w.writerows(proposals)
    print(f"{len(proposals)} proposals -> {out}")


if __name__ == "__main__":
    main()
