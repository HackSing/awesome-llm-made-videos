#!/usr/bin/env python3
"""Split pending candidates into JSONL chunks for triage (see scripts/triage/PROMPT.md).

  data/raw/triage/<platform>_<nn>.jsonl    input chunks (gitignored: they carry third-party text)
  data/triage/<platform>_<nn>.labels.jsonl expected output, one line per input line (committed)

X rows that only come from the athemeroy seed are skipped: that project already
reviewed or classified them. Only posts our own searches surfaced are triaged.

`x_seed` is the exception: the 160 cases athemeroy reviewed by hand (frames, the
creator's disclosure, source code where there is some). Their notes are passed in so
the labeller can carry the review over instead of guessing from a one-line topic.

Usage:
  python3 scripts/prepare_triage.py x bilibili x_seed [--chunk 150]
"""

import argparse
import glob
import json
import re

from _common import CANDIDATES, RAW, read_csv
from import_seed_athemeroy import fetch_csv, status_parts

TRIAGE = RAW / "triage"
TAG_RE = re.compile(r"<[^>]+>")


def bilibili_desc():
    """Latest description per BV id: detail response first, search result as fallback."""
    desc = {}
    for path in sorted(glob.glob(str(RAW / "bilibili" / "*" / "search" / "*.json"))):
        data = json.load(open(path, encoding="utf-8")).get("data") or {}
        for r in data.get("result") or []:
            if r.get("bvid"):
                desc[r["bvid"]] = TAG_RE.sub("", r.get("description", ""))
    for path in sorted(glob.glob(str(RAW / "bilibili" / "*" / "view" / "*.json"))):
        v = json.load(open(path, encoding="utf-8")).get("data") or {}
        if v.get("bvid"):
            desc[v["bvid"]] = v.get("desc", "")
    return desc


def seed_case_rows():
    """athemeroy's reviewed cases, joined to our candidate row (pinned commit, see import_seed_athemeroy.py)."""
    cands = {r["appearance_id"]: r for r in read_csv(CANDIDATES / "x.csv")}
    out = []
    for c in fetch_csv("data/cases.csv"):
        r = cands.get(f"x:{status_parts(c['source_url'])[1]}")
        if not r:
            continue
        out.append({
            "appearance_id": r["appearance_id"], "platform": "x", "url": r["url"], "uploader": r["uploader"],
            "title": r["title"], "published_utc": r["published_utc"], "duration_s": r["duration_s"],
            "athemeroy_label": c["label"], "athemeroy_primary_path": c["primary_path"],
            "creator_disclosure": c["creator_disclosure"], "observation": c["hypit_observation"],
            "review_note": c["review_note"],
        })
    return out


def rows_for(platform):
    if platform == "x_seed":
        return seed_case_rows()
    rows = [r for r in read_csv(CANDIDATES / f"{platform}.csv") if r.get("review_status") == "pending"]
    if platform == "x":
        rows = [r for r in rows if any(not q.startswith("seed:") for q in r["matched_queries"].split(";") if q)]
    desc = bilibili_desc() if platform == "bilibili" else {}
    out = []
    for r in rows:
        out.append({
            "appearance_id": r["appearance_id"],
            "platform": platform,
            "url": r["url"],
            "uploader": r["uploader"],
            "title": r["title"],
            "desc": " ".join(desc.get(r["platform_id"], "").split())[:400],
            "desc_links": r["desc_links"],
            "published_utc": r["published_utc"],
            "duration_s": r["duration_s"],
            "views": r["views"],
            "models_mentioned": r["models_mentioned"],
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("platforms", nargs="+")
    ap.add_argument("--chunk", type=int, default=150)
    args = ap.parse_args()
    TRIAGE.mkdir(parents=True, exist_ok=True)
    for platform in args.platforms:
        rows = rows_for(platform)
        for i in range(0, len(rows), args.chunk):
            path = TRIAGE / f"{platform}_{i // args.chunk + 1:02d}.jsonl"
            with path.open("w", encoding="utf-8") as f:
                for row in rows[i : i + args.chunk]:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(path.relative_to(RAW.parent.parent), len(rows[i : i + args.chunk]))


if __name__ == "__main__":
    main()
