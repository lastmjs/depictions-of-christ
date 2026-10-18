#!/usr/bin/env python3
"""Geocode the Complete Collection: where is each work held today?

Builds two lookup files from free, keyless services (Wikidata SPARQL and the
Wikidata API; OpenStreetMap Nominatim only as a last resort, at most one
request per second). Every network response is cached under .cache/geocode/.

  data/map/wd-places.json  Wikidata work QID -> {name, lat, lon, pid[, made, approx]}
  data/map/gazetteer.json  location text      -> {name, lat, lon[, pid][, approx]}
  data/map/unresolved.txt  location strings that could not be placed, with counts

assign(works) maps the final list of works (as harvest.py builds them) onto a
deduplicated list of places using only those files (no network).

Usage: python3 scripts/geocode.py
"""
import hashlib
import json
import math
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "geocode"
OUT = ROOT / "data" / "map"
SPARQL = "https://query.wikidata.org/sparql"
WDAPI = "https://www.wikidata.org/w/api.php"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"

# --------------------------------------------------------------------------
# Known institutions. Key = location text used by the sources; value = QID.
# Coordinates and labels are fetched from Wikidata (cached).
INSTITUTIONS = {
    "The Metropolitan Museum of Art": ("Metropolitan Museum of Art", "Q160236"),
    "Metropolitan Museum of Art": ("Metropolitan Museum of Art", "Q160236"),
    "Metropolitan Museum of Art, New York": ("Metropolitan Museum of Art", "Q160236"),
    "The Cloisters": ("The Cloisters", "Q1138030"),
    "The Cloisters, New York": ("The Cloisters", "Q1138030"),
    "National Gallery of Art, Washington": ("National Gallery of Art", "Q214867"),
    "National Gallery of Art": ("National Gallery of Art", "Q214867"),
    "Art Institute of Chicago": ("Art Institute of Chicago", "Q239303"),
    "Art Institute of Chicago, Chicago": ("Art Institute of Chicago", "Q239303"),
    "Cleveland Museum of Art": ("Cleveland Museum of Art", "Q657415"),
    "Cleveland Museum of Art, Cleveland": ("Cleveland Museum of Art", "Q657415"),
    "Rijksmuseum, Amsterdam": ("Rijksmuseum", "Q190804"),
    "Rijksmuseum": ("Rijksmuseum", "Q190804"),
    "Victoria and Albert Museum": ("Victoria and Albert Museum", "Q213322"),
    "Victoria and Albert Museum, London": ("Victoria and Albert Museum", "Q213322"),
    "Statens Museum for Kunst": ("Statens Museum for Kunst", "Q671384"),
    "Statens Museum for Kunst, Copenhagen": ("Statens Museum for Kunst", "Q671384"),
    "National Gallery of Denmark": ("Statens Museum for Kunst", "Q671384"),
    "Nationalmuseum, Stockholm": ("Nationalmuseum", "Q842858"),
    "Nationalmuseum": ("Nationalmuseum", "Q842858"),
    "Nationalmuseum Sweden": ("Nationalmuseum", "Q842858"),
    # The Finnish National Gallery is an organisation; its works hang in the
    # Ateneum (Finnish/modern art) or the Sinebrychoff (old masters): see assign().
    "Finnish National Gallery, Helsinki": ("Ateneum", "Q754507"),
    "Finnish National Gallery": ("Ateneum", "Q754507"),
    "Ateneum": ("Ateneum", "Q754507"),
    "Sinebrychoff Art Museum": ("Sinebrychoff Art Museum", "Q1393952"),
    "Yale University Art Gallery": ("Yale University Art Gallery", "Q1568434"),
    "Yale Center for British Art": ("Yale Center for British Art", "Q6352575"),
    "Princeton University Art Museum": ("Princeton University Art Museum", "Q2603905"),
    "J. Paul Getty Museum": ("J. Paul Getty Museum", "Q731126"),
    "Walters Art Museum": ("Walters Art Museum", "Q210081"),
}

