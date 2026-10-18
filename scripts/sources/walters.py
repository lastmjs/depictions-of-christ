#!/usr/bin/env python3
"""Harvest CC0 depictions of Christ from the Walters Art Museum.

The Walters API (v1) closed in 2023; the museum now publishes its full collection
data as static CSV files on GitHub (WaltersArtMuseum/api-thewalters-org), under
CC0 for data and images. This script downloads art.csv / media.csv /
creators.csv (cached under .cache/walters/), keeps records with an image whose
title (or, for generic object titles, the first sentence of the museum
description) passes subjects.classify plus the shared filters in aic.py, and
writes data/sources/walters.json.

Individual manuscript leaves and miniatures (e.g. "W.165.29V Entombment of
Christ") are catalogued as separate records in art.csv and are included.

Images are served from art.thewalters.org/images/art/<file>. That host sits
behind Cloudflare, which rejects non-browser clients (curl/urllib get a 403
challenge page) but serves browsers normally, so image URLs cannot be verified
from this script. Stdlib only.
"""
import csv
import html
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from subjects import classify  # noqa: E402,F401
from aic import NOT_CHRIST, decide  # noqa: E402  (shared filters)
from cma import GENERIC_TITLE, SPECIFIC_DESC  # noqa: E402

CACHE = ROOT / ".cache" / "walters"
OUT = ROOT / "data" / "sources" / "walters.json"
RAW = "https://raw.githubusercontent.com/WaltersArtMuseum/api-thewalters-org/main/"
IMG = "https://art.thewalters.org/images/art/"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"


def download(name):
    path = CACHE / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    delay = 2.0
    for attempt in range(6):
        req = urllib.request.Request(RAW + name, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read().decode("utf-8")
            CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(data, encoding="utf-8")
            return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                ra = e.headers.get("Retry-After")
                time.sleep(float(ra) if ra and ra.isdigit() else delay)
                delay = min(delay * 2, 120)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(delay)
            delay = min(delay * 2, 120)
    raise RuntimeError(f"giving up on {name}")


def read_csv(name):
    csv.field_size_limit(10 ** 9)
    return list(csv.DictReader(io.StringIO(download(name))))


def clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or "")).replace('""', '"')
    return re.sub(r"\s+", " ", s).strip()


# Extra false-positive filters for titles that name Christ without depicting him
# (shared with yale.py / princeton.py / getty.py).
EXTRA_NOT = re.compile(
    r"for christ'?s sake|\brelics? of christ|\b(sepulch(er|re)|tomb) of christ\b(?!.*\b(marys?|women|angel|christ (rising|risen)))|"
    r"\bgenealogy of christ\b(?!.*\b(nativity|virgin|crucif|resurrect))|\bancestors? of christ\b|"
    r"\bsymbols of (christ|the passion)|\binstruments of the passion\b|\barma christi\b(?!.*\b(man of sorrows|christ))|"
    r"^mary,? mother of jesus\b(?!.*\b(and|with) (jesus|christ|child))|"
    r"\b(virgin|saint|st\.?|john|mary|mourning|mourner|thief|angel)\w* from an? (crucifix|crucifixion|calvary|lamentation|entombment)\b|"
    r"\bchurch of (christ|jesus)\b|\bknights of christ\b|\border of christ\b|\bchrist'?s (hospital|college|church)\b|"
    r"\bimitation of christ\b(?!.*\b(with|showing|depicting)\b)|\b(guanyin|kannon|kuan ?yin|buddha|bodhisattva)\b",
    re.I)
CHILD_WORD = re.compile(r"\b(child|infant|son|jesus|christ|bambino|enfant|kind|madonna|hodegetria|eleousa|"
                        r"theotokos|nursing|suckling|lactans|holy family|nativity|magi|shepherds)\b", re.I)
MADONNA_OK = re.compile(r"\benthroned\b|\bof humility\b|\bin glory\b|\bwith (the )?(child|infant)\b", re.I)


def refine(title, u, extra=""):
    """Post-filter decide() output: drop titles that name Christ without depicting him,
    and 'Virgin ...' titles that only matched aic.SUPPLEMENT without any sign of the Child."""
    if not u or EXTRA_NOT.search(title):
        return []
    if "Madonna and Child" in u and not CHILD_WORD.search(f"{title} {extra}") and not MADONNA_OK.search(title):
        u = [x for x in u if x != "Madonna and Child"]
    return u


def description(r):
    d = clean(r.get("Description"))
    return re.sub(r"\s*For the latest information about this object.*$", "", d).strip()


def first_sentence(s):
    m = re.match(r"(.{20,300}?[.!?])(\s|$)", s)
    return m.group(1) if m else s[:300]


