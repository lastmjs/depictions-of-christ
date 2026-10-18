#!/usr/bin/env python3
"""Harvest Rijksmuseum (Amsterdam) open data for depictions of Christ. No API key.

1. The keyless Linked Art Search API (https://data.rijksmuseum.nl/search/collection)
   is queried by title for many Dutch/English Christ-related terms (only objects
   with an image), collecting object identifiers.
2. Each object's Europeana Data Model record is fetched from the keyless OAI-PMH
   endpoint (GetRecord), which carries title, dates, makers, place, materials,
   type, Iconclass subjects, rights and the IIIF image service in one response.
3. Dutch titles are mapped to English keywords and run through subjects.classify().

Responses are cached under .cache/rijks/. Output: data/sources/rijks.json

Usage: python3 scripts/sources/rijks.py [--workers N]
"""
import gzip
import hashlib
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subjects import classify, EXCLUDE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / ".cache" / "rijks"
OUT = ROOT / "data" / "sources" / "rijks.json"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
SEARCH = "https://data.rijksmuseum.nl/search/collection"
OAI = "https://data.rijksmuseum.nl/oai"
MIN_INTERVAL = 0.2  # seconds between any two requests (all workers combined)

TERMS = [
    "Christus", "Christ", "Jezus", "Jesus", "Christuskind", "Kindje Jezus",
    "Maria met kind", "Maria met het kind", "Maria en kind", "Madonna", "Virgin and Child",
    "Heilige Familie", "Heilige Maagschap", "Holy Family",
    "Geboorte van Christus", "Kerststal", "Kribbe", "Aanbidding", "Adoration", "Driekoningen",
    "Besnijdenis", "Opdracht in de tempel", "Vlucht naar Egypte", "Terugkeer uit Egypte",
    "Doop van Christus", "Doop in de Jordaan", "Verzoeking", "Bruiloft te Kana", "Bergrede",
    "Lazarus", "Wonderbare visvangst", "Broodvermenigvuldiging", "Transfiguratie", "Verheerlijking",
    "Intocht in Jeruzalem", "Tempelreiniging", "Geldwisselaars",
    "Laatste Avondmaal", "Avondmaal", "Voetwassing", "Hof van Olijven", "Gethsemane",
    "Judaskus", "Gevangenneming", "Verloochening", "Pilatus", "Kajafas", "Geseling",
    "Doornenkroning", "Bespotting", "Ecce Homo", "Kruisdraging", "Veronica", "Zweetdoek", "Vera Icon",
    "Kruisiging", "Crucifixion", "Kruisbeeld", "Crucifix", "Calvarie", "Golgotha",
    "Kruisafneming", "Bewening", "Piëta", "Pieta", "Graflegging", "Man van smarten",
    "Voorgeborchte", "Hellevaart", "Opstanding", "Verrijzenis", "Resurrection",
    "Noli me tangere", "Emmaüs", "Emmaus", "Ongelovige Thomas", "Ongeloof van Thomas",
    "Hemelvaart", "Laatste Oordeel", "Kroning van Maria", "Salvator Mundi", "Heiland", "Verlosser",
    "Goede Herder", "Heilig Hart", "Drie-eenheid", "Drievuldigheid", "Genadestoel",
    "Heilig Aanschijn", "Samaritaanse vrouw", "Overspelige vrouw", "Nicodemus",
    "Martha en Maria", "Kinderen tot mij", "Schatgeld", "Lam Gods",
]