# Source id prefix -> institution key (in INSTITUTIONS) holding every record of that source.
SOURCE_INST = {
    "met": "The Metropolitan Museum of Art",
    "nga": "National Gallery of Art, Washington",
    "aic": "Art Institute of Chicago",
    "cma": "Cleveland Museum of Art",
    "rijks": "Rijksmuseum, Amsterdam",
    "vam": "Victoria and Albert Museum",
    "va": "Victoria and Albert Museum",
    "smk": "Statens Museum for Kunst",
    "nationalmuseum": "Nationalmuseum, Stockholm",
    "nm": "Nationalmuseum, Stockholm",
    "fng": "Finnish National Gallery, Helsinki",
    "yale": "Yale University Art Gallery",
    "yuag": "Yale University Art Gallery",
    "ycba": "Yale Center for British Art",
    "princeton": "Princeton University Art Museum",
    "getty": "J. Paul Getty Museum",
    "walters": "Walters Art Museum",
}

# City names whose first Wikidata search hit is the wrong place.
CITY_HINTS = {
    "washington": "Q61", "washington, d.c.": "Q61", "washington dc": "Q61",
    "st. petersburg": "Q656", "st petersburg": "Q656", "saint petersburg": "Q656",
    "new york": "Q60", "london": "Q84", "paris": "Q90", "rome": "Q220", "florence": "Q2044",
    "venice": "Q641", "naples": "Q2634", "milan": "Q490", "berlin": "Q64", "vienna": "Q1741",
    "munich": "Q1726", "madrid": "Q2807", "moscow": "Q649", "budapest": "Q1781",
}

SKIP = re.compile(
    r"^(private|unknown|lost|destroyed|whereabouts|location unknown|present location|"
    r"formerly|art market|on the art market|in the art market|anonymous|n/?a\b|none|various|"
    r"web gallery of art|wikimedia|stolen)|private collection|privately owned|privatbesitz|"
    r"collection particuli|colección particular|collezione privata|art market|kunsthandel|"
    r"christie|sotheby|bonhams|dorotheum|lempertz|koller|hampel|neumeister|"
    r"artcurial|drouot|tajan|bukowskis|van ham|nagel auktion|auction|dealer|"
    r"destroyed|whereabouts unknown|^lost\b|^\(?\s*$",
    re.I,
)

# Nationality / culture adjectives that are not places (used for place-made "c").
NATIONALITY = re.compile(
    r"^(other|italian|flemish|german|spanish|french|dutch|netherlandish|austrian|hungarian|greek|"
    r"catalan|russian|bohemian|english|swiss|portuguese|danish|scottish|swedish|irish|belgian|"
    r"finnish|byzantine|american|british|polish|czech|norwegian|european|coptic|ethiopian|"
    r"armenian|georgian|serbian|bulgarian|romanian|mexican|peruvian|bolivian|ecuadorian|"
    r"colombian|filipino|japanese|chinese|indian|mughal|islamic|roman|late roman|egyptian|"
    r"syrian|frankish|carolingian|ottonian|merovingian|lombard|venetian|florentine|sienese|"
    r"southern|northern|western|eastern|upper|lower|central|south|north|east|west)\b",
    re.I,
)

