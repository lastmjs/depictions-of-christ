#!/usr/bin/env python3
"""Geocode the Complete Collection: where was each work made?

Every work is placed as close as possible to where it was CREATED, with a
precision level:

  made     an explicitly recorded place of creation (Wikidata P1071, or a museum's
           "place made" field naming a specific city/town/monastery)
  in situ  a work that is part of a building and still where it was made (frescoes,
           mosaics, stained glass, portal sculpture, altarpieces in churches,
           wayside crosses, outdoor statues...): its current location
  artist   the artist's documented place of work (P937 bracketing the work's date),
           else place of death (work in the last 20 years of life), else place of
           birth (work in the first 30 years)
  region   only a culture / school / nationality / country is known

Movable works are never placed at the museum that holds them today.

Lookups use free, keyless services only (Wikidata SPARQL and the Wikidata API;
OpenStreetMap Nominatim as a last resort, at most one request per second). Every
network response is cached under .cache/geocode/. Output, under data/map/:

  wd-places.json    Wikidata work QID -> {loc, made, origin, cr}
  artists.json      artist QID -> {n, b, d, w: workplaces, bp: birthplace, dp: deathplace}
  artist-names.json artist name text (source records) -> artist QID
  gazetteer.json    location / place-made text -> {name, lat, lon, pid?, kind, museum?, approx?}
  unresolved.txt    coverage statistics and the strings that could not be placed

assign(works) uses only those files (no network).

Usage: python3 scripts/geocode.py
"""
import hashlib
import http.client
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
# Known institutions (current locations of whole sources). Used to know that a
# source record is in a museum; coordinates are fetched from Wikidata.
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

# Source id prefixes whose records are all held by one museum (movable works).
MUSEUM_SOURCES = {"met", "nga", "aic", "cma", "rijks", "vam", "va", "smk", "nationalmuseum", "nm", "fng",
                  "yale", "yuag", "ycba", "princeton", "getty", "walters"}

# City names whose first Wikidata search hit is the wrong place.
CITY_HINTS = {
    "washington": "Q61", "washington, d.c.": "Q61", "washington dc": "Q61",
    "st. petersburg": "Q656", "st petersburg": "Q656", "saint petersburg": "Q656",
    "new york": "Q60", "london": "Q84", "paris": "Q90", "rome": "Q220", "florence": "Q2044",
    "venice": "Q641", "naples": "Q2634", "milan": "Q490", "berlin": "Q64", "vienna": "Q1741",
    "munich": "Q1726", "madrid": "Q2807", "moscow": "Q649", "budapest": "Q1781",
}

# Representative points for schools, cultures, nationalities and countries ("region").
# name: (lat, lon, "phrase|phrase|...") — phrases are matched on folded text.
_R = {
    "Italy": (42.83, 12.83, "italy|italian|italia|north italy|northern italy|north italian|northern italian|"
                             "central italy|central italian|south italy|southern italy|south italian|"
                             "southern italian|italian renaissance"),
    "Florence": (43.7696, 11.2558, "florentine|florence|firenze"),
    "Venice": (45.4408, 12.3155, "venetian|venice|venezia"),
    "Siena": (43.3188, 11.3308, "sienese|siena"),
    "Bologna": (44.4949, 11.3426, "bolognese|bologna|emilian|emilia"),
    "Rome": (41.9028, 12.4964, "roman|rome"),
    "Naples": (40.8518, 14.2681, "neapolitan|naples|napoli"),
    "Lombardy": (45.5850, 9.9300, "lombard|lombardy|milanese"),
    "Genoa": (44.4056, 8.9463, "genoese|genoa"),
    "Ferrara": (44.8381, 11.6198, "ferrarese|ferrara"),
    "Umbria": (43.1107, 12.3908, "umbrian|umbria"),
    "Padua": (45.4064, 11.8768, "paduan|padua"),
    "Piedmont": (45.0703, 7.6869, "piedmontese|piedmont"),
    "Marche": (43.6158, 13.5189, "marchigian|marche"),
    "Sicily": (37.6, 14.0, "sicilian|sicily"),
    "Flanders": (51.05, 3.72, "flemish|flanders"),
    "Southern Netherlands": (50.85, 4.35, "south netherlandish|southern netherlandish|southern netherlands|south netherlands|spanish netherlands|brabant|brabantine"),
    "Low Countries": (51.5, 4.9, "netherlandish|low countries|early netherlandish"),
    "Netherlands": (52.13, 5.29, "dutch|netherlands|northern netherlands|north netherlandish|northern netherlandish|dutch republic|holland"),
    "Belgium": (50.64, 4.67, "belgian|belgium"),
    "Germany": (51.0, 10.0, "holy roman empire|german|germany"),
    "Southern Germany": (48.6, 10.4, "south german|southern germany|southern german|south germany|swabian|swabia|suabian"),
    "Upper Rhine": (48.0, 7.85, "upper rhine|upper rhenish"),
    "Middle Rhine": (50.2, 7.6, "middle rhine|middle rhenish"),
    "Lower Rhine": (51.6, 6.4, "lower rhine|lower rhenish"),
    "Danube School": (48.72, 12.6, "danube school|danube"),
    "Franconia": (49.6, 11.0, "franconian|franconia"),
    "Bavaria": (48.8, 11.5, "bavarian|bavaria"),
    "Westphalia": (51.9, 7.6, "westphalian|westphalia"),
    "Saxony": (51.05, 13.35, "saxon|saxony"),
    "Austria": (47.6, 14.1, "austrian|austria|tyrolean|tyrol"),
    "Switzerland": (46.8, 8.2, "swiss|switzerland"),
    "France": (46.6, 2.4, "french|france"),
    "Northern France": (49.9, 2.8, "north french|northern france|northern french|picardy"),
    "Normandy": (49.1, 0.2, "norman|normandy"),
    "Burgundy": (47.05, 4.4, "burgundian|burgundy"),
    "Île-de-France": (48.75, 2.5, "ile de france|parisian"),
    "Spain": (40.2, -3.7, "spanish|spain|castilian|castile"),
    "Catalonia": (41.8, 1.6, "catalan|catalonia"),
    "Aragon": (41.6, -0.9, "aragonese|aragon"),
    "Andalusia": (37.4, -5.0, "andalusian|andalusia"),
    "Valencia": (39.47, -0.38, "valencian"),
    "Portugal": (39.6, -8.0, "portuguese|portugal"),
    "England": (52.5, -1.5, "english|england"),
    "Great Britain": (53.0, -2.0, "great britain|britain|united kingdom|british"),
    "Scotland": (56.5, -4.2, "scottish|scotland"),
    "Ireland": (53.3, -7.7, "irish|ireland"),
    "Wales": (52.3, -3.7, "welsh|wales"),
    "Constantinople": (41.0082, 28.9784, "byzantine|byzantine empire|byzantium|constantinople"),
    "Greece": (39.0, 22.0, "post byzantine|greek|greece|cretan|crete"),
    "Egypt": (26.8, 30.8, "coptic|egypt|egyptian"),
    "Ethiopia": (11.6, 39.0, "ethiopian|ethiopia|abyssinian"),
    "Russia": (56.0, 38.0, "novgorod school|russian|russia|muscovite"),
    "Ukraine": (49.0, 31.0, "ukrainian|ukraine|ruthenian"),
    "Armenia": (40.07, 45.04, "armenian|armenia|cilician"),
    "Georgia": (42.3, 43.4, "georgian|georgia"),
    "Serbia": (44.0, 20.9, "serbian|serbia"),
    "Bulgaria": (42.7, 25.5, "bulgarian|bulgaria"),
    "Romania": (45.9, 24.97, "romanian|romania|wallachian|moldavian"),
    "Hungary": (47.16, 19.5, "hungarian|hungary"),
    "Bohemia": (49.8, 15.5, "czech republic|bohemian|bohemia|czechia"),
    "Poland": (52.0, 19.4, "polish|poland"),
    "Slovakia": (48.67, 19.7, "slovak|slovakia"),
    "Croatia": (45.1, 15.2, "croatian|croatia|dalmatian"),
    "Slovenia": (46.15, 14.99, "slovenian|slovenia"),
    "Denmark": (56.0, 10.0, "danish|denmark"),
    "Sweden": (60.1, 15.0, "swedish|sweden"),
    "Norway": (61.0, 9.0, "norwegian|norway"),
    "Finland": (62.0, 26.0, "finnish|finland"),
    "Iceland": (64.9, -18.6, "icelandic|iceland"),
    "Lithuania": (55.2, 23.9, "lithuanian|lithuania"),
    "Latvia": (56.9, 24.6, "latvian|latvia"),
    "Estonia": (58.6, 25.0, "estonian|estonia"),
    "Syria": (35.0, 38.5, "syrian|syria"),
    "Lebanon": (33.9, 35.9, "lebanese|lebanon|maronite"),
    "Holy Land": (31.78, 35.22, "holy land|palestine|palestinian|jerusalem"),
    "Cyprus": (35.0, 33.2, "cypriot|cyprus"),
    "Malta": (35.9, 14.4, "maltese|malta"),
    "Persia": (32.4, 53.7, "persian|persia|iran|iranian|safavid"),
    "Mughal India": (27.18, 78.01, "mughal|mughal india"),
    "India": (22.0, 79.0, "indo portuguese|indian|india|goa|goan"),
    "Ceylon": (7.87, 80.77, "sri lanka|sinhalese|ceylon"),
    "China": (35.9, 104.2, "chinese|china"),
    "Japan": (36.2, 138.3, "japanese|japan"),
    "Korea": (36.5, 127.9, "korean|korea"),
    "Philippines": (12.9, 121.8, "filipino|philippine|philippines"),
    "Mexico": (19.43, -99.13, "new spain|mexican|mexico"),
    "Peru": (-12.05, -77.04, "peruvian|peru"),
    "Cusco": (-13.53, -71.97, "cuzco|cusco|cuzco school|cusco school"),
    "Quito": (-0.18, -78.47, "quito|quito school|ecuadorian|ecuador"),
    "Bolivia": (-16.5, -68.15, "bolivian|bolivia"),
    "Colombia": (4.6, -74.1, "new granada|colombian|colombia"),
    "Brazil": (-14.2, -51.9, "brazilian|brazil"),
    "Argentina": (-34.6, -58.4, "argentine|argentinian|argentina"),
    "Guatemala": (14.6, -90.5, "guatemalan|guatemala"),
    "United States": (39.8, -98.6, "united states|united states of america|american|usa"),
    "Canada": (56.1, -106.3, "canadian|canada"),
    "Australia": (-25.3, 133.8, "australian|australia"),
    "Kongo": (-5.0, 14.5, "kongo|kongo peoples|congo"),
}
REGIONS = {}
for _name, (_lat, _lon, _keys) in _R.items():
    for _p in [_name] + _keys.split("|"):
        _k = re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", _p.lower()).encode("ascii", "ignore").decode()).strip()
        if _k and (_k not in REGIONS or _p == _name):
            REGIONS[_k] = (_name, _lat, _lon)


