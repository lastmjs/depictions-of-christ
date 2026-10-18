#!/usr/bin/env python3
"""Harvest public-domain depictions of Christ from the Princeton University Art Museum.

Uses the museum's keyless open data API (https://data.artmuseum.princeton.edu):
many keyword searches (/search?type=artobj), then the full record of every hit
(/objects/{id}, cached under .cache/princeton/). Keeps objects with an image that
carry no reproduction restriction (object and image `restrictions` empty, no
copyright line, web use allowed, and not a post-1929 work by an artist who died
after 1955), whose title (plus Christ-specific subject terms) passes
subjects.classify via the shared filters in aic.py / walters.py.
Writes data/sources/princeton.json. Stdlib only.
"""
import hashlib
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
from aic import QUERIES as AIC_QUERIES, NOT_CHRIST, TAG_OK, TAG_BAD, decide  # noqa: E402
from walters import refine, kind_from  # noqa: E402
from getty import PREFILTER  # noqa: E402

CACHE = ROOT / ".cache" / "princeton"
OUT = ROOT / "data" / "sources" / "princeton.json"
API = "https://data.artmuseum.princeton.edu"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
MIN_INTERVAL = 0.25
PAGE = 1000
MAX_WINDOW = 10000

QUERIES = list(dict.fromkeys(AIC_QUERIES + [
    "Crucified", "Corpus", "Infant Christ", "Child Jesus", "Christ Child", "Christus", "Cristo",
    "Lamb", "Hodegetria", "Mandylion", "Vernicle", "Stigmata", "Arma Christi", "Instruments of the Passion",
    "Christ Blessing", "Life of Christ", "Life of the Virgin", "Small Passion", "Large Passion",
    "Engraved Passion", "Apocalypse", "Biblia", "Psalter", "Missal", "Initial", "Pax", "Reliquary",
    "Processional Cross", "Pentecost", "Christ Crowned", "Holy Women", "Miracle", "Parable", "Disciples",
    "Peter", "Magdalene", "John the Baptist", "Virgin Mary", "Pantokrator", "Theotokos", "Byzantine",
    "Bible", "Gospel", "Evangelists", "Icon", "Altar", "Saint Anne", "Joseph", "Simeon", "Holy Ghost",
]))

# Records found by these searches are fully fetched even when the title alone is
# not suggestive (their subject terms may still name Christ).
CORE = {"Christ", "Jesus", "Crucifixion", "Madonna", "Virgin and Child", "Nativity", "Holy Family",
        "Pietà", "Passion of Christ", "Christ Child", "Christus", "Cristo", "Infant Christ", "Life of Christ"}

_last = [0.0]


def fetch(path, params=None):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    key = hashlib.sha1(url.encode()).hexdigest()
    sub = "objects" if path.startswith("/objects/") else "search"
    cpath = CACHE / sub / f"{key}.json"
    if cpath.exists():
        return json.loads(cpath.read_text())
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
            cpath.parent.mkdir(parents=True, exist_ok=True)
            cpath.write_text(json.dumps(data))
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                cpath.parent.mkdir(parents=True, exist_ok=True)
                cpath.write_text("{}")
                return {}
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
    start = 0
    while True:
        data = fetch("/search", {"q": term, "type": "artobj", "size": PAGE, "from": start})
        hits = (data.get("hits") or {}).get("hits") or []
        yield from hits
        total = (data.get("hits") or {}).get("total") or 0
        if isinstance(total, dict):
            total = total.get("value", 0)
        start += PAGE
        if not hits or start >= min(total, MAX_WINDOW):
            if total > MAX_WINDOW:
                print(f"  ! {term!r} has {total} hits; only {MAX_WINDOW} reachable", file=sys.stderr)
            break


