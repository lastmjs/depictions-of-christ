#!/usr/bin/env python3
"""Harvest depictions of Christ from the Victoria and Albert Museum API (v2, keyless).

1. Runs many keyword searches (images_exist=1) plus searches on the V&A's
   depicted-person identifiers for Christ (id_person=N1515 "Jesus Christ", ...).
2. Keeps candidates whose title passes subjects.classify plus the shared
   filters in aic.py, that are tagged as depicting Christ, or that are untitled
   (their brief description is classified instead), and fetches each full record.
3. Keeps objects made before ~1900 (so the artwork itself is out of copyright)
   whose image is credited to the V&A itself (not a third-party rights holder).

V&A images are free for non-commercial use with credit ("© Victoria and Albert
Museum, London"), so records carry lic "V&A non-commercial" and a "cr" credit.
Images use the V&A IIIF image server. Raw API responses are cached under
.cache/vam/. Writes data/sources/vam.json. Stdlib only.
"""
import hashlib
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from subjects import classify  # noqa: E402,F401
from aic import QUERIES as AIC_QUERIES, NOT_CHRIST, decide  # noqa: E402  (shared filters)

CACHE = ROOT / ".cache" / "vam"
OUT = ROOT / "data" / "sources" / "vam.json"
API = "https://api.vam.ac.uk/v2"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
MIN_INTERVAL = 0.25  # between request starts, all workers combined
WORKERS = 3
PAGE = 100
MAX_RECORDS = 10000  # deepest reachable result for one search
LATEST_YEAR = 1900   # objects made later may still be in copyright

# V&A depicted-person ids for Christ (from the person cluster of Christ searches).
CHRIST_PEOPLE = {
    "N1515": "Jesus Christ", "N2525": "Jesus Christ", "N1461": "Christ", "N58": "Jesus",
    "N4624": "Christ Child", "N1185": "Christ Child", "N4691": "Virgin and Child",
}

QUERIES = list(dict.fromkeys(AIC_QUERIES + [
    "Crucified", "Corpus", "Infant Christ", "Child Jesus", "Christ Child", "Infant Jesus",
    "Hodegetria", "Eleousa", "Mandylion", "Vernicle", "Arma Christi", "Instruments of the Passion",
    "Christ Blessing", "Life of Christ", "Life of the Virgin", "Psalter", "Missal", "Book of Hours",
    "Pyx", "Reliquary", "Pax", "Processional Cross", "Altar Cross", "Chasse", "Orphrey",
    "Chasuble", "Cope", "Stained glass", "Rood", "Calvary group", "Tympanum", "Saviour", "Salvator",
    "Ecce Homo", "Vir dolorum", "Man of Sorrows", "Pieta", "Vesperbild", "Nativity", "Crib",
    "Presepio", "Bambino", "Agnus Dei", "Majesty", "Ascension", "Resurrection", "Entombment",
    "Lamentation", "Deposition", "Last Supper", "Passion", "Stations of the Cross",
]))
# Very broad terms: only their title matches are worth the full-record fetch.
BROAD = {"Virgin", "Mary", "Our Lady", "icon", "Bible", "Gospel", "Evangelist", "Apostles",
         "Disciples", "Altarpiece", "Triptych", "Diptych", "Book of Hours", "Christian subjects",
         "New Testament", "Psalter", "Missal", "Pyx", "Reliquary", "Pax", "Chasse", "Orphrey",
         "Chasuble", "Cope", "Stained glass", "Tympanum", "Majesty", "Saint Anne", "Shepherds",
         "Infant", "Adoration", "Magi", "Doctors", "Mocking", "Betrayal", "Judas", "Pilate",
         "Samaritan", "Adulteress", "Prodigal", "Lazarus", "Cana", "Limbo", "Holy Women",
         "Three Marys", "Saint Christopher", "Altar Cross", "Processional Cross", "Corpus", "Crib",
         "Passion", "Trinity", "Veronica", "Our Lord", "Gospel", "Theotokos", "Mother of God"}

_last = [0.0]
_rate = threading.Lock()


def get(path, params=None):
    url = f"{API}/{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    key = hashlib.sha1(url.encode()).hexdigest()
    cpath = CACHE / key[:2] / f"{key}.json"
    if cpath.exists():
        return json.loads(cpath.read_text())
    delay = 2.0
    for attempt in range(8):
        with _rate:
            wait = MIN_INTERVAL - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read().decode())
            cpath.parent.mkdir(parents=True, exist_ok=True)
            cpath.write_text(json.dumps(data, ensure_ascii=False))
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
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