# Wikidata classes that are never a place to put a work on the map.
BAD_TYPES = {
    "Q5", "Q4167410", "Q13406463", "Q101352", "Q202444", "Q4167836", "Q11266439",
    "Q5398426", "Q7725634", "Q3305213", "Q838948", "Q17537576", "Q16521", "Q4830453",
}
# Regions: countries, states, provinces, historical regions (not specific places).
REGION_TYPES = {
    "Q6256", "Q3624078", "Q7275", "Q35657", "Q82794", "Q1620908", "Q10864048", "Q107390",
    "Q3336843", "Q34876", "Q5107", "Q1048835", "Q15642541", "Q3024240", "Q417175", "Q28575",
    "Q13220204", "Q11828004", "Q15649510", "Q1221156", "Q1798622", "Q43702", "Q48091",
}
CITY_TYPES = {
    "Q515", "Q1549591", "Q1637706", "Q5119", "Q3957", "Q532", "Q747074", "Q484170", "Q200250",
    "Q1093829", "Q2039348", "Q15284", "Q42744322", "Q262166", "Q22865", "Q2074737", "Q3184121",
    "Q486972", "Q15127012", "Q1115575", "Q3558970", "Q13218690", "Q7930989", "Q1307214",
    "Q902814", "Q1187811", "Q15661340", "Q2264924", "Q133442", "Q1840161", "Q954172",
}

_last = defaultdict(float)


def _throttle(kind, gap):
    wait = _last[kind] + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    _last[kind] = time.time()


def fetch(url, data=None, kind="wd", gap=0.3, accept="application/json", tries=8):
    """GET/POST with on-disk cache, a polite gap between requests and backoff on 429/5xx."""
    key = hashlib.sha1((url + "\n" + (data or "")).encode()).hexdigest()
    path = CACHE / kind / (key + ".json")
    if path.exists():
        return json.loads(path.read_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    delay = 5
    for i in range(tries):
        _throttle(kind, gap)
        try:
            req = urllib.request.Request(
                url, data=data.encode() if data else None,
                headers={"User-Agent": UA, "Accept": accept,
                         **({"Content-Type": "application/x-www-form-urlencoded"} if data else {})})
            with urllib.request.urlopen(req, timeout=120) as r:
                out = json.loads(r.read())
            path.write_text(json.dumps(out, ensure_ascii=False))
            return out
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
            code = getattr(e, "code", None)
            if code == 400 or code == 404:
                raise
            ra = None
            if code == 429 and getattr(e, "headers", None):
                try:
                    ra = int(e.headers.get("Retry-After") or 0)
                except ValueError:
                    ra = None
            wait = max(ra or 0, delay)
            print(f"    retry {i + 1} ({code or e}) in {wait}s", file=sys.stderr)
            time.sleep(wait)
            delay = min(delay * 2, 120)
    raise RuntimeError("request failed: " + url)


def sparql(query):
    data = urllib.parse.urlencode({"query": query, "format": "json"})
    return fetch(SPARQL, data, kind="sparql", gap=1.0, accept="application/sparql-results+json")["results"]["bindings"]


def wbsearch(text, limit=7):
    url = WDAPI + "?" + urllib.parse.urlencode({
        "action": "wbsearchentities", "format": "json", "language": "en", "uselang": "en",
        "type": "item", "limit": limit, "search": text[:250]})
    try:
        return fetch(url, kind="wbsearch", gap=0.25).get("search", [])
    except urllib.error.HTTPError:
        return []


def cirrus(text, limit=6):
    url = WDAPI + "?" + urllib.parse.urlencode({
        "action": "query", "list": "search", "format": "json", "srlimit": limit, "srnamespace": 0,
        "srsearch": text[:250] + " haswbstatement:P625"})
    try:
        return [h["title"] for h in fetch(url, kind="cirrus", gap=0.25).get("query", {}).get("search", [])]
    except urllib.error.HTTPError:
        return []


def nominatim(text):
    url = NOMINATIM + "?" + urllib.parse.urlencode({"q": text, "format": "jsonv2", "limit": 1,
                                                    "accept-language": "en"})
    try:
        return fetch(url, kind="nominatim", gap=1.1)
    except urllib.error.HTTPError:
        return []


def qid(uri):
    return uri.rsplit("/", 1)[-1]


def val(row, k):
    return row.get(k, {}).get("value", "")


def point(wkt):
    m = re.match(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", wkt or "")
    if not m:
        return None
    lon, lat = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return round(lat, 6), round(lon, 6)


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))


