#!/usr/bin/env python3
"""Harvest Wikimedia Commons files whose structured data "depicts" (P180) Christ.

Complements scripts/harvest.py (Wikidata items) with files that have no Wikidata
item of their own: photos of frescoes, statues, stained glass, icons, manuscript
pages and so on. For each Christ-related subject QID a CirrusSearch
`haswbstatement:P180=<QID>` query lists matching files; their imageinfo /
extmetadata and structured data (MediaInfo entities) are then fetched in
batches of 50. When a file carries P6243 ("digital representation of") the
artwork's Wikidata QID is recorded as "wd" so the merger can skip duplicates.

The sort year is the date the ARTWORK was made, never the date a photo was
taken: SDC inception / extmetadata DateTimeOriginal are trusted only when the
file is not a modern photograph (or the date is old enough that it cannot be a
digital photo date); otherwise century/decade categories are parsed.

Raw API responses are cached under .cache/commons/. Output:
data/sources/commons.json. Stdlib only.

Usage: python3 scripts/sources/commons.py
"""
import ast
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subjects import classify  # noqa: E402

CACHE = ROOT / ".cache" / "commons"
OUT = ROOT / "data" / "sources" / "commons.json"
COMMONS = "https://commons.wikimedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
MIN_INTERVAL = 0.35  # <= 3 requests per second
MIN_WIDTH = 400
MODERN = 1955

QIDS = """Q302 Q942467 Q9309699 Q51636 Q3155356 Q51624 Q16519437 Q13365963 Q534805
Q3399226 Q217048 Q51637 Q51633 Q223689 Q49599 Q2033105 Q2467856 Q5686605 Q752121
Q972213 Q837143 Q618057 Q488841 Q1029715 Q51628 Q124362 Q152771 Q3937194 Q408284
Q51625 Q201201 Q608632 Q1540642 Q1821239 Q1093370 Q245230 Q3427290 Q13510036
Q625140 Q950667 Q116926 Q1576172""".split()


def load_subjects():
    """Read the SUBJECTS dict from harvest.py without importing (and running) it."""
    src = (ROOT / "scripts" / "harvest.py").read_text()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "SUBJECTS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit("SUBJECTS not found in harvest.py")


SUBJECTS = load_subjects()

# --------------------------------------------------------------------------- HTTP

_last = [0.0]


