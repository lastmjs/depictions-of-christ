#!/usr/bin/env python3
"""Harvest National Gallery of Art (Washington) open data for depictions of Christ.

Source: https://github.com/NationalGalleryOfArt/opendata (CC0 metadata).
Only objects with an open-access (CC0) primary image are kept.
Downloads are cached under .cache/nga/. Output: data/sources/nga.json

Usage: python3 scripts/sources/nga.py [--refresh]
"""
import csv
import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subjects import classify, EXCLUDE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / ".cache" / "nga"
OUT = ROOT / "data" / "sources" / "nga.json"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
RAW = "https://raw.githubusercontent.com/NationalGalleryOfArt/opendata/main/data/{}.csv"
FILES = ["objects", "published_images", "objects_terms"]

csv.field_size_limit(sys.maxsize)


def fetch(name, refresh=False):
    path = CACHE / f"{name}.csv"
    if path.exists() and path.stat().st_size > 0 and not refresh:
        return path
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"downloading {name}.csv ...", file=sys.stderr)
    req = urllib.request.Request(RAW.format(name), headers={"User-Agent": UA})
    tmp = path.with_suffix(".part")
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(path)
    time.sleep(1)
    return path


def rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


# NGA keyword/theme terms that are about Christ (used as extra classify text).
CHRIST_TERM = re.compile(r"\b(christ|jesus)\b", re.I)
NOT_CHRIST_TERM = re.compile(r"christ(opher|ina|ine|ian|mas|ie|offe|oph)", re.I)


def kind(r):
    cls = r["classification"]
    sub = (r["subclassification"] or "").lower()
    med = (r["medium"] or "").lower()
    if cls == "Painting":
        return "painting"
    if cls in ("Print", "Portfolio"):
        return "print"
    if cls in ("Drawing", "Index of American Design"):
        if re.search(r"\b(miniature|antiphon|gradual|choir ?book)", med) or (
                re.search(r"\b(vellum|parchment)\b", med) and re.search(r"\b(tempera|gold|gouache)\b", med)):
            return "manuscript"
        return "drawing"
    if cls == "Photograph":
        return "photograph"
    if cls == "Volume":
        if "drawing" in sub:
            return "drawing"
        if "photograph" in sub:
            return "photograph"
        return "print"
    if cls == "Sculpture":
        if "niello" in sub:
            return "metalwork"
        return "sculpture"
    if cls == "Decorative Art":
        if "textile" in sub or re.search(r"\b(silk|wool|linen|embroider|tapestr)", med):
            return "textile"
        if "enamel" in sub or re.search(r"\b(silver|gold|bronze|copper|brass|enamel|iron|gilt)\b", med):
            return "metalwork"
        if "statuette" in sub:
            return "sculpture"
        return "other"
    if "manuscript" in med or "illumin" in med:
        return "manuscript"
    return "other"


def main():
    refresh = "--refresh" in sys.argv
    paths = {n: fetch(n, refresh) for n in FILES}

    # Terms per object: school, style, place executed, Christ-related keywords/themes.
    school, style, place, cterms = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for t in rows(paths["objects_terms"]):
        oid, tt, term = t["objectid"], t["termtype"], (t["term"] or "").strip()
        if not term:
            continue
        if tt == "School":
            school[oid].append(term)
        elif tt == "Style":
            style[oid].append(term)
        elif tt == "Place Executed":
            place[oid].append(term)
        elif tt in ("Keyword", "Theme") and CHRIST_TERM.search(term) and not NOT_CHRIST_TERM.search(term):
            cterms[oid].append(term)

    # Candidate objects.
    cands = {}
    for r in rows(paths["objects"]):
        if r["isvirtual"] == "1":
            continue
        title = (r["title"] or "").strip()
        oid = r["objectid"]
        u = classify(title)
        if not u and cterms.get(oid) and not EXCLUDE.search(title):
            u = classify(title, " ".join(cterms[oid]))
        if u:
            cands[oid] = (r, u)
    print(f"candidates by title/keywords: {len(cands)}", file=sys.stderr)

    # Open-access images, prefer primary then lowest sequence.
    imgs = {}
    for im in rows(paths["published_images"]):
        oid = im["depictstmsobjectid"]
        if oid not in cands or im["openaccess"] != "1":
            continue
        key = (0 if im["viewtype"] == "primary" else 1, int(im["sequence"] or 0))
        if oid not in imgs or key < imgs[oid][0]:
            imgs[oid] = (key, im["iiifurl"])

    out = []
    for oid, (r, u) in cands.items():
        if oid not in imgs:
            continue
        iiif = imgs[oid][1].rstrip("/")
        try:
            y = int(r["beginyear"]) if r["beginyear"] else None
        except ValueError:
            y = None
        c = ", ".join(dict.fromkeys(school.get(oid) or place.get(oid) or []))
        rec = {
            "id": f"nga:{oid}",
            "t": (r["title"] or "").strip(),
            "y": y,
            "d": (r["displaydate"] or "").strip(),
            "a": (r["attribution"] or "").strip(),
            "c": c,
            "l": "National Gallery of Art, Washington",
            "m": re.sub(r"\s*//\s*", "; ", (r["medium"] or "").strip()),
            "s": ", ".join(dict.fromkeys(style.get(oid, []))),
            "k": kind(r),
            "img": f"{iiif}/full/!600,600/0/default.jpg",
            "big": f"{iiif}/full/!1600,1600/0/default.jpg",
            "src": f"https://www.nga.gov/collection/art-object-page.{oid}.html",
            "lic": "CC0",
            "u": u,
        }
        wd = (r.get("wikidataid") or "").strip()
        if re.fullmatch(r"Q\d+", wd):
            rec["wd"] = wd
        out.append(rec)

    out.sort(key=lambda x: (x["y"] is None, x["y"] or 0, x["t"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(out)} records to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