def search(params):
    page = 1
    while True:
        data = get("objects/search", dict(params, images_exist=1, page_size=PAGE, page=page)) or {}
        rows = data.get("records") or []
        yield from rows
        total = (data.get("info") or {}).get("record_count", 0)
        if not rows or page * PAGE >= min(total, MAX_RECORDS):
            if total > MAX_RECORDS:
                print(f"  ! {params} has {total} hits; only first {MAX_RECORDS} reachable", file=sys.stderr)
            break
        page += 1


def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


GENERIC_TITLE = re.compile(
    r"^(the )?(plaque|panel|pendant|medallion|pax|pyx|chasse|reliquary|cross|processional cross|altar cross|"
    r"book cover|cover|leaf|single leaf|folio|initial|historiated initial|orphrey|chasuble|cope|dalmatic|"
    r"altar frontal|textile|fragment|roundel|tile|window|stained glass|icon|triptych|diptych|altarpiece|"
    r"retable|polyptych|ring|badge|medal|token|seal|bowl|plate|dish|beaker|chalice|paten|ewer|box|casket|"
    r"comb|mirror case|crozier|crosier|cameo|intaglio|amulet|ampulla|lamp|vessel|jug|tankard|cup|binding|"
    r"relief|figure|statuette|statue|group|carving|print|drawing|painting|miniature|cutting|"
    r"manuscript cutting|page|sheet)s?\b", re.I)
BAD_TYPES = re.compile(r"theatre|costume|poster|programme|playbill|ticket|toy|doll|puppet|record|"
                       r"greeting card|christmas card|postcard|photograph album|lock|key|keyhole", re.I)
BRIEF_NOT = re.compile(r"\b(at|before|holding|with|contemplating|adoring|kneeling before|venerating) (a|the) "
                       r"(crucifix|cross)\b|\bmary magdalen\w* (in|standing|seated|kneeling)\b|"
                       r"\btormentors? of christ\b|\brosa,? salvator\b|\bchrist church\b|\blamentation for\b|\bmusic score\b", re.I)
# A brief description must name a Christ subject outright to be used.
BRIEF_NEEDS = re.compile(r"\b(christ|jesus|virgin and child|virgin child|madonna|crucifix\w*|crucified|piet[aà]|"
                         r"nativity|agnus dei|lamb of god|man of sorrows|holy family|ecce homo|entombment|"
                         r"lamentation|deposition|resurrection|ascension|last supper|last judg\w*|trinity|"
                         r"adoration of the (magi|shepherds)|flight into egypt)\b", re.I)
ABBREV = re.compile(r"(\b[A-Z]|\b(St|ca|Ca|c|Mr|Dr|no|No|fl|Fr|Ste|Sta|Ltd|Co))$")
MONOGRAM_ONLY = re.compile(r"\b(monogram|ihs|i\.h\.s|chi-?rho|sacred monogram)\b", re.I)
FIGURATIVE = re.compile(r"\b(figure|christ|virgin|madonna|crucifix|crucifixion|scene|depicting|showing|"
                        r"child|infant|pieta|pietà|nativity|lamb|man of sorrows)\b", re.I)


def brief_subject(r):
    """The subject part of a briefDescription: 'Panel, stained glass, the Crucifixion, England, ...'
    (the leading object type is dropped); sentence-style descriptions are returned whole."""
    b = clean(r.get("briefDescription"))
    parts = [p.strip() for p in b.split(",")]
    ot = (r.get("objectType") or "").strip().lower()
    if len(parts) > 1 and ot and parts[0].lower() == ot:
        return ", ".join(parts[1:])
    return b


def brief_title(r):
    """A readable title for an untitled object: the first sentence of its brief description."""
    b = clean(r.get("briefDescription"))
    # First sentence; a full stop after an initial or abbreviation ("A.W.N.", "St.", "ca.") does not end it.
    t = b
    for m in re.finditer(r"[.;](?=\s+[A-Z(]|\s*$)", b):
        if m.start() >= 15 and not ABBREV.search(b[:m.start()]):
            t = b[:m.start()]
            break
    t = t.rstrip(".;")
    if len(t) > 140:
        t = t[:140].rsplit(",", 1)[0]
    return t[:1].upper() + t[1:] if t else ""


