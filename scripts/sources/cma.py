#!/usr/bin/env python3
"""Harvest CC0 depictions of Christ from the Cleveland Museum of Art Open Access API.

Runs many keyword searches (cc0=1, has_image=1), keeps records whose title
(or, for generic object titles, the first sentence of the museum description)
passes subjects.classify plus the shared filters in aic.py, and writes
data/sources/cma.json. Raw API responses are cached under .cache/cma/. Stdlib only.
"""
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from subjects import classify  # noqa: E402,F401
from aic import QUERIES as AIC_QUERIES, NOT_CHRIST, decide  # noqa: E402  (shared filters)

CACHE = ROOT / ".cache" / "cma"
OUT = ROOT / "data" / "sources" / "cma.json"
API = "https://openaccess-api.clevelandart.org/api/artworks/"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
MIN_INTERVAL = 0.25  # <= 4 req/s
PAGE = 100

QUERIES = list(dict.fromkeys(AIC_QUERIES + [
    "Crucified", "Corpus", "Infant Christ", "Child Jesus", "Christ Child", "Vierge", "Enfant",
    "Lamb", "Hodegetria", "Eleousa", "Mandylion", "Vernicle", "Stigmata", "Rosary",
    "Arma Christi", "Instruments of the Passion", "Christ Blessing", "Christ Pantocrator",
    "Life of Christ", "Life of the Virgin", "Psalter", "Gradual", "Antiphonary", "Missal",
    "Initial", "Pyx", "Reliquary", "Pax", "Processional Cross", "Altar Cross", "Chasse",
    "Orphrey", "Chasuble", "Cope", "Plaque",
]))

GENERIC_TITLE = re.compile(
    r"^(the )?(plaque|panel|pendant|medallion|pax|pyx|chasse|reliquary|cross|processional cross|"
    r"altar cross|book cover|cover|leaf|single leaf|folio|initial|historiated initial|orphrey|"
    r"chasuble|cope|dalmatic|altar frontal|textile|fragment|roundel|tile|window|stained glass|"
    r"icon|triptych|diptych|altarpiece|retable|polyptych|ring|badge|medal|token|seal|bowl|plate|"
    r"dish|beaker|chalice|paten|ewer|box|casket|comb|mirror case|crozier|crosier|cameo|intaglio|"
    r"devotional|amulet|ampulla|lamp|vessel|jug|tankard|cup|bookcover|binding)\b", re.I)
SPECIFIC_DESC = re.compile(
    r"\b(christ|jesus|virgin and child|madonna and child|christ child|infant christ|crucifixion|"
    r"crucified|nativity|lamentation|entombment|resurrection|ascension|pietà|pieta)\b", re.I)

_last = [0.0]


def fetch(params):
    url = API + "?" + urllib.parse.urlencode(params)
    key = hashlib.sha1(url.encode()).hexdigest()
    path = CACHE / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    delay = 2.0
    for attempt in range(8):
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read().decode())
            CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
            return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                ra = e.headers.get("Retry-After")
                time.sleep(float(ra) if ra and ra.isdigit() else delay)
                delay = min(delay * 2, 120)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            time.sleep(delay)
            delay = min(delay * 2, 120)
    raise RuntimeError(f"giving up on {url}")


def search(term):
    skip = 0
    while True:
        data = fetch({"q": term, "cc0": 1, "has_image": 1, "limit": PAGE, "skip": skip})
        rows = data.get("data") or []
        yield from rows
        total = (data.get("info") or {}).get("total", 0)
        skip += PAGE
        if not rows or skip >= total:
            break


def clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", "", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def first_sentence(s):
    s = clean(s)
    m = re.match(r"(.{20,300}?[.!?])(\s|$)", s)
    return m.group(1) if m else s[:300]


def artist(r):
    for c in r.get("creators") or []:
        d = (c.get("description") or "").strip()
        if d:
            return re.sub(r"\s*\(.*$", "", d).strip()
    return ""


TYPE_MAP = [
    ("icon", r"\bicon"),
    ("manuscript", r"manuscript|miniature"),
    ("painting", r"^painting|^portrait miniature"),
    ("print", r"^print"),
    ("drawing", r"^drawing"),
    ("photograph", r"^photograph"),
    ("sculpture", r"^sculpture|^relief|^wood"),
    ("textile", r"^textile|^embroidery|^lace|^costume|^tapestry|^garment|^vestment|^velvet|^silk|^woven"),
    ("book", r"^book|^bookbinding|^book binding|^portfolio"),
    ("ivory", r"^ivory"),
    ("enamel", r"^enamel"),
    ("glass", r"^glass|^stained glass"),
    ("ceramic", r"^ceramic|^porcelain|^earthenware|^stoneware|^pottery|^tile"),
    ("metalwork", r"^metalwork|^jewelry|^silver|^coin|^medal|^arms|^gold"),
]