SKIP = re.compile(
    r"^(private|unknown|lost|destroyed|whereabouts|location unknown|present location|"
    r"formerly|art market|on the art market|in the art market|anonymous|n/?a\b|none|various|"
    r"web gallery of art|wikimedia|stolen)|^(others?|europe|european|western europe|eastern europe|n\.? ?d\.?|"
    r"various places|see notes?|court school.*)$|private collection|privately owned|privatbesitz|"
    r"collection particuli|colección particular|collezione privata|art market|kunsthandel|"
    r"christie|sotheby|bonhams|dorotheum|lempertz|koller|hampel|neumeister|"
    r"artcurial|drouot|tajan|bukowskis|van ham|nagel auktion|auction|dealer|"
    r"destroyed|whereabouts unknown|^lost\b|^\(?\s*$",
    re.I,
)

MUSEUM_RE = re.compile(
    r"museum|museo|musei|raccolt|collezion|musée|musee|muzeum|múzeum|muzej|музей|museu|museet|museo|gallery|galleria|galerie|"
    r"gemäldegalerie|pinakothek|pinacoteca|kunsthalle|collection|sammlung|library|biblioth|biblioteca|"
    r"archiv|hermitage|louvre|prado|uffizi|kunstkammer|staatliche|nationalmuseum|ateneum|"
    r"kunsthistorisches|academy|accademia|akademie|institute|university|foundation|fondazione|stiftung|"
    r"palazzo pitti|getty|cloisters|\btate\b|salon carr|doughty house|pinacoteca|national trust|royal collection|treasury of|schatzkammer",
    re.I,
)

# Work types that are part of a building or fixed outdoors (only "in situ" when not in a museum).
INSITU_K = re.compile(
    r"fresco|mural|wall painting|mosaic|stained|leadlight|church window|window|tympan|portal|reredos|retable|"
    r"altarpiece|altar|triptych|polyptych|predella|wayside|calvary|monument|memorial|marian column|"
    r"column with statue|statue|pulpit|rood|choir stall|baptismal font|font|epitaph|tomb|relief|capital|"
    r"facade|façade|apse|ceiling|vault|graffit|catacomb|crucifix|icon|group of sculptures|colossal|chapel|"
    r"sculpture|cross|ciborium|tabernacle|iconostasis|antependium|lunette|bas-relief|high relief|"
    r"sarcophagus|stele|architectural|wall|church",
    re.I,
)
INSITU_M = re.compile(r"fresco|mural|wall painting|mosaic|stained glass|secco|sgraffito", re.I)

