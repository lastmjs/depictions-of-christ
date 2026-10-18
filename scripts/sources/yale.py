#!/usr/bin/env python3
"""Harvest public-domain depictions of Christ from Yale (Yale University Art Gallery
and Yale Center for British Art) via LUX, Yale's Linked Art collections platform.

1. Many title ("name") keyword searches against https://lux.collections.yale.edu/api/search/item,
   restricted to objects with a digital image in YUAG or YCBA sets. (Full-text
   searches return ~25k mostly irrelevant objects, and only titles are classified.)
2. Each hit's Linked Art record (/data/object/{uuid}, cached under .cache/yale/)
   supplies title, maker, date, medium, culture; the title is classified with
   subjects.classify via the shared filters in aic.py / walters.py.
3. For candidates, the museum's IIIF v3 manifest is fetched (cached) and the
   object is kept only if its "Copyright Statement" is "Public Domain"; the
   image comes from the manifest's IIIF Image service.
Writes data/sources/yale.json. Stdlib only.
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
from aic import QUERIES as AIC_QUERIES, NOT_CHRIST, decide  # noqa: E402
from walters import refine, kind_from, EXTRA_NOT  # noqa: E402

CACHE = ROOT / ".cache" / "yale"
OUT = ROOT / "data" / "sources" / "yale.json"
LUX = "https://lux.collections.yale.edu"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
MIN_INTERVAL = 0.25
PAGE = 100
UNITS = {"Yale University Art Gallery": "yuag", "Yale Center for British Art": "ycba"}

QUERIES = list(dict.fromkeys(AIC_QUERIES + [
    "Crucified", "Corpus", "Infant Christ", "Child Jesus", "Christ Child", "Christus", "Cristo",
    "Hodegetria", "Mandylion", "Stigmata", "Arma Christi", "Christ Blessing", "Life of Christ",
    "Life of the Virgin", "Small Passion", "Large Passion", "Engraved Passion", "Psalter", "Missal",
    "Pax", "Reliquary", "Processional Cross", "Christ Crowned", "Miracle", "Parable", "Magdalene",
    "Virgin Mary", "Pantokrator", "Icon", "Saint Joseph", "Simeon", "Nunc Dimittis", "Thorns",
    "Lord's Supper", "Holy Child", "Savior", "Messiah", "Crucifixion of Christ", "Jesus Christ",
]))

_last = [0.0]


def fetch(url, sub):
    key = hashlib.sha1(url.encode()).hexdigest()
    path = CACHE / sub / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    delay = 2.0
    for attempt in range(5):
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode())
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
            return data
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}")
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
    q = {"AND": [{"name": term}, {"hasDigitalImage": 1},
                 {"OR": [{"memberOf": {"name": n}} for n in UNITS]}]}
    page = 1
    while True:
        url = f"{LUX}/api/search/item?" + urllib.parse.urlencode(
            {"q": json.dumps(q, separators=(",", ":")), "page": page, "pageLength": PAGE})
        data = fetch(url, "search")
        items = data.get("orderedItems") or []
        for it in items:
            yield it["id"]
        if not items or not data.get("next"):
            break
        page += 1


def contents(rec, label_rx):
    out = []
    for r in rec.get("referred_to_by") or []:
        labs = " ".join(c.get("_label") or "" for c in r.get("classified_as") or [])
        if re.search(label_rx, labs, re.I) and r.get("content"):
            out.append(r["content"].strip())
    return out


def title_of(rec):
    for n in rec.get("identified_by") or []:
        if n.get("type") == "Name" and any((c.get("_label") or "") in ("Primary Title", "Primary Name")
                                           for c in n.get("classified_as") or []):
            return n.get("content", "").strip()
    return (rec.get("_label") or "").strip()


def other_titles(rec, primary):
    out = []
    for n in rec.get("identified_by") or []:
        c = (n.get("content") or "").replace(" ", " ").strip()
        if n.get("type") == "Name" and c and c != primary:
            out.append(c)
    return list(dict.fromkeys(out))


def unit_of(rec):
    for o in (rec.get("current_owner") or []) + (rec.get("member_of") or []):
        lab = o.get("_label") or ""
        for name, code in UNITS.items():
            if name in lab:
                return name, code
    return None, None


def clean_artist(s):
    s = re.sub(r"^[A-Z][\w ,]{0,30}:\s*", "", s)
    s = re.sub(r"\s*\((?=[^)]*\b(\d{3,4}|active|born|died|century|ca\.)\b)[^)]*\)\s*$", "", s)
    s = re.sub(r",\s*\d{3,4}\s*[–-]\s*\d{3,4}\s*$", "", s)
    return s.strip()


def production(rec):
    p = rec.get("produced_by") or {}
    parts = list(p.get("part") or []) or [p]
    names = []
    for q in parts:
        roles = " ".join(c.get("_label") or "" for c in q.get("classified_as") or [])
        if re.search(r"publisher|printer|after|copyist|author|editor|seller", roles, re.I) and len(parts) > 1:
            continue
        for c in q.get("carried_out_by") or []:
            n = clean_artist(c.get("_label") or "")
            if n and not re.search(r"^(unknown|unidentified|anonymous)\b", n, re.I):
                names.append(n)
    ts = p.get("timespan") or {}
    display = ""
    for n in ts.get("identified_by") or []:
        if n.get("content"):
            display = n["content"].strip()
            break
    if not display:
        display = "; ".join(d.get("_label") or "" for d in p.get("during") or [])
    return list(dict.fromkeys(names)), display, ts.get("begin_of_the_begin"), ts.get("end_of_the_end")


def yr(s):
    m = re.match(r"(-?)0*(\d+)-", s or "")
    if not m:
        return None
    return -int(m.group(2)) if m.group(1) else int(m.group(2))


def year(b, e, display):
    s, t = yr(b), yr(e)
    if s is None and t is None:
        m = re.search(r"\b(\d{3,4})\b", display or "")
        if m:
            return int(m.group(1))
        m = re.search(r"\b(\d{1,2})(st|nd|rd|th) century", display or "", re.I)
        return (int(m.group(1)) - 1) * 100 + 50 if m else None
    if s is None or t is None:
        return s if s is not None else t
    return int(round((s + t) / 2)) if 0 <= t - s <= 300 else s


def links(rec):
    page = manifest = None
    for s in rec.get("subject_of") or []:
        for c in s.get("digitally_carried_by") or []:
            for a in c.get("access_point") or []:
                u = a.get("id") or ""
                if "manifests.collections.yale.edu" in u and "/v2/" not in u:
                    manifest = manifest or u
                elif re.search(r"artgallery\.yale\.edu/collections/objects|collections\.britishart\.yale\.edu/catalog", u):
                    page = page or u
    return page, manifest


def wikidata(rec):
    for e in rec.get("equivalent") or []:
        m = re.match(r"https?://www\.wikidata\.org/entity/(Q\d+)$", e.get("id") or "")
        if m:
            return m.group(1)
    return None


def manifest_info(man):
    """(copyright statement, IIIF image service base) from a IIIF v3 manifest."""
    cr = ""
    for m in man.get("metadata") or []:
        lab = " ".join(sum((m.get("label") or {}).values(), []))
        if re.search(r"copyright|rights", lab, re.I):
            cr = " ".join(sum((m.get("value") or {}).values(), []))
            break
    base = None
    for canvas in man.get("items") or []:
        for page in canvas.get("items") or []:
            for anno in page.get("items") or []:
                body = anno.get("body") or {}
                for s in body.get("service") or []:
                    base = s.get("@id") or s.get("id")
                    if base:
                        return cr, base
                if body.get("id"):
                    m = re.match(r"(.*?/iiif/2/[^/]+)/", body["id"])
                    if m:
                        return cr, m.group(1)
    return cr, base


def kind(rec, title):
    types = " ".join(c.get("_label") or "" for c in rec.get("classified_as") or [])
    med = " ".join(contents(rec, r"^Medium$|Material Statement"))
    if re.search(r"\bicon\b", title, re.I) and re.search(r"tempera|panel|wood", med, re.I):
        return "icon"
    if re.search(r"Manuscript|Illuminat", types, re.I) or re.search(r"^(leaf|folio|cutting|miniature)\b", title, re.I) \
            and re.search(r"vellum|parchment", med, re.I):
        return "manuscript"
    for k, rx in [("photograph", r"Photograph"), ("print", r"\bPrints?\b"), ("drawing", r"Drawings?|Watercolou?r"),
                  ("painting", r"Paintings?")]:
        if re.search(rx, types):
            return k
    if re.search(r"Sculpture", types):
        return kind_from(med) if re.search(r"ivory|enamel", med, re.I) else "sculpture"
    return kind_from(types.replace("Visual Works", "").replace("Collection Item", ""), med)


def main():
    seen = {}
    for term in QUERIES:
        n = 0
        for oid in search(term):
            seen.setdefault(oid, term)
            n += 1
        print(f"{term!r}: {n} hits, {len(seen)} unique", file=sys.stderr)

    out, stats = [], {"unit": 0, "rights": 0, "noimg": 0, "nomanifest": 0}
    rights_seen = {}
    for i, oid in enumerate(sorted(seen)):
        if i % 500 == 0:
            print(f"  {i}/{len(seen)} objects checked, {len(out)} kept", file=sys.stderr)
        try:
            rec = fetch(oid + "?profile=results", "objects")
        except RuntimeError:  # a few compact "results" views 504 persistently; full record works
            try:
                rec = fetch(oid, "objects")
            except RuntimeError as e:
                print(f"  ! {e}", file=sys.stderr)
                continue
        if not rec:
            continue
        unit, code = unit_of(rec)
        if not unit:
            stats["unit"] += 1
            continue
        title = re.sub(r"\s+", " ", title_of(rec).replace(" ", " ")).strip()
        if not title or NOT_CHRIST.search(title):
            continue
        names, display, b, e = production(rec)
        a = "; ".join(names)
        u = refine(title, decide(title, "", a))
        if not u and not re.match(r"verso\b", title, re.I) and not EXTRA_NOT.search(title):
            # YCBA album sheets and coins carry the depicted subject only in an
            # alternate title ("Arthur Pond's Album" / "The Holy Family with Saint John").
            # "Verso: ..." records show the back of a sheet whose recto title is the alternate.
            for alt in other_titles(rec, title):
                alt = re.sub(r"^([IVXLC]+|\d+)\s*[-.]\s+", "", alt).strip()
                u = refine(alt, decide(alt, "", a))
                if u:
                    title = alt
                    break
        if not u:
            continue
        page, murl = links(rec)
        if not murl:
            stats["nomanifest"] += 1
            continue
        man = fetch(murl, "manifests")
        cr, base = manifest_info(man)
        rights_seen[cr] = rights_seen.get(cr, 0) + 1
        if not re.fullmatch(r"\s*public domain\.?\s*", cr or "", re.I):
            stats["rights"] += 1
            continue
        if not base:
            stats["noimg"] += 1
            continue
        num = murl.rstrip("/").rsplit("/", 1)[-1]
        rec_out = {
            "id": f"{code}:{num}",
            "t": title,
            "y": year(b, e, display),
            "d": display,
            "a": a,
            "c": "; ".join(contents(rec, r"^Culture$")),
            "l": unit,
            "m": "; ".join(contents(rec, r"^Medium$|Material Statement")),
            "s": "",
            "k": kind(rec, title),
            "img": f"{base}/full/!600,600/0/default.jpg",
            "big": f"{base}/full/!1600,1600/0/default.jpg",
            "src": page or (f"https://artgallery.yale.edu/collections/objects/{num}" if code == "yuag"
                            else f"https://collections.britishart.yale.edu/catalog/tms:{num}"),
            "lic": "Public domain",
            "u": u,
        }
        wd = wikidata(rec)
        if wd:
            rec_out["wd"] = wd
        out.append(rec_out)
    print("copyright statements among candidates:", json.dumps(rights_seen, indent=1), file=sys.stderr)
    print("skipped:", stats, file=sys.stderr)
    out.sort(key=lambda x: (x["y"] is None, x["y"] or 0, x["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {len(out)} records (of {len(seen)} candidates) to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