def api(endpoint, params, tries=8):
    params = dict(params, format="json", formatversion="2", maxlag="5")
    key = hashlib.sha1((endpoint + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    cache = CACHE / key[:2] / f"{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    data = urllib.parse.urlencode(params).encode()
    delay = 5
    for attempt in range(tries):
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(endpoint, data=data, headers={
                "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=120) as r:
                out = json.loads(r.read())
            err = out.get("error")
            if err:
                if err.get("code") == "maxlag":
                    print(f"    maxlag, sleeping {delay}s", flush=True)
                    time.sleep(delay)
                    delay = min(delay * 2, 120)
                    continue
                raise RuntimeError(f"API error: {err}")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(out, ensure_ascii=False))
            return out
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                ra = e.headers.get("Retry-After")
                s = int(ra) if ra and ra.isdigit() else delay
                print(f"    HTTP {e.code}, sleeping {s}s", flush=True)
                time.sleep(s)
                delay = min(delay * 2, 300)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            print(f"    {e!r}, sleeping {delay}s", flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 300)
    raise RuntimeError(f"giving up on {endpoint} {params}")


def chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]

# --------------------------------------------------------------------------- harvesting


def search(query):
    """All (pageid, title) for a CirrusSearch query in the File namespace."""
    out, offset, total = [], 0, None
    while offset is not None and offset < 10000:
        r = api(COMMONS, {"action": "query", "list": "search", "srnamespace": "6", "srsearch": query,
                          "srlimit": "500", "sroffset": str(offset), "srprop": "", "srinfo": "totalhits"})
        q = r.get("query", {})
        total = q.get("searchinfo", {}).get("totalhits", total)
        out += [(p["pageid"], p["title"]) for p in q.get("search", [])]
        offset = r.get("continue", {}).get("sroffset")
    return out, total or 0


def gather():
    files = {}
    subj = defaultdict(set)
    for q in QIDS:
        label = SUBJECTS.get(q, "Jesus Christ")
        hits, total = search(f"haswbstatement:P180={q}")
        if total > 10000:
            # split by file type to stay below the 10k search window
            hits = []
            for ft in ("bitmap", "drawing", "multimedia", "office"):
                h, _ = search(f"haswbstatement:P180={q} filetype:{ft}")
                hits += h
        for pid, title in hits:
            files[pid] = title
            subj[pid].add(label)
        print(f"  {label:32s} {q:10s} {total:6d}  (files {len(files)})", flush=True)
    return files, subj


def fetch_info(pageids):
    info = {}
    for n, batch in enumerate(chunks(sorted(pageids), 50)):
        r = api(COMMONS, {"action": "query", "pageids": "|".join(map(str, batch)),
                          "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
                          "iiextmetadatalanguage": "en", "iiextmetadatamultilang": "0"})
        for p in r.get("query", {}).get("pages", []):
            if p.get("imageinfo"):
                info[p["pageid"]] = p
        if n % 20 == 0:
            print(f"    imageinfo {n * 50}/{len(pageids)}", flush=True)
    return info


def fetch_entities(endpoint, ids, props):
    ents = {}
    ids = sorted(set(ids))
    for n, batch in enumerate(chunks(ids, 50)):
        params = {"action": "wbgetentities", "ids": "|".join(batch), "props": props}
        if "labels" in props:
            params.update(languages="en", languagefallback="1")
        r = api(endpoint, params)
        ents.update(r.get("entities", {}))
        if n % 20 == 0:
            print(f"    entities {n * 50}/{len(ids)}", flush=True)
    return ents

# --------------------------------------------------------------------------- parsing helpers


def sval(v):
    """extmetadata values may be multilingual dicts; pick English or the first language."""
    if isinstance(v, dict):
        v = v.get("en") or next((x for k, x in v.items() if not k.startswith("_")), "")
    return v if isinstance(v, str) else ("" if v is None else str(v))


def strip_html(s):
    s = sval(s)
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def ordinal(c):
    suf = "th" if 10 <= c % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(c % 10, "th")
    return f"{c}{suf}"


def statements(ent, prop):
    """Values of a statement (MediaInfo uses 'statements', Wikidata items 'claims')."""
    sts = (ent.get("statements") or ent.get("claims") or {})
    if isinstance(sts, list):
        return []
    out = []
    for s in sts.get(prop, []):
        dv = s.get("mainsnak", {}).get("datavalue")
        if dv:
            out.append((dv["value"], s))
    return out


def item_ids(ent, prop):
    return [v["id"] for v, _ in statements(ent, prop) if isinstance(v, dict) and "id" in v]


def time_value(ent, prop="P571"):
    """(year, precision, circa) of the earliest dated statement, or None."""
    best = None
    for v, s in statements(ent, prop):
        if not isinstance(v, dict) or "time" not in v:
            continue
        m = re.match(r"^([+-]?\d+)-", v["time"])
        if not m:
            continue
        y, p = int(m.group(1)), int(v.get("precision", 9))
        if p < 7:
            continue
        if p == 7:  # Wikidata centuries are usually stored as the last year (1500 = 15th c.)
            c = (y - 1) // 100 + 1 if y > 0 else y // 100
            y = (c - 1) * 100 + 50
        elif p == 8:
            y = (y // 10) * 10 + 5
        circa = any(q.get("datavalue", {}).get("value", {}).get("id") == "Q5727902"
                    for q in s.get("qualifiers", {}).get("P1480", []))
        if best is None or y < best[0]:
            best = (y, p, circa)
    return best


def display_from(y, p, circa=False):
    if y is None:
        return ""
    if p == 7:
        return f"{ordinal((y - 1) // 100 + 1)} century"
    if p == 8:
        return f"{(y // 10) * 10}s"
    return f"c. {y}" if circa else str(y)


ART_CAT = re.compile(r"paintings?|frescos?|frescoes|murals?|icons?|sculptures?|statues?|stained glass|"
                     r"windows|mosaics?|manuscripts?|illuminat|miniatures?|altarpieces?|altars?|"
                     r"crucifix(es)?|reliefs?|carvings?|engravings?|woodcuts?|etchings?|prints|drawings|"
                     r"art\b|artworks|pietà|pietas|panels?|triptychs?|retables?|tapestr|ivor|enamels?|"
                     r"metalwork|goldsmith|book of hours|gospels?|psalters?|bibles?|wall paintings", re.I)
NONART_CAT = re.compile(r"photograph|churches|chapels|cathedrals|buildings|architecture|houses|towers|"
                        r"bells|organs|furniture|interiors?|exteriors?|events|in art\b|costumes|"
                        r"books|newspapers|postcards|films", re.I)
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
         "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15, "XVI": 16, "XVII": 17, "XVIII": 18,
         "XIX": 19, "XX": 20, "XXI": 21}


def date_from_categories(cats):
    """Best (year, display) parsed from artwork-related categories, or (None, '')."""
    found = []
    for c in cats:
        if not ART_CAT.search(c) or NONART_CAT.search(c):
            continue
        m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)[- ]century\b", c, re.I)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 21:
                found.append((2, (n - 1) * 100 + 50, f"{ordinal(n)} century"))
            continue
        m = re.search(r"\bcentury ([XVI]+)\b|\b([XVI]+)(?:e|th)? (?:century|siècle|secolo)", c)
        if m:
            n = ROMAN.get(m.group(1) or m.group(2))
            if n:
                found.append((2, (n - 1) * 100 + 50, f"{ordinal(n)} century"))
            continue
        m = re.search(r"\b(1[0-9]\d0|[5-9]\d0)s\b", c)
        if m:
            d = int(m.group(1))
            found.append((1, d + 5, f"{d}s"))
            continue
        m = re.search(r"^(1[0-9]\d\d|[3-9]\d\d) (paintings|frescoes|sculptures|icons|engravings|prints|"
                      r"drawings|works|mosaics|stained glass|altarpieces|woodcuts|etchings|manuscripts)", c, re.I)
        if m:
            found.append((0, int(m.group(1)), m.group(1)))
    if not found:
        return None, ""
    found.sort()  # most precise first
    return found[0][1], found[0][2]


YEAR = re.compile(r"(?<![\d.A-Za-z:/#])(20[0-2]\d|1[0-9]\d\d|[2-9]\d\d)(?![\d])")


def date_from_text(text):
    """(year, display) from a DateTimeOriginal-style string."""
    t = strip_html(text)
    if not t or len(t) > 160:
        t = t[:160]
    t = re.sub(r"\bQS:P\d+.*?(?=\s|$)", " ", t)
    t = re.sub(r"\b(upload|uploaded|photograph(ed)?|taken)\b.*", "", t, flags=re.I).strip()
    if re.match(r"^\s*(19[89]\d|20[0-2]\d)-\d\d(-\d\d)?\b", t) or re.search(r"\b\d{1,2} \w+ (19[89]\d|20[0-2]\d)\b", t):
        return None, ""  # an EXIF/upload timestamp, not an artwork date
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*[-–/]\s*(\d{1,2})(?:st|nd|rd|th)[- ]centur", t, re.I)
    if m and 1 <= int(m.group(1)) < int(m.group(2)) <= 21:
        a, b = int(m.group(1)), int(m.group(2))
        return a * 100, f"{ordinal(a)}–{ordinal(b)} century"
    m = re.search(r"\bbetween (\d{3,4}) and (\d{3,4})\b", t, re.I)
    if m:
        return int(m.group(1)), f"{m.group(1)}–{m.group(2)}"
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)[- ]century\b", t, re.I)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 21:
            pre = re.search(r"\b(early|late|mid|first half|second half)\b[^\d]{0,15}" + m.group(1), t, re.I)
            y = (n - 1) * 100 + 50
            if pre:
                w = pre.group(1).lower()
                y = (n - 1) * 100 + {"early": 15, "late": 85, "mid": 50, "first half": 25, "second half": 75}[w]
            return y, (f"{pre.group(1)} " if pre else "") + f"{ordinal(n)} century"
    m = re.search(r"\b(1[0-9]\d0|[5-9]\d0)s\b", t)
    if m:
        return int(m.group(1)) + 5, f"{m.group(1)}s"
    ys = [int(x) for x in YEAR.findall(t)]
    if ys:
        y = min(ys)
        circa = re.search(r"\b(c\.|ca\.?|circa|um|vers|around|about)\s*" + str(y), t, re.I)
        rng = re.search(rf"{y}\s*[-–—/]\s*(1[0-9]\d\d|[2-9]\d\d)\b", t)
        if rng:
            return y, f"{y}–{rng.group(1)}"
        return y, (f"c. {y}" if circa else str(y))
    return None, ""

