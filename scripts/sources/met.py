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
MAX_RPS = 8.0

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
    "Betrayal", "Pilate", "Caiaphas", "Mocking", "Harrowing of Hell",
    "Limbo", "Anastasis", "Holy Women at the Tomb", "Mystic Marriage",
    "Saint Catherine", "Saint Anne", "Saint Joseph", "Saint Christopher",
    "Sermon on the Mount", "Temptation", "Entry into Jerusalem",
    "Palm Sunday", "Money Changers", "Woman taken in Adultery", "Samaritan",
    "Tribute Money", "Miraculous Draught", "Walking on the Water",
    "Loaves and Fishes", "Feeding of the Five Thousand", "Healing",
    "Washing of the Feet", "Communion of the Apostles", "Lamb of God",
    "Agnus Dei", "IHS", "Our Lord", "Lord", "Sacra Conversazione",
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
    "Altar cross", "Cross", "Plaquette", "Ivory diptych", "Limoges",
    "Stained glass", "Annunciation to the Shepherds", "Shepherds",
    "Christmas", "Jesse", "Tree of Jesse", "Prodigal Son",
    "Parable", "Feast in the House of Simon", "Mary and Martha",
    "Christ and the Woman", "Suffer the Little Children", "Holy Night",
    "Manger", "Crèche", "Presepio", "Hours of the Cross", "Arma Christi",
    "Instruments of the Passion", "Five Wounds", "Mass of Saint Gregory",
    "Vision", "Majesty", "Apocalypse", "Revelation", "Lamb",
    "Corpus Christi", "Christ Crucified", "Dead Christ", "Risen Christ",
    "Head of Christ", "Virgin", "Mary", "Maria", "Notre Dame", "Our Lady",
    "Lady", "Child", "Saint", "Saints", "Holy", "Sainte", "San", "Santa",
]

# Searched with tags=true (the Met tags subjects, e.g. {"term": "Jesus"}).
TAG_QUERIES = ["Jesus", "Christ", "Madonna", "Madonna and Child", "Virgin Mary",
               "Crucifixion", "Crucifix", "Nativity", "Holy Family", "Pietà",
               "Trinity", "Last Supper", "Resurrection", "Adoration"]

_lock = threading.Lock()
_next_slot = [0.0]


def _throttle():
    with _lock:
        now = time.monotonic()
        slot = max(now, _next_slot[0])
        _next_slot[0] = slot + 1.0 / MAX_RPS
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def get_json(url, tries=7):
    """GET url as JSON. Returns None on 404. Retries with backoff on 403/429/5xx."""
    last = None
    for attempt in range(tries):
        _throttle()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
            if e.code not in (403, 429) and e.code < 500:
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


def subjects_for(obj):
    title = (obj.get("title") or "").strip()
    u = classify(title)
    if u:
        return u, "title"
    if EXCLUDE.search(title):
        return [], None
    terms = [t.get("term", "") for t in (obj.get("tags") or []) if isinstance(t, dict)]
    good = [t for t in terms if CHRIST_TAG.search(t)]
    if not good:
        return [], None
    u = classify(title, " ".join(good))
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
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--refresh-search", action="store_true")
    args = ap.parse_args()
    OBJ_CACHE.mkdir(parents=True, exist_ok=True)

    ids = set()
    jobs = [(q, "title") for q in QUERIES] + [(q, "tags") for q in TAG_QUERIES]
    for i, (q, field) in enumerate(jobs, 1):
        res = search(q, field, args.refresh_search)
        new = set(res["objectIDs"]) - ids
        ids |= new
        print(f"[{i}/{len(jobs)}] {field}:{q!r}: {len(res['objectIDs'])} hits, +{len(new)} new (union {len(ids)})", flush=True)

    ids = sorted(ids)
    print(f"Fetching {len(ids)} objects ...", flush=True)
    objs, failed = {}, []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_object, oid): oid for oid in ids}
        for n, f in enumerate(as_completed(futs), 1):
            oid = futs[f]
            try:
                objs[oid] = f.result()
            except Exception as e:  # noqa: BLE001
                failed.append(oid)
                print(f"  failed {oid}: {e}", file=sys.stderr)
            if n % 1000 == 0:
                print(f"  {n}/{len(ids)}", flush=True)

    drops = Counter()
    via = Counter()
    out = []
    for oid in ids:
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
