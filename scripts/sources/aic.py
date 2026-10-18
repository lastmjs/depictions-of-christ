#!/usr/bin/env python3
"""Harvest public-domain depictions of Christ from the Art Institute of Chicago API.

Runs many phrase searches against title / alt titles / subject & term tags,
keeps public-domain records with an image whose title (plus Christ-related
subject tags) passes subjects.classify, and writes data/sources/aic.json.
Raw API responses are cached under .cache/aic/. Stdlib only.
"""
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subjects import classify  # noqa: E402

CACHE = ROOT / ".cache" / "aic"
OUT = ROOT / "data" / "sources" / "aic.json"
API = "https://api.artic.edu/api/v1/artworks/search"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
IIIF = "https://www.artic.edu/iiif/2"
MIN_INTERVAL = 1.1  # AIC asks anonymous clients for <= 60 req/min
PAGE = 100
MAX_WINDOW = 10000  # Elasticsearch from+size cap

QUERIES = [
    "Christ", "Jesus", "Madonna", "Virgin and Child", "Virgin and child/Madonna and child",
    "Crucifixion", "crucifixions", "Crucifix", "Pietà", "Pieta", "Nativity", "Holy Family",
    "Adoration", "Magi", "Three Kings", "Epiphany", "Lamentation", "Entombment", "Deposition",
    "Descent from the Cross", "Resurrection", "Last Supper", "Ecce Homo", "Man of Sorrows",
    "Calvary", "Golgotha", "Passion", "Transfiguration", "Ascension", "Baptism", "Flight into Egypt",
    "Rest on the Flight", "Last Judgment", "Emmaus", "Gethsemane", "Agony in the Garden",
    "Flagellation", "Crowning with Thorns", "Salvator", "Saviour", "Savior", "Redeemer",
    "Pantocrator", "Christ in Majesty", "icon", "Sacred Heart", "Trinity", "Veronica",
    "Sudarium", "Holy Face", "Good Shepherd", "Lazarus", "Cana", "Infant", "Christ Child",
    "Bambino", "Noli me tangere", "Doubting Thomas", "Incredulity", "Circumcision",
    "Presentation in the Temple", "Doctors", "Mystic Marriage", "Entry into Jerusalem",
    "Money Changers", "Betrayal", "Judas", "Pilate", "Mocking", "Carrying the Cross",
    "Bearing the Cross", "Stations of the Cross", "Harrowing of Hell", "Limbo", "Anastasis",
    "Lamb of God", "Agnus Dei", "Deesis", "Holy Women", "Three Marys", "Sermon on the Mount",
    "Samaritan", "Adulteress", "Tribute Money", "Prodigal", "Our Lord", "Our Lady",
    "Virgin", "Mary", "Theotokos", "Mother of God", "Shepherds", "Holy Kinship", "Saint Anne",
    "Madonna and Child", "Coronation of the Virgin", "Throne of Mercy", "Saint Christopher",
    "Christian subjects", "New Testament", "Gospel", "Evangelist", "Apostles", "Disciples",
    "Altarpiece", "Triptych", "Diptych", "Book of Hours", "Bible", "Passion of Christ",
]

FIELDS = [
    "id", "title", "alt_titles", "date_start", "date_end", "date_display", "artist_title",
    "artist_display", "place_of_origin", "medium_display", "style_title", "artwork_type_title",
    "classification_title", "classification_titles", "department_title", "image_id",
    "is_public_domain", "subject_titles", "term_titles",
]

# Subject/term tags that are allowed to contribute to classification.
TAG_OK = re.compile(r"\b(jesus|christ|madonna|virgin and child|crucifix\w*|pietà|pieta|"
                    r"nativity|holy family|last supper|resurrection|entombment|lamentation|"
                    r"adoration of the (magi|shepherds)|man of sorrows|ecce homo)\b", re.I)
TAG_BAD = re.compile(r"\bchristian(ity| subjects)?\b|\bchristmas\b", re.I)

