#!/usr/bin/env python3
"""Harvest public-domain depictions of Christ from The Met's Open Access API.

Uses the paginated /v1.1/search endpoint (the old /v1/search was retired on
2026-10-01) with many Christ-related queries, fetches every matching object
from /v1/objects/{id} (raw JSON cached under .cache/met/), keeps public-domain
objects with an image whose title (or Christ-related tags) classify as
depicting Christ, and writes data/sources/met.json.

Stdlib only. Rate limited to < 10 requests/second.

Usage: python3 scripts/sources/met.py [--workers N] [--refresh-search]
"""
import argparse
import http.cookiejar
import json
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subjects import classify, EXCLUDE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / ".cache" / "met"
OBJ_CACHE = CACHE / "objects"
SEARCH_CACHE = CACHE / "search"
OUT = ROOT / "data" / "sources" / "met.json"

API = "https://collectionapi.metmuseum.org/public/collection"
SEARCH_URL = API + "/v1.1/search"
OBJECT_URL = API + "/v1/objects/{}"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
PAGE = 500  # max page size accepted by v1.1/search
MAX_RPS = 1.0  # the Met's WAF (Imperva) 403-blocks this client after ~100 requests even at 2.5 rps

QUERIES = [
    "Christ", "Jesus", "Christus", "Cristo", "Saviour", "Savior", "Redeemer",
    "Madonna", "Virgin and Child", "Madonna and Child", "Virgin and Infant",
    "Mother of God", "Theotokos", "Hodegetria", "Eleousa", "Virgin Mary",
    "Crucifixion", "Crucifix", "Crucified", "Corpus", "Pietà", "Pieta",
    "Nativity", "Holy Family", "Adoration", "Adoration of the Magi",
    "Adoration of the Shepherds", "Magi", "Three Kings", "Epiphany",
    "Circumcision", "Presentation in the Temple", "Flight into Egypt",
    "Rest on the Flight", "Massacre of the Innocents", "Christ among the Doctors",
    "Lamentation", "Entombment", "Deposition", "Descent from the Cross",
    "Resurrection", "Last Supper", "Ecce Homo", "Man of Sorrows", "Calvary",
    "Golgotha", "Passion", "Transfiguration", "Ascension", "Baptism",
    "Baptism of Christ", "Last Judgment", "Emmaus", "Gethsemane",
    "Agony in the Garden", "Flagellation", "Crowning with Thorns",
    "Crown of Thorns", "Carrying the Cross", "Bearing the Cross",
    "Road to Calvary", "Stations of the Cross", "Salvator", "Salvator Mundi",
    "Pantocrator", "Christ in Majesty", "Deesis", "Icon", "Sacred Heart",
    "Holy Face", "Mandylion", "Infant", "Christ Child", "Infant Christ",
    "Bambino", "Santo Niño", "Trinity", "Holy Trinity", "Throne of Mercy",
    "Veronica", "Sudarium", "Good Shepherd", "Lazarus", "Cana",
    "Magdalene", "Noli me tangere", "Doubting Thomas", "Incredulity",
    "Apostles", "Disciples", "Saint Peter", "Saint John", "Judas",
    "Betrayal", "Caiaphas", "Mocking", "Harrowing of Hell",
    "Limbo", "Anastasis", "Holy Women at the Tomb", "Mystic Marriage",
    "Saint Catherine", "Saint Anne", "Saint Joseph", "Saint Christopher",
    "Sermon on the Mount", "Temptation", "Entry into Jerusalem",
    "Palm Sunday", "Money Changers", "Woman taken in Adultery", "Samaritan",
    "Tribute Money", "Miraculous Draught", "Walking on the Water",
    "Loaves and Fishes", "Feeding of the Five Thousand", "Healing",
    "Washing of the Feet", "Communion of the Apostles", "Lamb of God",
    "Agnus Dei", "IHS", "Our Lord", "Sacra Conversazione",
    "Enthroned", "Maestà", "Coronation of the Virgin", "Rosary",
    "Altarpiece", "Triptych", "Diptych", "Retable", "Reliquary",
    "Book of Hours", "Gospel", "Gospels", "Evangelist", "Bible",
    "Psalter", "Missal", "Antiphonary", "Gradual", "Choir book",
    "Holy Kinship", "Holy Sepulchre", "Sepulchre", "Tomb of Christ",
    "Passion of Christ", "Life of Christ", "Life of the Virgin",
    "Seven Sorrows", "Mater Dolorosa", "Stabat Mater", "Christ Blessing",
    "Blessing", "Benediction", "Christ the King", "Cristo crucificado",
    "Virgen", "Vierge", "Jésus", "Gesù", "Christi", "Christo",
    "Ethiopian icon", "Byzantine", "Coptic", "Russian icon", "Greek icon",
    "Santos", "Retablo", "Christ Child doll", "Pax", "Processional cross",
    "Altar cross", "Plaquette", "Ivory diptych", "Limoges",
    "Stained glass", "Annunciation to the Shepherds",
    "Jesse", "Tree of Jesse", "Prodigal Son",
    "Parable", "Feast in the House of Simon", "Mary and Martha",
    "Christ and the Woman", "Suffer the Little Children", "Holy Night",
    "Manger", "Crèche", "Presepio", "Hours of the Cross", "Arma Christi",
    "Instruments of the Passion", "Five Wounds", "Mass of Saint Gregory",
    "Vision", "Majesty", "Apocalypse", "Revelation", "Lamb",
    "Corpus Christi", "Christ Crucified", "Dead Christ", "Risen Christ",
    "Head of Christ", "Virgin", "Mary", "Maria", "Notre Dame", "Our Lady",
    "Saint with the Christ Child", "Saint with the Infant Christ",
]
# Note: very generic title words (Saint, Cross, Child, Lady, Lord, Holy, ...)
# add ~11k extra objects that almost never pass classify(); they are skipped.

