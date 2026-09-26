"""Shared helpers for the platform collectors. Standard library only."""

import csv
import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"  # gitignored: full API responses stay local
RECEIPTS = DATA / "receipts"  # public: what was queried, when, and what came back
CANDIDATES = DATA / "candidates"  # public: metadata of posts that mention a tracked model

# One row per platform post. A reviewed row later gets a work_id and moves to
# data/appearances.csv; see docs/methodology.md.
CANDIDATE_FIELDS = [
    "appearance_id",
    "platform",
    "url",
    "platform_id",
    "uploader",
    "uploader_id",
    "title",
    "published_utc",
    "duration_s",
    "views",
    "likes",
    "favorites",
    "platform_copyright",
    "models_mentioned",
    "desc_links",
    "matched_queries",
    "first_captured_utc",
    "last_captured_utc",
    "review_status",
    "work_id",
    "notes",
]
REVIEW_FIELDS = ("review_status", "work_id", "notes")

RECEIPT_FIELDS = [
    "captured_utc",
    "endpoint",
    "query",
    "order",
    "page",
    "status",
    "code",
    "returned",
    "num_results",
]

URL_RE = re.compile(r"https?://[^\s<>\"'，。、）)】」]+")


def utc_now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_from_ts(ts):
    return iso(datetime.fromtimestamp(int(ts), timezone.utc))


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def new_run_id():
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def load_config(path=None):
    path = Path(path) if path else ROOT / "scripts" / "queries.json"
    return json.loads(path.read_text("utf-8"))


def model_patterns(cfg):
    return {key: re.compile(m["match"], re.I) for key, m in cfg["models"].items()}


def models_mentioned(text, patterns, cfg=None):
    """Tracked model keys named in `text`. A generic entry ("gpt-6") is dropped
    when one of the specific variants listed in its `fallback_for` also matched."""
    found = [key for key, pat in patterns.items() if pat.search(text or "")]
    if cfg:
        found = [
            key for key in found
            if not set(cfg["models"][key].get("fallback_for", [])) & set(found)
        ]
    return found


def extract_links(text):
    seen = []
    for url in URL_RE.findall(text or ""):
        url = url.rstrip(".,;:!?")
        if url not in seen:
            seen.append(url)
    return seen


def slug(text, limit=60):
    s = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE).strip("_")
    return s[:limit] or "q"


class Pacer:
    """Randomised gaps between requests, plus a longer pause every `burst` calls."""

    def __init__(self, lo, hi, burst=25, burst_pause=(15, 30)):
        self.lo, self.hi = lo, hi
        self.burst, self.burst_pause = burst, burst_pause
        self.calls = 0

    def wait(self):
        if self.calls:
            gap = random.uniform(self.lo, self.hi)
            if self.burst and self.calls % self.burst == 0:
                gap += random.uniform(*self.burst_pause)
            time.sleep(gap)
        self.calls += 1


def read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def merge_candidates(path, new_rows):
    """Upsert by appearance_id. Keeps first capture time and any review work."""
    merged = {r["appearance_id"]: r for r in read_csv(path)}
    for row in new_rows:
        old = merged.get(row["appearance_id"])
        if old:
            row["first_captured_utc"] = old.get("first_captured_utc") or row["first_captured_utc"]
            queries = set(filter(None, old.get("matched_queries", "").split(";")))
            queries |= set(filter(None, row["matched_queries"].split(";")))
            row["matched_queries"] = ";".join(sorted(queries))
            for key in REVIEW_FIELDS:
                if old.get(key):
                    row[key] = old[key]
        merged[row["appearance_id"]] = row
    rows = sorted(merged.values(), key=lambda r: r.get("published_utc", ""), reverse=True)
    write_csv(path, rows, CANDIDATE_FIELDS)
    return rows