def years(r):
    ys = []
    for pd in r.get("productionDates") or []:
        d = pd.get("date") or {}
        for k in ("earliest", "latest"):
            m = re.match(r"(-?\d{1,4})", d.get(k) or "")
            if m:
                ys.append(int(m.group(1)))
    return ys


def date_text(r):
    for pd in r.get("productionDates") or []:
        t = (pd.get("date") or {}).get("text")
        if t:
            return t
    return ""


def mid_year(r):
    ys = years(r)
    if not ys:
        return None
    s, e = min(ys), max(ys)
    return int(round((s + e) / 2)) if 0 <= e - s <= 300 else s


def artist(r):
    for a in r.get("artistMakerPerson") or []:
        n = ((a.get("name") or {}).get("text") or "").strip()
        if n and n.lower() not in ("unknown", "anonymous"):
            # "Dürer, Albrecht" -> "Albrecht Dürer"
            if n.count(",") == 1 and not re.search(r"\(|\d", n):
                last, first = [x.strip() for x in n.split(",")]
                n = f"{first} {last}"
            return n
    for a in r.get("artistMakerOrganisations") or []:
        n = ((a.get("name") or {}).get("text") or "").strip()
        if n and n.lower() != "unknown":
            return n
    return ""


def place(r):
    for p in r.get("placesOfOrigin") or []:
        t = ((p.get("place") or {}).get("text") or "").strip()
        if t:
            return t
    return ""


KIND = [
    ("icon", r"\bicon"),
    ("manuscript", r"manuscript|illuminat|miniature|cutting|book of hours|psalter|missal|leaf"),
    ("painting", r"painting|altarpiece|triptych|polyptych|panel painting"),
    ("print", r"\bprint|engraving|etching|woodcut|lithograph|mezzotint|aquatint|drypoint"),
    ("drawing", r"drawing|design|sketch|study|watercolou?r|cartoon"),
    ("photograph", r"photograph|albumen|negative"),
    ("glass", r"stained glass|\bglass\b|panel \(stained"),
    ("textile", r"textile|embroider|tapestry|vestment|chasuble|cope|orphrey|dalmatic|altar frontal|lace|velvet|silk"),
    ("ivory", r"ivory"),
    ("enamel", r"enamel"),
    ("ceramic", r"ceramic|porcelain|earthenware|stoneware|maiolica|faience|tile|terracotta|creamware|delftware"),
    ("sculpture", r"sculpture|relief|statue|statuette|figure|group|bust|carving|crucifix|corpus|tympanum|model"),
    ("metalwork", r"metal|silver|gold|bronze|copper|brass|pewter|iron|medal|plaquette|coin|pendant|jewel|ring"),
    ("book", r"\bbook\b|binding"),
]


def kind(r):
    ot = (r.get("objectType") or "").lower()
    mt = (r.get("materialsAndTechniques") or "").lower()
    cats = " ".join((c.get("text") or "") for c in r.get("categories") or []).lower()
    for text in (ot, mt, cats):
        for k, rx in KIND:
            if text and re.search(rx, text):
                return k
    return "other"


def credit_ok(meta, ref):
    """True when the image of asset `ref` is credited to the V&A itself."""
    for m in (meta.get("images") or {}).get("_images_meta") or []:
        if m.get("assetRef") == ref:
            c = (m.get("copyright") or "").lower()
            return ("victoria" in c and "albert" in c) and not m.get("sensitiveImage")
    return False


def christ_people(r):
    return [p.get("text") for p in r.get("contentPerson") or []
            if p.get("id") in CHRIST_PEOPLE]