def as_list(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return []
    return v if isinstance(v, list) else []


def subject_tags(o):
    tags = [t.get("term") or "" for t in as_list(o.get("terms")) if (t.get("termtype") or "") == "Subject"]
    tags += [d.get("depicted") or d.get("term") or "" if isinstance(d, dict) else str(d) for d in as_list(o.get("depicted"))]
    return sorted(set(t for t in tags if t and TAG_OK.search(t) and not TAG_BAD.search(t)))


def is_open(o):
    if o.get("restrictions"):
        return False
    if str(o.get("nowebuse") or "").lower() == "true":
        return False
    if "©" in (o.get("creditlinerepro") or "") or re.search(r"copyright|artists rights|vaga|ars\b", o.get("creditlinerepro") or "", re.I):
        return False
    media = [m for m in as_list(o.get("media")) if m.get("isprimary")] or as_list(o.get("media"))[:1]
    if media and media[0].get("restrictions"):
        return False
    # Belt and braces: modern works only if every maker died before 1956.
    y = o.get("datebegin") or o.get("datecomputed") or 0
    if y and y >= 1930:
        deaths = [m.get("dateend") or 0 for m in as_list(o.get("makers"))]
        if not deaths or any(d == 0 or d >= 1956 for d in deaths):
            return False
    return True


def image(o):
    media = [m for m in as_list(o.get("media")) if (m.get("mediatypeid") in (1, None)) and m.get("uri")]
    media.sort(key=lambda m: (not m.get("isprimary"), m.get("rank") or 99))
    if media:
        return media[0]["uri"]
    prim = as_list(o.get("primaryimage"))
    return prim[0] if prim else None


def artist(o):
    names = []
    for m in sorted(as_list(o.get("makers")), key=lambda m: m.get("displayorder") or 99):
        n = (m.get("displayname") or "").strip()
        if n and not re.search(r"^(unknown|unidentified|anonymous)\b", n, re.I):
            prefix = (m.get("prefix") or "").strip()
            names.append(f"{prefix} {n}".strip() if prefix and re.search(r"after|follower|workshop|circle|school|attributed|possibly|copy", prefix, re.I) else n)
    return "; ".join(dict.fromkeys(names))


def kind(o):
    cls = " ".join([o.get("classification") or ""] + [c.get("classification") or "" for c in as_list(o.get("classifications"))])
    med = o.get("medium") or ""
    title = o.get("displaytitle") or ""
    if re.search(r"\bicons?\b", cls, re.I) or re.search(r"\bicon\b", title, re.I) and re.search(r"tempera|panel", med, re.I):
        return "icon"
    if re.search(r"manuscript|illuminat", cls, re.I):
        return "manuscript"
    if re.search(r"photograph", cls, re.I):
        return "photograph"
    if re.search(r"^prints?\b|\bprints\b", cls, re.I):
        return "print"
    if re.search(r"drawing", cls, re.I):
        return "drawing"
    if re.search(r"painting", cls, re.I):
        return "painting"
    if re.search(r"sculpture", cls, re.I):
        return kind_from(med) if re.search(r"ivory|enamel", med, re.I) else "sculpture"
    return kind_from(cls, med)


def year(o):
    s, e = o.get("datebegin"), o.get("dateend")
    s = s if isinstance(s, int) and s != 0 else None
    e = e if isinstance(e, int) and e != 0 else None
    if s is None and e is None:
        c = o.get("datecomputed")
        return c if isinstance(c, int) and c != 0 else None
    if s is None or e is None:
        return s if s is not None else e
    return int(round((s + e) / 2)) if 0 <= e - s <= 300 else s


def culture(o):
    if o.get("displayculture"):
        return o["displayculture"]
    cs = [c.get("culture") for c in as_list(o.get("cultureterms")) if c.get("culture")]
    return "; ".join(dict.fromkeys(cs))


def main():
    seen, core = {}, set()
    for term in QUERIES:
        n = 0
        for h in search(term):
            oid = str(h.get("_id"))
            seen.setdefault(oid, h.get("_source") or {})
            if term in CORE:
                core.add(oid)
            n += 1
        print(f"{term!r}: {n} hits, {len(seen)} unique", file=sys.stderr)
    seen = {k: v for k, v in seen.items() if k in core or PREFILTER.search(v.get("displaytitle") or "")}
    print(f"{len(seen)} records to fetch", file=sys.stderr)

    out, stats = [], {"closed": 0, "noimg": 0}
    for i, oid in enumerate(sorted(seen, key=lambda x: int(x) if x.isdigit() else 0)):
        src = seen[oid]
        if i % 500 == 0:
            print(f"  {i}/{len(seen)} objects checked, {len(out)} kept", file=sys.stderr)
        o = fetch(f"/objects/{oid}")
        if not o:
            continue
        title = (o.get("displaytitle") or src.get("displaytitle") or "").strip()
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", title))
        if not title or NOT_CHRIST.search(title):
            continue
        a = artist(o)
        alts = [t.get("title") or "" for t in as_list(o.get("titles")) if (t.get("title") or "") != title]
        tags = subject_tags(o)
        u = refine(title, decide(title, " ".join(tags), a), " ".join(tags))
        if not u:
            for alt in alts:
                u = refine(alt, decide(alt, "", a))
                if u:
                    break
        if not u:
            continue
        if not is_open(o):
            stats["closed"] += 1
            continue
        im = image(o)
        if not im:
            stats["noimg"] += 1
            continue
        out.append({
            "id": f"princeton:{o.get('objectid') or oid}",
            "t": title,
            "y": year(o),
            "d": o.get("displaydate") or o.get("daterange") or "",
            "a": a,
            "c": culture(o),
            "l": "Princeton University Art Museum",
            "m": (o.get("medium") or "").strip(),
            "s": "",
            "k": kind(o),
            "img": f"{im}/full/!600,600/0/default.jpg",
            "big": f"{im}/full/!1600,1600/0/default.jpg",
            "src": f"https://artmuseum.princeton.edu/art/collections/objects/{o.get('objectid') or oid}",
            "lic": "Public domain",
            "u": u,
        })
    print("skipped:", stats, file=sys.stderr)
    out.sort(key=lambda x: (x["y"] is None, x["y"] or 0, x["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {len(out)} records (of {len(seen)} candidates) to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