# Searched with tags=true (the Met tags subjects, e.g. {"term": "Jesus"}).
TAG_QUERIES = ["Jesus", "Christ", "Madonna", "Madonna and Child", "Virgin Mary",
               "Crucifixion", "Crucifix", "Nativity", "Holy Family", "Pietà",
               "Trinity", "Last Supper", "Resurrection", "Adoration"]

# Keep the CDN's session cookies like a normal HTTP client would.
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
_lock = threading.Lock()
_next_slot = [0.0]
_blocked = {"until": 0.0, "strikes": 0}


def _cooldown():
    """Called on 403/429: pause ALL workers, with growing cool-down."""
    with _lock:
        now = time.monotonic()
        if now < _blocked["until"]:
            return
        _blocked["strikes"] += 1
        wait = min(600, 90 * _blocked["strikes"])
        _blocked["until"] = now + wait
        print(f"  blocked (403/429); pausing all requests {wait}s", file=sys.stderr, flush=True)


def _ok():
    """A success well after the last block resets the cool-down escalation."""
    with _lock:
        if _blocked["strikes"] and time.monotonic() > _blocked["until"] + 300:
            _blocked["strikes"] = 0


def _throttle():
    with _lock:
        now = time.monotonic()
        slot = max(now, _next_slot[0], _blocked["until"])
        _next_slot[0] = slot + 1.0 / MAX_RPS
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def get_json(url, tries=12):
    """GET url as JSON. Returns None on 404. Retries with backoff on 403/429/5xx."""
    last = None
    for attempt in range(tries):
        _throttle()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with _opener.open(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
            _ok()
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
            if e.code in (403, 429):
                _cooldown()
                continue
            if e.code < 500:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            last = e
        wait = min(120, 2 ** attempt + random.random())
        print(f"  retry {attempt + 1} for {url} in {wait:.1f}s ({last})", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f"giving up on {url}: {last}")


def search(q, field="title", refresh=False):
    """Search one field ("title" or "tags"). Multi-word queries are sent as a
    quoted phrase; unquoted, v1.1 ORs the words and returns huge result sets."""
    SEARCH_CACHE.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^a-z0-9]+", "_", q.lower()).strip("_") or "q"
    path = SEARCH_CACHE / f"{field}_{key}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    ids, offset, total = [], 0, None
    while True:
        qq = f'"{q}"' if " " in q else q
        params = urllib.parse.urlencode({"q": qq, field: "true", "hasImages": "true", "offset": offset, "limit": PAGE})
        d = get_json(f"{SEARCH_URL}?{params}") or {}
        total = d.get("total") or 0
        batch = d.get("objectIDs") or []
        ids.extend(batch)
        offset += PAGE
        if not batch or offset >= total:
            break
    ids = sorted(set(ids))
    path.write_text(json.dumps({"q": q, "total": total, "objectIDs": ids}))
    return {"q": q, "total": total, "objectIDs": ids}


