#!/usr/bin/env python3
"""Harvest the complete collection of artworks depicting Christ from Wikidata.

Every Wikidata item that depicts (P180) or has as main subject (P921) Jesus or
an episode/iconographic subject in which Christ appears, and that has an image
on Wikimedia Commons (and is therefore freely licensed), is gathered with its
date, creator, place, collection, material and movement. The result is written
to data/collection/ as one JSON file per century plus an index.

Usage: python3 scripts/harvest.py
"""
import hashlib
import os
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from datefix import parse_date  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "collection"
CACHE = ROOT / ".cache" / "harvest"
ENDPOINT = "https://query.wikidata.org/sparql"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ; static site build)"

# Subjects in which Christ himself is pictured.
SUBJECTS = {
    "Q302": "Jesus Christ",
    "Q642420": "Jesus Christ",
    "Q19076584": "Jesus Christ",
    "Q942467": "Christ Child",
    "Q9309699": "Madonna and Child",
    "Q1576172": "Madonna and Child",
    "Q3558015": "Madonna and Child",
    "Q1328300": "Madonna and Child",
    "Q132413": "Madonna and Child",
    "Q51628": "Nativity",
    "Q1029715": "Adoration of the Shepherds",
    "Q488841": "Adoration of the Magi",
    "Q13510036": "Circumcision",
    "Q152771": "Presentation in the Temple",
    "Q837143": "Flight into Egypt",
    "Q3427290": "Rest on the Flight into Egypt",
    "Q618057": "Holy Family",
    "Q473437": "Holy Family",
    "Q245230": "Christ among the Doctors",
    "Q20729926": "Mystic Marriage of St Catherine",
    "Q217048": "Baptism",
    "Q625140": "Wedding at Cana",
    "Q625241": "Miraculous Draught of Fishes",
    "Q51640": "Sermon on the Mount",
    "Q492663": "Woman Taken in Adultery",
    "Q957918": "Healing at Bethesda",
    "Q3937194": "Raising of Lazarus",
    "Q201201": "Transfiguration",
    "Q1267621": "Cleansing of the Temple",
    "Q950667": "Good Shepherd",
    "Q51633": "Last Supper",
    "Q2467856": "Agony in the Garden",
    "Q10289604": "Kiss of Judas",
    "Q115474394": "Christ before Caiaphas",
    "Q2033105": "Flagellation",
    "Q63977136": "Crowning with Thorns",
    "Q16934170": "Mocking of Christ",
    "Q534805": "Ecce Homo",
    "Q3399226": "Carrying the Cross",
    "Q608632": "Veil of Veronica",
    "Q51636": "Crucifixion",
    "Q13365963": "Descent from the Cross",
    "Q3155356": "Lamentation",
    "Q223689": "Pietà",
    "Q51637": "Entombment",
    "Q49599": "Man of Sorrows",
    "Q623306": "Passion",
    "Q1540642": "Harrowing of Hell",
    "Q51624": "Resurrection",
    "Q124362": "Noli me tangere",
    "Q16519437": "Supper at Emmaus",
    "Q718702": "Supper at Emmaus",
    "Q5686605": "Ascension",
    "Q752121": "Pantocrator",
    "Q972213": "Christ in Majesty",
    "Q51625": "Salvator Mundi",
    "Q116926": "Holy Face",
    "Q408284": "Sacred Heart",
    "Q380174": "Throne of Mercy",
    "Q1821239": "Last Judgment",
    "Q1093370": "Coronation of the Virgin",
}

# Item types that are not works of visual art (buildings, places, organisations...).
EXCLUDE_TYPES = re.compile(
    r"church|chapel|cathedral|basilica|monastery|abbey|building|parish|diocese|village|"
    r"street|square|school|hospital|organization|organisation|human\b|film|book|"
    r"literary|musical|song|album|opera|oratorio|cantata|hymn|feast|holiday|festival|"
    r"event|episode|biblical|tradition|devotion|sermon|edition|bridge|cemetery|museum|"
    r"order|congregation|confraternity|convent|shrine|pilgrimage|play\b|television|"
    r"video|poem|novel|written work|scholarly|article|manuscript collection|"
    r"administrative|municipality|town|city|mountain|hill|river|lake|island|"
    r"railway|station|ship|street|road|park|garden|well\b|fountain",
    re.I,
)


