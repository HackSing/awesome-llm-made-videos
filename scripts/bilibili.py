#!/usr/bin/env python3
"""Collect Bilibili videos that mention a tracked model.

Uses the anonymous public search and video-detail endpoints, paced with random
gaps, and stops at the first risk-control response instead of retrying.

  raw API responses -> data/raw/bilibili/<run>/            (gitignored)
  query receipts    -> data/receipts/bilibili-<run>.csv
  candidates        -> data/candidates/bilibili.csv        (merged across runs)

Usage:
  python3 scripts/bilibili.py                  # full run from scripts/queries.json
  python3 scripts/bilibili.py --max-queries 1 --max-pages 1 --max-views 3   # smoke test
"""

import argparse
import gzip
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from _common import (
    CANDIDATES,
    RAW,
    RECEIPT_FIELDS,
    RECEIPTS,
    Pacer,
    extract_links,
    iso,
    iso_from_ts,
    load_config,
    merge_candidates,
    model_patterns,
    models_mentioned,
    new_run_id,
    parse_iso,
    slug,
    utc_now,
    write_csv,
)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
HOME = "https://www.bilibili.com/"
SEARCH = "https://api.bilibili.com/x/web-interface/search/type"
VIEW = "https://api.bilibili.com/x/web-interface/view"
# -412 request blocked, -352 risk-control check, -799 too frequent, -509 overload
RISK_CODES = {-412, -352, -799, -509}
TAG_RE = re.compile(r"<[^>]+>")


class RiskControl(Exception):
    pass


class Client:
    def __init__(self, pacer):
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.pacer = pacer

    def _get(self, url, referer):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Referer": referer,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip",
            },
        )
        try:
            with self.opener.open(req, timeout=20) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as e:
            if e.code == 412:
                raise RiskControl(-412, "HTTP 412") from e
            raise

    def bootstrap(self):
        """Visit the home page once so the API sees the usual anonymous cookies."""
        self._get(HOME, HOME)

    def api(self, url, params, referer):
        self.pacer.wait()
        body = self._get(url + "?" + urllib.parse.urlencode(params), referer)
        data = json.loads(body)
        if data.get("code") in RISK_CODES:
            raise RiskControl(data.get("code"), data.get("message"))
        return data


def duration_seconds(text):
    parts = [int(p) for p in str(text).split(":") if p.strip().isdigit()]
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def save_raw(run_dir, name, data):
    path = run_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")