ART_DESC = re.compile(
    r"painter|sculptor|engraver|printmaker|artist|illuminator|illustrator|draughts|draftsman|drawer|"
    r"goldsmith|silversmith|etcher|woodcut|miniatur|glass|carver|mosaicist|architect|lithograph|"
    r"polymath|medallist|medalist|master|iconographer|icon|enamel|cartographer|publisher|designer|"
    r"stonemason|woodworker|cabinetmaker|maker|craftsman|workshop",
    re.I,
)
ANON_ARTIST = re.compile(
    r"^(anonymous|anon\b|unknown|unidentified|not identified|artist unknown|onbekend|anoniem|"
    r"after\b|copy after|copy of|follower|workshop|atelier|studio of|circle of|school of|style of|"
    r"manner of|imitator|imitation|attributed workshop|various|byzantine|coptic|italian|german|french|"
    r"flemish|netherlandish|dutch|spanish|english|british|american|russian|greek|austrian|swiss)|"
    r"\bcentury\b|\bmaster \(|unknown master|^master$|\(italian\)|\(german\)|\(flemish\)|"
    r"^(okänd|ingen uppgift|unbekannt|anonym|inconnu|anonimo|desconocido|ukendt|tuntematon)\b|"
    r"mästare av|meister von|maître de|maestro de|maestro di|school$|schule$",
    re.I,
)

# Wikidata classes that are never a place to put a work on the map.
BAD_TYPES = {
    "Q5", "Q4167410", "Q13406463", "Q101352", "Q202444", "Q4167836", "Q11266439",
    "Q5398426", "Q7725634", "Q3305213", "Q838948", "Q17537576", "Q16521", "Q4830453",
    # languages and peoples (some carry coordinates)
    "Q34770", "Q33742", "Q38058796", "Q45762", "Q1288568", "Q436240", "Q41710", "Q33384", "Q2315359",
    "Q20162172", "Q1068946",
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
BIG_CITY = {"Q1549591", "Q5119", "Q1637706", "Q200250", "Q1066984", "Q22865"}
ANONYMOUS_QIDS = {"Q4233718", "Q3816639", "Q19595382", "Q24238356"}

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
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError,
                http.client.HTTPException) as e:
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


def item_rows(template, var, qids, batch=200):
    """SPARQL rows for many items, cached per item (so a changed collection only queries new items).

    template has one %s for the VALUES list; var names the variable holding the item."""
    path = CACHE / "items" / (hashlib.sha1(template.encode()).hexdigest()[:16] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    store = json.loads(path.read_text()) if path.exists() else {}
    qs = qsort(qids)
    missing = [q for q in qs if q not in store]
    for n, chunk in enumerate(chunks(missing, batch)):
        rows = sparql(template % " ".join("wd:" + q for q in chunk))
        for q in chunk:
            store[q] = []
        for r in rows:
            q = qid(val(r, var))
            if q in store:
                store[q].append(r)
        if n % 10 == 9:
            path.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")))
        print(f"  query {min((n + 1) * batch, len(missing))}/{len(missing)}", end="\r", file=sys.stderr)
    if missing:
        path.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")))
    return [r for q in qs for r in store.get(q, [])]


def wbsearch(text, limit=7):
    url = WDAPI + "?" + urllib.parse.urlencode({
        "action": "wbsearchentities", "format": "json", "language": "en", "uselang": "en",
        "type": "item", "limit": limit, "search": text[:250]})
    try:
        return fetch(url, kind="wbsearch", gap=0.5).get("search", [])
    except urllib.error.HTTPError:
        return []


def cirrus(text, limit=6):
    url = WDAPI + "?" + urllib.parse.urlencode({
        "action": "query", "list": "search", "format": "json", "srlimit": limit, "srnamespace": 0,
        "srsearch": text[:250] + " haswbstatement:P625"})
    try:
        return [h["title"] for h in fetch(url, kind="cirrus", gap=0.5).get("query", {}).get("search", [])]
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


def year_of(ts):
    m = re.match(r"^([+-]?\d+)-", ts or "")
    return int(m.group(1)) if m else None


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))


def fold(s):
    s = (s or "").replace("û", "ű").replace("õ", "ő")
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def qsort(qs):
    return sorted({q for q in qs if re.match(r"^Q\d+$", q or "")}, key=lambda q: int(q[1:]))


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