# Types marking an item as a concept or text rather than a physical work of art.
CONCEPT_TYPES = re.compile(
    r"^(artistic theme|artistic type|chapter of the bible|legend|new testament apocrypha|religious text|"
    r"gospel|gospel episode|bible story|theatrical genre|phrase|custom|miracle|schism|aspect of history|"
    r"parable of jesus|pericope|form of art|christian holy day|gregorian chant|lyric poetry|literary work|"
    r"exhibition|sequence|pseudepigraph|patron saint|personification|type of architectural structure|"
    r"courtroom|product|internet meme|religious art|biblical episode|iconographic theme|subject in christian art)$",
    re.I,
)


def sparql(query, tries=6):
    delay = 5
    for i in range(tries):
        try:
            req = urllib.request.Request(
                ENDPOINT,
                data=urllib.parse.urlencode({"query": query, "format": "json"}).encode(),
                headers={"User-Agent": UA, "Accept": "application/sparql-results+json",
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())["results"]["bindings"]
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            code = getattr(e, "code", None)
            print(f"    retry {i + 1} ({code or e})", file=sys.stderr)
            if code == 400:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 90)
    raise RuntimeError("SPARQL failed")


def qid(uri):
    return uri.rsplit("/", 1)[-1]


def gather_items():
    """Map item QID -> set of subject labels."""
    items = defaultdict(set)
    for q, label in SUBJECTS.items():
        cache = CACHE / f"subject-{q}.json"
        if cache.exists():
            rows = json.loads(cache.read_text())
        else:
            rows = sparql(f"""
              SELECT DISTINCT ?item WHERE {{
                {{ ?item wdt:P180 wd:{q} }} UNION {{ ?item wdt:P921 wd:{q} }}
                ?item wdt:P18 ?img .
              }}""")
            rows = [qid(r["item"]["value"]) for r in rows]
            cache.write_text(json.dumps(rows))
            time.sleep(1)
        for i in rows:
            items[i].add(label)
        print(f"  {label:32s} {q:10s} {len(rows):6d}  (total {len(items)})")
    return items


DETAIL = """
SELECT ?item
  (SAMPLE(?lab) AS ?label) (SAMPLE(?anyLab) AS ?label2) (SAMPLE(?img) AS ?image)
  (MIN(?inc) AS ?inception) (SAMPLE(?incPrec) AS ?precision)
  (GROUP_CONCAT(DISTINCT ?typeL; SEPARATOR="|") AS ?types)
  (GROUP_CONCAT(DISTINCT ?creatorL; SEPARATOR="|") AS ?creators)
  (MIN(?cBirth) AS ?birth) (MAX(?cDeath) AS ?death)
  (GROUP_CONCAT(DISTINCT ?collL; SEPARATOR="|") AS ?collections)
  (GROUP_CONCAT(DISTINCT ?locL; SEPARATOR="|") AS ?locations)
  (GROUP_CONCAT(DISTINCT ?madeL; SEPARATOR="|") AS ?made)
  (GROUP_CONCAT(DISTINCT ?countryL; SEPARATOR="|") AS ?countries)
  (GROUP_CONCAT(DISTINCT ?matL; SEPARATOR="|") AS ?materials)
  (GROUP_CONCAT(DISTINCT ?moveL; SEPARATOR="|") AS ?movements)
  (SAMPLE(?wp) AS ?wiki)
WHERE {
  VALUES ?item { %s }
  ?item wdt:P18 ?img .
  OPTIONAL { ?item rdfs:label ?lab FILTER(LANG(?lab) = "en") }
  OPTIONAL { ?item rdfs:label ?anyLab FILTER(LANG(?anyLab) IN ("fr","de","it","es","nl","pt","ru","pl","ca","sv","cs","hu","mul")) }
  OPTIONAL { ?item p:P571/psv:P571 ?incV . ?incV wikibase:timeValue ?inc ; wikibase:timePrecision ?incPrec . }
  OPTIONAL { ?item wdt:P31 ?type . ?type rdfs:label ?typeL FILTER(LANG(?typeL) = "en") }
  OPTIONAL { ?item wdt:P170 ?creator .
             OPTIONAL { ?creator rdfs:label ?creatorL FILTER(LANG(?creatorL) = "en") }
             OPTIONAL { ?creator wdt:P569 ?cBirth } OPTIONAL { ?creator wdt:P570 ?cDeath } }
  OPTIONAL { ?item wdt:P195 ?coll . ?coll rdfs:label ?collL FILTER(LANG(?collL) = "en") }
  OPTIONAL { ?item wdt:P276 ?loc . ?loc rdfs:label ?locL FILTER(LANG(?locL) = "en") }
  OPTIONAL { ?item wdt:P1071 ?made_ . ?made_ rdfs:label ?madeL FILTER(LANG(?madeL) = "en") }
  OPTIONAL { ?item wdt:P495 ?country . ?country rdfs:label ?countryL FILTER(LANG(?countryL) = "en") }
  OPTIONAL { ?item wdt:P186 ?mat . ?mat rdfs:label ?matL FILTER(LANG(?matL) = "en") }
  OPTIONAL { ?item wdt:P135 ?move . ?move rdfs:label ?moveL FILTER(LANG(?moveL) = "en") }
  OPTIONAL { ?wp schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> }
}
GROUP BY ?item
"""