def receipt(endpoint, query, order, page, status, code="", returned="", num_results=""):
    return {
        "captured_utc": iso(utc_now()),
        "endpoint": endpoint,
        "query": query,
        "order": order,
        "page": page,
        "status": status,
        "code": code,
        "returned": returned,
        "num_results": num_results,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--max-queries", type=int, help="only run the first N queries")
    ap.add_argument("--max-pages", type=int, help="cap pages per query/order")
    ap.add_argument("--max-views", type=int, help="cap detail lookups (default: all relevant)")
    ap.add_argument("--no-view", action="store_true", help="skip detail lookups")
    args = ap.parse_args()

    cfg = load_config(args.config)
    patterns = model_patterns(cfg)
    window_start = parse_iso(cfg["window_start_utc"])
    queries = cfg["bilibili"]["queries"][: args.max_queries]
    orders = cfg["bilibili"]["orders"]

    run = new_run_id()
    run_dir = RAW / "bilibili" / run
    receipts = []
    hits = {}  # bvid -> search result
    matched = {}  # bvid -> {"query|order"}
    stopped = None

    client = Client(Pacer(2.0, 5.0))
    client.bootstrap()

    # 1. Search
    for q in queries:
        if stopped:
            break
        for order, max_pages in orders.items():
            if stopped:
                break
            if args.max_pages:
                max_pages = min(max_pages, args.max_pages)
            for page in range(1, max_pages + 1):
                params = {"search_type": "video", "keyword": q, "order": order, "page": page}
                referer = "https://search.bilibili.com/all?keyword=" + urllib.parse.quote(q)
                try:
                    d = client.api(SEARCH, params, referer)
                except RiskControl as e:
                    receipts.append(receipt("search", q, order, page, "risk_control", e.args[0]))
                    stopped = f"search risk control {e.args}"
                    break
                except Exception as e:  # network or parse failure: record and move on
                    receipts.append(receipt("search", q, order, page, f"error:{type(e).__name__}"))
                    break
                save_raw(run_dir, f"search/{slug(q)}__{order}__p{page}.json", d)
                data = d.get("data") or {}
                results = data.get("result") or []
                receipts.append(
                    receipt("search", q, order, page, "ok" if d.get("code") == 0 else "api_error",
                            d.get("code"), len(results), data.get("numResults", ""))
                )
                for r in results:
                    bvid = r.get("bvid")
                    if not bvid:
                        continue
                    hits.setdefault(bvid, r)
                    matched.setdefault(bvid, set()).add(f"{q}|{order}")
                if not results or page >= (data.get("numPages") or 0):
                    break
                if order == "pubdate" and all(int(r.get("pubdate", 0)) < window_start.timestamp() for r in results):
                    break

    # 2. Keep posts that name a tracked model and were published inside the window
    now = iso(utc_now())
    rows = {}
    dropped_no_model = dropped_before_window = 0
    for bvid, r in hits.items():
        title = TAG_RE.sub("", r.get("title", ""))
        text = " ".join([title, r.get("description", ""), r.get("tag", "")])
        models = models_mentioned(text, patterns, cfg)
        if not models:
            dropped_no_model += 1
            continue
        if int(r.get("pubdate", 0)) < window_start.timestamp():
            dropped_before_window += 1
            continue
        rows[bvid] = {
            "appearance_id": f"bilibili:{bvid}",
            "platform": "bilibili",
            "url": f"https://www.bilibili.com/video/{bvid}",
            "platform_id": bvid,
            "uploader": r.get("author", ""),
            "uploader_id": r.get("mid", ""),
            "title": title,
            "published_utc": iso_from_ts(r.get("pubdate", 0)),
            "duration_s": duration_seconds(r.get("duration", "")),
            "views": r.get("play", ""),
            "likes": r.get("like", ""),
            "favorites": r.get("favorites", ""),
            "platform_copyright": "",
            "models_mentioned": ";".join(models),
            "desc_links": " ".join(extract_links(r.get("description", ""))),
            "matched_queries": ";".join(sorted(matched[bvid])),
            "first_captured_utc": now,
            "last_captured_utc": now,
            "review_status": "pending",
            "work_id": "",
            "notes": "",
        }

    # 3. Detail lookups: copyright flag, full description links, exact duration
    views_done = 0
    if not args.no_view and not stopped:
        order_by_views = sorted(rows, key=lambda b: int(rows[b]["views"] or 0), reverse=True)
        for bvid in order_by_views[: args.max_views]:
            try:
                d = client.api(VIEW, {"bvid": bvid}, f"https://www.bilibili.com/video/{bvid}")
            except RiskControl as e:
                receipts.append(receipt("view", bvid, "", "", "risk_control", e.args[0]))
                stopped = f"view risk control {e.args}"
                break
            except Exception as e:
                receipts.append(receipt("view", bvid, "", "", f"error:{type(e).__name__}"))
                continue
            save_raw(run_dir, f"view/{bvid}.json", d)
            receipts.append(receipt("view", bvid, "", "", "ok" if d.get("code") == 0 else "api_error", d.get("code"), 1))
            v = d.get("data") or {}
            if d.get("code") != 0 or not v:
                continue
            row = rows[bvid]
            row["platform_copyright"] = v.get("copyright", "")
            row["duration_s"] = v.get("duration", row["duration_s"])
            stat = v.get("stat") or {}
            row["views"] = stat.get("view", row["views"])
            row["likes"] = stat.get("like", row["likes"])
            row["favorites"] = stat.get("favorite", row["favorites"])
            owner = v.get("owner") or {}
            row["uploader"] = owner.get("name", row["uploader"])
            row["uploader_id"] = owner.get("mid", row["uploader_id"])
            row["desc_links"] = " ".join(extract_links(v.get("desc", "")))
            views_done += 1

    # 4. Write outputs
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    write_csv(RECEIPTS / f"bilibili-{run}.csv", receipts, RECEIPT_FIELDS)
    merged = merge_candidates(CANDIDATES / "bilibili.csv", list(rows.values()))
    summary = {
        "run": run,
        "launch": cfg["launch"],
        "queries": len(queries),
        "search_calls": sum(1 for r in receipts if r["endpoint"] == "search"),
        "search_ok": sum(1 for r in receipts if r["endpoint"] == "search" and r["status"] == "ok"),
        "unique_hits": len(hits),
        "dropped_no_model_mention": dropped_no_model,
        "dropped_before_window": dropped_before_window,
        "candidates_this_run": len(rows),
        "detail_lookups_ok": views_done,
        "candidates_total_after_merge": len(merged),
        "stopped": stopped,
    }
    (RECEIPTS / f"bilibili-{run}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 2 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