# Title patterns where a rule matches but Christ is not (or not certainly) depicted.
# Shared with cma.py.
NOT_CHRIST = re.compile(
    r"\bcrucifixion of (saint|st\.?|san|santo|sant'|the saint)\b|\bcrucifixion of (peter|andrew|philip)\b|"
    r"\bpassion ?flower\b|\bsaint lazarus\b|\blazarus and dives\b|\bdives and lazarus\b|"
    r"\brich man and lazarus\b|\bchrist'?s hospital\b|\bchrist'?s college\b|\bsalvator rosa\b|"
    r"\btrinity (college|house|church|chapel|hall|school|street|square|term)\b|\bof trinity\b|"
    r"\b(amida|buddhist|shaka|hindu) trinity\b|\bcastles in the air\b|\byear of our lord\b|"
    r"\bjesus (bruno|maria|garc|mart|rodr|gonz|l[oó]pez)\w*\b|\bchrist, it\b|"
    r"\b(a|little|japanese|oklahoma|gypsy|modern|italian|dutch|peasant|breton|negro|indian|mexican) madonna\b|"
    r"\bmadonna of the (tenements|slums|streets|prairie|trail)\b|\bmadonna \(a mother\)|"
    r"\bmadonna del parto\b|\b(madonna|virgin) of (sorrows|the seven sorrows)\b|\bmourning (madonna|virgin)\b|"
    r"\bmater dolorosa\b|\bdress for a madonna\b|\bmadonna statue\b|\bmadonna lily\b|"
    r"\bmadonna seated on clouds\b",
    re.I)
# (artist, exact title) pairs that are secular despite the title.
NOT_CHRIST_PAIRS = {("edvard munch", "madonna")}

# Supplementary rules for common museum phrasings subjects.py misses.
SUPPLEMENT = [
    ("Madonna and Child", re.compile(
        r"^(the )?(virgin|madonna) (on|with|in|by|and|nursing|suckling|seated|standing|enthroned|"
        r"of humility)\b(?!.*\b(annunciat\w*|assum\w*|death|dormition|marriage|presentation|"
        r"education|birth|joachim|immaculate|coronation)\b)|\bia orana maria\b", re.I)),
    ("Resurrection", re.compile(
        r"\bresurrection\b(?! of (st\b|st\.|saint|the dead|mary|the flesh|the body|the bodies))", re.I)),
]
# Series suffixes ("..., from The Passion of Christ") should not by themselves
# make an item about Christ, unless the head names a Gospel scene.
SERIES = re.compile(r",?\s+(?:(?:plate|page|folio|leaf|no\.?|number)\s+[\w.]+\s+)?(?:from|of the series|after)\s+(?=[\"“'A-Z(]|the\b)", re.I)
HEAD_SCENE = re.compile(
    r"cross|resurrect|crucif|entomb|tomb|sepulch|supper|nativity|birth of (christ|jesus)|adoration of the (magi|shepherds|kings)|"
    r"baptism of|flagell|scourg|crown(ed|ing)|mock|betray|arrest|taken|captive|judas|pilate|caiaphas|calvary|"
    r"golgotha|lament|deposition|descent|ascension|transfig|agony|gethsemane|olive|disrob|stripp|nail|pierc|"
    r"dead|piet|ecce homo|emmaus|noli|limbo|harrowing|last judg|entry into|jerusalem|money.?changers|magi|"
    r"shepherds|flight into|circumcis|presentation in|holy family|title page|frontispiece|washing (of )?the feet|"
    r"last supper|lazarus|cana|healing|miracle|parable|sermon", re.I)


def decide(title, extra="", artist=""):
    """classify() plus museum-specific false-positive filters and supplements."""
    title = (title or "").strip()
    if not title or NOT_CHRIST.search(title):
        return []
    if (artist.strip().lower(), title.lower()) in NOT_CHRIST_PAIRS:
        return []
    parts = SERIES.split(title, maxsplit=1)
    head = parts[0].strip() if len(parts) > 1 and parts[0].strip() else title
    u = classify(head, extra)
    if not u and head != title and HEAD_SCENE.search(head) \
            and not re.search(r"\b(st\.?|saint|martyrdom|annunciation|visitation)\b", head, re.I):
        u = classify(title, extra)
    if not u or u == ["Jesus Christ"]:
        sup = [label for label, rx in SUPPLEMENT if rx.search(head)]
        if sup:
            u = sup
    return u


_last = [0.0]