# --------------------------------------------------------------------------- record building


CAMERA_PROPS = ("P4082", "P2151", "P6790", "P6789", "P6757")
CAMERA_CODE = re.compile(r"(\b|_)(IMG|DSC[NF]?|DSCF|P\d{6,}|PICT|CIMG|DCIM|SAM|PXL|_MG|MG)[_ -]?\d+.*$", re.I)


def clean_title(name):
    t = re.sub(r"^File:", "", name)
    t = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", t)
    t = t.replace("_", " ")
    t = re.sub(r"^\s*(19|20)\d\d[ ._-]\d\d[ ._-]\d\d(?:[ _-]+\d\d[ .:-]?\d\d(?:[ .:-]?\d\d)?)?\s*[-–,]?\s*", "", t)
    t = CAMERA_CODE.sub("", t)
    for _ in range(3):
        t2 = re.sub(r"[\s,;\-–(]*\b(\d{6,}|\d{8}[ _-]?\d{4,6}|\d{4}-\d{2}-\d{2}|\d{1,4}|"
                    r"\d+[a-z]?|[A-Z]{2,4}\d{3,})\)?\s*$", "", t)
        t2 = re.sub(r"\s*\((d\.j\.b\.|[A-Za-z]{1,4}\.?|\d+)\)\s*$", "", t2)
        t2 = re.sub(r"\s*[-–_,]\s*$", "", t2)
        if t2 == t:
            break
        t = t2
    return re.sub(r"\s+", " ", t).strip(" -–,") or re.sub(r"^File:|\.[^.]+$", "", name)


