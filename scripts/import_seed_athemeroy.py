#!/usr/bin/env python3
"""Seed X candidates from athemeroy/awesome-opus-5-5-videos (CC BY 4.0).

That project searched X for Claude Opus 5.5 videos through 2026-09-25 and
published two tables we reuse, pinned to one commit and credited in notes:

  data/domain-style.csv  1,138 byte-distinct MP4 posts with a classifier label
                         (opus_made yes/likely/no), topic and style
  data/cases.csv         160 human-reviewed cases with a primary production path

Their labels are carried into `notes` as *their* judgement, not ours; every row
still starts at review_status=pending here.

Usage:
  python3 scripts/import_seed_athemeroy.py
"""

import csv
import io
import json
import urllib.request
from datetime import datetime, timezone

from _common import (
    CANDIDATES,
    RECEIPTS,
    iso,
    load_config,
    merge_candidates,
    parse_iso,
    utc_now,
)

REPO = "athemeroy/awesome-opus-5-5-videos"
COMMIT = "c8c496d3084b4847be192ebb26e6b9b6ee720ee7"  # main @ 2026-09-25T15:25:45Z
SOURCE = f"seed:{REPO}@{COMMIT[:7]}"
X_EPOCH_MS = 1288834974657


def fetch_csv(path):
    url = f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/{path}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))


def status_parts(url):
    # https://x.com/<handle>/status/<id>
    parts = url.rstrip("/").split("/")
    return parts[-3], parts[-1]


def snowflake_time(post_id):
    ms = (int(post_id) >> 22) + X_EPOCH_MS
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def main():
    cfg = load_config()
    window_start = parse_iso(cfg["window_start_utc"])
    now = iso(utc_now())

    cases = {}
    for c in fetch_csv("data/cases.csv"):
        _, pid = status_parts(c["source_url"])
        cases[pid] = c

    rows, before_window = {}, 0
    for d in fetch_csv("data/domain-style.csv"):
        handle, pid = status_parts(d["post_url"])
        published = snowflake_time(pid)
        if published < window_start:
            before_window += 1
            continue
        note = f"{SOURCE} opus_made={d['opus_made']}; domain={d['domain']}; style={d['style']}"
        if pid in cases:
            note += f"; reviewed_case primary_path={cases[pid]['primary_path']}"
        rows[f"x:{pid}"] = {
            "appearance_id": f"x:{pid}",
            "platform": "x",
            "url": f"https://x.com/{handle}/status/{pid}",
            "platform_id": pid,
            "uploader": d["author"],
            "uploader_id": d["author"].lstrip("@"),
            "title": d["topic_en"],
            "published_utc": iso(published),
            "duration_s": d["duration_s"],
            "views": "",
            "likes": "",
            "favorites": "",
            "platform_copyright": "",
            "models_mentioned": "",
            "desc_links": "",
            "matched_queries": SOURCE,
            "first_captured_utc": now,
            "last_captured_utc": now,
            "review_status": "pending",
            "work_id": "",
            "notes": note,
        }

    # Reviewed cases that are not in domain-style (e.g. no distinct MP4) still count
    only_cases = 0
    for pid, c in cases.items():
        key = f"x:{pid}"
        if key in rows:
            continue
        handle, _ = status_parts(c["source_url"])
        published = snowflake_time(pid)
        if published < window_start:
            before_window += 1
            continue
        only_cases += 1
        rows[key] = {
            "appearance_id": key, "platform": "x",
            "url": f"https://x.com/{handle}/status/{pid}", "platform_id": pid,
            "uploader": "@" + handle, "uploader_id": handle, "title": c["label"],
            "published_utc": iso(published), "duration_s": c["duration_s"],
            "views": "", "likes": "", "favorites": "", "platform_copyright": "",
            "models_mentioned": "", "desc_links": "", "matched_queries": SOURCE,
            "first_captured_utc": now, "last_captured_utc": now,
            "review_status": "pending", "work_id": "",
            "notes": f"{SOURCE} reviewed_case primary_path={c['primary_path']}",
        }

    merged = merge_candidates(CANDIDATES / "x.csv", list(rows.values()))
    summary = {
        "source": SOURCE,
        "imported": len(rows),
        "reviewed_cases_in_import": sum(1 for r in rows.values() if "reviewed_case" in r["notes"]),
        "cases_without_domain_row": only_cases,
        "dropped_before_window": before_window,
        "candidates_total_after_merge": len(merged),
    }
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    (RECEIPTS / "x-seed-athemeroy.summary.json").write_text(json.dumps(summary, indent=2) + "\n", "utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