def fold(s):
    s = (s or "").replace("û", "ű").replace("õ", "ő")
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# --------------------------------------------------------------------------
# Inputs

def load_works():
    works = []
    for f in sorted((ROOT / "data" / "collection").glob("c-*.json")):
        works += json.loads(f.read_text())
    return works


def load_sources():
    recs = []
    for f in sorted((ROOT / "data" / "sources").glob("*.json")):
        recs += json.loads(f.read_text())
    return recs


def prefix(w):
    return (w.get("id") or "").split(":", 1)[0] if w.get("id") else ""


# --------------------------------------------------------------------------
# 1. Wikidata works -> place

WD_QUERY = """
SELECT ?item ?kind ?place ?coord ?settle WHERE {
  VALUES ?item { %s }
  {
    ?item p:P276 ?st . ?st ps:P276 ?place ; wikibase:rank ?r .
    FILTER(?r != wikibase:DeprecatedRank) FILTER NOT EXISTS { ?st pq:P582 [] }
    ?place wdt:P625 ?coord . BIND("loc" AS ?kind)
  } UNION {
    ?item p:P195 ?st . ?st ps:P195 ?place ; wikibase:rank ?r .
    FILTER(?r != wikibase:DeprecatedRank) FILTER NOT EXISTS { ?st pq:P582 [] }
    ?place wdt:P625 ?coord . BIND("coll" AS ?kind)
  } UNION {
    ?item p:P276|p:P195 ?st . ?st ps:P276|ps:P195 ?x ; wikibase:rank ?r .
    FILTER(?r != wikibase:DeprecatedRank) FILTER NOT EXISTS { ?st pq:P582 [] }
    FILTER NOT EXISTS { ?x wdt:P625 [] }
    ?x wdt:P131 ?place . ?place wdt:P625 ?coord . BIND("admin" AS ?kind)
  } UNION {
    ?item wdt:P1071 ?place . ?place wdt:P625 ?coord . BIND("made" AS ?kind)
  }
  BIND(EXISTS { ?place wdt:P1082 [] } AS ?settle)
}"""

LABEL_QUERY = """
SELECT ?x ?xLabel WHERE {
  VALUES ?x { %s }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,mul,en-gb,fr,de,it,es,nl,pt,pl,ru,ca,sv,da,cs,hu,el,uk,ro,hr,sl,sk,fi,la". }
}"""

INFO_QUERY = """
SELECT ?x ?coord ?en ?type ?pop ?cap WHERE {
  VALUES ?x { %s }
  OPTIONAL { ?x wdt:P625 ?coord }
  OPTIONAL { ?x rdfs:label ?en FILTER(LANG(?en) = "en") }
  OPTIONAL { ?x wdt:P31 ?type }
  BIND(EXISTS { ?x wdt:P1082 [] } AS ?pop)
  BIND(EXISTS { ?x wdt:P36 [] } AS ?cap)
}"""

RANK = {("loc", False): 0, ("coll", False): 1, ("loc", True): 2, ("coll", True): 3, ("admin", False): 4,
        ("admin", True): 4, ("made", False): 5, ("made", True): 5}


def labels_for(qids):
    out = {}
    qids = sorted(set(qids), key=lambda q: int(q[1:]))
    for n in range(0, len(qids), 300):
        chunk = qids[n:n + 300]
        for r in sparql(LABEL_QUERY % " ".join("wd:" + q for q in chunk)):
            lab = val(r, "xLabel")
            q = qid(val(r, "x"))
            if lab and lab != q:
                out[q] = lab
    return out