def val(row, k):
    return row.get(k, {}).get("value", "")


def year_of(ts):
    m = re.match(r"^([+-]?\d+)-", ts or "")
    return int(m.group(1)) if m else None


def date_label(year, precision):
    if year is None:
        return ""
    p = int(precision or 9)
    if p >= 9:
        return str(year) if year > 0 else f"{-year} BC"
    if p == 8:
        return f"{(year // 10) * 10}s"
    if p == 7:
        c = (year - 1) // 100 + 1 if year > 0 else year // 100
        suf = "th" if 10 <= c % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(c % 10, "th")
        return f"{c}{suf} century"
    return f"c. {year}"


def split(s):
    return [x for x in (s or "").split("|") if x]


def fetch_details(ids):
    details = {}
    ids = sorted(ids, key=lambda q: int(q[1:]))
    batch = 200
    for n in range(0, len(ids), batch):
        chunk = ids[n:n + batch]
        cache = CACHE / f"detail-{chunk[0]}-{chunk[-1]}-{len(chunk)}.json"
        if cache.exists():
            rows = json.loads(cache.read_text())
        else:
            rows = sparql(DETAIL % " ".join("wd:" + q for q in chunk))
            cache.write_text(json.dumps(rows))
            time.sleep(1)
        for r in rows:
            details[qid(r["item"]["value"])] = r
        print(f"  details {min(n + batch, len(ids))}/{len(ids)}", end="\r")
    print()
    return details


# Wikidata properties holding the identifiers used by each museum source.
EXT = {"met": "P3634", "aic": "P4610", "cma": "P11110", "nga": "P4683", "rijks": "P13234"}


def external_ids():
    """Map "<source>:<museum id>" -> Wikidata QID for every museum source."""
    out = {}
    for src, prop in EXT.items():
        cache = CACHE / f"ext-{prop}.json"
        if cache.exists():
            rows = json.loads(cache.read_text())
        else:
            res = sparql(f"SELECT ?item ?id WHERE {{ ?item wdt:{prop} ?id ; wdt:P18 ?img . }}")
            rows = [[qid(r["item"]["value"]), r["id"]["value"]] for r in res]
            cache.write_text(json.dumps(rows))
            time.sleep(1)
        for q, i in rows:
            out[f"{src}:{i}"] = q
        print(f"  {src:6s} {prop:8s} {len(rows):7d} ids")
    return out


def norm_title(t):
    return re.sub(r"[^a-z]+", " ", (t or "").lower()).strip()