def fetch(body):
    raw = json.dumps(body, sort_keys=True)
    key = hashlib.sha1(raw.encode()).hexdigest()
    path = CACHE / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    delay = 2.0
    for attempt in range(8):
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        req = urllib.request.Request(API, data=raw.encode(), method="POST", headers={
            "User-Agent": UA, "AIC-User-Agent": UA, "Content-Type": "application/json",
            "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode())
            CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
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
    raise RuntimeError(f"giving up on {raw[:200]}")


def search(term):
    q = {"bool": {
        "must": [{"multi_match": {"query": term, "type": "phrase",
                                  "fields": ["title", "alt_titles", "subject_titles", "term_titles"]}}],
        "filter": [{"term": {"is_public_domain": True}}, {"exists": {"field": "image_id"}}],
    }}
    page = 1
    while True:
        data = fetch({"query": q, "fields": FIELDS, "limit": PAGE, "page": page})
        rows = data.get("data", [])
        yield from rows
        total = data.get("pagination", {}).get("total", 0)
        if not rows or page * PAGE >= min(total, MAX_WINDOW):
            if total > MAX_WINDOW:
                print(f"  ! '{term}' has {total} hits; only first {MAX_WINDOW} reachable", file=sys.stderr)
            break
        page += 1


def kind(r):
    t = " ".join(filter(None, [r.get("artwork_type_title"), r.get("classification_title"),
                               " ".join(r.get("classification_titles") or []),
                               r.get("medium_display"), r.get("department_title")])).lower()
    at = (r.get("artwork_type_title") or "").lower()
    rules = [
        ("icon", r"\bicons?\b"),
        ("manuscript", r"manuscript|illuminat|book of hours|antiphon|gradual|psalter"),
        ("painting", r"^painting|miniature"),
        ("print", r"^print|engraving|etching|woodcut|lithograph|mezzotint|aquatint"),
        ("drawing", r"^drawing|watercolor|chalk|graphite|pen and"),
        ("photograph", r"^photograph|albumen|gelatin silver|daguerreotype"),
        ("sculpture", r"^sculpture|statue|relief|carving"),
        ("textile", r"^textile|^costume|embroider|tapestry|silk|wool|linen|velvet|lace"),
        ("book", r"^book|bound volume"),
        ("ivory", r"\bivory\b"),
        ("enamel", r"\benamel"),
        ("glass", r"stained glass|\bglass\b"),
        ("ceramic", r"^vessel|ceramic|porcelain|earthenware|stoneware|faience|maiolica|terracotta|terra cotta"),
        ("metalwork", r"^metalwork|silver|gold|bronze|copper|brass|pewter|iron|medal|coin"),
        ("painting", r"\boil on\b|tempera|on panel|on canvas|fresco"),
    ]
    for k, rx in rules:
        if re.search(rx, at) or re.search(rx, t):
            return k
    return "other"


def year(r):
    s, e = r.get("date_start"), r.get("date_end")
    if s is None and e is None:
        return None
    if s is None or e is None:
        return int(s if s is not None else e)
    return int(round((s + e) / 2)) if 0 <= e - s <= 300 else int(s)


def main():
    seen = {}
    for term in QUERIES:
        n = 0
        for r in search(term):
            seen.setdefault(r["id"], r)
            n += 1
        print(f"{term!r}: {n} hits, {len(seen)} unique", file=sys.stderr)

    out = []
    for r in seen.values():
        if not r.get("is_public_domain") or not r.get("image_id"):
            continue
        title = (r.get("title") or "").strip()
        tags = [t for t in (r.get("subject_titles") or []) + (r.get("term_titles") or [])
                if TAG_OK.search(t) and not TAG_BAD.search(t)]
        alt = " ".join(r.get("alt_titles") or [])
        u = decide(title, " ".join([alt] + sorted(set(tags))).strip(), r.get("artist_title") or "")
        if not u:
            continue
        rec = {
            "id": f"aic:{r['id']}",
            "t": title,
            "y": year(r),
            "d": r.get("date_display") or "",
            "a": (r.get("artist_title") or "").strip(),
            "c": r.get("place_of_origin") or "",
            "l": "Art Institute of Chicago",
            "m": r.get("medium_display") or "",
            "s": r.get("style_title") or "",
            "k": kind(r),
            "img": f"{IIIF}/{r['image_id']}/full/600,/0/default.jpg",
            "big": f"{IIIF}/{r['image_id']}/full/1686,/0/default.jpg",
            "src": f"https://www.artic.edu/artworks/{r['id']}",
            "lic": "CC0",
            "u": u,
        }
        out.append(rec)
    out.sort(key=lambda x: (x["y"] is None, x["y"] or 0, x["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {len(out)} records (of {len(seen)} candidates) to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