def fetch_object(oid):
    path = OBJ_CACHE / f"{oid}.json"
    if path.exists():
        return json.loads(path.read_text())
    d = get_json(OBJECT_URL.format(oid))
    if d is None:
        d = {"objectID": oid, "_missing": True}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    tmp.replace(path)
    return d


# Tags that by themselves mean Christ appears in the work.
CHRIST_TAG = re.compile(r"\b(jesus|christ|madonna|crucifixion|crucifix|pieta|pietà|nativity|holy family|man of sorrows|ecce homo|lamentation|entombment|deposition|resurrection|last supper|adoration|last judgment|transfiguration|ascension|trinity)\b", re.I)

KIND_MAP = [
    ("manuscript", r"manuscript|illuminat|codices|book of hours|leaf|folio|miniature"),
    ("icon", r"\bicons?\b"),
    ("painting", r"painting|fresco|panel"),
    ("print", r"print|engraving|etching|woodcut|lithograph|mezzotint|ephemera"),
    ("drawing", r"drawing|sketch"),
    ("photograph", r"photograph"),
    ("book", r"\bbooks?\b|bound volume|album"),
    ("sculpture", r"sculpt|statue|statuette|relief|figure|plaquette|corpus|bust|wood.?carving|stone"),
    ("ivory", r"ivor|bone"),
    ("enamel", r"enamel"),
    ("glass", r"glass"),
    ("ceramic", r"ceramic|porcelain|maiolica|faience|earthenware|stoneware|pottery|terracotta"),
    ("textile", r"textile|tapestr|embroider|vestment|lace|cope|chasuble|costume|rug|carpet"),
    ("metalwork", r"metal|gold|silver|bronze|brass|copper|jewel|medal|coin|pendant|cross|reliquar|pax|chalice|plaque|badge"),
]


def kind_of(obj):
    cls = (obj.get("classification") or "").lower()
    name = (obj.get("objectName") or "").lower()
    for text in (cls, name):
        if not text:
            continue
        for k, rx in KIND_MAP:
            if re.search(rx, text):
                return k
    return "other"


def year_of(obj):
    y = _year_of(obj)
    # Some records store the century number itself ("15th century" -> 15).
    m = re.search(r"\b(\d{1,2})(st|nd|rd|th)[\s–-]*(century|c\.)", obj.get("objectDate") or "", re.I)
    if m and y is not None and 0 < y < 100 and "B.C" not in (obj.get("objectDate") or ""):
        lo = (int(m.group(1)) - 1) * 100
        if not (lo - 100 <= y <= lo + 200):
            return lo + 50
    return y


def _year_of(obj):
    b, e = obj.get("objectBeginDate"), obj.get("objectEndDate")
    if not isinstance(b, int) and not isinstance(e, int):
        return None
    if b == 0 and e == 0 and not (obj.get("objectDate") or "").strip():
        return None
    if isinstance(b, int) and isinstance(e, int):
        if e >= b and e - b <= 60:
            return (b + e) // 2
        return b
    return b if isinstance(b, int) else e