# Aggregated records must name Christ or a Christ-specific scene in their own title (any language).
STRONG = re.compile(
    r"christ|jesu|jésus|gesù|iisus|isus|хрис|иисус|spasitel|salvator|saviour|savior|redeemer|erlöser|heiland|"
    r"madonna|virgin and child|vierge à l.enfant|maria mit (dem )?kind|muttergottes mit|mare de déu amb|"
    r"heilige familie|holy family|sainte famille|sacra famiglia|sagrada familia|"
    r"crucif|kreuzig|gekreuzig|kruisig|ukrzyż|răstign|pietà|pieta|vesperbild|beweinung|lamentation|"
    r"abendmahl|last supper|cène|ultima cena|nativity|geburt christi|nașterea|рождество|adoration|anbetung|"
    r"ecce homo|schmerzensmann|man of sorrows|kreuztragung|carrying the cross|grablegung|entombment|"
    r"auferstehung|resurrection|himmelfahrt christi|ascension of christ|transfigur|verklärung|emmaus|"
    r"noli me tangere|pantocrator|pantokrator|deesis|mandylion|veronica|schweißtuch|sacred heart|herz jesu|"
    r"agnus dei|lamb of god|christkind|infant jesus|santo niño|jesulein|good shepherd|guter hirte|"
    r"last judg|jüngste(s)? gericht|weltgericht|flight into egypt|flucht nach ägypten|presentation in the temple|"
    r"darbringung|circumcision|beschneidung|baptism of christ|taufe christi|lazarus|cana",
    re.I)
# Later reproductions of other works (casts, electrotypes, fictile ivories) are not original depictions.
REPRODUCTION = re.compile(r"fictile ivory|plaster cast|\bcast of\b|electrotype|galvano|\breproduction\b|\breplica\b|\bfacsimile\b", re.I)
NOT_CHRIST = re.compile(r"v[oó]{1,2}r christus|before christ|\bv\.\s?chr\b|av\. ?j\.-?c|companhia de jesus|compañía de jesús|society of jesus|jesuit|jezuit|amyloid|pancrea|diabet", re.I)

# Europeana providers we also harvest directly (avoid double entries).
DIRECT_PROVIDERS = {"National Gallery of Denmark", "Nationalmuseum Sweden", "Finnish National Gallery",
                    "Statens Museum for Kunst", "Rijksmuseum", "Wikimedia Commons"}


ALIASES = {}  # Wikidata id -> curated record id whose hand-check applies to it