def info_for(qids):
    """QID -> {coord, label, types, pop, cap} (cached SPARQL)."""
    out = {}
    qids = sorted({q for q in qids if re.match(r"^Q\d+$", q)}, key=lambda q: int(q[1:]))
    for n in range(0, len(qids), 250):
        chunk = qids[n:n + 250]
        for r in sparql(INFO_QUERY % " ".join("wd:" + q for q in chunk)):
            q = qid(val(r, "x"))
            d = out.setdefault(q, {"coord": None, "label": "", "types": set(), "pop": False, "cap": False})
            if not d["coord"]:
                d["coord"] = point(val(r, "coord"))
            if val(r, "en"):
                d["label"] = val(r, "en")
            if val(r, "type"):
                d["types"].add(qid(val(r, "type")))
            d["pop"] = d["pop"] or val(r, "pop") == "true"
            d["cap"] = d["cap"] or val(r, "cap") == "true"
        print(f"  info {min(n + 250, len(qids))}/{len(qids)}", end="\r", file=sys.stderr)
    missing = [q for q, d in out.items() if not d["label"]]
    if missing:
        for q, lab in labels_for(missing).items():
            out[q]["label"] = lab
    return out


def build_wd_places(works):
    qs = sorted({w["q"] for w in works if w.get("q")}, key=lambda q: int(q[1:]))
    print(f"Wikidata works: {len(qs)}")
    best = {}
    for n in range(0, len(qs), 200):
        chunk = qs[n:n + 200]
        for r in sparql(WD_QUERY % " ".join("wd:" + q for q in chunk)):
            q, kind, place = qid(val(r, "item")), val(r, "kind"), qid(val(r, "place"))
            pt = point(val(r, "coord"))
            if not pt:
                continue
            rank = (RANK[(kind, val(r, "settle") == "true")], int(place[1:]))
            if q not in best or rank < best[q][0]:
                best[q] = (rank, kind, place, pt)
        print(f"  places {min(n + 200, len(qs))}/{len(qs)}  resolved {len(best)}", end="\r", file=sys.stderr)
    print(file=sys.stderr)
    labels = labels_for({b[2] for b in best.values()})
    out = {}
    kinds = Counter()
    for q in qs:
        if q not in best:
            continue
        rank, kind, place, (lat, lon) = best[q]
        e = {"name": labels.get(place, place), "lat": lat, "lon": lon, "pid": place}
        if kind == "made":
            e["made"] = True
        if kind == "admin":
            e["approx"] = "admin"
        out[q] = e
        kinds[kind] += 1
    print(f"  resolved {len(out)}/{len(qs)} ({100 * len(out) / max(1, len(qs)):.1f}%): {dict(kinds)}")
    return out


# --------------------------------------------------------------------------
# 2. Gazetteer for location text

def split_place(s):
    """'Museum, City' -> ('Museum', 'City'); handles 'X (City, Region)' too."""
    s = s.strip()
    m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", s)
    if m and not re.search(r"\d", m.group(2)):
        inner = m.group(2).split(",")[0].strip()
        return m.group(1).strip(" ;,"), inner
    if "," in s:
        a, b = s.rsplit(",", 1)
        return a.strip(), b.strip()
    return s, ""


def wanted_strings(works, sources, wd_places):
    """Location strings to resolve -> counts (from works; source-only strings count 0)."""
    l_counts, c_counts = Counter(), Counter()
    for w in works:
        p = prefix(w)
        if w.get("q") and w["q"] in wd_places:
            continue
        l = (w.get("l") or "").strip()
        if l:
            l_counts[l] += 1
        c = (w.get("c") or "").strip()
        if c and p not in SOURCE_INST and not NATIONALITY.match(c):
            c_counts[c] += 1
    for r in sources:
        l = (r.get("l") or "").strip()
        if l:
            l_counts.setdefault(l, 0)
        c = (r.get("c") or "").strip()
        if c and prefix(r) not in SOURCE_INST and not NATIONALITY.match(c):
            c_counts.setdefault(c, 0)
    # Europeana data providers (institution names) seen in the source cache.
    for f in (ROOT / ".cache" / "europeana").glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        for it in (d.get("items") or []) if isinstance(d, dict) else []:
            for prov in it.get("dataProvider") or []:
                l_counts.setdefault(prov, 0)
    return l_counts, c_counts


