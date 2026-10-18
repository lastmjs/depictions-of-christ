#!/usr/bin/env python3
"""Harvest the Web Gallery of Art (https://www.wga.hu/) catalogue for depictions of Christ.

Source: the free WGA image catalogue (https://www.wga.hu/database/download/data_txt.zip),
a tab-separated cp1252 text file (catalog.txt) with columns
AUTHOR, BORN-DIED, TITLE, DATE, TECHNIQUE, LOCATION, URL, FORM, TYPE, SCHOOL, TIMEFRAME.
All works are pre-1900 European art, i.e. public domain.

The wga.hu server frequently stalls after ~190 KB on a single response, so if the
live download fails we fall back to the identical Internet Archive copy.

Dedupe aid: Wikidata items that link to a WGA artwork page (described at URL /
reference URLs) are collected via the MediaWiki exturlusage API, filtered to
non-human items, and attached as "wd".  (Wikidata P1882 "Web Gallery of Art ID"
is an *artist* ID such as "b/backer", so it cannot identify artworks.)

Downloads are cached under .cache/wga/. Output: data/sources/wga.json
Usage: python3 scripts/sources/wga.py [--refresh]
"""
import csv
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subjects import classify, EXCLUDE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / ".cache" / "wga"
OUT = ROOT / "data" / "sources" / "wga.json"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
ZIP_URLS = [
    "https://www.wga.hu/database/download/data_txt.zip",
    "https://web.archive.org/web/2024id_/https://www.wga.hu/database/download/data_txt.zip",
]
WD_API = "https://www.wikidata.org/w/api.php"
WD_SPARQL = "https://query.wikidata.org/sparql"


def http_get(url, timeout=120, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- catalogue

def fetch_catalog(refresh=False):
    path = CACHE / "data_txt.zip"
    if not (path.exists() and zipfile.is_zipfile(path)) or refresh:
        CACHE.mkdir(parents=True, exist_ok=True)
        for url in ZIP_URLS:
            print(f"downloading {url} ...", file=sys.stderr)
            try:
                blob = http_get(url, timeout=180)
                if zipfile.is_zipfile(io.BytesIO(blob)):
                    path.write_bytes(blob)
                    break
                print("  not a valid zip", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"  failed: {e}", file=sys.stderr)
        else:
            sys.exit("could not download the WGA catalogue")
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
        text = z.read(name).decode("cp1252", errors="replace")
    return list(csv.DictReader(io.StringIO(text, newline=""), delimiter="\t"))


# ---------------------------------------------------------------- wikidata

def wga_key(url):
    """Normalise any WGA artwork URL to its path key, e.g. 'a/aachen/adonis'."""
    url = urllib.parse.unquote(url)
    m = re.search(r"html(?:_m)?/([a-z0-9_]+/[^?&#\s]+?)\.html?\b", url, re.I)
    return m.group(1).lower() if m else None


def fetch_wd_links(refresh=False):
    """Map WGA path key -> set of QIDs for all Wikidata items linking to wga.hu."""
    path = CACHE / "wikidata_wga_links.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    pairs = []
    for proto in ("https", "http"):
        for host in ("www.wga.hu", "wga.hu"):
            cont = {}
            while True:
                params = {"action": "query", "list": "exturlusage", "euquery": host,
                          "euprotocol": proto, "eunamespace": "0", "eulimit": "500",
                          "format": "json", **cont}
                for attempt in range(5):
                    try:
                        d = json.loads(http_get(WD_API + "?" + urllib.parse.urlencode(params)))
                        break
                    except Exception as e:  # noqa: BLE001
                        print(f"  retry ({e})", file=sys.stderr)
                        time.sleep(5 * (attempt + 1))
                else:
                    sys.exit("exturlusage failed")
                for u in d.get("query", {}).get("exturlusage", []):
                    pairs.append([u["title"], u["url"]])
                if "continue" not in d:
                    break
                cont = d["continue"]
            print(f"  {proto}://{host}: {len(pairs)} links so far", file=sys.stderr)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pairs))
    return pairs


def fetch_humans(qids, refresh=False):
    """Return the subset of qids that are humans (P31 Q5) - artist pages, not artworks."""
    path = CACHE / "wikidata_humans.json"
    known = {}
    if path.exists() and not refresh:
        known = json.loads(path.read_text())
    todo = sorted(q for q in qids if q not in known)
    for i in range(0, len(todo), 300):
        chunk = todo[i:i + 300]
        q = ("SELECT ?item WHERE { VALUES ?item { %s } ?item wdt:P31 wd:Q5 }"
             % " ".join("wd:" + x for x in chunk))
        body = urllib.parse.urlencode({"query": q, "format": "json"}).encode()
        for attempt in range(5):
            try:
                d = json.loads(http_get(WD_SPARQL, data=body, timeout=120))
                break
            except Exception as e:  # noqa: BLE001
                print(f"  sparql retry ({e})", file=sys.stderr)
                time.sleep(10 * (attempt + 1))
        else:
            sys.exit("sparql failed")
        humans = {b["item"]["value"].rsplit("/", 1)[1] for b in d["results"]["bindings"]}
        for x in chunk:
            known[x] = x in humans
        time.sleep(1)
    path.write_text(json.dumps(known))
    return {q for q, h in known.items() if h}