def merge_sources(works):
    """Add museum open-access records that Wikidata does not already cover."""
    files = sorted((ROOT / "data" / "sources").glob("*.json"))
    only = os.environ.get("SOURCES")
    if only:
        files = [f for f in files if f.stem in only.split(",")]
    if not files:
        return works
    print("Merging museum sources…")
    ext = external_ids()
    by_q = {w["q"]: w for w in works}
    def surname(a):
        return re.sub(r"[^a-z]", "", (a or "").split(",")[0].strip().split(" ")[-1].lower())

    keys = defaultdict(set)
    for w in works:
        keys[(norm_title(w["t"]), w["y"])].add(surname(w.get("a")))
    files_seen = {w["i"] for w in works if w.get("i")}
    # (title, artist surname) -> years, to catch the same work catalogued twice
    fuzzy = defaultdict(list)

    def fkey(t, a):
        last = re.sub(r"[^a-z]", "", (a or "").split(",")[0].strip().split(" ")[-1].lower())
        return (norm_title(t), last) if last and last not in ("unknown", "anonymous", "master") else None

    def remember(w):
        k = fkey(w.get("t"), w.get("a"))
        if k:
            fuzzy[k].append(w.get("y"))

    for w in works:
        remember(w)
    added = Counter()
    for f in files:
        recs = json.loads(f.read_text())
        for r in recs:
            q = r.get("wd") or ext.get(r["id"])
            if q and q in by_q and r["id"].startswith("cur:"):
                # A hand-researched record supersedes the raw Wikidata record of the same work:
                # take its fields and image, keep the Wikidata id for links, and alias its hand-check.
                w = by_q[q]
                for k in ("i", "h"):
                    w.pop(k, None)
                for k in ("t", "y", "d", "a", "c", "l", "m", "s", "k", "img", "big", "src", "u", "lic"):
                    if r.get(k) not in (None, "", []):
                        w[k] = r[k]
                ALIASES[q] = r["id"]
                added[f.stem + " (merged into Wikidata item)"] += 1
                continue
            if q and q in by_q:
                by_q[q].setdefault("src", r.get("src"))
                added[f.stem + " (matched)"] += 1
                continue
            curated = r["id"].startswith("cur:")  # hand-researched records: trust them, skip fuzzy dedupe
            if r["id"].startswith("commons:") and r.get("src"):
                fname = urllib.parse.unquote(r["src"].rsplit("File:", 1)[-1]).replace(" ", "_")
                if fname in files_seen:
                    added[f.stem + " (same file)"] += 1
                    continue
            same = keys.get((norm_title(r.get("t")), r.get("y")))
            if not curated and same and r.get("y") is not None and (surname(r.get("a")) in same or "" in same or not surname(r.get("a"))):
                added[f.stem + " (dup title)"] += 1
                continue
            if not r.get("img") or not r.get("u"):
                continue
            if f.stem in ("rijks", "europeana", "getty", "vam", "fng") and r.get("k") == "photograph":
                continue  # photographic reproductions of other artworks
            if REPRODUCTION.search(f"{r.get('t', '')} {r.get('m', '')}") and not r["id"].startswith("cur:"):
                added[f.stem + " (reproduction)"] += 1
                continue
            if f.stem == "europeana":
                if r.get("l") in DIRECT_PROVIDERS:
                    continue  # harvested directly from the museum itself
                text = f"{r.get('t', '')} {r.get('ot', '')}"
                if not STRONG.search(text) or NOT_CHRIST.search(text):
                    added["europeana (weak title)"] += 1
                    continue
            k = fkey(r.get("t"), r.get("a"))
            if not curated and k and any(y is None or r.get("y") is None or abs(y - r["y"]) <= 15 for y in fuzzy.get(k, [])):
                added[f.stem + " (dup artist+title)"] += 1
                continue
            w = {k: r.get(k) for k in ("t", "y", "d", "a", "c", "l", "m", "s", "k", "img", "big", "src", "u", "lic", "ot", "cr") if r.get(k) not in (None, "", [])}
            w.setdefault("t", "Untitled")
            w["y"] = r.get("y")
            w["d"] = r.get("d") or (str(r["y"]) if r.get("y") is not None else "")
            w["id"] = r["id"]
            if q:
                w["q"] = q
            works.append(w)
            keys[(norm_title(w["t"]), w["y"])].add(surname(w.get("a")))
            remember(w)
            added[f.stem] += 1
    print("  " + ", ".join(f"{k}: {v}" for k, v in sorted(added.items())))
    return works


def shard_of(key):
    """Stable 0-255 shard for a work key (djb2; mirrored in assets/js/collection.js)."""
    h = 5381
    for ch in key.encode("utf-8"):
        h = ((h * 33) ^ ch) & 0xFFFFFFFF
    return "%02x" % (h & 0xFF)