def city_choice(text, info, cands):
    """Best settlement among search candidates for a city name."""
    hint = CITY_HINTS.get(text.lower().strip())
    if hint and hint in info and info[hint]["coord"]:
        return hint
    best, score = None, -1e9
    for i, q in enumerate(cands):
        d = info.get(q)
        if not d or not d["coord"] or d["types"] & BAD_TYPES or d["types"] & REGION_TYPES or d["cap"]:
            continue
        s = -i
        if d["types"] & CITY_TYPES:
            s += 10
        if d["pop"]:
            s += 5
        if not (d["types"] & CITY_TYPES) and not d["pop"]:
            continue
        if s > score:
            best, score = q, s
    return best


def is_settlement(d):
    return bool(d["types"] & CITY_TYPES) or d["pop"]


def label_matches(query, hit):
    """Does a search hit's matched text equal the query (accent/case folded)?"""
    t = fold(hit.get("match", {}).get("text", ""))
    return t == fold(query) or fold(hit.get("label", "")) == fold(query)


def build_gazetteer(l_counts, c_counts):
    gaz = {}
    # 2a. hardcoded institutions
    inst_info = info_for({q for _, q in INSTITUTIONS.values()})
    for key, (name, q) in INSTITUTIONS.items():
        d = inst_info.get(q)
        if d and d["coord"]:
            gaz[key] = {"name": name, "lat": d["coord"][0], "lon": d["coord"][1], "pid": q}
        else:
            print(f"  ! no coordinates for institution {key} ({q})")

    todo = {}
    for s in list(l_counts) + list(c_counts):
        if s in gaz or s in todo:
            continue
        if SKIP.search(s) or len(s) < 3 or not re.search(r"[A-Za-zÀ-ÿ]", s):
            continue
        todo[s] = "c" if s in c_counts and s not in l_counts else "l"
    print(f"Gazetteer: {len(gaz)} institutions; {len(todo)} strings to resolve")

    # 2b. search (cached): full string, the part before the last comma, the city
    searches = {}
    for i, s in enumerate(todo):
        name, city = split_place(s)
        res = {"full": wbsearch(s)}
        if name and name != s:
            res["name"] = wbsearch(name)
        if city:
            res["city"] = wbsearch(city)
        searches[s] = res
        if i % 50 == 0:
            print(f"  search {i}/{len(todo)}", end="\r", file=sys.stderr)
    print(file=sys.stderr)
    cand = set(CITY_HINTS.values())
    for res in searches.values():
        for hits in res.values():
            cand.update(h["id"] for h in hits)
    info = info_for(cand)

    def entry(q, approx=None, fallback=""):
        d = info[q]
        e = {"name": d["label"] or fallback or q, "lat": d["coord"][0], "lon": d["coord"][1], "pid": q}
        if approx:
            e["approx"] = approx
        return e

    def decide(s, extra=None):
        name, city = split_place(s)
        res = searches[s]
        city_q, city_pts = None, []
        if city:
            cands = [h["id"] for h in res.get("city", [])]
            city_q = city_choice(city, info, cands)
            city_pts = [info[q]["coord"] for q in cands
                        if q in info and info[q]["coord"] and is_settlement(info[q])
                        and not (info[q]["types"] & REGION_TYPES) and not info[q]["cap"]
                        and fold(info[q]["label"]) == fold(city)]
            if city_q:
                city_pts.append(info[city_q]["coord"])
        hits = res.get("full", []) + res.get("name", []) + [{"id": q, "x": 1} for q in (extra or [])]
        seen, exact = set(), []
        for h in hits:
            q = h["id"]
            if q in seen:
                continue
            seen.add(q)
            d = info.get(q)
            if not d or not d["coord"] or d["types"] & BAD_TYPES or d["types"] & REGION_TYPES or d["cap"]:
                continue
            settle = is_settlement(d)
            if h.get("x"):
                # full-text hit: its label must share most words of the name
                want, have = set(fold(name).split()), set(fold(d["label"]).split())
                if not want or len(want & have) / len(want) < 0.75:
                    continue
            if city_pts:
                if settle or q == city_q or min(km(d["coord"], p) for p in city_pts) > 30:
                    continue
                return entry(q)
            if not h.get("x") and not (label_matches(s, h) or label_matches(name, h)):
                continue
            exact.append(q)
        if exact:
            # an unqualified name: accept only if every exact match is in the same place
            pts = [info[q]["coord"] for q in exact]
            if max(km(pts[0], p) for p in pts) <= 30:
                q = exact[0]
                return entry(q, "city" if is_settlement(info[q]) else None)
            return None
        if city_q:
            return entry(city_q, "city", city)
        return None

    for s, role in todo.items():
        e = decide(s)
        if e:
            gaz[s] = e

    # 2c. full-text search (any language) for strings that stayed vague or unresolved
    retry = [s for s in todo if s not in gaz or gaz[s].get("approx")]
    print(f"  full-text retry for {len(retry)} strings")
    extra = {}
    for i, s in enumerate(retry):
        name, city = split_place(s)
        q = (name + " " + city).strip() if city else s
        extra[s] = cirrus(q)
        if city and not extra[s]:
            extra[s] = cirrus(name)
        if i % 50 == 0:
            print(f"  cirrus {i}/{len(retry)}", end="\r", file=sys.stderr)
    print(file=sys.stderr)
    info.update(info_for({q for v in extra.values() for q in v} - set(info)))
    for s in retry:
        e = decide(s, extra[s])
        if e and (s not in gaz or not e.get("approx")):
            gaz[s] = e

    # 2d. OpenStreetMap Nominatim, only for strings that still have nothing
    left = sorted((s for s in todo if s not in gaz), key=lambda s: -(l_counts.get(s, 0) + c_counts.get(s, 0)))
    left = [s for s in left if l_counts.get(s, 0) + c_counts.get(s, 0) >= 2]
    print(f"  Nominatim for {len(left)} strings")
    for s in left:
        res = nominatim(s)
        if not res:
            continue
        r = res[0]
        cat, typ = r.get("category") or r.get("class"), r.get("type")
        if cat == "boundary" and typ == "administrative" and int(r.get("place_rank", 30)) < 12:
            continue  # a country or region
        if cat == "place" and typ in ("country", "state", "region", "province", "county", "continent"):
            continue
        name = (r.get("name") or r.get("display_name", "").split(",")[0]).strip()
        e = {"name": name or s, "lat": round(float(r["lat"]), 6), "lon": round(float(r["lon"]), 6), "src": "osm"}
        if cat in ("place", "boundary"):
            e["approx"] = "city"
        gaz[s] = e
    return gaz, todo