def place_of(obj):
    city, state, country, region, culture = (
        (obj.get(k) or "").strip() for k in ("city", "state", "country", "region", "culture"))
    parts = []
    for p in (city, state, country):
        if p and p not in parts:
            parts.append(p)
    if not parts and region:
        parts.append(region)
    if parts:
        return ", ".join(parts)
    return culture


def school_of(obj):
    for k in ("period", "dynasty"):
        v = (obj.get(k) or "").strip()
        if v:
            return v
    nat = (obj.get("artistNationality") or "").strip()
    if nat:
        return nat.split("|")[0].strip()
    return ""


# ---- Met-specific title filtering -------------------------------------------
# Phrases removed from a title before classify(): names, churches, festivals
# etc. that contain Christ-words but do not depict him.
STRIP = re.compile("|".join([
    r"\bsalvator rosa\b", r"\b(st\.?|saint|san|college church of st\.?) salvator\b",
    r"\bchurch of (the )?(holy )?(saviou?r|redeemer|ascension|holy trinity|trinity)\b",
    r"\b(il|the) ges[uù]\b", r"\bges[uù] (nuovo|e maria)\b",
    r"\b(compagnie|societ[ée]|society|societate|societas) (de |of )?j[eé]sus?\b|\bsocietate jesu\b",
    r"\b(teresa|theresa|th[ée]r[èe]se|felipe|juan|mar[ií]a|marie|anne?) (of|de|del) (the )?(child )?j[eé]s[uú]s\b",
    r"\bj[eé]s[uú]s (bruno|negrete|mart[ií]nez|garc[ií]a)\b",
    r"\bmonte cristo\b", r"\bantichrist\b", r"\bfestival of the redeemer\b",
    r"\bworld'?s decorations\b.*", r"\btrinity (college|church|chapel|bay|mountains?|guild|house)\b",
    r"\bballoon ascension\b", r"\bascension day\b", r"\b(fiery )?ascension of (a |saint |st\.? )\w+",
    r"\belias'?s fiery ascension\b", r"\bbritish calvary\b", r"\bcalvary combat\b",
    r"\bcourt deposition\b", r"\bdeposition/record\b",
    r"\b(celebration of a|instruments? for|box for) circumcision\b|\bcircumcision instruments\b",
    r"\bmadonna (del(la|l')?|di) (popolo|salute|orto|sasso)\b",
    r"\b(spanish|viva la) madonna\b|\bmadonna (dress|cape)\b|\bbeech tree of the madonna\b",
    r"\bwith the madonna, pointing\b",
]), re.I)
# Titles that never depict Christ even if a Christ-word survives.
MET_FALSE = re.compile(r"\b(buddh\w*|bodhisattva|tara|avalokiteshvara|amitayus|amitabha|guanyin|kannon|hindu|vishnu|krishna|shiva|cuneiform)\b|\bfrom b\. christ\b|\bw\.\s?b\. christ\b|\bwhere our lord\b|^\[?garden of gethsemane\b|\bcr[eè]che (accessory|figures)\b|\bregardant la cr[eè]che\b|\btemple dedicated to the trinity\b", re.I)
# Only a monogram / bare symbol, not a depiction.
ONLY_SYMBOL = re.compile(r"\bihs\b", re.I)
# A single figure from a group (crèche, Calvary, Deposition...) — keep only if it is Christ.
GROUP_FIGURE = re.compile(r"\bfrom (an? |the )?(nativity|cr[eè]che|presepio|calvary|crucifixion|deposition|lamentation|entombment)( group| scene| set)?\b", re.I)
IS_CHRIST = re.compile(r"\b(child|infant|christ|jesus|bambino|ni[nñ]o|corpus|crucifix|saviou?r)\b", re.I)
# Bare "Madonna" in a title that is clearly the Virgin alone (mourning etc.).
MADONNA_ALONE = re.compile(r"\b(mourning|weeping|dolorosa|mercy|misericordia|penserosa|riposata|head of the madonna|kneeling madonna)\b", re.I)
# "Mary ... son" that is a queen/empress with her son, not the Virgin.
NOT_THE_VIRGIN = re.compile(r"\b(queen of scots|empress|duchess|countess|princess|marquise)\b", re.I)
# Photographs of Holy Land sites / expression studies, not depictions.
PHOTO_FALSE = re.compile(r"\bstation\b|^arcade de l'ecce homo|\bmount of transfiguration\b|^figure \d+:", re.I)
# Tag fallback is unreliable for these (e.g. "Crucifixion" tag on a saint's martyrdom).
NO_TAG_FALLBACK = re.compile(r"\bmartyrdom\b|\bcrucifixion of (st|saint|san)\b", re.I)
# Series clause: `, from "Series"` / `from 'Series'` / `from the series ...`.
SERIES = re.compile(r"""[,;]?\s*\bfrom\s+(the\s+series\s+)?(["'“‘]|(?=christ\b.*apostles)|(?=les grands)).*$""", re.I)
PASSION_SERIES = re.compile(r"\bpassion\b|\blife of (christ|jesus|our lord)\b|\bvita christi\b|\bpassionis christi\b|\bpassio\b", re.I)
APOSTLE_SERIES = re.compile(r"apost|ap[oô]tres|sauveur", re.I)