# Dutch -> English keyword mapping applied to lowercased Dutch titles before classify().
NL_EN = [
    (r"\bmaria[- ]?hemelvaart\b|\bhemelvaart van (de )?(heilige )?maria\b|\btenhemelopneming\b", "assumption"),
    (r"\bhemelvaart( van christus)?\b", "ascension"),
    (r"\bonbevlekte ontvangenis\b", "immaculate conception"),
    (r"\b(verkondiging|annunciatie|boodschap) (aan maria|van de engel|van de geboorte van christus aan maria)?", "annunciation "),
    (r"\bvisitatie\b|\bbezoek van maria aan elisabeth?\b", "visitation"),
    (r"\bchristuskind(je)?\b|\bkind(je)? jezus\b|\bjezuskind(je)?\b", "christ child"),
    (r"\bkerststal\b|\bkribbe\b|\bkerstnacht\b", "nativity"),
    (r"\bgeboorte van (christus|jezus)\b", "nativity"),
    (r"\baanbidding (der|van de) herders\b|\bherders aanbidden\b", "adoration of the shepherds"),
    (r"\bverkondiging aan de herders\b", "annunciation to the shepherds"),
    (r"\baanbidding (der|van de) (koningen|wijzen|drie koningen)\b|\bdriekoningen\b|\bdrie koningen\b", "adoration of the magi"),
    (r"\bbesnijdenis\b", "circumcision"),
    (r"\bopdracht (van christus )?in de tempel\b|\bopdracht van (christus|jezus)\b", "presentation in the temple"),
    (r"\brust op de vlucht\b", "rest on the flight"),
    (r"\bvlucht naar egypte\b", "flight into egypt"),
    (r"\bterugkeer uit egypte\b", "return from egypt"),
    (r"\bheilige familie\b", "holy family"),
    (r"\bheilige maagschap\b", "holy kinship"),
    (r"\b(twaalfjarige )?(christus|jezus) (tussen|onder|bij) de (schriftgeleerden|leraren|rabbijnen)\b|\btwaalfjarige jezus\b", "christ among the doctors"),
    (r"\bmystieke? (huwelijk|bruiloft|verloving)\b", "mystic marriage"),
    (r"\bdoop van (christus|jezus)\b|\b(christus|jezus) gedoopt\b", "baptism of christ"),
    (r"\bverzoeking van (christus|jezus)\b|\b(christus|jezus) (wordt )?(in de woestijn|verzocht)\b", "temptation of christ"),
    (r"\bbruiloft (te|van|in) kana\b", "wedding at cana"),
    (r"\bbergrede\b", "sermon on the mount"),
    (r"\bopwekking van (lazarus|de dochter van ja[ïi]rus|de jongeling)\b", "raising of lazarus"),
    (r"\bwonderbare visvangst\b", "miraculous draught"),
    (r"\bbroodvermenigvuldiging\b|\bspijziging\b", "feeding of the five thousand"),
    (r"\b(christus|jezus) loopt over het water\b|\bwandelen(d)? over het water\b", "walking on water"),
    (r"\bgenezing van\b|\b(christus|jezus) geneest\b", "healing of the"),
    (r"\btransfiguratie\b|\bverheerlijking (op de berg|van christus)\b", "transfiguration"),
    (r"\bintocht (van christus )?in jeruzalem\b", "entry into jerusalem"),
    (r"\btempelreiniging\b|\breiniging van de tempel\b|\bverdrijving van de (geldwisselaars|kooplieden)\b|\bgeldwisselaars\b", "cleansing of the temple"),
    (r"\blaatste avondmaal\b|\bheilig avondmaal\b", "last supper"),
    (r"\bvoetwassing\b|\b(christus|jezus) wast de voeten\b", "christ washing the disciples feet"),
    (r"\bhof van (olijven|gethsemane|getsemane)\b|\bolijfberg\b|\bgebed in de hof\b|\bgethsemane\b", "agony in the garden"),
    (r"\bjudaskus\b|\bkus van judas\b|\bverraad van (christus|jezus)\b", "kiss of judas"),
    (r"\bgevangenneming( van (christus|jezus))?\b", "arrest of christ"),
    (r"\b(christus|jezus) voor (pilatus|kajafas|ka[ïi]fas|annas|hanna[sh]?|herodes|de hogepriester)\b", "christ before pilate"),
    (r"\bpilatus wast\b|\bhandwassing van pilatus\b", "pilate washing"),
    (r"\bgeseling\b|\bgegeseld\b|\b(christus|jezus) aan de (geselpaal|zuil)\b", "flagellation"),
    (r"\bdoornenkroning\b|\bkroning met doornen\b|\bmet doornen gekroond\b|\bdoornenkroon\b", "crowning with thorns"),
    (r"\bbespotting( van (christus|jezus))?\b|\b(christus|jezus) bespot\b", "mocking of christ"),
    (r"\bkruisdraging\b|\b(christus|jezus) draagt (het|zijn) kruis\b|\bweg naar (golgotha|calvarie)\b|\bkruisweg(statie)?\b", "christ carrying the cross"),
    (r"\bzweetdoek( van veronica)?\b|\bdoek van veronica\b|\bvera icon\b", "veil of veronica"),
    (r"\b(christus|jezus) aan het kruis\b|\bkruisiging\b|\bkruisigt\b", "crucifixion"),
    (r"\bgekruisigde\b", "crucified"),
    (r"\bkruisbeeld\b", "crucifix"),
    (r"\bcalvarie(berg)?\b", "calvary"),
    (r"\bkruisafneming\b|\bafneming van het kruis\b", "descent from the cross"),
    (r"\bbewening\b|\bbeweend\b", "lamentation"),
    (r"\bpi[eë]ta\b", "pietà"),
    (r"\bgraflegging\b|\bbegrafenis van (christus|jezus)\b", "entombment"),
    (r"\bdode (christus|lichaam van christus)\b|\blijk van christus\b|\b(christus|jezus) in het graf\b", "dead christ"),
    (r"\bman van smarten\b|\bsmartenman\b", "man of sorrows"),
    (r"\b(christus )?in het voorgeborchte\b|\bnederdaling (ter|in de) hel(le)?\b|\bhellevaart\b", "christ in limbo"),
    (r"\bopstanding( van (christus|jezus))?\b|\bverrijzenis( van (christus|jezus))?\b|\bverrezen (christus|jezus)\b", "resurrection of christ"),
    (r"\b(vrouwen|drie maria'?s) bij het (graf|lege graf)\b", "holy women at the tomb"),
    (r"\bema[uü]s\b|\bemmaüs\b", "emmaus"),
    (r"\bongelo(of|vigheid) van (thomas|tomas)\b|\bongelovige (thomas|tomas)\b", "incredulity of thomas"),
    (r"\b(christus|jezus) verschijnt aan\b|\bverschijning van (christus|jezus)\b", "christ appearing to"),
    (r"\blaatste oordeel\b|\bjongste (oordeel|dag|gericht)\b", "last judgment"),
    (r"\bkroning van (de heilige )?maria\b|\bmaria gekroond\b", "coronation of the virgin"),
    (r"\b(christus|jezus) in majesteit\b|\btronende christus\b|\bchristus op de troon\b", "christ in majesty"),
    (r"\bheilig(e)? aanschijn\b|\bhoofd van (christus|jezus)\b|\bgelaat van (christus|jezus)\b|\bportret van (christus|jezus)\b|\bbuste van (christus|jezus)\b", "holy face"),
    (r"\bheilig hart\b", "sacred heart"),
    (r"\bgoede herder\b", "good shepherd"),
    (r"\bheilige drie[- ]?(eenheid|vuldigheid)\b|\bdrie[- ]?(eenheid|vuldigheid)\b", "trinity"),
    (r"\bgenadestoel\b", "throne of mercy"),
    (r"\bmaria met (het )?kind\b|\bmaria en kind\b|\bmadonna met (het )?kind\b|\bmaagd met (het )?kind\b", "virgin and child"),
    (r"\b(christus|jezus) en de (samaritaanse|overspelige) vrouw\b|\bsamaritaanse vrouw\b|\boverspelige vrouw\b", "christ and the woman"),
    (r"\blaat de kinderen tot mij\b|\b(christus|jezus) zegent de kinderen\b|\bkinderzegen\b", "suffer the little children"),
    (r"\b(martha en maria|maria en martha)\b", "mary and martha"),
    (r"\bschatgeld\b|\bcijnspenning\b", "tribute money"),
    (r"\b(christus|jezus) (predikt|preekt|onderwijst)\b|\bprediking van (christus|jezus)\b", "christ preaching"),
    (r"\broeping van (petrus|mattheus|matteüs|andreas|de apostelen)\b", "calling of peter"),
    (r"\bheiland\b|\bverlosser\b|\bzaligmaker\b", "saviour"),
    (r"\blam gods\b", "lamb of god"),
    (r"\bchristus\b|\bchristi\b", "christ"),
    (r"\bjezus\b", "jesus"),
]
NL_EN = [(re.compile(rx, re.I), en) for rx, en in NL_EN]