# --------------------------------------------------------------------------
# 3. Assignment (offline)

_maps = None


def _load_maps():
    global _maps
    if _maps is None:
        wd = json.loads((OUT / "wd-places.json").read_text()) if (OUT / "wd-places.json").exists() else {}
        gz = json.loads((OUT / "gazetteer.json").read_text()) if (OUT / "gazetteer.json").exists() else {}
        _maps = (wd, gz)
    return _maps


def place_of(w):
    """The place dict for one work, or None. Uses only the cached map files."""
    wd, gz = _load_maps()
    p = prefix(w)
    inst_key = SOURCE_INST.get(p)
    if p == "met" and ("/CRDImages/cl/" in (w.get("img") or "") or "cloister" in (w.get("l") or "").lower()):
        inst_key = "The Cloisters"
    if p == "fng" and w.get("y") is not None and w["y"] < 1800:
        inst_key = "Sinebrychoff Art Museum"
    inst = gz.get(inst_key) if inst_key else None
    e = wd.get(w.get("q") or "")
    if e and not (e.get("made") and inst):
        return e
    l = (w.get("l") or "").strip()
    g = gz.get(l)
    if l and inst_key and l in INSTITUTIONS:
        g = None  # the source's own generic label: the institution logic above decides
    if g and not g.get("approx"):
        return g
    if inst:
        return inst
    if g:
        return g
    if e:
        return e  # place of creation, as a last resort
    c = (w.get("c") or "").strip()
    if c and not NATIONALITY.match(c):
        g = gz.get(c)
        if g:
            return dict(g, made=True)
    return None