def base_key(name):
    """File name minus a trailing photo-series number (01, (2), -- 2020 -- 1404, MET s1/s2 view suffix)."""
    t = re.sub(r"^File:|\.[^.]+$", "", name).replace("_", " ").lower()
    met = re.match(r"^(.+?) met \S+$", t)
    if met:  # Metropolitan Museum views: "<title> MET DT7724", "... MET sf1986-3-5d1"
        return met.group(1) + " met"
    for _ in range(2):
        t = re.sub(r"(\s*--\s*\d{4}\s*--\s*\d+|(?<=\d)[sd]\d{1,2}|[\s\-_]*\(?\b\d{1,2}[a-z]?\)?)\s*$", "", t)
    return re.sub(r"[\s()\-_.,]+", " ", t).strip()


KIND_RULES = [
    ("stained glass", r"stained[- ]glass|glasmalerei|vitra(il|ux|le)|vidriera|buntglas|kirchenfenster|church window|glass window"),
    ("mosaic", r"mosaic|mosaik|mosaïque|mosaico"),
    ("fresco", r"fresco|fresque|fresken|wandmalerei|wall painting|mural|deckenfresko|deckengemälde|ceiling painting"),
    ("icon", r"\bicons?\b|\bikon|\bicône|\bicona"),
    ("manuscript", r"manuscript|illuminat|miniature|book of hours|psalter|gospel book|codex|evangeliar|"
                   r"stundenbuch|folio|\bfol\.|\bms\.? ?\d|handschrift|lectionary|breviary|missal|bible moralis"),
    ("print", r"engraving|woodcut|etching|lithograph|mezzotint|\bprints?\b|kupferstich|holzschnitt|radierung|"
              r"gravure|incisione|book illustration|bible illustration"),
    ("relief", r"\brelief|tympan|capital|kapitell|portal|bas-relief"),
    ("sculpture", r"sculpture|statue|skulptur|figur|plastik|crucifix|kruzifix|wegkreuz|feldkreuz|calvaire|"
                  r"calvary|carving|bildstock|wayside cross|wooden|holzfigur|pietà sculpture|bust\b|"
                  r"nativity scene|krippe|crèche|presepe|ivory|elfenbein"),
    ("painting", r"painting|gemälde|tableau|dipinto|pintura|altarpiece|altarbild|altarblatt|oil on|tempera|"
                 r"panel|triptych|retable|retablo|canvas|leinwand|tafelbild|schilderij"),
]
KIND_RX = [(k, re.compile(rx, re.I)) for k, rx in KIND_RULES]


def kind_of(*texts):
    text = " | ".join(t for t in texts if t)
    for k, rx in KIND_RX:
        if rx.search(text):
            return k
    return "other"


PLACE_CAT = re.compile(r"(church|kirche|chapel|kapelle|cathedral|dom\b|münster|basilica|basilik|abbey|abtei|"
                       r"monastery|kloster|convent|museum|musée|museo|gallery|galleria|eglise|église|iglesia|"
                       r"chiesa|kerk|kościół|cerkiew|duomo|sé\b|catedral|cattedrale|stift|priory|minster|"
                       r"collegiate|oratory|sanctuary|santuario|monastir|lavra|sobor|библиотек|library|bibliothè)",
                       re.I)