def decide_full(r, summary_title):
    title = clean(summary_title) or clean(((r.get("titles") or [{}])[0] or {}).get("title"))
    a = artist(r)
    ot = r.get("objectType") or ""
    if BAD_TYPES.search(ot):
        return [], title
    if title and NOT_CHRIST.search(title):
        return [], title
    tagged = christ_people(r)
    u = decide(title, "", a) if title else []
    brief = brief_subject(r)
    if not u and (not title or GENERIC_TITLE.match(title)):
        if not NOT_CHRIST.search(brief) and not BRIEF_NOT.search(brief) and BRIEF_NEEDS.search(brief):
            u = decide(brief, "", a)
            if u == ["Jesus Christ"] and MONOGRAM_ONLY.search(brief) and not tagged:
                u = []
            if u:
                # Name the subject in the title: "Leaf from a Gradual: Nativity".
                seg = next((x.strip() for x in re.split(r"[;,]|\.\s", brief) if BRIEF_NEEDS.search(x)), "")
                base = title or ot or "Object"
                if seg and len(seg) < 120 and seg.lower() not in base.lower():
                    title = f"{base}: {seg[:1].upper()}{seg[1:]}"
    if not u and tagged:
        # Curated "depicts" tags. A bare 'Jesus' tag on IHS-monogram metalwork is not a depiction.
        if MONOGRAM_ONLY.search(brief) and not FIGURATIVE.search(re.sub(MONOGRAM_ONLY, "", brief)):
            return [], title
        u = classify(" ".join(tagged)) or ["Jesus Christ"]
    if not title:
        title = brief_title(r) or ot
    return u, title


def main():
    cands = {}

    def add(row, via, broad=False):
        sid = row.get("systemNumber")
        if not sid:
            return
        c = cands.setdefault(sid, {"row": row, "via": set(), "broad": True})
        c["via"].add(via)
        if not broad:
            c["broad"] = False

    for pid in CHRIST_PEOPLE:
        n = 0
        for row in search({"id_person": pid}):
            add(row, "person")
            n += 1
        print(f"id_person={pid}: {n} hits, {len(cands)} unique", file=sys.stderr)
    for term in QUERIES:
        n = 0
        for row in search({"q": term}):
            add(row, "q", broad=term in BROAD)
            n += 1
        print(f"{term!r}: {n} hits, {len(cands)} unique", file=sys.stderr)

    # Pre-filter on the summary before fetching full records.
    todo = []
    for sid, c in cands.items():
        title = clean(c["row"].get("_primaryTitle"))
        if "person" in c["via"]:
            todo.append(sid)
        elif title and NOT_CHRIST.search(title):
            continue
        elif title and decide(title):
            todo.append(sid)
        elif (not title or GENERIC_TITLE.match(title)) and not c["broad"]:
            todo.append(sid)
    print(f"{len(cands)} candidates, fetching {len(todo)} full records", file=sys.stderr)

    lock = threading.Lock()
    done = [0]

    def fetch_full(sid):
        d = get(f"object/{sid}")
        with lock:
            done[0] += 1
            if done[0] % 500 == 0:
                print(f"  {done[0]}/{len(todo)} full records", file=sys.stderr)
        return sid, d

    with ThreadPoolExecutor(WORKERS) as ex:
        fulls = dict(ex.map(fetch_full, todo))

    out = []
    for sid in todo:
        d = fulls.get(sid)
        if not d or not d.get("record"):
            continue
        r, meta = d["record"], d.get("meta") or {}
        ys = years(r)
        if not ys or max(ys) > LATEST_YEAR:
            continue
        u, title = decide_full(r, cands[sid]["row"].get("_primaryTitle"))
        if not u:
            continue
        refs = [x for x in r.get("images") or [] if credit_ok(meta, x)]
        if not refs:
            continue
        base = f"https://framemark.vam.ac.uk/collections/{refs[0]}"
        credit = next((m.get("copyright") for m in (meta.get("images") or {}).get("_images_meta") or []
                       if m.get("assetRef") == refs[0]), "") or "© Victoria and Albert Museum, London"
        rec = {
            "id": f"vam:{sid}",
            "t": title,
            "y": mid_year(r),
            "d": date_text(r),
            "a": artist(r),
            "c": place(r),
            "l": "Victoria and Albert Museum, London",
            "m": clean(r.get("materialsAndTechniques")),
            "s": ", ".join((s.get("text") or "") for s in r.get("styles") or [] if s.get("text")),
            "k": kind(r),
            "img": f"{base}/full/!600,600/0/default.jpg",
            "big": f"{base}/full/!1600,1600/0/default.jpg",
            "src": f"https://collections.vam.ac.uk/item/{sid}/",
            "lic": "V&A non-commercial",
            "cr": credit,
            "u": u,
        }
        out.append(rec)
    out.sort(key=lambda x: (x["y"] is None, x["y"] or 0, x["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {len(out)} records (of {len(todo)} fetched, {len(cands)} candidates) to {OUT}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