# ---------------------------------------------------------------- field helpers

PARTICLES = {"da", "di", "de", "del", "della", "dei", "degli", "delle", "dal", "dalla", "van", "von",
             "der", "den", "ter", "te", "het", "du", "des", "la", "le", "lo", "y", "of", "the", "zu",
             "and", "il", "in", "au", "aux", "el", "dos", "das", "do", "dell'", "dall'", "d'", "de'"}
NATIONALITY = re.compile(r"^(?:[A-Z][a-z]+(?:ian|an|ish|ese|ch|ic|ine|ss|ek|ic)|Other|Unknown)$")


def tc_word(w, first):
    """Title-case an uppercase word, keeping particles lowercase (except first)."""
    if not w.isupper() or len(w) < 2 or re.fullmatch(r"\(?[IVX]+\)?", w):
        return w
    low = w.lower()
    if not first and low in PARTICLES:
        return low
    m = re.match(r"^(d|dell|dall|nell|de|sant)'(.+)$", low)  # d'Ognabene, dell'Arca, Sant'Agnese
    if m:
        pre = m.group(1)
        if first or pre == "sant":
            pre = pre.capitalize()
        return pre + "'" + m.group(2).capitalize()
    return "-".join(p[:1].upper() + p[1:] for p in low.split("-"))


def tc(s, first=True):
    out = []
    for i, w in enumerate(s.split()):
        out.append(tc_word(w, first and i == 0))
    return " ".join(out)


SUFFIX = re.compile(r"^(?:the )?(elder|younger|old|young)\b.*|^(?:II|III|IV|Jr\.?|Sr\.?)$", re.I)


def artist_name(a):
    a = a.strip().replace("ANQUETIN. ", "ANQUETIN, ")
    if "," not in a:
        return re.sub(r"\b(dell|dall|d)['’] ", r"\1'", tc(a))
    sur, rest = [p.strip() for p in a.split(",", 1)]
    sur_t = tc(sur)
    parts = [p.strip() for p in rest.split(",")]
    if len(parts) == 1 and NATIONALITY.match(parts[0]) and sur.isupper() and (
            " " in sur or re.search(r"MASTER|UNKNOWN|PAINTER|SCULPTOR|ILLUMINATOR|GOLDSMITH", sur)):
        return f"{sur_t} ({parts[0]})"  # e.g. "Unknown Cabinetmaker (Hungarian)"
    given, extra = parts[0], parts[1:]
    m = re.search(r"\s+(the (?:Elder|Younger|Old|Young)|II|III)$", given)
    if m:
        extra.insert(0, m.group(1))
        given = given[:m.start()]
    # particles trailing the given name ("Charles van der") go before the surname
    name = f"{given} {sur_t}".strip()
    if extra:
        name += " " + " ".join(extra)
    name = re.sub(r"\b([Dd]ell|[Dd]all|[Dd])[\'’] ", lambda m: m.group(1).lower() + "'", name)
    name = re.sub(r" De' ", " de' ", name)
    return re.sub(r"\s+", " ", name)


CENT = re.compile(r"(early|mid-?|late|first half of the|second half of the|end of the|beginning of the)?\s*"
                  r"(\d{1,2})(?:st|nd|rd|th)\s+century", re.I)
CENT_OFF = {"early": 20, "mid": 50, "mid-": 50, "late": 80, "first half of the": 25,
            "second half of the": 75, "end of the": 90, "beginning of the": 10}


def sort_year(date, timeframe):
    d = (date or "").strip()
    m = CENT.search(d)
    if m and not re.search(r"\b\d{3,4}\b", d[:m.start()]):
        c = int(m.group(2))
        off = CENT_OFF.get((m.group(1) or "").lower().strip(), 50)
        return (c - 1) * 100 + off
    m = re.search(r"(?<!\d)(\d{3,4})(?!\d)", d)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d{4})-(\d{4})$", (timeframe or "").strip())
    if m:  # undated: midpoint of the WGA period bucket for the artist
        return (int(m.group(1)) + int(m.group(2))) // 2
    return None


def kind(form, technique):
    f = form.lower()
    t = technique.lower()
    if f == "graphics":
        if re.search(r"engraving|etching|woodcut|lithograph|mezzotint|aquatint|drypoint|print|"
                     r"metalcut|stipple|wood-engraving", t):
            return "print"
        if re.search(r"pen|ink|chalk|pencil|charcoal|drawing|watercolou?r|gouache|wash|"
                     r"stylus|silverpoint|metalpoint|crayon|pastel|sanguine|bistre", t):
            return "drawing"
        return "print"
    return {"painting": "painting", "sculpture": "sculpture", "illumination": "manuscript",
            "stained-glass": "stained glass", "glassware": "glass", "metalwork": "metalwork",
            "ceramics": "ceramic", "mosaic": "mosaic", "furniture": "furniture",
            "tapestry": "textile"}.get(f, "other")