BAD_PLACE_CAT = re.compile(r"photograph|taken|images|files|media|featured|quality|valued|uploaded|by user|"
                           r"self-published|^(churches|chapels|cathedrals|museums) |in art\b|^art |depict|"
                           r"wikidata|cc-|pd |license|unidentified|exteriors?\b|interiors? of|maps|"
                           r"^(paintings|icons|frescoes|sculptures|stained glass|mosaics) (in|of|from)|"
                           r"collections of|^\d", re.I)


def place_from_categories(cats):
    for c in cats:
        m = re.match(r"^.+? (?:in|of|at|from) (?:the )?(.+)$", c, re.I)
        if m and PLACE_CAT.search(m.group(1)) and not BAD_PLACE_CAT.search(m.group(1)) and len(m.group(1)) < 90:
            return re.sub(r"\s*-\s*(interior|altars?|details?).*$", "", m.group(1), flags=re.I)
    for c in cats:
        if PLACE_CAT.search(c) and not BAD_PLACE_CAT.search(c) and len(c) < 90:
            return re.sub(r"\s*-\s*(interior|altars?|details?).*$", "", c, flags=re.I)
    return ""


def label_of(labels, q):
    e = labels.get(q) or {}
    lab = (e.get("labels") or {}).get("en") or {}
    return lab.get("value", "")


MODERN_HINT = re.compile(r"\b(19[6-9]\d|20[0-2]\d)\b.*\b(paintings|sculptures|works|murals|stained glass|art|"
                         r"icons|mosaics|statues)\b|\b(contemporary|modern) (art|religious art|icons|sculpture)|"
                         r"\b21st[- ]century (paintings|sculptures|art|icons|murals|works|stained)|"
                         r"\b20th[- ]century (?:.*)?\b(icons|paintings|sculptures|murals|stained glass)\b.*\b(19[6-9]\d)|"
                         r"\bsweet media\b|\bbiblica\b|\bAI[- ]generated|\bdigital art\b|\bclip ?art\b|\bcomics?\b|"
                         r"\bmemes?\b|\bscreenshots?\b|\bvideo games?\b|\bfilm stills?\b|\bcosplay|"
                         r"\breenactment|\bpassion plays?\b|\bactors?\b", re.I)
ART_TITLE = re.compile(r"altar|tympan|t[íi]mpano|evangel|retabl|bild\b|fenster|vitra|window|pulpit|kanzel|"
                       r"portal|capital|kreuz|cross|christ|jesus|jezus|ges[uù]|cristo|madonna|maria|marie|"
                       r"virgin|vierge|virgen|pieta|piet[àa]|icon|ikon|fresk|fresc|folio|book of hours|"
                       r"manuscript|codex|panel|painting|gemälde|statue|figur|relief|window|glass|mosaic|"
                       r"jes[uú]s|ni[ñn]o|adora|cruz|crucif|kruzif|se[ñn]or|gospels|friso|frieze|cuadro|"
                       r"tableau|imagen|image|sculpt|talla|paso\b|calvar|piedad|sagrad|sacr|heilig|santo|"
                       r"lamenta|descent|deposi|nativ|natal|beweinung|grablegung|auferst|kreuzig|krusig|retaul|"
                       r"tenture|tapest|teppich|passion|parable|gleichnis|stations?|vía crucis|via crucis|"
                       r"bildstock|wegkreuz|calvaire|ecce|piet|herz jesu|sacr[ée]|sagrat|altare|krist|christus|"
                       r"trinidad|trinity|trinit|dopfunt|font\b|borduur|embroider|croix|crucero|divino|"
                       r"\b1[0-8]\d\d\b|\bs\. ?[XVI]+\b|siglo|century|jahrhundert|si[èe]cle|secolo|exvoto",
                       re.I)
BUILDING_TITLE = re.compile(r"church|kirche|iglesia|[ée]glise|chiesa|kerk|ko[śs]ci[óo][łl]|chapel|kapelle|capilla|"
                            r"cappella|cathedral|catedral|kathedrale|\bdom\b|m[üu]nster|basilica|templo|temple|"
                            r"interior|exterior|innen|au[ßs]en|patio|fachada|fa[çc]ade|zabudowa|panorama|view|"
                            r"ansicht|santuario|sanctuary|monastery|kloster|monasterio|convento|abbey|abbaye|"
                            r"friedhof|cemetery|cementerio|kirchspiel|-- \d{4} --|presbiterio|nave\b|choir|chor\b|"
                            r"stra[ßs]e|platz|street|square|plaza|village|dorf",
                            re.I)