def kind(r):
    t = (r.get("type") or "").lower()
    tech = (r.get("technique") or "").lower()
    if t == "bound volume":
        return "manuscript" if re.search(r"vellum|parchment|tempera", tech) else "book"
    for k, rx in TYPE_MAP:
        if re.search(rx, t):
            if k == "sculpture" and re.search(r"\bivory\b", tech):
                return "ivory"
            if k in ("sculpture", "metalwork") and re.search(r"\benamel", tech):
                return "enamel"
            return k
    for k, rx in [("ivory", r"\bivory\b"), ("enamel", r"enamel"), ("glass", r"\bglass\b"),
                  ("ceramic", r"porcelain|earthenware|stoneware|faience|maiolica|terracotta"),
                  ("metalwork", r"silver|gold|bronze|copper|brass|pewter|iron|gilt"),
                  ("textile", r"silk|wool|linen|embroider|velvet")]:
        if re.search(rx, tech):
            return k
    return "other"


def culture(r):
    c = "; ".join(x for x in (r.get("culture") or []) if x)
    c = re.sub(r",[^,;]*\b(centur(y|ies)|period)\b.*$", "", c, flags=re.I)
    c = re.sub(r",\s*made for .*$", "", c, flags=re.I)
    return c.strip(" ,;")


def year(r):
    s, e = r.get("creation_date_earliest"), r.get("creation_date_latest")
    if s is None and e is None:
        return None
    if s is None or e is None:
        return int(s if s is not None else e)
    return int(round((s + e) / 2)) if 0 <= e - s <= 300 else int(s)


def img(r, *keys):
    im = r.get("images") or {}
    for k in keys:
        u = (im.get(k) or {}).get("url")
        if u:
            return u
    return None


def main():
    seen = {}
    for term in QUERIES:
        n = 0
        for r in search(term):
            seen.setdefault(r["id"], r)
            n += 1
        print(f"{term!r}: {n} hits, {len(seen)} unique", file=sys.stderr)

    out = []
    for r in seen.values():
        if (r.get("share_license_status") or "").upper() != "CC0":
            continue
        small = img(r, "web", "print", "full")
        if not small:
            continue
        title = clean(r.get("title"))
        # Text-only leaves/pages (often sharing alternate titles with their picture side).
        if re.search(r"(^|:\s*)text\b", title, re.I):
            continue
        a = artist(r)
        if NOT_CHRIST.search(title):
            continue
        u = decide(title, "", a)
        if not u:
            for alt in r.get("alternate_titles") or []:
                alt = clean(alt)
                if alt and not re.search(r"masihnama|mirror of holiness", alt, re.I):
                    u = decide(alt, "", a)
                    if u:
                        break
        # Fall back to the description only for generic object titles that do
        # not already name their own subject ("Pyx", "Processional Cross").
        if not u and GENERIC_TITLE.search(title) and not re.search(r":|\bwith\b|\bdepicting\b|\bof\b", title, re.I):
            fs = first_sentence(r.get("description"))
            if SPECIFIC_DESC.search(fs):
                u = decide(title, fs, a)
        if not u:
            continue
        wd = None
        for w in ((r.get("external_resources") or {}).get("wikidata") or []):
            m = re.search(r"(Q\d+)", w)
            if m:
                wd = m.group(1)
                break
        rec = {
            "id": f"cma:{r['id']}",
            "t": title,
            "y": year(r),
            "d": r.get("creation_date") or "",
            "a": a,
            "c": culture(r),
            "l": "Cleveland Museum of Art",
            "m": clean(r.get("technique")),
            "s": "",
            "k": kind(r),
            "img": small,
            "big": img(r, "print", "web") or small,
            "src": r.get("url") or f"https://clevelandart.org/art/{r.get('accession_number')}",
            "lic": "CC0",
            "u": u,
        }
        if wd:
            rec["wd"] = wd
        out.append(rec)
    out.sort(key=lambda x: (x["y"] is None, x["y"] or 0, x["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {len(out)} records (of {len(seen)} candidates) to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