DETAIL = re.compile(r"\s*\(+[^()]*\b(details?|before restoration|after restoration|"
                    r"during restoration|after cleaning|before cleaning)\b[^()]*\)+\s*\.?$", re.I)
DETAIL_PREFIX = re.compile(r"^details?\b", re.I)


def clean_title(t):
    t = re.sub(r"\s+", " ", t.strip())
    is_detail = bool(DETAIL.search(t) or DETAIL_PREFIX.match(t))
    base = DETAIL.sub("", t).strip()
    base = re.sub(r"\.$", "", base).strip() or t
    return base, is_detail


# Local false positives seen in the WGA catalogue.
WGA_FALSE = re.compile(
    r"portrait of .{0,60} as a child|lady with her infant child|entombment of atala|lamentation au roi|"
    r"feast of the (redeemer|madonna)|\bplan for\b|\bdesign for the (church|fa[cç]ade)|"
    r"view of the (cappella|chapel)|return from egypt\b.*allegory|allegory of the state of france|"
    r"g[âa]teau des rois|ancestors of christ|name of jesus|symbol of christ|crucifixion of (st\.? |saint )?"
    r"(peter|andrew|philip)\b(?!.*christ)|expulsion of (joachim|heliodorus)|money-?changers' guild|"
    r"trinity college|mary's presentation|tempietto del volto santo|scheme of the perspective|"
    r"disputation on the trinity|massacre of the innocents|soldiers of christ|mount of olives in jerusalem", re.I)


def subjects_for(r):
    title = r["TITLE"]
    u = classify(title)
    if not u:
        return []
    if WGA_FALSE.search(title):
        return []
    if r["TYPE"] == "mythological" and re.fullmatch(r"\s*madonna\s*", title, re.I):
        return []  # Munch's "Madonna"
    if u == ["Jesus Christ"] and EXCLUDE.search(title):
        # e.g. "Scenes from the Life of Christ: 23. Pentecost" - Christ not shown
        tail = title.split(":")[-1]
        if EXCLUDE.search(tail):
            return []
    return u


# ---------------------------------------------------------------- main

def main():
    refresh = "--refresh" in sys.argv
    rows = fetch_catalog(refresh)
    print(f"catalogue rows: {len(rows)}", file=sys.stderr)

    cands = []
    for r in rows:
        if r["FORM"].strip().lower() == "architecture":
            continue
        u = subjects_for(r)
        if not u:
            continue
        base, is_detail = clean_title(r["TITLE"])
        cands.append((r, u, base, is_detail))

    # Detail handling: group entries of the same work (artist + base title + location).
    groups = defaultdict(list)
    for c in cands:
        r, _, base, _ = c
        groups[(r["AUTHOR"], base.lower(), r["LOCATION"].strip().lower())].append(c)
    kept = []
    dropped_details = 0
    for g in groups.values():
        whole = [c for c in g if not c[3]]
        if whole:
            kept.extend(whole)
            dropped_details += len(g) - len(whole)
        else:
            kept.append(g[0])  # only details exist: keep the first one
            dropped_details += len(g) - 1

    # Wikidata links
    pairs = fetch_wd_links(refresh)
    by_key = defaultdict(set)
    for qid, url in pairs:
        k = wga_key(url)
        if k:
            by_key[k].add(qid)
    keys = {wga_key(c[0]["URL"]) for c in kept}
    wanted = set().union(*(by_key[k] for k in keys if k in by_key)) if keys else set()
    humans = fetch_humans(wanted, refresh)

    out = []
    for r, u, base, _ in kept:
        url = r["URL"].strip()
        key = wga_key(url)
        img = re.sub(r"/html/", "/art/", url, count=1)
        img = re.sub(r"\.html?$", ".jpg", img)
        date = r["DATE"].strip()
        rec = {
            "id": "wga:" + key,
            "t": base,
            "y": sort_year(date, r["TIMEFRAME"]),
            "d": "" if date in ("-", "") else date,
            "a": artist_name(r["AUTHOR"]),
            "c": r["SCHOOL"].strip(),
            "l": r["LOCATION"].strip(),
            "m": r["TECHNIQUE"].strip(),
            "s": r["TIMEFRAME"].strip(),
            "k": kind(r["FORM"].strip(), r["TECHNIQUE"]),
            "img": img,
            "big": img,
            "src": url,
            "lic": "Public domain",
            "u": u,
        }
        q = sorted(by_key.get(key, set()) - humans, key=lambda x: int(x[1:]))
        if q:
            rec["wd"] = q[0]
        out.append(rec)

    out.sort(key=lambda x: (x["y"] if x["y"] is not None else 9999, x["id"]))
    seen = set()
    out = [x for x in out if not (x["id"] in seen or seen.add(x["id"]))]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"candidates {len(cands)}, dropped details {dropped_details}, wrote {len(out)} -> {OUT}",
          file=sys.stderr)
    print("by kind:", dict(Counter(x["k"] for x in out).most_common()), file=sys.stderr)
    print("with wd:", sum(1 for x in out if "wd" in x), file=sys.stderr)
    print("details kept:", sum(1 for _, _, _, d in kept if d), file=sys.stderr)


if __name__ == "__main__":
    main()