MODERN_EVENT = re.compile(r"\b(protests?|demonstrations?|riots?|rallies|rally|concerts?|politicians|"
                          r"elections?|graffiti|street art|tattoos?|t-shirts?|advertis|billboards?|storming)\b",
                          re.I)
MODERN_TITLE_YEAR = re.compile(r"(?:\(|,\s*)(19[6-9]\d|20[0-2]\d)\)?\s*(?:\.\w+)?$")
BUILDING_LABEL = re.compile(r"\b(church|chapel|cathedral|basilica|building|tower|steeple|bell tower|"
                            r"facade|façade|exterior|cemetery|graveyard|street|square|parish|"
                            r"house|village|town|lake|river|road|roof|clock|monastery|"
                            r"castle|bridge|car|procession|architecture)\b", re.I)


def build(files, subj, info, media, labels, wd_items):
    recs = []
    stats = Counter()
    dropped = defaultdict(list)  # reason -> titles, for debugging

    def drop(reason, title):
        stats[reason] += 1
        dropped[reason].append(title)
    for pid, title in files.items():
        p = info.get(pid)
        if not p:
            stats["no imageinfo"] += 1
            continue
        ii = p["imageinfo"][0]
        mime = ii.get("mime", "")
        if not mime.startswith("image/") or mime in ("image/svg+xml", "image/vnd.djvu"):
            stats["mime"] += 1
            continue
        w = ii.get("width", 0) or 0
        if w < MIN_WIDTH:
            stats["too small"] += 1
            continue
        em = ii.get("extmetadata", {})
        ev = lambda k: sval((em.get(k) or {}).get("value", ""))  # noqa: E731
        cats = [c for c in ev("Categories").split("|") if c]
        ent = media.get(f"M{pid}", {})
        if "missing" in ent:
            ent = {}

        p180 = item_ids(ent, "P180")
        christ_idx = [i for i, q in enumerate(p180) if q in SUBJECTS]
        u = []
        for q in p180:
            lab = SUBJECTS.get(q)
            if lab and lab not in u:
                u.append(lab)
        for lab in sorted(subj.get(pid, ())):
            if lab not in u:
                u.append(lab)
        if not u:
            stats["no subject"] += 1
            continue

        name = re.sub(r"^File:", "", title)
        on = em.get("ObjectName") or {}
        obj_name = strip_html(on.get("value", "")) if on.get("source") == "commons-desc-page" else ""
        obj_name = re.sub(r"\s*\b(label|title)?\s*QS:.*$", "", obj_name).strip()
        artist_raw = ev("Artist")
        credit = ev("Credit")
        desc = strip_html(ev("ImageDescription"))[:300]
        cat_text = " | ".join(cats)

        # --- photo evidence
        p31 = item_ids(ent, "P31")
        is_photo = bool(
            "Q125191" in p31
            or any(statements(ent, pr) for pr in CAMERA_PROPS)
            or re.search(r"photographs taken on|taken with|self-published work", cat_text, re.I)
            or re.search(r"int-own-work|own work", credit, re.I)
            or re.search(r"User:", artist_raw)
        )

        # --- relevance: is the Christ subject the focus of the file?
        title_subj = classify(clean_title(name)) or classify(obj_name)
        desc_subj = classify(desc)
        cat_subj = classify(cat_text)
        nonchrist = [q for q in p180 if q not in SUBJECTS]
        building_like = [q for q in nonchrist if BUILDING_LABEL.search(label_of(labels, q))]
        art_title = bool(ART_TITLE.search(name) or ART_TITLE.search(obj_name)
                         or kind_of(name, obj_name) != "other")
        if is_photo and BUILDING_TITLE.search(name) and not (title_subj or desc_subj or art_title):
            first = christ_idx[0] if christ_idx else 99
            if len(p180) >= 5 and first >= 2 and not cat_subj:
                drop("minor depicts (many P180)", title)
                continue
            if len(building_like) >= 2 or (building_like and first >= 1 and len(p180) >= 4):
                drop("building/landscape photo", title)
                continue
        if u == ["Jesus Christ"] and ev("LicenseShortName").startswith("CC BY") and not (title_subj or desc_subj or cat_subj or art_title):
            drop("generic Jesus depict, no art signal", title)
            continue
        if not art_title and any(MODERN_EVENT.search(x) for x in cats):
            drop("modern event photo", title)
            continue
        if any(MODERN_HINT.search(x) for x in cats) or MODERN_HINT.search(name) or (
                not is_photo and MODERN_TITLE_YEAR.search(name)):
            drop("modern hint", title)
            continue

        # --- Wikidata artwork it represents (P6243)
        wd = ""
        rep = item_ids(ent, "P6243")
        if rep:
            wd = rep[0]
        wdent = wd_items.get(wd, {}) if wd else {}

        # --- date of the artwork
        y, d, how = None, "", ""
        tv = time_value(wdent) if wdent else None
        if tv:
            y, d, how = tv[0], display_from(*tv), "wd"
        if y is None:
            tv = time_value(ent)
            exact_recent = tv and tv[1] >= 10 and tv[0] >= 1990
            if tv and not exact_recent and (not is_photo or tv[0] < 1900):
                y, d, how = tv[0], display_from(*tv), "sdc"
        if y is None:
            dto = ev("DateTimeOriginal")
            artworkish = (on.get("source") == "commons-desc-page"
                          or re.search(r"\bpaintings?\b|\bcentury\b|\bicons?\b|\bmanuscripts?\b|\bengravings?\b",
                                       cat_text, re.I))
            if dto and (artworkish or not is_photo):
                yy, dd = date_from_text(dto)
                if yy is not None and (yy < 1900 or not is_photo):
                    y, d, how = yy, dd, "dto"
        if y is None:
            yy, dd = date_from_categories(cats)
            if yy is not None:
                y, d, how = yy, dd, "cat"
        if y is not None and (y > 2026 or y < 0):
            y, d, how = None, "", ""
        stats[f"date:{how or 'none'}"] += 1
        if y is not None and y > MODERN:
            artwork_template = on.get("source") == "commons-desc-page"
            if how in ("dto", "sdc") and not artwork_template and not MODERN_TITLE_YEAR.search(name):
                # likely the date a (scanned/archival) photo was taken, not of the artwork
                stats["recent date discarded"] += 1
                y, d, how = None, "", ""
            else:
                drop("modern (y > 1955)", title)
                continue

        # --- artist
        a = ""
        if wdent:
            a = ", ".join(filter(None, (label_of(labels, q) for q in item_ids(wdent, "P170")[:2])))
        if not a and artist_raw and not re.search(r"User:|int-own-work", artist_raw):
            at = strip_html(artist_raw)
            at = re.split(r"\.\s*(Scanned|Photo|Photograph|Foto|Uploaded)\b|\s+\(photo", at, flags=re.I)[0]
            at = re.sub(r"\s*(Deutsch|English|Français|Italiano|Español):?\s*", " ", at).strip(" .;,")
            at = re.sub(r"\b(Life time|Lifetime)\b.*$", "", at).strip(" .;,")
            if re.match(r"^(unknown|anonymous|unbekannt|anonyme?|anonimo|desconocido|author unknown)\b", at, re.I):
                at = "Unknown"
            # a CC BY(-SA) file is licensed by its photographer/recorder, not by an old artwork's maker
            photo_artist = ev("LicenseShortName").startswith("CC BY") or (
                is_photo and on.get("source") != "commons-desc-page")
            if at and len(at) <= 80 and not photo_artist and not re.search(r"https?://|photographer|wikimedia", at, re.I):
                a = at

        # --- locations
        c = ""
        l = ""
        src_ent = wdent or ent
        for prop in ("P195", "P276"):
            labs = [label_of(labels, q) for q in item_ids(src_ent, prop)]
            labs = [x for x in labs if x]
            if labs:
                l = labs[0]
                break
        if not l and wdent:
            for prop in ("P195", "P276"):
                labs = [label_of(labels, q) for q in item_ids(ent, prop)]
                if labs and labs[0]:
                    l = labs[0]
                    break
        if not l:
            l = place_from_categories(cats)
        if not l:
            locs = [label_of(labels, q) for q in item_ids(ent, "P1071")]
            if locs and locs[0] and is_photo:
                l = locs[0]  # where the photo was taken = where the object is
        if wdent:
            cl = [label_of(labels, q) for q in item_ids(wdent, "P1071")]
            c = cl[0] if cl and cl[0] else ""
        elif not is_photo:
            cl = [label_of(labels, q) for q in item_ids(ent, "P1071")]
            c = cl[0] if cl and cl[0] else ""

        # --- title, kind, images
        t = obj_name if obj_name and len(obj_name) <= 160 else clean_title(name)
        if wdent and not obj_name:
            wl = label_of(labels, wd)
            if wl:
                t = wl
        p180_labels = " ".join(label_of(labels, q) for q in nonchrist)
        k = kind_of(cat_text, t, name, p180_labels, desc)

        url = ii["url"].split("?")[0]
        m = re.match(r"^https://upload\.wikimedia\.org/wikipedia/commons/(\w/\w\w)/(.+)$", url)
        if not m:
            stats["odd url"] += 1
            continue
        path, fn = m.groups()
        ext = fn.rsplit(".", 1)[-1].lower()

        def thumb(width):
            if ext in ("tif", "tiff"):
                return f"https://upload.wikimedia.org/wikipedia/commons/thumb/{path}/{fn}/lossy-page1-{width}px-{fn}.jpg"
            return f"https://upload.wikimedia.org/wikipedia/commons/thumb/{path}/{fn}/{width}px-{fn}"
        img = thumb(330)
        if w > 1280:
            big = thumb(1280)
        elif ext in ("tif", "tiff"):
            big = thumb(960 if w > 960 else 500)
        else:
            big = url

        recs.append({
            "id": f"commons:{pid}", "t": t, "y": y, "d": d, "a": a, "c": c, "l": l, "m": "", "s": "",
            "k": k, "img": img, "big": big, "src": ii.get("descriptionurl", ""),
            "lic": strip_html(ev("LicenseShortName")), "u": u, "wd": wd,
            "_key": (base_key(name), tuple(sorted(u)), tuple(sorted(p180)), wd,
                     (y, a) if base_key(name).endswith(" met") else None), "_px": w * (ii.get("height") or 0),
        })

    # near-duplicates: same base file name (ignoring numbers), same subjects and depicts -> keep largest
    groups = defaultdict(list)
    for r in recs:
        groups[r["_key"]].append(r)
    out = []
    build.dupes = [[r["src"].rsplit("File:", 1)[-1] for r in g] for g in groups.values() if len(g) > 1]
    for g in groups.values():
        g.sort(key=lambda r: -r["_px"])
        keep = g[0]
        if keep["y"] is None:  # borrow a date found on a sibling
            for r in g[1:]:
                if r["y"] is not None:
                    keep["y"], keep["d"] = r["y"], r["d"]
                    break
        out.append(keep)
        stats["near-duplicate"] += len(g) - 1
    for r in out:
        del r["_key"], r["_px"]
    out.sort(key=lambda r: (r["y"] is None, r["y"] or 0, r["t"]))
    build.dropped = dropped
    return out, stats