lock = threading.Lock()
last_req = [0.0]


def http_get(url, accept=None, tries=4):
    for attempt in range(tries):
        with lock:  # global politeness spacing across workers
            wait = last_req[0] + MIN_INTERVAL - time.time()
            if wait > 0:
                time.sleep(wait)
            last_req[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Accept": accept} if accept else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None
            err = e
        except Exception as e:  # network hiccup
            err = e
        time.sleep(3 * (attempt + 1))
    print(f"  failed {url}: {err}", file=sys.stderr)
    return None


def search_ids(term):
    """All object ids for a title search (with images), cached per term."""
    key = hashlib.sha1(term.encode()).hexdigest()[:16]
    path = CACHE / "search" / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())["ids"]
    url = f"{SEARCH}?{urllib.parse.urlencode({'title': term, 'imageAvailable': 'true'})}"
    ids = []
    while url:
        raw = http_get(url, "application/json")
        if raw is None:
            break
        d = json.loads(raw)
        ids += [it["id"].rsplit("/", 1)[-1] for it in d.get("orderedItems", [])]
        url = (d.get("next") or {}).get("id")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"term": term, "ids": ids}))
    return ids


def get_record(oid):
    path = CACHE / "rec" / oid[-2:] / f"{oid}.xml.gz"
    if path.exists():
        return gzip.decompress(path.read_bytes())
    raw = http_get(f"{OAI}?verb=GetRecord&metadataPrefix=edm&identifier=https://id.rijksmuseum.nl/{oid}")
    if raw is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(raw))
    return raw


NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "edm": "http://www.europeana.eu/schemas/edm/",
    "edmfp": "http://www.europeanafashion.eu/edmfp/",
    "ore": "http://www.openarchives.org/ore/terms/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "svcs": "http://rdfs.org/sioc/services#",
}
RES = f"{{{NS['rdf']}}}resource"
ABOUT = f"{{{NS['rdf']}}}about"
LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def texts(el, path):
    """{lang: [text,...]} for child elements."""
    out = {}
    for e in el.findall(path, NS):
        if e.text and e.text.strip():
            out.setdefault(e.get(LANG, ""), []).append(e.text.strip())
    return out


def pick(d, *langs):
    for lg in langs:
        if d.get(lg):
            return d[lg][0]
    for v in d.values():
        return v[0]
    return ""


KIND_RULES = [
    ("manuscript", r"\b(manuscript|miniature|illumination|book of hours|handschrift|getijdenboek)\b"),
    ("painting", r"\b(painting|schilderij|panel|altarpiece|triptych|diptych|icon)\b"),
    ("print", r"\b(print|prent|engraving|etching|woodcut|book illustration)\b"),
    ("drawing", r"\b(drawing|tekening|sketch|study)\b"),
    ("photograph", r"\b(photo|photograph|foto|albumen|daguerreotype)\b"),
    ("textile", r"\b(textile|tapestry|embroidery|lace|vestment|chasuble|cloth)\b"),
    ("metalwork", r"\b(medal|plaquette|coin|token|silverwork|goldwork|monstrance|chalice|pax|reliquary|pendant|badge|metal)\b"),
    ("sculpture", r"\b(sculpture|statue|statuette|figure|relief|bust|beeld|carving|crucifix|corpus|group)\b"),
]
KIND_RULES = [(k, re.compile(rx, re.I)) for k, rx in KIND_RULES]


def kind_of(types):
    s = " | ".join(types)
    for k, rx in KIND_RULES:
        if rx.search(s):
            return k
    return "other"