def apply_enrichment(works):
    """Merge hand-checked entries from data/enrich/batches/ into the works.

    Returns (works, details) where details maps shard -> {key: detail} holding the long text.
    """
    entries = {}
    for f in sorted((ROOT / "data" / "enrich" / "batches").glob("*.json")):
        for e in json.loads(f.read_text()):
            entries[e["key"]] = e  # later batches win
    if not entries:
        return works, {}
    kept, details, stats = [], defaultdict(dict), Counter()
    for w in works:
        k = w.get("q") or w.get("id")
        e = entries.get(k) or entries.get(w.get("id", "")) or entries.get(ALIASES.get(k, ""))
        if not e:
            kept.append(w)
            continue
        if e.get("status") == "rejected":
            stats["rejected"] += 1
            continue
        if e.get("status") == "dated":
            # triage only: a date was established, but the work has not been fully hand-checked yet
            for field, value in (e.get("set") or {}).items():
                if field in ("d", "y", "c", "l", "t", "a", "k"):
                    w[field] = value
            stats["dated"] += 1
            kept.append(w)
            continue
        for field, value in (e.get("set") or {}).items():
            if field in ("t", "a", "d", "y", "c", "l", "m", "s", "k", "img", "big"):
                w[field] = value
            elif field == "i":  # a better Commons file for the same work
                w["i"] = value.replace(" ", "_")
                w["h"] = hashlib.md5(w["i"].encode()).hexdigest()[:2]
        w["v"] = 1  # hand-checked
        detail = {x: e[x] for x in ("summary", "scripture", "facts", "links", "notes", "set") if e.get(x)}
        if "original_title" in (e.get("set") or {}):
            detail["original_title"] = e["set"]["original_title"]
        detail.pop("set", None)
        details[shard_of(k)][k] = detail
        stats[e.get("status", "verified")] += 1
        kept.append(w)
    print("  hand-checked: " + ", ".join(f"{k} {v}" for k, v in sorted(stats.items())))
    return kept, details


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    print("Gathering items by subject…")
    items = gather_items()
    print(f"{len(items)} distinct items. Fetching details…")
    details = fetch_details(list(items))

    works = []
    seen_images = set()
    dropped = Counter()
    for q, subjects in items.items():
        r = details.get(q)
        if not r:
            dropped["no details"] += 1
            continue
        types = split(val(r, "types"))
        if any(CONCEPT_TYPES.search(t) for t in types):
            dropped["concept"] += 1
            continue
        if types and all(EXCLUDE_TYPES.search(t) for t in types):
            dropped["non-artwork type"] += 1
            continue
        img = urllib.parse.unquote(val(r, "image").rsplit("/", 1)[-1]).replace(" ", "_")
        if not img or img.lower().endswith((".pdf", ".djvu", ".webm", ".ogv", ".stl")):
            dropped["no usable image"] += 1
            continue
        if img in seen_images:
            dropped["duplicate image"] += 1
            continue
        seen_images.add(img)
        title = val(r, "label") or val(r, "label2") or q
        year = year_of(val(r, "inception"))
        prec = int(val(r, "precision") or 9)
        if year is not None and year < 30:
            year = None  # impossible for a depiction of Christ: a data-entry error
        sort_year = year
        if year is not None and prec == 7:
            sort_year = ((year - 1) // 100) * 100 + 50
        elif year is not None and prec == 8:
            sort_year = year + 5
        elif year is not None and prec < 7:
            year = sort_year = None  # only a millennium is known: too vague to place
        est = False
        if year is None:
            death, birth = year_of(val(r, "death")), year_of(val(r, "birth"))
            if death:
                year, est = death - 10 if not birth else (birth + death) // 2 + 5, True
            elif birth:
                year, est = birth + 35, True
        creators = [c for c in split(val(r, "creators")) if not re.match(r"^(Q\d+|t\d+)$", c)]
        made = split(val(r, "made")) or split(val(r, "countries"))
        w = {
            "q": q,
            "t": title,
            "y": sort_year if not est else year,
            "d": (f"c. {year}" if est else date_label(year, prec)) if year is not None else "",
            "a": ", ".join(creators[:3]),
            "c": ", ".join(made[:2]),
            "l": ", ".join((split(val(r, "collections")) or split(val(r, "locations")))[:2]),
            "m": ", ".join(split(val(r, "materials"))[:3]),
            "s": ", ".join(split(val(r, "movements"))[:2]),
            "k": (types[0] if types else ""),
            "i": img,
            "h": hashlib.md5(img.encode()).hexdigest()[:2],
            "u": sorted(subjects),
        }
        wp = val(r, "wiki")
        if wp:
            w["w"] = urllib.parse.unquote(wp.rsplit("/", 1)[-1])
        if est:
            w["e"] = 1
        works.append(w)

    works = merge_sources(works)
    works, details = apply_enrichment(works)
    # Hand corrections where the catalogue data is known to be wrong.
    OVERRIDES = {
        "Q577890": {"y": 200},  # Alexamenos graffito: usually dated c. AD 200 (Wikidata says 100)
    }
    for w in works:
        w.update(OVERRIDES.get(w.get("q"), {}))
    fixed = 0
    for w in works:
        y = w.get("y")
        if y is not None and y < 30:
            w["y"] = y = None
        p = parse_date(w.get("d", ""))
        if p and (y is None or not (p[0] - 30 <= y <= p[1] + 30) or p[1] - p[0] > 300):
            w["y"] = (p[0] + p[1]) // 2
            fixed += 1
    print(f"  corrected {fixed} sort years from their display dates")

    # Place every work on the map (uses only cached geocoding data; see scripts/geocode.py).
    # If geocoding is unavailable, keep the last good placements rather than dropping the map.
    map_dir = ROOT / "data" / "map"
    map_dir.mkdir(exist_ok=True)
    try:
        from geocode import assign
        places, assignment = assign(works)
        keymap = {}
        for i, w in enumerate(works):
            w.pop("p", None)
            pi = assignment[i] if isinstance(assignment, list) else assignment.get(i)
            if pi is not None:
                w["p"] = pi
                keymap[w.get("q") or w.get("id")] = pi
        # Reviewed, per-work placements take precedence (data/map/overrides.json: key -> [name, lat, lon, precision]).
        ov_path = map_dir / "overrides.json"
        if ov_path.exists():
            index = {(p[0], round(p[1], 4), round(p[2], 4), p[3] if len(p) > 3 else ""): i for i, p in enumerate(places)}
            overrides = json.loads(ov_path.read_text())
            n_ov = 0
            for w in works:
                o = overrides.get(w.get("q") or w.get("id")) or overrides.get(w.get("id", ""))
                if not o:
                    continue
                if o == "none":
                    w.pop("p", None)
                    keymap.pop(w.get("q") or w.get("id"), None)
                    continue
                k = (o[0], round(o[1], 4), round(o[2], 4), o[3])
                if k not in index:
                    index[k] = len(places)
                    places.append([o[0], round(o[1], 4), round(o[2], 4), o[3]])
                w["p"] = index[k]
                keymap[w.get("q") or w.get("id")] = index[k]
                n_ov += 1
            print(f"  applied {n_ov} reviewed map placements")
        (map_dir / "places.json").write_text(json.dumps(places, ensure_ascii=False, separators=(",", ":")))
        (map_dir / "assign.json").write_text(json.dumps(keymap, separators=(",", ":")))
        print(f"  mapped {len(keymap)} works to {len(places)} places")
    except Exception as e:
        keymap = json.loads((map_dir / "assign.json").read_text()) if (map_dir / "assign.json").exists() else {}
        for w in works:
            pi = keymap.get(w.get("q") or w.get("id"))
            if pi is not None:
                w["p"] = pi
        print(f"  WARNING: geocoding failed ({e}); kept last good placements for {sum(1 for w in works if 'p' in w)} works")
    works.sort(key=lambda w: (w["y"] is None, w["y"] or 0, w["t"]))

    by_century = defaultdict(list)
    for w in works:
        c = "undated" if w["y"] is None else str((w["y"] - 1) // 100 + 1 if w["y"] > 0 else 0)
        by_century[c].append(w)

    for f in OUT.glob("c-*.json"):
        f.unlink()
    index = []
    for c, ws in sorted(by_century.items(), key=lambda kv: (kv[0] == "undated", int(kv[0]) if kv[0] != "undated" else 0)):
        (OUT / f"c-{c}.json").write_text(json.dumps(ws, ensure_ascii=False, separators=(",", ":")))
        index.append({"century": c, "count": len(ws)})

    det_dir = OUT / "detail"
    det_dir.mkdir(exist_ok=True)
    for f in det_dir.glob("*.json"):
        f.unlink()
    for shard, d in details.items():
        (det_dir / f"{shard}.json").write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")))

    subj_counts = Counter(s for w in works for s in w["u"])
    type_counts = Counter(w["k"] for w in works if w["k"])
    meta = {
        "generated": time.strftime("%Y-%m-%d"),
        "total": len(works),
        "checked": sum(1 for w in works if w.get("v")),
        "centuries": index,
        "subjects": subj_counts.most_common(),
        "types": type_counts.most_common(40),
    }
    (OUT / "index.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"{len(works)} works written. Dropped: {dict(dropped)}")
    for e in index:
        print(f"  century {e['century']:>8}: {e['count']}")


if __name__ == "__main__":
    main()