def main():
    print("Searching Commons for files depicting Christ subjects...", flush=True)
    files, subj = gather()
    print(f"{len(files)} distinct files", flush=True)
    print("Fetching imageinfo...", flush=True)
    info = fetch_info(files.keys())
    print("Fetching structured data...", flush=True)
    media = fetch_entities(COMMONS, [f"M{p}" for p in files], "claims")
    wd_ids = set()
    for e in media.values():
        wd_ids.update(item_ids(e, "P6243"))
    print(f"Fetching {len(wd_ids)} Wikidata artworks referenced by P6243...", flush=True)
    wd_items = fetch_entities(WIKIDATA, wd_ids, "claims|labels") if wd_ids else {}
    need = set()
    for e in media.values():
        for prop in ("P180", "P195", "P276", "P1071", "P170"):
            need.update(item_ids(e, prop))
    for e in wd_items.values():
        for prop in ("P195", "P276", "P1071", "P170"):
            need.update(item_ids(e, prop))
    need = {q for q in need if q not in SUBJECTS}
    print(f"Fetching {len(need)} labels...", flush=True)
    labels = fetch_entities(WIKIDATA, need, "labels")
    for q, e in wd_items.items():
        labels.setdefault(q, e)
    recs, stats = build(files, subj, info, media, labels, wd_items)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(recs, separators=(",", ":"), ensure_ascii=False))
    print("Stats:")
    for k, v in sorted(stats.items()):
        print(f"  {k:30s} {v}")
    print(f"Wrote {len(recs)} records to {OUT.relative_to(ROOT)} "
          f"({sum(1 for r in recs if r['wd'])} with P6243, {sum(1 for r in recs if r['y'] is not None)} dated)")


if __name__ == "__main__":
    main()