def region_of(text):
    """A school / culture / nationality / country string -> (name, lat, lon) or None."""
    t = fold(re.sub(r"\(.*?\)", " ", text or ""))
    t = re.sub(r"\b(probably|possibly|perhaps|likely|made in|style|medieval|renaissance|baroque|"
               r"\d+(st|nd|rd|th)? ?(century|c)|century|or|and|active in|workshop|school)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    if t in REGIONS:
        return REGIONS[t]
    parts = [p.strip() for p in re.split(r"[,;/]| or | and ", fold_keep_commas(text)) if p.strip()]
    found = [REGIONS[fold(p)] for p in parts if fold(p) in REGIONS]
    if found:
        return next((r for r in found if r[0] not in COARSE), found[0])
    for p in sorted((k for k in REGIONS if " " in k), key=len, reverse=True):
        if re.search(r"\b" + p + r"\b", t):
            return REGIONS[p]
    words = t.split()
    for w in words:
        if w in REGIONS:
            return REGIONS[w]
    return None


def culture_only(p):
    """True if a place-made part only names a culture / school / country (e.g. 'Florentine, Italy')."""
    name, _ = split_place(p)
    for t in (fold(p), fold(name)):
        if t in REGIONS and not (REGIONS[t][0] in CITY_REGIONS and t == fold(REGIONS[t][0])):
            return True
    return False


# Table entries that are cities: their plain name ("Rome") is a place made, not just a school.
CITY_REGIONS = {"Florence", "Venice", "Siena", "Bologna", "Rome", "Naples", "Genoa", "Ferrara", "Padua",
                "Constantinople", "Cusco", "Quito", "Valencia"}


COARSE = {"Italy", "Germany", "France", "Spain", "Netherlands", "Low Countries", "Great Britain", "England",
          "United States", "Russia", "Greece", "Constantinople", "Austria", "Switzerland", "Belgium", "Portugal",
          "Poland", "Hungary", "Bohemia", "Mexico", "Peru", "India", "China", "Japan", "Egypt", "Ethiopia"}


def fold_keep_commas(s):
    return ",".join(fold(x) for x in re.split(r"[,;/]", s or ""))


# --------------------------------------------------------------------------
# Generic Wikidata lookups

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

MUSEUM_QUERY = """
SELECT DISTINCT ?x WHERE {
  VALUES ?x { %s }
  { ?x wdt:P31/wdt:P279* ?m . } UNION { ?x wdt:P361 ?whole . ?whole wdt:P31/wdt:P279* ?m . }
  FILTER(?m IN (wd:Q33506, wd:Q7075, wd:Q166118, wd:Q207694, wd:Q1007870, wd:Q2668072))
}"""


def labels_for(qids):
    out = {}
    for r in item_rows(LABEL_QUERY, "x", qids, 300):
        lab = val(r, "xLabel")
        q = qid(val(r, "x"))
        if lab and lab != q:
            out[q] = lab
    return out


def info_for(qids):
    """QID -> {coord, label, types, pop, cap} (cached SPARQL)."""
    out = {}
    if True:
        for r in item_rows(INFO_QUERY, "x", qids, 250):
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
    missing = [q for q, d in out.items() if not d["label"]]
    if missing:
        for q, lab in labels_for(missing).items():
            out[q]["label"] = lab
    return out


def museums_among(qids):
    """The subset of QIDs that are museums, libraries, archives or galleries."""
    out = set()
    try:
        rows = item_rows(MUSEUM_QUERY, "x", qids, 150)
    except Exception as e:  # a timeout: fall back to names only
        print(f"  museum check failed ({e})", file=sys.stderr)
        rows = []
    out.update(qid(val(r, "x")) for r in rows)
    return out


def is_settlement(d):
    return bool(d["types"] & CITY_TYPES) or d["pop"]


def is_region(d):
    if d["types"] & CITY_TYPES:
        return False  # city-states such as Berlin, Hamburg or Vienna are places, not regions
    return bool(d["types"] & REGION_TYPES) or d["cap"]


# --------------------------------------------------------------------------
# 1. Wikidata works

WD_LOC = """
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
  }
  BIND(EXISTS { ?place wdt:P1082 [] } AS ?settle)
}"""

WD_MADE = """
SELECT ?item ?made ?place ?coord ?via ?region WHERE {
  VALUES ?item { %s }
  ?item wdt:P1071 ?made .
  { ?made wdt:P625 ?coord . BIND(?made AS ?place) BIND("p" AS ?via) }
  UNION
  { FILTER NOT EXISTS { ?made wdt:P625 [] } ?made wdt:P131 ?place . ?place wdt:P625 ?coord . BIND("a" AS ?via) }
  BIND(EXISTS { ?place wdt:P36 [] } AS ?region)
}"""

WD_ORIGIN = """
SELECT ?item ?place ?coord WHERE {
  VALUES ?item { %s }
  ?item wdt:P495 ?place . ?place wdt:P625 ?coord .
}"""

WD_CREATORS = """
SELECT ?item ?cr WHERE {
  VALUES ?item { %s }
  ?item wdt:P170 ?cr . FILTER(isIRI(?cr))
}"""

RANK = {("loc", False): 0, ("coll", False): 1, ("loc", True): 2, ("coll", True): 3, ("admin", False): 4,
        ("admin", True): 4}


def build_wd_places(works):
    qs = qsort(w["q"] for w in works if w.get("q"))
    print(f"Wikidata works: {len(qs)}")
    loc, made, origin, creators = {}, {}, {}, defaultdict(list)
    for r in item_rows(WD_LOC, "item", qs):
        q, kind, place = qid(val(r, "item")), val(r, "kind"), qid(val(r, "place"))
        pt = point(val(r, "coord"))
        if not pt:
            continue
        rank = (RANK[(kind, val(r, "settle") == "true")], int(place[1:]))
        if q not in loc or rank < loc[q][0]:
            loc[q] = (rank, kind, place, pt)
    for r in item_rows(WD_MADE, "item", qs):
        q, place = qid(val(r, "item")), qid(val(r, "place"))
        pt = point(val(r, "coord"))
        if not pt:
            continue
        region = val(r, "region") == "true"
        rank = (region, val(r, "via") != "p", int(place[1:]))
        if q not in made or rank < made[q][0]:
            made[q] = (rank, place, pt, region)
    for r in item_rows(WD_ORIGIN, "item", qs):
        q, place = qid(val(r, "item")), qid(val(r, "place"))
        pt = point(val(r, "coord"))
        if pt and (q not in origin or int(place[1:]) < int(origin[q][0][1:])):
            origin[q] = (place, pt)
    for r in item_rows(WD_CREATORS, "item", qs):
        q, c = qid(val(r, "item")), qid(val(r, "cr"))
        if re.match(r"^Q\d+$", c) and c not in ANONYMOUS_QIDS and c not in creators[q]:
            creators[q].append(c)
    print(file=sys.stderr)
    places = {v[2] for v in loc.values()} | {v[1] for v in made.values()} | {v[0] for v in origin.values()}
    labels = labels_for(places)
    museums = museums_among({v[2] for v in loc.values()})
    out = {}
    for q in qs:
        e = {}
        if q in loc:
            _, kind, place, (lat, lon) = loc[q]
            name = labels.get(place, place)
            e["loc"] = {"name": name, "lat": lat, "lon": lon, "pid": place,
                        "museum": place in museums or bool(MUSEUM_RE.search(name))}
            if kind == "admin":
                e["loc"]["approx"] = "admin"
        if q in made:
            _, place, (lat, lon), region = made[q]
            e["made"] = {"name": labels.get(place, place), "lat": lat, "lon": lon, "pid": place}
            if region:
                e["made"]["region"] = True
        if q in origin:
            place, (lat, lon) = origin[q]
            e["origin"] = {"name": labels.get(place, place), "lat": lat, "lon": lon, "pid": place}
        if creators.get(q):
            e["cr"] = sorted(creators[q], key=lambda c: int(c[1:]))
        if e:
            out[q] = e
    print(f"  current location {len(loc)}, place made {len(made)}, origin {len(origin)}, "
          f"with creator {len(creators)}")
    return out


# --------------------------------------------------------------------------
# 2. Artists

ART_LIFE = """
SELECT ?a ?b ?d ?bp ?bpc ?dp ?dpc WHERE {
  VALUES ?a { %s }
  OPTIONAL { ?a wdt:P569 ?b }
  OPTIONAL { ?a wdt:P570 ?d }
  OPTIONAL { ?a wdt:P19 ?bp . ?bp wdt:P625 ?bpc . }
  OPTIONAL { ?a wdt:P20 ?dp . ?dp wdt:P625 ?dpc . }
}"""

ART_WORK = """
SELECT ?a ?wl ?c ?s ?e ?cap WHERE {
  VALUES ?a { %s }
  ?a p:P937 ?st . ?st ps:P937 ?wl ; wikibase:rank ?r . FILTER(?r != wikibase:DeprecatedRank)
  ?wl wdt:P625 ?c .
  OPTIONAL { ?st pq:P580 ?s } OPTIONAL { ?st pq:P582 ?e }
  BIND(EXISTS { ?wl wdt:P36 [] } AS ?cap)
}"""

ART_DATES = """
SELECT ?a ?b ?d WHERE {
  VALUES ?a { %s }
  OPTIONAL { ?a wdt:P569 ?b } OPTIONAL { ?a wdt:P570 ?d }
}"""


def artist_dates(qids):
    out = {}
    if True:
        for r in item_rows(ART_DATES, "a", qids, 250):
            a = qid(val(r, "a"))
            d = out.setdefault(a, [None, None])
            b, de = year_of(val(r, "b")), year_of(val(r, "d"))
            if b is not None and (d[0] is None or b < d[0]):
                d[0] = b
            if de is not None and (d[1] is None or de > d[1]):
                d[1] = de
    return out


def clean_artist(name):
    """Normalise an artist string from a source record; '' if anonymous / not a person."""
    a = (name or "").strip()
    a = re.split(r";|\s+\|\s+|\n", a)[0].strip()
    a = re.sub(r"^(attributed to|attr\.|possibly by|probably by|designed by|after a design by|"
               r"published by|printed by|engraved by|by)\s+", "", a, flags=re.I)
    if not a or ANON_ARTIST.search(a) or len(a) < 3 or region_of(a) and fold(a) in REGIONS:
        return ""
    a = re.sub(r"\s+d\.\s*ä\.?$", " the Elder", a, flags=re.I)
    a = re.sub(r"\s+d\.\s*j\.?$", " the Younger", a, flags=re.I)
    m = re.match(r"^([A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ'\- ]+[A-ZÀ-ÖØ-Þ])\s+([A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ].*)$", a)
    if m and len(m.group(1)) >= 3:  # "MORELLI Domenico" -> "Domenico Morelli"
        a = f"{m.group(2)} {m.group(1).title()}"
    return a


# German / Swedish / Dutch exonyms of artists used by some providers.
EXONYMS = {"raffael": "Raphael", "rafael": "Raphael", "raffaello santi": "Raphael", "tizian": "Titian",
           "tiziano": "Titian", "tiziano vecellio": "Titian", "tintoretto jacopo": "Tintoretto",
           "rembrandt harmensz van rijn": "Rembrandt", "lucas cranach d a": "Lucas Cranach the Elder"}


def search_variants(a):
    """Search strings for an artist name: as given, with '(II)' -> 'II', without it, before a comma."""
    out = [a]
    if fold(a) in EXONYMS:
        out.append(EXONYMS[fold(a)])
    m = re.match(r"^(.*?)\s*\(([IVX]+)\)\s*$", a)
    if m:
        out = [f"{m.group(1)} {m.group(2)}", m.group(1)]
    elif re.search(r"\(.*\)", a):  # "Alexander Master (painter)", "Rafael (eg. Raffaello Sanzio)"
        out.append(re.sub(r"\s*\(.*?\)\s*", " ", a).strip())
        inner = re.sub(r"^(eg\.|egentligen|i\.e\.|real name|born)\s*", "", re.search(r"\((.*?)\)", a).group(1))
        if len(inner.split()) >= 2:
            out.append(inner)
    if "," in a:
        first = a.split(",")[0].strip()
        if len(first.split()) >= 2:
            out.append(first)
    p = re.sub(r"\s*\(.*?\)\s*", " ", a).split(",")
    if len(p) == 2 and p[0].strip() and p[1].strip():  # "Burne-Jones, Edward Coley", "DÜRER, Albrecht"
        last = p[0].strip()
        out.append(f"{p[1].strip()} {last.title() if last.isupper() else last}")
    return list(dict.fromkeys(out))


def resolve_artist_names(names_years):
    """artist text -> QID, accepting only an unambiguous artist whose life fits the works' dates."""
    searches = {}
    for i, a in enumerate(names_years):
        searches[a] = [h for v in search_variants(a) for h in wbsearch(v)]
        if i % 50 == 0:
            print(f"  artist search {i}/{len(names_years)}", end="\r", file=sys.stderr)
    print(file=sys.stderr)
    cands = {h["id"] for hits in searches.values() for h in hits if ART_DESC.search(h.get("description") or "")}
    dates = artist_dates(cands)
    out = {}
    for a, hits in searches.items():
        years = [y for y in names_years[a] if y is not None]
        want = {fold(v) for v in search_variants(a)}
        ok, seen = [], set()
        for h in hits:
            q = h["id"]
            if q in seen or not ART_DESC.search(h.get("description") or ""):
                continue
            seen.add(q)
            texts = [fold(h.get("match", {}).get("text")), fold(h.get("label"))]
            exact = any(t in want for t in texts)
            # also "Marten de Vos" ~ "Marten de Vos the Elder", "Raffaello Sanzio" ~ "... da Urbino"
            near = any(set(v.split()) <= set(t.split()) and len(t.split()) - len(v.split()) <= 2
                       for t in texts for v in want if t)
            if not (exact or near):
                continue
            b, d = dates.get(q, [None, None])
            if years and (b is not None or d is not None):
                lo = (b + 10) if b is not None else (d - 80)
                hi = (d + 10) if d is not None else (b + 90)
                if not any(lo <= y <= hi for y in years):
                    continue
            elif b is None and d is None and years:
                continue  # no dates to check the match against
            ok.append((q, exact, fold(h.get("label")) in want))
        exacts = [q for q, e, _ in ok if e]
        labels = [q for q, _, lab in ok if lab]
        if len(ok) == 1:
            out[a] = ok[0][0]
        elif len(exacts) == 1:
            out[a] = exacts[0]
        elif len(labels) == 1:
            out[a] = labels[0]  # the one whose main label (not an alias) is the name
    return out


def build_artists(creator_qids):
    qs = qsort(creator_qids)
    print(f"Artists: {len(qs)}")
    life, work = {}, defaultdict(list)
    if True:
        for r in item_rows(ART_LIFE, "a", qs, 150):
            a = qid(val(r, "a"))
            d = life.setdefault(a, {"b": None, "d": None, "bp": None, "dp": None})
            b, de = year_of(val(r, "b")), year_of(val(r, "d"))
            if b is not None and (d["b"] is None or b < d["b"]):
                d["b"] = b
            if de is not None and (d["d"] is None or de > d["d"]):
                d["d"] = de
            for k in ("bp", "dp"):
                pt = point(val(r, k + "c"))
                if pt and (d[k] is None or int(qid(val(r, k))[1:]) < int(d[k][0][1:])):
                    d[k] = (qid(val(r, k)), pt)
        for r in item_rows(ART_WORK, "a", qs, 150):
            if val(r, "cap") == "true":
                continue  # a country or region, not a place of work
            pt = point(val(r, "c"))
            if pt:
                work[qid(val(r, "a"))].append((qid(val(r, "wl")), pt, year_of(val(r, "s")), year_of(val(r, "e"))))
    places = {p[0] for d in life.values() for p in (d["bp"], d["dp"]) if p} | {w[0] for ws in work.values() for w in ws}
    labels = labels_for(places | set(qs))
    out = {}
    for a in qs:
        d = life.get(a, {})
        e = {"n": labels.get(a, a)}
        if d.get("b") is not None:
            e["b"] = d["b"]
        if d.get("d") is not None:
            e["d"] = d["d"]
        ws = sorted(set(work.get(a, [])), key=lambda w: (w[2] is None, w[2] or 0, int(w[0][1:])))
        if ws:
            e["w"] = [[labels.get(p, p), pt[0], pt[1], s, en, p] for p, pt, s, en in ws]
        for k in ("bp", "dp"):
            if d.get(k):
                p, pt = d[k]
                e[k] = [labels.get(p, p), pt[0], pt[1], p]
        out[a] = e
    print(f"  with workplace {sum(1 for e in out.values() if 'w' in e)}, birthplace "
          f"{sum(1 for e in out.values() if 'bp' in e)}, deathplace {sum(1 for e in out.values() if 'dp' in e)}")
    return out


# --------------------------------------------------------------------------
# 3. Gazetteer for location and place-made text

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


def c_parts(c):
    """Place-made text -> candidate place strings, most specific first."""
    c = re.sub(r"\b(probably|possibly|perhaps|likely|made in|active in|workshops?|provenance|found at)\b|\?",
               " ", c, flags=re.I)
    c = re.sub(r"\((in situ|replica[^)]*|now [^)]*|original[^)]*)\)", " ", c, flags=re.I)
    c = re.sub(r"[()]", ",", c)
    c = re.sub(r"\s+", " ", c)
    c = re.sub(r"\s*,[\s,]*", ", ", c).strip(" ,;?")
    parts = [p.strip(" ?") for p in re.split(r"[,;/]|\bor\b|\band\b", c) if p.strip(" ?")]
    out = []
    if c:
        out.append(c)
    for p in reversed(parts):
        if p not in out:
            out.append(p)
    return out


def city_choice(text, info, hits):
    """Best settlement among search hits whose label or alias is the city name."""
    hint = CITY_HINTS.get(text.lower().strip())
    if hint and hint in info and info[hint]["coord"]:
        return hint
    best, score = None, -1e9
    for i, h in enumerate(hits):
        d = info.get(h["id"])
        if not d or not label_matches(text, h):
            continue
        if is_region(d) and best is None:
            return None  # "(Arizona)" names a state, not the town of that name in Honduras
        if not d["coord"] or d["types"] & BAD_TYPES or is_region(d) or not is_settlement(d):
            continue
        sc = -i + (10 if d["types"] & CITY_TYPES else 0) + (5 if d["pop"] else 0)
        if sc > score:
            best, score = h["id"], sc
    return best


def label_matches(query, hit):
    t = fold(hit.get("match", {}).get("text", ""))
    return t == fold(query) or fold(hit.get("label", "")) == fold(query)


def build_gazetteer(l_counts, c_counts):
    """Resolve location strings (l) and place-made strings (c, or parts of them)."""
    gaz = {}
    inst_info = info_for({q for _, q in INSTITUTIONS.values()})
    for key, (name, q) in INSTITUTIONS.items():
        d = inst_info.get(q)
        if d and d["coord"]:
            gaz[key] = {"name": name, "lat": d["coord"][0], "lon": d["coord"][1], "pid": q, "kind": "site",
                        "museum": True}

    todo = {}
    for s in l_counts:
        if s not in gaz and not SKIP.search(s) and len(s) >= 3 and re.search(r"[A-Za-zÀ-ÿ]", s):
            todo[s] = l_counts[s]
    for c, n in c_counts.items():
        for p in c_parts(c):
            if culture_only(p):
                continue
            if p not in gaz and not SKIP.search(p) and len(p) >= 3 and re.search(r"[A-Za-zÀ-ÿ]", p):
                todo[p] = todo.get(p, 0) + n
    print(f"Gazetteer: {len(todo)} strings to resolve")

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

    def entry(q, kind, approx=None, fallback=""):
        d = info[q]
        e = {"name": d["label"] or fallback or q, "lat": d["coord"][0], "lon": d["coord"][1], "pid": q,
             "kind": kind}
        if approx:
            e["approx"] = approx
        return e

    def decide(s, extra=None):
        name, city = split_place(s)
        res = searches[s]
        city_q, city_pts = None, []
        if city:
            chits = res.get("city", [])
            city_q = city_choice(city, info, chits)
            city_pts = [info[h["id"]]["coord"] for h in chits
                        if h["id"] in info and info[h["id"]]["coord"] and is_settlement(info[h["id"]])
                        and not is_region(info[h["id"]]) and label_matches(city, h)]
            if city_q:
                city_pts.append(info[city_q]["coord"])
        if city and re.search(r"\)\s*$", s):
            # "Town (Region)": the town itself
            for h in res.get("name", []):
                d = info.get(h["id"])
                if d and d["coord"] and is_settlement(d) and not is_region(d) and label_matches(name, h) \
                        and not d["types"] & BAD_TYPES:
                    return entry(h["id"], "settlement")
        hits = res.get("full", []) + res.get("name", []) + [{"id": q, "x": 1} for q in (extra or [])]
        seen, exact, regions = set(), [], []
        for h in hits:
            q = h["id"]
            if q in seen:
                continue
            seen.add(q)
            d = info.get(q)
            if not d or not d["coord"] or d["types"] & BAD_TYPES:
                continue
            if is_region(d):
                if not h.get("x") and (label_matches(s, h) or (not city_q and label_matches(name, h))):
                    regions.append(h)
                continue
            settle = is_settlement(d)
            if h.get("x"):
                want, have = set(fold(name).split()), set(fold(d["label"]).split())
                if not want or len(want & have) / len(want) < 0.75:
                    continue
            if city_pts:
                if settle or q == city_q or min(km(d["coord"], p) for p in city_pts) > 30:
                    continue
                return entry(q, "site")
            if not h.get("x") and not (label_matches(s, h) or label_matches(name, h)):
                continue
            exact.append(h)

        def equal(h):
            lab = fold(info[h["id"]]["label"])
            return lab == fold(s) or lab == fold(name)
        ranked = sorted(
            [(0 if equal(h) and is_settlement(info[h["id"]]) else 2 if equal(h) else
              3 if is_settlement(info[h["id"]]) else 4, i, h, "x") for i, h in enumerate(exact)] +
            [(1 if equal(h) else 5, i, h, "region") for i, h in enumerate(regions)],
            key=lambda t: (t[0], t[1]))
        if ranked:
            top, _, h, what = ranked[0]
            if what == "region":
                if not city_q or top == 1:
                    return entry(h["id"], "region")
            else:
                same = [info[t[2]["id"]]["coord"] for t in ranked if t[0] == top and t[3] == "x"]
                big = top == 0 and info[h["id"]]["types"] & BIG_CITY  # "Berlin", not Berlin (New Hampshire)
                if big or max(km(same[0], p) for p in same) <= 30:
                    q = h["id"]
                    return entry(q, "settlement" if is_settlement(info[q]) else "site")
                if not city_q:
                    return None
        if city_q:
            return entry(city_q, "settlement", "city", city)
        return None

    for s in todo:
        e = decide(s)
        if e:
            gaz[s] = e

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

    left = sorted((s for s in todo if s not in gaz), key=lambda s: -todo[s])
    left = [s for s in left if todo[s] >= 2]
    print(f"  Nominatim for {len(left)} strings")
    for s in left:
        res = nominatim(s)
        if not res:
            continue
        r = res[0]
        cat, typ = r.get("category") or r.get("class"), r.get("type")
        rank = int(r.get("place_rank", 30))
        name = (r.get("name") or r.get("display_name", "").split(",")[0]).strip()
        want = {t for t in fold(s).split() if len(t) >= 3}
        if not want & set(fold(r.get("display_name", "") + " " + name).split()):
            continue
        if fold(name) in REGIONS and r.get("addresstype") not in ("country", "state", "region", "province"):
            continue  # a pub or street named "Italy", not the country
        e = {"name": name or s, "lat": round(float(r["lat"]), 6), "lon": round(float(r["lon"]), 6), "src": "osm"}
        if r.get("addresstype") in ("city", "town", "village", "municipality", "hamlet", "suburb"):
            e["kind"] = "settlement"
        elif rank < 12 or (cat == "place" and typ in ("country", "state", "region", "province", "county", "continent")):
            e["kind"] = "region"
        elif cat in ("place", "boundary"):
            e["kind"] = "settlement"
        else:
            e["kind"] = "site"
        gaz[s] = e

    # museums: by Wikidata class, or by name
    mus = museums_among({e["pid"] for e in gaz.values() if e.get("pid")})
    for s, e in gaz.items():
        if e["kind"] == "site":
            e["museum"] = bool(e.get("museum") or e.get("pid") in mus or MUSEUM_RE.search(e["name"])
                               or MUSEUM_RE.search(s))
    return gaz, todo


# --------------------------------------------------------------------------
# 4. Assignment (offline)

_maps = None


def _load_maps():
    global _maps
    if _maps is None:
        def load(n):
            p = OUT / n
            return json.loads(p.read_text()) if p.exists() else {}
        _maps = (load("wd-places.json"), load("gazetteer.json"), load("artists.json"), load("artist-names.json"))
    return _maps


def made_from_c(c, gz):
    """Place-made text -> ('made', entry) for a specific place, ('region', entry), or None."""
    c = (c or "").strip()
    if not c:
        return None
    best, found = None, []
    reg = region_of(c)
    for p in c_parts(c):
        if culture_only(p):
            continue
        g = gz.get(p)
        if g and reg and km((g["lat"], g["lon"]), (reg[1], reg[2])) > 1500:
            continue  # e.g. a same-named place on another continent
        if g and g.get("kind") in ("site", "settlement") and not g.get("museum"):
            found.append(g)
        if g and g.get("kind") == "region" and not best:
            best = g
    towns = [g for g in found if g["kind"] == "settlement"]
    for g in sorted(found, key=lambda g: g["kind"] != "site"):
        # a specific site wins over a town, if it lies near the town named in the same text
        if g["kind"] != "site" or not towns or min(km((g["lat"], g["lon"]), (t["lat"], t["lon"])) for t in towns) <= 50:
            return "made", g
    if reg:
        return "region", {"name": reg[0], "lat": reg[1], "lon": reg[2]}
    if best:
        return "region", best
    return None


def artist_place(qs, y, arts):
    """The artist rule: workplace whose dates bracket the year (else the nearest dated one within
    ten years, else an undated one, preferring the city where the artist died or was born), else
    place of death (work in the last 20 years of life), else place of birth (first 30 years)."""
    for q in qs or []:
        a = arts.get(q)
        if not a:
            continue
        ws = a.get("w") or []
        if ws:
            pick = None
            dated = [w for w in ws if w[3] is not None or w[4] is not None]
            if y is not None and dated:
                def gap(w):
                    s, e = w[3], w[4]
                    if (s is None or s <= y) and (e is None or y <= e):
                        return 0
                    return min(abs(y - x) for x in (s, e) if x is not None)
                best = min(dated, key=lambda w: (gap(w), w[3] or 0))
                if gap(best) <= 10:
                    pick = best
            if not pick:
                undated = [w for w in ws if w[3] is None and w[4] is None] or ws
                prefer = []
                if a.get("dp") and (y is None or a.get("d") is None or y >= a["d"] - 20):
                    prefer.append(a["dp"][3])
                if a.get("bp"):
                    prefer.append(a["bp"][3])
                if a.get("dp"):
                    prefer.append(a["dp"][3])
                pick = next((w for p in prefer for w in undated if w[5] == p), None)
                if not pick and y is not None:
                    # undated workplaces are an unordered set: the city of the artist's youth or old age, when
                    # the work falls in it, is the better guess
                    if a.get("dp") and a.get("d") is not None and a["d"] - 20 <= y <= a["d"] + 2:
                        return {"name": f"{a['dp'][0]} (artist's city)", "lat": a["dp"][1], "lon": a["dp"][2]}
                    if a.get("bp") and a.get("b") is not None and y <= a["b"] + 30:
                        return {"name": f"{a['bp'][0]} (artist's city)", "lat": a["bp"][1], "lon": a["bp"][2]}
                pick = pick or undated[0]
            return {"name": f"{pick[0]} (artist's workplace)", "lat": pick[1], "lon": pick[2]}
        if y is None:
            continue
        if a.get("dp") and a.get("d") is not None and a["d"] - 20 <= y <= a["d"] + 2:
            return {"name": f"{a['dp'][0]} (artist's city)", "lat": a["dp"][1], "lon": a["dp"][2]}
        if a.get("bp") and a.get("b") is not None and y <= a["b"] + 30:
            return {"name": f"{a['bp'][0]} (artist's city)", "lat": a["bp"][1], "lon": a["bp"][2]}
    return None


def fixed_type(w):
    k, m = w.get("k") or "", w.get("m") or ""
    if INSITU_M.search(m) or INSITU_M.search(k):
        return True
    if re.search(r"^(painting|print|drawing|manuscript|photograph|book|textile|ceramic|enamel|ivory|"
                 r"metalwork|coin|glass|other|work of art|panel|tondo|watercolor)", k, re.I) and \
            not re.search(r"stained|wall|altar", k, re.I):
        return False
    return bool(INSITU_K.search(k))


def current_site(w, wd, gz):
    """Current location as a non-museum site (for in-situ works), or None."""
    p = prefix(w)
    if p in MUSEUM_SOURCES:
        return None
    l = (w.get("l") or "").strip()
    e = (wd.get(w.get("q") or "") or {}).get("loc")
    if e:
        wall = bool(INSITU_M.search((w.get("k") or "") + " " + (w.get("m") or "")))
        if MUSEUM_RE.search(e["name"]) or (e.get("museum") and not wall) or MUSEUM_RE.search(l):
            return None
        return e
    if MUSEUM_RE.search(l):
        return None
    hits = [gz.get(part) for part in l_parts(l)]
    hits = [g for g in hits if g and g.get("kind") in ("site", "settlement") and not g.get("museum")]
    towns = [g for g in hits if g["kind"] == "settlement"]
    for g in hits:
        # a part resolved on its own must lie near the town named in the same text
        if not towns or g in towns or min(km((g["lat"], g["lon"]), (t["lat"], t["lon"])) for t in towns) <= 50:
            return g
    return None


def l_parts(l):
    """A location text and, for long 'Chapel, Church, Abbey, Town, Country' strings, its parts."""
    l = (l or "").strip()
    out = [l] if l else []
    core = re.sub(r"\s*\((in situ|replica[^)]*|now [^)]*|original[^)]*|MS [^)]*|inv\.[^)]*)\)\s*", " ", l, flags=re.I).strip()
    if core and core != l:
        out.append(core)
    parts = [p.strip() for p in core.split(",") if p.strip()]
    if len(parts) >= 3:
        out += [p for p in parts if not (fold(p) in REGIONS)]
    return list(dict.fromkeys(out))


def place_of(w):
    """(entry, precision) for one work, or (None, None). Uses only the cached map files."""
    wd, gz, arts, names = _load_maps()
    q = w.get("q") or ""
    e = wd.get(q) or {}
    y = w.get("y")
    region = None
    # 1. made
    m = e.get("made")
    if m and culture_only(m["name"]):
        m = dict(m, region=True)  # P1071 = "Franconia", "Flanders"...
    if m and not m.get("region"):
        return m, "made"
    if m:
        region = m
    if not m:
        r = made_from_c(w.get("c"), gz)
        if r and r[0] == "made":
            return r[1], "made"
        if r and not region:
            region = r[1]
    # 2. in situ
    if fixed_type(w):
        s = current_site(w, wd, gz)
        if s:
            return s, "in situ"
    # 3. artist
    qs = list(e.get("cr") or [])
    a = clean_artist(w.get("a")) if not qs else ""
    if a and names.get(a):
        qs.append(names[a])
    ap = artist_place(qs, y, arts)
    if ap:
        return ap, "artist"
    # 4. region
    if not region and e.get("origin"):
        o = e["origin"]
        r = region_of(o["name"])
        region = {"name": r[0], "lat": r[1], "lon": r[2]} if r else o
    if not region and q and w.get("c"):
        r = region_of(w["c"])
        if r:
            region = {"name": r[0], "lat": r[1], "lon": r[2]}
    if not region and w.get("a") and not qs and not clean_artist(w["a"]):
        r = region_of(w["a"])  # "German", "Anonymous, Flemish, 16th century"...
        if r:
            region = {"name": r[0], "lat": r[1], "lon": r[2]}
    if region:
        r = region_of(region["name"])
        if r:
            region = {"name": r[0], "lat": r[1], "lon": r[2]}
        return region, "region"
    return None, None


def assign(works):
    """Return (places, assignment): places = [[name, lat, lon, precision], ...] deduplicated by
    name + coordinates rounded to 4 decimals + precision; assignment maps work index -> place index or None."""
    places, index, assignment = [], {}, {}
    for i, w in enumerate(works):
        e, prec = place_of(w)
        if not e:
            assignment[i] = None
            continue
        lat, lon = round(e["lat"], 4), round(e["lon"], 4)
        k = (e["name"], lat, lon, prec)
        if k not in index:
            index[k] = len(places)
            places.append([e["name"], lat, lon, prec])
        assignment[i] = index[k]
    return places, assignment


# --------------------------------------------------------------------------

def dump(name, obj, indent=None):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=indent,
                                       separators=(",", ":") if indent is None else None))


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    works = load_works()
    sources = load_sources()
    print(f"{len(works)} works, {len(sources)} source records")

    wd = build_wd_places(works)
    dump("wd-places.json", wd)

    # location strings (for in-situ works) and place-made strings
    l_counts, c_counts = Counter(), Counter()
    for w in works:
        e = wd.get(w.get("q") or "") or {}
        if prefix(w) not in MUSEUM_SOURCES and not e.get("loc") and fixed_type(w):
            for part in l_parts(w.get("l")):
                l_counts[part] += 1
        if not e.get("made"):
            c = (w.get("c") or "").strip()
            if c:
                c_counts[c] += 1
    for r in sources:  # strings of records not (yet) in the collection: resolved too, at zero weight
        c = (r.get("c") or "").strip()
        if c:
            c_counts.setdefault(c, 0)
    gaz, todo = build_gazetteer(l_counts, c_counts)
    dump("gazetteer.json", gaz, indent=0)

    # artists: creators of Wikidata works and named artists of source records still unplaced
    global _maps
    _maps = (wd, gaz, {}, {})
    need = defaultdict(list)
    for w in works:
        e = wd.get(w.get("q") or "") or {}
        if e.get("cr"):
            continue
        _, prec = place_of(w)
        if prec in ("made", "in situ"):
            continue
        a = clean_artist(w.get("a"))
        if a:
            need[a].append(w.get("y"))
    print(f"Artist names to resolve: {len(need)}")
    names = resolve_artist_names(need)
    dump("artist-names.json", names, indent=0)
    creators = {c for e in wd.values() for c in e.get("cr", [])} | set(names.values())
    arts = build_artists(creators)
    dump("artists.json", arts)

    _maps = None
    places, assignment = assign(works)
    have = sum(1 for v in assignment.values() if v is not None)
    prec = Counter(places[v][3] if v is not None else "none" for v in assignment.values())
    per_src = defaultdict(Counter)
    for i, w in enumerate(works):
        v = assignment[i]
        per_src[prefix(w) or "wikidata"][places[v][3] if v is not None else "none"] += 1
    unresolved_c = Counter()
    for i, w in enumerate(works):
        if assignment[i] is None:
            unresolved_c[(w.get("c") or "").strip() or "(no place made)"] += 1
    lines = [
        f"# Generated by scripts/geocode.py on {time.strftime('%Y-%m-%d')}",
        f"# works with a place: {have}/{len(works)} ({100 * have / max(1, len(works)):.1f}%); "
        f"distinct places: {len(places)}",
        "# by precision: " + ", ".join(f"{k} {n} ({100 * n / len(works):.1f}%)" for k, n in prec.most_common()),
    ]
    for k, c in sorted(per_src.items()):
        lines.append(f"#   {k}: " + ", ".join(f"{p} {n}" for p, n in c.most_common()))
    lines.append(f"# gazetteer {len(gaz)} strings; artists {len(arts)}; artist names resolved {len(names)}/{len(need)}")
    lines.append("# count\tplace-made text of works without a place")
    lines += [f"{n}\t{s}" for s, n in unresolved_c.most_common(300)]
    lines += ["", "# location / place-made strings that could not be resolved (weight)"]
    lines += [f"{todo[s]}\t{s}" for s in sorted((s for s in todo if s not in gaz), key=lambda s: -todo[s])]
    lines += ["", "# artist names that could not be resolved (works)"]
    lines += [f"{len(need[a])}\t{a}" for a in sorted((a for a in need if a not in names), key=lambda a: -len(need[a]))]
    (OUT / "unresolved.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[1:3 + len(per_src) + 2]))
    counts = Counter(v for v in assignment.values() if v is not None)
    print("Top places:")
    for p, n in counts.most_common(25):
        print(f"  {n:6d}  {places[p][0]} [{places[p][3]}] ({places[p][1]}, {places[p][2]})")


if __name__ == "__main__":
    main()
