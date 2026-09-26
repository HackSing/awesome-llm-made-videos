#!/usr/bin/env python3
"""Build a local page for a person to watch candidates and pick the ones worth featuring.

Reads works.csv / appearances.csv (after build.py) plus the review overlays, and writes
data/raw/shortlist.html next to the Bilibili frame sprites in data/raw/frames/ (both
gitignored). Ticking boxes and pressing the button copies the chosen work_ids.

Usage:
  python3 scripts/review/shortlist_page.py [--all]   # default: shortlist=yes or evidence A/A*
"""

import argparse
import glob
import html
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _common import DATA, RAW, read_csv  # noqa: E402
from build import MODEL_NAMES, PATH_NAMES, PLATFORM_NAMES as PLATFORM  # noqa: E402

GRID = 10  # Bilibili videoshot sprites are 10 x 10 thumbnails


def lit_cells(sprite):
    """Indices of sprite cells that hold a frame (short videos leave most cells black).

    Uses macOS `sips` to decode the JPEG into an uncompressed BMP; without it every
    cell is assumed to be filled.
    """
    if not shutil.which("sips"):
        return list(range(GRID * GRID))
    with tempfile.TemporaryDirectory() as tmp:
        bmp = Path(tmp) / "s.bmp"
        subprocess.run(["sips", "-s", "format", "bmp", "-Z", "600", str(sprite), "--out", str(bmp)],
                       capture_output=True, check=True)
        data = bmp.read_bytes()
    offset, = struct.unpack_from("<I", data, 10)
    width, height = struct.unpack_from("<ii", data, 18)
    bpp, = struct.unpack_from("<H", data, 28)
    step, stride = bpp // 8, (width * bpp // 8 + 3) & ~3
    cw, ch = width // GRID, abs(height) // GRID
    cells = []
    for i in range(GRID * GRID):
        cx, cy = (i % GRID) * cw, (i // GRID) * ch
        total = n = 0
        for y in range(cy + 2, cy + ch - 2, 3):
            row = (abs(height) - 1 - y) if height > 0 else y
            for x in range(cx + 2, cx + cw - 2, 3):
                px = offset + row * stride + x * step
                total += data[px] + data[px + 1] + data[px + 2]
                n += 3
        if n and total / n > 12:
            cells.append(i)
    return cells


def frame_grid(sprite, count=8):
    cells = lit_cells(sprite)
    if not cells:
        return '<div class="noimg">抽帧全黑</div>'
    if len(cells) <= count:
        pick = cells
    else:
        pick = [cells[round(k * (len(cells) - 1) / (count - 1))] for k in range(count)]
    tiles = "".join(
        f'<div class="f" style="background-image:url(frames/{sprite.name});'
        f'background-position:{(i % GRID) * 100 / (GRID - 1):.3f}% {(i // GRID) * 100 / (GRID - 1):.3f}%"></div>'
        for i in pick
    )
    return f'<div class="frames">{tiles}</div><p class="meta">抽帧 {len(cells)} 张，均匀取 {len(pick)} 张</p>'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every reviewed work, not only the flagged ones")
    args = ap.parse_args()

    review = {}
    for path in sorted(glob.glob(str(DATA / "review" / "*.jsonl"))):
        for line in open(path, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                review[r["appearance_id"].replace(":", "-", 1)] = r
    works = {w["work_id"]: w for w in read_csv(DATA / "works.csv")}
    apps = {}
    for a in read_csv(DATA / "appearances.csv"):
        apps.setdefault(a["work_id"], []).append(a)

    picked = [
        w for w in works.values()
        if args.all and w["work_id"] in review
        or review.get(w["work_id"], {}).get("shortlist") == "yes"
        or w["evidence_level"] in ("A", "A*")
    ]
    picked.sort(key=lambda w: (w["kind"] == "comparison", w["primary_path"], w["evidence_level"]))

    cards = []
    for w in picked:
        r = review.get(w["work_id"], {})
        links = " · ".join(
            f'<a href="{html.escape(a["url"])}" target="_blank">{PLATFORM.get(a["platform"], a["platform"])}'
            f'{"（转载）" if "repost" in a["repost_status"] else ""}</a>'
            for a in sorted(apps.get(w["work_id"], []), key=lambda a: a["published_utc"])
        )
        frames = sorted(glob.glob(str(RAW / "frames" / f"{w['work_id'].split('-', 1)[1]}-*.jpg")))
        img = frame_grid(Path(frames[0])) if frames else '<div class="noimg">无抽帧（X 视频请点链接看）</div>'
        rows = [
            ("做法", r.get("method_zh") or w.get("method", "")),
            ("画面", r.get("observed_zh") or w.get("observed", "")),
            ("代码", r.get("code_check_zh", "")),
            ("推荐理由", r.get("shortlist_zh", "")),
            ("对比结论", r.get("verdict_zh", "")),
        ]
        detail = "".join(f"<dt>{k}</dt><dd>{html.escape(v)}</dd>" for k, v in rows if v)
        cards.append(f"""<section class="card">
  <label class="pick"><input type="checkbox" value="{w['work_id']}"> 选这支</label>
  <h2>{html.escape(w['title'])}{'（对比）' if w['kind'] == 'comparison' else ''}</h2>
  <p class="meta">{html.escape(w['creator'])} · {html.escape('、'.join(MODEL_NAMES.get(m, m) for m in w['models'].split(';') if m))} · {PATH_NAMES.get(w['primary_path'], w['primary_path'])} · 证据 {w['evidence_level']}</p>
  <p class="links">{links}</p>
  {img}
  <dl>{detail}</dl>
</section>""")

    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>选片</title>
<style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;margin:0;background:#f5f3ee;color:#1a1a1a}}
header{{position:sticky;top:0;background:#1a1a1a;color:#fff;padding:12px 20px;display:flex;gap:16px;align-items:center;z-index:2}}
header button{{font-size:15px;padding:6px 14px}}
main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(460px,1fr));gap:16px;padding:16px}}
.card{{background:#fff;border-radius:10px;padding:14px 16px;border:1px solid #ddd}}
.card h2{{font-size:18px;margin:6px 0}} .meta,.links{{font-size:13px;color:#555;margin:4px 0}}
.frames{{display:grid;grid-template-columns:repeat(4,1fr);gap:3px;margin-top:6px}}
.f{{aspect-ratio:16/9;background-size:1000% 1000%;border-radius:3px;background-color:#000}} .noimg{{padding:24px;text-align:center;color:#999;background:#f0f0f0;border-radius:6px}}
dl{{font-size:13px;display:grid;grid-template-columns:64px 1fr;gap:4px 8px}} dt{{color:#888}} dd{{margin:0}}
.pick{{float:right;font-size:14px}}
</style></head><body>
<header><b>选片：{len(picked)} 件</b><span id="n">已选 0</span><button onclick="copyPicked()">复制已选编号</button></header>
<main>{''.join(cards)}</main>
<script>
const boxes=[...document.querySelectorAll('input[type=checkbox]')];
boxes.forEach(b=>b.addEventListener('change',()=>{{document.getElementById('n').textContent='已选 '+boxes.filter(x=>x.checked).length}}));
function copyPicked(){{const t=boxes.filter(x=>x.checked).map(x=>x.value).join('\\n');navigator.clipboard.writeText(t).then(()=>alert('已复制，粘贴回对话即可'),()=>prompt('复制下面的内容',t));}}
</script></body></html>"""
    out = RAW / "shortlist.html"
    out.write_text(page, "utf-8")
    print(f"{len(picked)} works -> {out}")


if __name__ == "__main__":
    main()