def classify_title(title):
    t = STRIP.sub(" ", title)
    if MET_FALSE.search(t):
        return []
    u = classify(t)
    if u == ["Jesus Christ"] and ONLY_SYMBOL.search(t) and not classify(ONLY_SYMBOL.sub(" ", t)):
        return []
    if u and GROUP_FIGURE.search(t) and not IS_CHRIST.search(GROUP_FIGURE.sub(" ", t)):
        return []
    if u == ["Madonna and Child"] and MADONNA_ALONE.search(t) and not IS_CHRIST.search(t):
        return []
    if u == ["Madonna and Child"] and NOT_THE_VIRGIN.search(t):
        return []
    m = SERIES.search(t)
    if not u and m and PASSION_SERIES.search(m.group(0)) and not EXCLUDE.search(t) \
            and not re.search(r"\b(judas|peter|virgin|vierge|mary|marie|john|jean|magdalen\w*|adam|eve|paradise|fall of man)\b", t[:m.start()], re.I):
        # A plate from a Passion / Life of Christ series with a generic or
        # foreign-language title ("La Condamnation a Mort", "Plate 5").
        return ["Jesus Christ"]
    if u and m:
        head = t[:m.start()]
        hu = classify(head)
        if hu:
            return hu
        # Christ is only named in the series title. Prints from an apostles
        # series ("Christ and the Apostles") mostly show a single apostle.
        if APOSTLE_SERIES.search(m.group(0)):
            return []
    return u


def subjects_for(obj):
    title = (obj.get("title") or "").strip()
    if kind_of(obj) == "photograph" and PHOTO_FALSE.search(title):
        return [], None
    u = classify_title(title)
    if u:
        return u, "title"
    if EXCLUDE.search(title) or MET_FALSE.search(title) or NO_TAG_FALLBACK.search(title) or STRIP.search(title):
        return [], None
    m = SERIES.search(title)
    if m and APOSTLE_SERIES.search(m.group(0)):
        return [], None
    terms = [t.get("term", "") for t in (obj.get("tags") or []) if isinstance(t, dict)]
    good = [t for t in terms if CHRIST_TAG.search(t)]
    if not good:
        return [], None
    u = classify(STRIP.sub(" ", title), " ".join(good))
    return u, ("tags" if u else None)