def translate(nl):
    t = nl.lower()
    for rx, en in NL_EN:
        t = rx.sub(f" {en} ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_years(s):
    ys = [int(x) for x in re.findall(r"(?<!\d)(\d{3,4})(?!\d)", s)]
    return ys


def parse(oid, raw):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    cho = root.find(".//edm:ProvidedCHO", NS)
    agg = root.find(".//ore:Aggregation", NS)
    if cho is None or agg is None:
        return None
    # label lookup for every resource described in the record
    labels = {}
    for el in root.iter():
        about = el.get(ABOUT)
        if about:
            pl = texts(el, "skos:prefLabel")
            if pl:
                labels[about] = pl
    nota = {}
    for c in root.findall(".//skos:Concept", NS):
        for alt in c.findall("skos:altLabel", NS):
            if alt.get(LANG) is None and alt.text:
                nota[c.get(ABOUT)] = alt.text.strip()

    def lab(uri, *langs):
        return pick(labels.get(uri, {}), *(langs or ("en", "nl")))

    rights = (agg.find("edm:rights", NS).get(RES) if agg.find("edm:rights", NS) is not None else "")
    if "publicdomain/zero" in rights:
        lic = "CC0"
    elif "publicdomain/mark" in rights:
        lic = "Public domain"
    else:
        return None
    shown = agg.find("edm:isShownBy", NS)
    if shown is None:
        return None
    service = None
    for wr in root.findall(".//edm:WebResource", NS):
        if wr.get(ABOUT) == shown.get(RES):
            s = wr.find("svcs:has_service", NS)
            if s is not None:
                service = s.get(RES)
    if not service:
        m = re.match(r"(https://iiif\.micr\.io/[^/]+)/", shown.get(RES) or "")
        service = m.group(1) if m else None
    if not service:
        return None

    titles = texts(cho, "dc:title")
    title_nl = pick(titles, "nl", "")
    title_en = pick(titles, "en") if titles.get("en") else ""
    objnum = (cho.findtext("dc:identifier", default="", namespaces=NS) or "").strip()

    # subjects: iconclass labels for Christ (11D) and New Testament (73) codes
    ic_labels = []
    for s in cho.findall("dc:subject", NS):
        uri = s.get(RES)
        code = nota.get(uri, "")
        if code.startswith(("11D", "73")):
            ic_labels.append(lab(uri, "en"))

    en_for_match = title_en or translate(title_nl)
    u = classify(en_for_match)
    if not u and title_en:
        u = classify(translate(title_nl))
    if not u and ic_labels and not EXCLUDE.search(en_for_match):
        u = classify(en_for_match, " ; ".join(ic_labels))
    if not u:
        return None
    if u == ["Jesus Christ"] and ic_labels:
        refined = [x for x in classify(en_for_match, " ; ".join(ic_labels)) if x != "Jesus Christ"]
        if refined:
            u = refined

    created = texts(cho, "dcterms:created")
    dates = created.get("en") or created.get("nl") or next(iter(created.values()), [])
    best, y = "", None
    for dstr in dates:
        ys = parse_years(dstr)
        if not ys:
            continue
        span = max(ys) - min(ys)
        if y is None or span < bspan:
            best, y, bspan = dstr, min(ys), span
    if not best and dates:
        best = dates[0]

    creators = []
    for c in cho.findall("dc:creator", NS):
        name = lab(c.get(RES)) if c.get(RES) else (c.text or "").strip()
        if name and name not in creators:
            creators.append(name)
    named = [c for c in creators if c.lower() not in ("anonymous", "anoniem")]
    artist = "; ".join((named or creators)[:3])

    places = []
    for p in cho.findall("dcterms:spatial", NS):
        n = lab(p.get(RES)) if p.get(RES) else (p.text or "").strip()
        if n and n not in places and n.lower() not in ("unknown", "onbekend"):
            places.append(n)

    types = [lab(t.get(RES)) for t in cho.findall("dc:type", NS) if t.get(RES)]
    techs = [lab(t.get(RES)) for t in cho.findall("edmfp:technique", NS) if t.get(RES)]
    mats = [lab(t.get(RES)) for t in cho.findall("dcterms:medium", NS) if t.get(RES)]
    medium = ", ".join(dict.fromkeys(x for x in techs if x))
    if mats:
        medium = (medium + " on " if medium else "") + ", ".join(dict.fromkeys(x for x in mats if x))

    rec = {
        "id": f"rijks:{objnum or oid}",
        "t": title_en or title_nl,
        "y": y,
        "d": best,
        "a": artist,
        "c": ", ".join(places[:2]),
        "l": "Rijksmuseum, Amsterdam",
        "m": medium,
        "s": "",
        "k": kind_of(types + ([title_en] if title_en else [])),
        "img": f"{service}/full/!600,600/0/default.jpg",
        "big": f"{service}/full/!1600,1600/0/default.jpg",
        "src": f"https://www.rijksmuseum.nl/en/collection/{urllib.parse.quote(objnum)}" if objnum
               else f"https://id.rijksmuseum.nl/{oid}",
        "lic": lic,
        "u": u,
    }
    for sa in cho.findall("owl:sameAs", NS):
        m = re.search(r"wikidata\.org/entity/(Q\d+)", sa.get(RES) or "")
        if m:
            rec["wd"] = m.group(1)
            break
    return rec


def main():
    workers = 3
    if "--workers" in sys.argv:
        workers = int(sys.argv[sys.argv.index("--workers") + 1])
    ids = {}
    for term in TERMS:
        got = search_ids(term)
        new = sum(1 for i in got if i not in ids)
        for i in got:
            ids.setdefault(i, term)
        print(f"search {term!r}: {len(got)} ({new} new; total {len(ids)})", file=sys.stderr)

    todo = list(ids)
    out, done = [], [0]

    def work(oid):
        raw = get_record(oid)
        rec = parse(oid, raw) if raw else None
        with lock:
            done[0] += 1
            if done[0] % 500 == 0:
                print(f"  records {done[0]}/{len(todo)}", file=sys.stderr)
        return rec

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rec in ex.map(work, todo):
            if rec:
                out.append(rec)

    seen, uniq = set(), []
    for r in out:
        if r["id"] not in seen:
            seen.add(r["id"])
            uniq.append(r)
    uniq.sort(key=lambda x: (x["y"] is None, x["y"] or 0, x["t"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(uniq, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(uniq)} records to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
