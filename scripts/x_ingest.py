#!/usr/bin/env python3
"""Turn X search captures (from scripts/x_collect.js) into candidates + receipts.

Input: one or more JSON files, each shaped like
  {"query": "...", "mode": "top|live", "result": <collectX() return value>}

  raw captures -> data/raw/x/<run>/                (gitignored)
  receipts     -> data/receipts/x-<run>.csv
  candidates   -> data/candidates/x.csv            (merged across runs)

Usage:
  python3 scripts/x_ingest.py capture1.json capture2.json ...
"""

import json
import re
import shutil
import sys
from pathlib import Path

from _common import (
    CANDIDATES,
    RAW,
    RECEIPT_FIELDS,
    RECEIPTS,
    iso,
    load_config,
    merge_candidates,
    model_patterns,
    models_mentioned,
    new_run_id,
    parse_iso,
    utc_now,
    write_csv,
)

# aria-label of the action bar, e.g. "15 回复、24 次转帖、332 喜欢、351 书签、69277 次观看"
# or "15 replies, 24 reposts, 332 likes, 351 bookmarks, 69277 views"
STAT_RE = {
    "likes": re.compile(r"([\d,]+)\s*(?:喜欢|likes?)", re.I),
    "favorites": re.compile(r"([\d,]+)\s*(?:书签|bookmarks?)", re.I),
    "views": re.compile(r"([\d,]+)\s*(?:次观看|查看|views?)", re.I),
}


def stat(label, key):
    m = STAT_RE[key].search(label or "")
    return m.group(1).replace(",", "") if m else ""


def main(paths):
    cfg = load_config()
    patterns = model_patterns(cfg)
    window_start = parse_iso(cfg["window_start_utc"])
    run = new_run_id()
    raw_dir = RAW / "x" / run
    raw_dir.mkdir(parents=True, exist_ok=True)

    receipts, rows = [], {}
    dropped_no_video = dropped_no_model = dropped_before_window = 0
    for p in paths:
        cap = json.loads(Path(p).read_text("utf-8"))
        shutil.copy(p, raw_dir / Path(p).name)
        res = cap["result"]
        posts = res.get("posts", [])
        if res.get("hidden_during_capture") and not cap.get("degraded"):
            cap["degraded"] = "tab_hidden_during_capture"
        receipts.append({
            "captured_utc": res.get("captured_utc", "")[:19] + "Z",
            "endpoint": "search_page",
            "query": cap["query"],
            "order": cap["mode"],
            "page": cap.get("screens", ""),
            # "degraded" marks a capture that ran while the tab was hidden or
            # otherwise impaired; it still counts as seen, not as full coverage
            "status": "blocked" if res.get("blocked") else (f"degraded:{cap['degraded']}" if cap.get("degraded") else "ok"),
            "code": "",
            "returned": len(posts),
            "num_results": "",
        })
        for post in posts:
            if not post.get("has_video"):
                dropped_no_video += 1
                continue
            models = models_mentioned(post.get("text", ""), patterns, cfg)
            if not models:
                dropped_no_model += 1
                continue
            published = post["published_utc"][:19] + "Z"
            if parse_iso(published) < window_start:
                dropped_before_window += 1
                continue
            key = f"x:{post['id']}"
            prev = rows.get(key)
            queries = set(prev["matched_queries"].split(";")) if prev else set()
            queries.add(f"{cap['query']}|{cap['mode']}")
            text = " ".join(post.get("text", "").split())
            rows[key] = {
                "appearance_id": key,
                "platform": "x",
                "url": f"https://x.com/{post['handle']}/status/{post['id']}",
                "platform_id": post["id"],
                "uploader": "@" + post["handle"],
                "uploader_id": post["handle"],
                "title": text[:200],
                "published_utc": published,
                "duration_s": "",
                "views": stat(post.get("stats"), "views"),
                "likes": stat(post.get("stats"), "likes"),
                "favorites": stat(post.get("stats"), "favorites"),
                "platform_copyright": "",
                "models_mentioned": ";".join(models),
                "desc_links": " ".join(post.get("links", []) + post.get("related_status", [])),
                "matched_queries": ";".join(sorted(queries)),
                "first_captured_utc": iso(utc_now()),
                "last_captured_utc": iso(utc_now()),
                "review_status": "pending",
                "work_id": "",
                "notes": "",
            }

    write_csv(RECEIPTS / f"x-{run}.csv", receipts, RECEIPT_FIELDS)
    merged = merge_candidates(CANDIDATES / "x.csv", list(rows.values()))
    summary = {
        "run": run,
        "captures": len(paths),
        "blocked_captures": sum(1 for r in receipts if r["status"] == "blocked"),
        "posts_seen": sum(r["returned"] for r in receipts),
        "dropped_no_video": dropped_no_video,
        "dropped_no_model_mention": dropped_no_model,
        "dropped_before_window": dropped_before_window,
        "candidates_this_run": len(rows),
        "candidates_total_after_merge": len(merged),
    }
    (RECEIPTS / f"x-{run}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
