#!/usr/bin/env python3
"""Split pending candidates into JSONL chunks for triage (see scripts/triage/PROMPT.md).

  data/raw/triage/<platform>_<nn>.jsonl    input chunks (gitignored: they carry third-party text)
  data/triage/<platform>_<nn>.labels.jsonl expected output, one line per input line (committed)

X rows that only come from the athemeroy seed are skipped: that project already
reviewed or classified them. Only posts our own searches surfaced are triaged.

Usage:
  python3 scripts/prepare_triage.py x bilibili [--chunk 150]
"""

import argparse
import glob
import json
import re

from _common import CANDIDATES, RAW, read_csv

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


def rows_for(platform):
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
