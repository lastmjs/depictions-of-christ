#!/usr/bin/env python3
"""Build the static data bundle for the site.

Reads data/eras.json and data/eras/*.json, resolves every Wikimedia Commons
file through the Commons API (URL, size, license, attribution), downloads the
image once into .cache/, writes optimized WebP renditions into assets/img/ and
emits assets/js/data.js for the frontend.

Usage: python3 scripts/build.py [--force]
"""
import hashlib
import html
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = ROOT / ".cache"
IMG_OUT = ROOT / "assets" / "img"
JS_OUT = ROOT / "assets" / "js" / "data.js"

API = "https://commons.wikimedia.org/w/api.php"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ; static site build)"
FETCH_WIDTH = 1920
LARGE = 1800
SMALL = 560
FORCE = "--force" in sys.argv


def http_get(url, binary=False, attempts=6):
    delay = 2
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                return body if binary else body.decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < attempts - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except urllib.error.URLError:
            if i < attempts - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def strip_html(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def commons_info(titles):
    """Return {requested_title: imageinfo} for a list of File: titles."""
    out = {}
    for i in range(0, len(titles), 40):
        batch = titles[i:i + 40]
        params = {
            "action": "query",
            "format": "json",
            "redirects": "1",
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": str(FETCH_WIDTH),
            "titles": "|".join(batch),
        }
        data = json.loads(http_get(API + "?" + urllib.parse.urlencode(params)))
        q = data.get("query", {})
        alias = {}
        for n in q.get("normalized", []):
            alias[n["to"]] = n["from"]
        for rd in q.get("redirects", []):
            alias[rd["to"]] = alias.get(rd["from"], rd["from"])
        for page in q.get("pages", {}).values():
            title = page.get("title")
            requested = alias.get(title, title)
            # follow the alias chain back to the originally requested title
            while requested in alias and alias[requested] != requested:
                requested = alias[requested]
            if "missing" in page or "imageinfo" not in page:
                out[requested] = None
            else:
                out[requested] = page["imageinfo"][0]
        time.sleep(0.5)
    return out


def palette(img):
    """Average colour and a deep, saturated accent derived from the artwork."""
    small = img.copy()
    small.thumbnail((64, 64))
    avg = small.resize((1, 1), Image.LANCZOS).getpixel((0, 0))
    q = small.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[:18]
    counts = sorted(q.getcolors(), reverse=True)
    best = None
    for count, idx in counts:
        r, g, b = pal[idx * 3: idx * 3 + 3]
        mx, mn = max(r, g, b), min(r, g, b)
        sat = (mx - mn) / (mx or 1)
        score = count * (0.35 + sat) * (0.4 + mx / 255)
        if best is None or score > best[0]:
            best = (score, (r, g, b))
    to_hex = lambda c: "#%02x%02x%02x" % tuple(int(v) for v in c[:3])
    return to_hex(avg), to_hex(best[1])


def process_image(entry, info):
    eid = entry["id"]
    large_path = IMG_OUT / f"{eid}.webp"
    small_path = IMG_OUT / f"{eid}-sm.webp"
    src = info.get("thumburl") or info["url"]
    key = hashlib.sha1(src.encode()).hexdigest()[:16]
    cached = CACHE / f"{eid}-{key}"
    if not cached.exists():
        print(f"  downloading {eid}")
        cached.write_bytes(http_get(src, binary=True))
        time.sleep(0.4)
    img = Image.open(io.BytesIO(cached.read_bytes()))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB",):
        bg = Image.new("RGB", img.size, (12, 10, 8))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
    if FORCE or not large_path.exists():
        big = img.copy()
        big.thumbnail((LARGE, LARGE), Image.LANCZOS)
        big.save(large_path, "WEBP", quality=82, method=6)
    if FORCE or not small_path.exists():
        sm = img.copy()
        sm.thumbnail((SMALL, SMALL), Image.LANCZOS)
        sm.save(small_path, "WEBP", quality=78, method=6)
    with Image.open(large_path) as big:
        w, h = big.size
    avg, accent = palette(img)
    meta = info.get("extmetadata", {})
    get = lambda k: strip_html(meta.get(k, {}).get("value", ""))
    return {
        "src": f"assets/img/{eid}.webp",
        "thumb": f"assets/img/{eid}-sm.webp",
        "w": w,
        "h": h,
        "color": avg,
        "accent": accent,
        "original": info["url"],
        "page": info.get("descriptionurl"),
        "license": get("LicenseShortName") or "See source",
        "licenseUrl": meta.get("LicenseUrl", {}).get("value"),
        "credit": get("Artist")[:160],
    }


def main():
    CACHE.mkdir(exist_ok=True)
    IMG_OUT.mkdir(parents=True, exist_ok=True)
    eras = json.loads((DATA / "eras.json").read_text())
    all_entries = []
    for era in eras:
        path = DATA / "eras" / era["file"]
        entries = json.loads(path.read_text()) if path.exists() else []
        extra = DATA / "eras" / "additions" / era["file"]
        if extra.exists():
            seen = {e["id"] for e in entries} | {e.get("commons") for e in entries if e.get("commons")}
            for e in json.loads(extra.read_text()):
                if e["id"] in seen or (e.get("commons") and e["commons"] in seen):
                    print(f"  skipping duplicate addition {e['id']}")
                    continue
                entries.append(e)
        for e in entries:
            e["era"] = era["key"]
        era["entries"] = entries
        all_entries.extend(entries)

    ids = [e["id"] for e in all_entries]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        sys.exit(f"duplicate ids: {dupes}")

    titles = sorted({e["commons"] for e in all_entries if e.get("commons")})
    print(f"{len(all_entries)} entries, {len(titles)} commons files")
    infos = commons_info(titles)

    problems = []
    for e in all_entries:
        t = e.get("commons")
        e["image"] = None
        if not t:
            continue
        info = infos.get(t)
        if not info:
            problems.append(f"MISSING  {e['id']}: {t}")
            continue
        try:
            e["image"] = process_image(e, info)
        except Exception as ex:  # keep building; report at end
            problems.append(f"FAILED   {e['id']}: {t} ({ex})")

    for era in eras:
        era["entries"].sort(key=lambda e: e["year"])
        era.pop("file", None)
    used = {f"{e['id']}.webp" for e in all_entries if e["image"]} | {
        f"{e['id']}-sm.webp" for e in all_entries if e["image"]}
    for f in IMG_OUT.glob("*.webp"):
        if f.name not in used:
            f.unlink()

    JS_OUT.write_text(
        "// Generated by scripts/build.py — do not edit by hand.\n"
        "window.DEPICTIONS = " + json.dumps({"eras": eras}, ensure_ascii=False, indent=1) + ";\n"
    )
    print(f"wrote {JS_OUT.relative_to(ROOT)}")
    if problems:
        print("\nProblems:")
        print("\n".join(problems))
        sys.exit(1)


if __name__ == "__main__":
    main()