def record(obj, u):
    oid = obj["objectID"]
    rec = {
        "id": f"met:{oid}",
        "t": (obj.get("title") or "").strip(),
        "y": year_of(obj),
        "d": (obj.get("objectDate") or "").strip(),
        "a": (obj.get("artistDisplayName") or "").strip(),
        "c": place_of(obj),
        "l": "The Metropolitan Museum of Art",
        "m": (obj.get("medium") or "").strip(),
        "s": school_of(obj),
        "k": kind_of(obj),
        "img": obj.get("primaryImageSmall") or obj.get("primaryImage"),
        "big": obj.get("primaryImage"),
        "src": obj.get("objectURL") or f"https://www.metmuseum.org/art/collection/search/{oid}",
        "lic": "CC0",
        "u": u,
    }
    m = re.search(r"(Q\d+)", obj.get("objectWikidata_URL") or "")
    if m:
        rec["wd"] = m.group(1)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--rps", type=float, default=1.0)
    ap.add_argument("--cached-only", action="store_true",
                    help="no object fetches; build output from whatever is cached")
    ap.add_argument("--refresh-search", action="store_true")
    args = ap.parse_args()
    global MAX_RPS
    MAX_RPS = min(args.rps, 9.0)
    OBJ_CACHE.mkdir(parents=True, exist_ok=True)

    ids = {}  # insertion-ordered: IDs from the most specific queries are fetched first
    jobs = [(q, "title") for q in QUERIES] + [(q, "tags") for q in TAG_QUERIES]
    for i, (q, field) in enumerate(jobs, 1):
        res = search(q, field, args.refresh_search)
        new = [i for i in res["objectIDs"] if i not in ids]
        ids.update(dict.fromkeys(new))
        print(f"[{i}/{len(jobs)}] {field}:{q!r}: {len(res['objectIDs'])} hits, +{len(new)} new (union {len(ids)})", flush=True)

    ids = list(ids)
    objs, failed = {}, []
    if args.cached_only:
        for oid in ids:
            path = OBJ_CACHE / f"{oid}.json"
            if path.exists():
                objs[oid] = json.loads(path.read_text())
        print(f"cached-only: {len(objs)}/{len(ids)} objects in cache", flush=True)
        ids = [i for i in ids if i in objs]
    print(f"Fetching {len(ids)} objects ...", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_object, oid): oid for oid in ids if oid not in objs}
        for n, f in enumerate(as_completed(futs), 1):
            oid = futs[f]
            try:
                objs[oid] = f.result()
            except Exception as e:  # noqa: BLE001
                failed.append(oid)
                print(f"  failed {oid}: {e}", file=sys.stderr)
            if n % 500 == 0:
                print(f"  {n}/{len(ids)}", flush=True)

    drops = Counter()
    via = Counter()
    out = []
    for oid in sorted(ids):
        obj = objs.get(oid)
        if obj is None:
            drops["fetch failed"] += 1
            continue
        if obj.get("_missing"):
            drops["object 404"] += 1
            continue
        if not obj.get("isPublicDomain"):
            drops["not public domain"] += 1
            continue
        if not obj.get("primaryImage"):
            drops["no primary image"] += 1
            continue
        u, how = subjects_for(obj)
        if not u:
            drops["not classified as Christ"] += 1
            continue
        via[how] += 1
        out.append(record(obj, u))

    out.sort(key=lambda r: (r["y"] is None, r["y"] if r["y"] is not None else 0, r["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))

    print("\n=== Met summary ===")
    print(f"queries: {len(QUERIES)} title + {len(TAG_QUERIES)} tags; unique searched IDs: {len(ids)}")
    print(f"kept: {len(out)}  (via title {via['title']}, via tags {via['tags']})")
    for reason, c in drops.most_common():
        print(f"dropped – {reason}: {c}")
    print("by kind:", dict(Counter(r["k"] for r in out).most_common()))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