# Shared, text-based medium classification (also used by yale.py / princeton.py / getty.py).
KIND_RULES = [
    ("icon", r"\bicons?\b"),
    ("manuscript", r"manuscript|illuminat|miniatures? \(paintings\)|book of hours|antiphon|gradual|psalter|"
                   r"\bmissal|breviary|gospel book|folios? \(leaves\)|\bcuttings?\b|historiated initial"),
    ("print", r"\bprints?\b|engraving|etching|woodcut|lithograph|mezzotint|aquatint|drypoint|wood engraving|"
              r"\bwoodblock|stipple|photomechanical"),
    ("photograph", r"photograph|albumen|gelatin silver|daguerreotype|salted paper|collodion|platinum print"),
    ("drawing", r"\bdrawings?\b|watercolou?r|\bchalk\b|graphite|pen and|\bink and wash|gouache|pastel"),
    ("painting", r"\bpaintings?\b|oil on|tempera|on panel|on canvas|fresco|altarpiece|predella|triptych|diptych"),
    ("ivory", r"\bivory\b|\bbone\b"),
    ("enamel", r"\benamel"),
    ("glass", r"stained glass|\bglass\b"),
    ("textile", r"textile|embroider|tapestr|\bsilk\b|\bwool\b|\blinen\b|velvet|\blace\b|brocade|vestment|chasuble|orphrey"),
    ("sculpture", r"sculpture|statue|statuette|relief|figur(e|ine)|\bbust\b|carving|crucifix|corpus|marble|"
                  r"alabaster|terracotta|terra cotta|limestone|\bwood\b"),
    ("ceramic", r"ceramic|porcelain|earthenware|stoneware|faience|maiolica|majolica|pottery|\btiles?\b"),
    ("metalwork", r"metal|silver|\bgold\b|bronze|copper|brass|pewter|\biron\b|medal|coin|\bpax\b|reliquar|"
                  r"pendant|\brings?\b|jewel|niello|gilt"),
    ("book", r"\bbooks?\b|bound volume|binding|album"),
]
KIND_COMPILED = [(k, re.compile(rx, re.I)) for k, rx in KIND_RULES]


def kind_from(*texts):
    """First matching kind, trying each text in priority order."""
    for t in texts:
        if not t:
            continue
        for k, rx in KIND_COMPILED:
            if rx.search(t):
                return k
    return "other"


def kind(r):
    on, cls, med = (r.get("ObjectName") or ""), (r.get("Classification") or ""), (r.get("Medium") or "")
    if re.search(r"illuminated manuscripts|folios \(leaves\)|miniatures \(paintings\)", on, re.I):
        return "manuscript"
    if re.search(r"\bicons?\b", on, re.I):
        return "icon"
    if re.search(r"Ivory", cls):
        return "ivory"
    if re.search(r"Enamels", cls):
        return "enamel"
    return kind_from(on, cls, med)


def year(r):
    def num(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None
    s, e = num(r.get("DateBeginYear")), num(r.get("DateEndYear"))
    if s == 0 and e == 0:
        return None
    if s is None and e is None:
        return None
    if s is None or e is None:
        return s if s is not None else e
    return int(round((s + e) / 2)) if 0 <= e - s <= 300 else s


def pick_image(r, media):
    files = [m for m in media.get(r["ObjectID"], []) if (m.get("MediaType") or "Image") == "Image"]
    if not files:
        files = [{"Filename": f, "IsPrimary": "0", "Rank": str(i)} for i, f in
                 enumerate(x for x in (r.get("Images") or "").split("|") if x.strip())]
    if not files:
        return None

    def score(m):
        f = m.get("Filename") or ""
        bad = bool(re.search(r"^CON_|_XR|_IR\b|_IR_|_UV|_Xray|radiograph", f, re.I))  # conservation imaging
        bw = bool(re.search(r"_BW\b|_BW_|_NG_|_NF_", f))  # old black-and-white / negative scans
        front = bool(re.search(r"_Fnt|_Fp|_Fp_", f, re.I))
        prim = (m.get("IsPrimary") or "0") == "1"
        try:
            rank = int(m.get("Rank") or 99)
        except ValueError:
            rank = 99
        return (bad, bw, not prim, not front, rank)
    best = min(files, key=score)
    return IMG + best["Filename"].strip()


def main():
    art = read_csv("art.csv")
    media = {}
    for m in read_csv("media.csv"):
        media.setdefault(m["ObjectID"], []).append(m)
    creators = {c["id"]: c for c in read_csv("creators.csv")}
    print(f"{len(art)} objects, {sum(len(v) for v in media.values())} media rows", file=sys.stderr)

    out = []
    for r in art:
        title = clean(r.get("Title"))
        if not title or NOT_CHRIST.search(title):
            continue
        names = []
        for cid in (r.get("Creators") or "").split("|"):
            c = creators.get(cid.strip())
            if c and c.get("name") and not re.search(r"^(unknown|anonymous)\b", c["name"], re.I):
                names.append(c["name"].strip())
        a = "; ".join(dict.fromkeys(names))
        u = decide(title, "", a)
        desc = description(r)
        if not u and GENERIC_TITLE.search(title) and not re.search(r":|\bwith\b|\bdepicting\b|\bof\b", title, re.I):
            fs = first_sentence(desc)
            if SPECIFIC_DESC.search(fs):
                u = decide(title, fs, a)
        u = refine(title, u)
        if not u:
            continue
        im = pick_image(r, media)
        if not im:
            continue
        acc = r["AccessionNumber"].strip()
        out.append({
            "id": f"walters:{acc}",
            "t": title,
            "y": year(r),
            "d": clean(r.get("DateText")),
            "a": a,
            "c": clean(r.get("Culture")),
            "l": "Walters Art Museum",
            "m": clean(r.get("Medium")),
            "s": clean(r.get("Style")),
            "k": kind(r),
            "img": im,
            "big": im,
            "src": r.get("ResourceURL") or f"https://art.thewalters.org/object/{acc}/",
            "lic": "CC0",
            "u": u,
        })
    out.sort(key=lambda x: (x["y"] is None, x["y"] or 0, x["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {len(out)} records (of {len(art)} objects) to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