def assign(works):
    """Return (places, assignment): places = [[name, lat, lon], ...] deduplicated by
    name + coordinates rounded to 4 decimals; assignment maps work index -> place index or None."""
    places, index, assignment = [], {}, {}
    for i, w in enumerate(works):
        e = place_of(w)
        if not e:
            assignment[i] = None
            continue
        lat, lon = round(e["lat"], 4), round(e["lon"], 4)
        k = (e["name"], lat, lon)
        if k not in index:
            index[k] = len(places)
            places.append([e["name"], lat, lon])
        assignment[i] = index[k]
    return places, assignment


# --------------------------------------------------------------------------

def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    works = load_works()
    sources = load_sources()
    print(f"{len(works)} works, {len(sources)} source records")

    wd_places = build_wd_places(works)
    (OUT / "wd-places.json").write_text(json.dumps(wd_places, ensure_ascii=False, separators=(",", ":"), sort_keys=True))

    l_counts, c_counts = wanted_strings(works, sources, wd_places)
    gaz, todo = build_gazetteer(l_counts, c_counts)
    (OUT / "gazetteer.json").write_text(json.dumps(gaz, ensure_ascii=False, indent=0, sort_keys=True))

    global _maps
    _maps = None
    places, assignment = assign(works)
    have = sum(1 for v in assignment.values() if v is not None)
    per_src = defaultdict(lambda: [0, 0])
    for i, w in enumerate(works):
        k = prefix(w) or "wikidata"
        per_src[k][1] += 1
        per_src[k][0] += assignment[i] is not None
    unresolved = Counter()
    for i, w in enumerate(works):
        if assignment[i] is None:
            unresolved[(w.get("l") or "").strip() or "(no location)"] += 1
    lines = [
        f"# Generated by scripts/geocode.py on {time.strftime('%Y-%m-%d')}",
        f"# works with a place: {have}/{len(works)} ({100 * have / max(1, len(works)):.1f}%); "
        f"distinct places: {len(places)}",
        "# by source: " + ", ".join(f"{k} {a}/{b}" for k, (a, b) in sorted(per_src.items())),
        f"# gazetteer: {len(gaz)} strings ({sum(1 for v in gaz.values() if v.get('approx'))} approximate to a city); "
        f"wd-places: {len(wd_places)}",
        "# count\tlocation text of works without a place",
    ]
    lines += [f"{n}\t{s}" for s, n in unresolved.most_common()]
    unplaced_strings = [s for s in todo if s not in gaz]
    lines += ["", "# location strings that could not be resolved (count in collection)"]
    lines += [f"{l_counts.get(s, 0) + c_counts.get(s, 0)}\t{s}" for s in
              sorted(unplaced_strings, key=lambda s: -(l_counts.get(s, 0) + c_counts.get(s, 0)))]
    (OUT / "unresolved.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[1:4]))
    counts = Counter(v for v in assignment.values() if v is not None)
    print("Top places:")
    for p, n in counts.most_common(25):
        print(f"  {n:6d}  {places[p][0]}  ({places[p][1]}, {places[p][2]})")


if __name__ == "__main__":
    main()
