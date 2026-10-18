#!/usr/bin/env python3
"""Harvest CC0 depictions of Christ from the Finnish National Gallery open data.

The keyless endpoint https://kokoelma.kansallisgalleria.fi/api/v1/objects
returns the whole collection (~90k records, ~30 MB gzipped) in one JSON array;
the gzipped response is cached under .cache/fng/. Records with a CC0 image whose
English title (or the Finnish / Swedish title mapped to English keywords, plus
Christ-specific subject keywords) passes subjects.classify and the shared
filters in aic.py are written to data/sources/fng.json. Stdlib only.
"""
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from subjects import classify  # noqa: E402,F401
from aic import NOT_CHRIST, decide  # noqa: E402  (shared filters)
from nationalmuseum import translate as sv_translate  # noqa: E402  (Swedish -> English)

CACHE = ROOT / ".cache" / "fng"
OUT = ROOT / "data" / "sources" / "fng.json"
API = "https://kokoelma.kansallisgalleria.fi/api/v1/objects"
BASE = "https://kokoelma.kansallisgalleria.fi"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"


def load():
    path = CACHE / "objects.json.gz"
    if not path.exists():
        delay = 5.0
        for attempt in range(6):
            req = urllib.request.Request(API, headers={
                "User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "gzip"})
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    raw = r.read()
                    if r.headers.get("Content-Encoding") != "gzip":
                        raw = gzip.compress(raw)
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504):
                    ra = e.headers.get("Retry-After")
                    time.sleep(float(ra) if ra and ra.isdigit() else delay)
                    delay *= 2
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(delay)
                delay *= 2
        else:
            raise RuntimeError("giving up on FNG objects dump")
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return json.loads(gzip.decompress(path.read_bytes()).decode())


# --- Finnish -> English keyword mapping (applied to lowercased Finnish titles) ---
# Finnish is heavily inflected, so stems are matched with \w* suffixes.
FI_EN = [
    (r"\bneitsyt marian taivaaseen\w*\b|\bmarian taivaaseen\w*\b", "assumption"),
    (r"\btaivaaseen ?astum\w*\b|\b(kristuksen|jeesuksen) taivaaseen\w*\b", "ascension"),
    (r"\bilmestys\w*\b|\bmarian ilmestys\w*\b", "annunciation"),
    (r"\bmarian (vierailu|käynti) elisabet\w*\b", "visitation"),
    (r"\bjeesus-?lap\w*\b|\bjeesuslap\w*\b|\bkristus-?lap\w*\b|\blapsi jeesus\b|\bjeesus lapse\w*\b", "christ child"),
    (r"\b(kristuksen|jeesuksen) syntymä\w*\b|\bseimi\w*\b|\bseimessä\b|\bjouluyö\b", "nativity"),
    (r"\bpaimenten (kumarrus|palvonta|ylistys)\w*\b|\bpaimenet kumartavat\b", "adoration of the shepherds"),
    (r"\b(tietäjien|itämaan tietäjien|kolmen kuninkaan) (kumarrus|palvonta|lahjat|ylistys)\w*\b|\bitämaan tietäjä\w*\b|\bkolme kuningasta\b|\bloppiai\w*\b", "adoration of the magi"),
    (r"\bympärileikkau\w*\b", "circumcision"),
    (r"\b(jeesuksen|kristuksen) (esittäminen|tuominen) temppeli\w*\b|\btemppeliin tuominen\b", "presentation in the temple"),
    (r"\blepo\w* pakomatkalla\b|\blepohetki pakomatka\w*\b|\blepo egyptiin\w*\b", "rest on the flight"),
    (r"\bpako\w* egyptiin\b|\bpakomatka egyptiin\b|\bpaluu egyptistä\b", "flight into egypt"),
    (r"\bpyhä perhe\b|\bpyhän perheen\b|\bpyhä perhe\w*\b", "holy family"),
    (r"\b(12|kaksitoista)-?vuotias jeesus\b|\bjeesus temppelissä\b|\bjeesus (kirjanoppineiden|opettajien) (keskellä|parissa)\b", "christ among the doctors"),
    (r"\bmystinen (kihlaus|avioliitto)\b|\bkatariinan (mystinen )?kihlaus\b", "mystic marriage"),
    (r"\b(kristuksen|jeesuksen) kaste\w*\b|\bjeesus kasteella\b", "baptism of christ"),
    (r"\b(kristuksen|jeesuksen) kiusau\w*\b|\b(kristus|jeesus) erämaassa\b", "temptation of christ"),
    (r"\bkaanaan häi\w*\b|\bhäät kaanaassa\b", "wedding at cana"),
    (r"\bvuorisaarna\w*\b", "sermon on the mount"),
    (r"\blasaruksen herättä\w*\b|\bjairuksen tyttäre\w*\b|\bherättää .{0,30} kuollei\w*\b", "raising of lazarus"),
    (r"\bihmeellinen kalansaa\w*\b", "miraculous draught"),
    (r"\bviidentuhannen ruokki\w*\b|\bleipäihme\w*\b", "feeding of the five thousand"),
    (r"\b(kristus|jeesus) kävelee veden päällä\b", "walking on water"),
    (r"\b(kristus|jeesus) parantaa\b|\bparantaa sairaa\w*\b", "healing of the"),
    (r"\b(kristuksen|jeesuksen) kirkastu\w*\b|\bkirkastusvuore\w*\b", "transfiguration"),
    (r"\b(kristuksen|jeesuksen) (saapuminen|tulo|ratsastus) jerusalemiin\b|\bjerusalemiin ratsastus\b|\bpalmusunnuntai\w*\b", "entry into jerusalem"),
    (r"\btemppelin puhdistu\w*\b|\brahanvaihtaj\w*\b", "cleansing of the temple"),
    (r"\bviimei\w* ehtoolli\w*\b|\behtoollise\w* asettaminen\b|\bpyhä ehtoollinen\b", "last supper"),
    (r"\bjalkojen pes\w*\b|\bjeesus pesee\b", "christ washing the disciples feet"),
    (r"\bgetsemane\w*\b|\böljymäe\w*\b|\b(kristus|jeesus) yrttitarha\w*\b|\byrttitarha\w*\b", "agony in the garden"),
    (r"\bjuudaksen suudelma\w*\b|\bjuudas pettää\b", "kiss of judas"),
    (r"\b(kristuksen|jeesuksen) vangitse\w*\b|\bjeesus vangitaan\b", "arrest of christ"),
    (r"\b(kristus|jeesus) (pilatuksen|kaifaan|hannaan|herodeksen|ylipapin) edessä\b", "christ before pilate"),
    (r"\bpilatus pesee\b", "pilate washing"),
    (r"\bruoski\w*\b|\b(kristus|jeesus) paalu\w*\b|\bpiiska\w*\b", "flagellation"),
    (r"\borjantappura\w*\b", "crowning with thorns"),
    (r"\b(kristuksen|jeesuksen) pilkka\w*\b|\bjeesusta pilkataan\b|\bpilkattu kristus\b", "mocking of christ"),
    (r"\bristinkanta\w*\b|\bristiä kantava\w*\b|\b(kristus|jeesus) kantaa ristiä\w*\b|\bristin kantaminen\b|\bristintie\w*\b|\btie golgatalle\b|\bvia crucis\b", "christ carrying the cross"),
    (r"\bveronikan liina\w*\b|\bhikiliina\w*\b", "veil of veronica"),
    (r"\bristiinnaulit\w*\b|\bristiinaulit\w*\b|\b(kristus|jeesus) ristillä\b|\bristillä\b|\bkrusifiks\w*\b|\bristiinnaulittu\w*\b", "crucifixion"),
    (r"\bgolgata\w*\b|\bkalvaria\w*\b", "calvary"),
    (r"\bristiltä otto\b|\bristiltä ottaminen\b|\bristiltäotto\b|\bristiltä laske\w*\b", "descent from the cross"),
    (r"\b(kristuksen|jeesuksen) (valitus|surem\w*|itkem\w*)\b|\bvalitus kristuksen\b", "lamentation"),
    (r"\bpi[eè]t[àa]\b", "pietà"),
    (r"\b(kristuksen|jeesuksen) hautaa\w*\b|\bhautaanpan\w*\b|\bhautaan pan\w*\b|\bhautaaminen\b", "entombment"),
    (r"\bkuollut kristus\b|\bkuolleen kristuksen\b|\b(kristuksen|jeesuksen) ruumi\w*\b|\b(kristus|jeesus) haudassa\b", "dead christ"),
    (r"\bkärsivä kristus\b|\bkärsimyksen mies\b|\bkivun mies\b", "man of sorrows"),
    (r"\b(kristuksen|jeesuksen) helvettiin\w*\b|\btuonelaan laskeutu\w*\b", "christ in limbo"),
    (r"\b(kristuksen|jeesuksen) ylösnousem\w*\b|\bylösnousemu\w*\b|\bylösnoussut\b|\bylösnousseen\b", "resurrection of christ"),
    (r"\bnaiset haudalla\b|\bnaiset tyhjällä haudalla\b|\bkolme mariaa\b", "holy women at the tomb"),
    (r"\bemmau\w*\b", "emmaus"),
    (r"\bepäilevä tuomas\b|\btuomaan epäily\w*\b|\bepäuskoinen tuomas\b", "incredulity of thomas"),
    (r"\b(kristus|jeesus) ilmestyy\b|\b(kristuksen|jeesuksen) ilmestyminen\b", "christ appearing to"),
    (r"\bviimei\w* tuomio\w*\b|\btuomiopäiv\w*\b", "last judgment"),
    (r"\bmarian kruunau\w*\b|\bneitsyt marian kruunau\w*\b", "coronation of the virgin"),
    (r"\bvaltaistuimella istuva kristus\b|\bkristus valtaistuimella\b|\bpantokrator\b", "christ in majesty"),
    (r"\b(kristuksen|jeesuksen) (pää|kasvot|muotokuva|rintakuva)\w*\b|\bkristuksen pään\b", "holy face"),
    (r"\bjeesuksen sydän\b|\bpyhä sydän\b", "sacred heart"),
    (r"\bhyvä paimen\b|\bhyvän paimenen\b", "good shepherd"),
    (r"\bpyhä kolminaisuus\b|\bkolminaisuu\w*\b", "trinity"),
    (r"\bmadonna ja lapsi\b|\bneitsyt maria ja (jeesus-?)?lapsi\b|\bmaria ja lapsi\b|\bmadonna lapsen kanssa\b|\bmaria lapsen kanssa\b|\bneitsyt ja lapsi\b|\bjumalanäiti\b|\bjumalansynnyttäjä\w*\b", "virgin and child"),
    (r"\b(kristus|jeesus) ja samarialai\w* nai\w*\b|\bsamarialainen nainen\b|\blangenneen naisen\b|\baviorikokse\w* tavattu\b|\bsyntinen nainen\b", "christ and the woman"),
    (r"\bsallikaa lasten\b|\b(kristus|jeesus) siunaa lapsia\b|\blasten siunaaminen\b", "suffer the little children"),
    (r"\bmartta ja maria\b|\bmaria ja martta\b", "mary and martha"),
    (r"\bveroraha\w*\b", "tribute money"),
    (r"\b(kristus|jeesus) saarnaa\w*\b|\b(kristus|jeesus) opettaa\b", "christ preaching"),
    (r"\bvapahtaja\w*\b|\bvapahtajan\b|\blunastaja\w*\b", "saviour"),
    (r"\bjumalan karitsa\b", "lamb of god"),
    (r"\bkristu(s|ksen|sta|kselle|ksessa|kseen|ksesta)\b", "christ"),
    (r"\bjeesu(s|ksen|sta|kselle|ksessa|kseen|ksesta)\b", "jesus"),
]
FI_EN = [(re.compile(rx, re.I), en) for rx, en in FI_EN]


def fi_translate(fi):
    t = fi.lower()
    for rx, en in FI_EN:
        t = rx.sub(f" {en} ", t)
    return re.sub(r"\s+", " ", t).strip()


# Christ-specific subject keywords (English labels) that may support classification.
KW = {
    "jesus": "Jesus", "christ": "Christ", "baby jesus": "Christ Child", "christ child": "Christ Child",
    "madonna and child": "Madonna and Child", "crucifixion": "Crucifixion", "crucified": "Crucifixion",
    "jesus on the cross": "Crucifixion", "dead christ": "dead Christ", "body of christ": "dead Christ",
    "sufferings of christ": "Christ", "jesus baptized": "Baptism of Christ",
    "baptism of christ": "Baptism of Christ", "nativity": "Nativity", "glory of christ": "Transfiguration",
    "jesus in the temple": "Christ among the doctors", "jesus at the feast": "Christ",
    "ascent of christ into heaven": "Ascension", "life of christ": "Christ", "holy family": "Holy Family",
    "ecce homo": "Ecce Homo", "pietà": "Pietà", "last supper": "Last Supper", "resurrection": "",
    "lamentation": "", "entombment": "",
}
KW_ONLY_BAD = re.compile(r"\b(connoisseurs?|gallery|galleria\w*|interior|interiööri|still life|asetelma|"
                         r"letter|kirje|portrait of|muotokuva)\b", re.I)
SECULAR_KW = re.compile(r"\b(christmas|father christmas|santa claus)\b", re.I)
SKIP_CATEGORY = {"letter", "Document", "collection", "series"}


def tx(d, *langs):
    d = d or {}
    for lang in langs:
        v = (d.get(lang) or "").strip()
        if v:
            return v
    return ""


def keywords(r):
    return [(k.get("en") or "").strip() for k in r.get("keywords") or [] if k.get("en")]


def kind(r):
    cls = " ".join((c.get("en") or "") for c in r.get("classifications") or []).lower()
    mat = " ".join((m.get("en") or "") for m in r.get("materials") or []).lower()
    for k, rx in [("icon", r"\bicon"), ("manuscript", r"persian miniature|indian miniature"),
                  ("print", r"graphic arts|woodblock print|late impression|poster|book illustration"),
                  ("drawing", r"drawing|sketch"), ("painting", r"painting|miniature"),
                  ("sculpture", r"sculpture|relief|medallion"), ("photograph", r"photograph"),
                  ("other", r"mosaic|crafts")]:
        if re.search(rx, cls):
            return k
    for k, rx in [("print", r"etching|engraving|woodcut|lithograph|mezzotint|aquatint|print"),
                  ("drawing", r"pencil|chalk|ink|watercolou?r|graphite|charcoal|gouache|pastel"),
                  ("painting", r"\boil\b|tempera|canvas"), ("sculpture", r"marble|bronze|plaster|terracotta"),
                  ("photograph", r"photograph|albumen|gelatin")]:
        if re.search(rx, mat):
            return k
    cat = (r.get("category") or {}).get("en") or ""
    if cat == "image" or "Arkisto" in (r.get("responsibleOrganisation") or ""):
        return "photograph"  # archive photographs (mostly reproductions of artworks)
    if cat == "part of a work":
        return "drawing"  # unclassified sketchbook / album leaves
    return "other"


def year(r):
    s, e = r.get("yearFrom"), r.get("yearTo")
    if s is None and r.get("dateFrom"):
        m = re.match(r"(\d{4})", r["dateFrom"])
        s = int(m.group(1)) if m else None
    if s is None and e is None:
        return None
    if s is None or e is None:
        return int(s if s is not None else e)
    return int(round((s + e) / 2)) if 0 <= e - s <= 300 else int(s)


def date_text(r):
    s, e = r.get("yearFrom"), r.get("yearTo")
    pre = (r.get("datePrefix") or "")
    pre = pre.get("en", "") if isinstance(pre, dict) else str(pre)
    if s and e and e != s:
        d = f"{s}–{e}"
    elif s:
        d = str(s)
    elif r.get("dateFrom"):
        d = r["dateFrom"][:4]
    else:
        d = ""
    return f"{pre} {d}".strip() if d else ""


def artist(r):
    for p in r.get("people") or []:
        n = " ".join(x for x in [p.get("firstName"), p.get("familyName")] if x).strip()
        if n:
            return n
    return ""


def decide_record(r):
    en = tx(r.get("title"), "en")
    fi = tx(r.get("title"), "fi")
    sv = tx(r.get("title"), "sv")
    a = artist(r)
    for t in (en, fi, sv):
        if t and NOT_CHRIST.search(t):
            return [], en or fi or sv
    kws = [k for k in keywords(r) if k.lower() in KW and not SECULAR_KW.search(k)]
    extra = " ".join(sorted({KW[k.lower()] for k in kws if KW[k.lower()]}))
    title = en or fi or sv
    u = []
    if en:
        u = decide(en, "", a)
    if not u and fi:
        u = decide(fi_translate(fi), "", a)
    if not u and sv:
        u = decide(sv_translate(sv), "", a)
    if u and u != ["Jesus Christ"]:
        return u, title
    # Fall back to the curated subject keywords (only when they name Christ and
    # the title does not describe a gallery interior or still life).
    if extra and not KW_ONLY_BAD.search(f"{en} {fi}"):
        u2 = classify(extra)
        if u2:
            return (u2 if u2 != ["Jesus Christ"] or not u else u), title
    return u, title


def main():
    data = load()
    print(f"{len(data)} records in dump", file=sys.stderr)
    out = []
    for r in data:
        mm = [m for m in r.get("multimedia") or [] if (m.get("license") or "").upper() == "CC0" and m.get("jpg")]
        if not mm:
            continue
        cat = (r.get("category") or {}).get("en") or ""
        if cat in SKIP_CATEGORY:
            continue
        u, title = decide_record(r)
        if not u:
            continue
        m = mm[0]
        jpg = m["jpg"]
        small = jpg.get("500") or jpg.get("1000") or jpg.get("250")
        big = jpg.get("2000") or jpg.get("1000") or small
        rec = {
            "id": f"fng:{r['objectId']}",
            "t": title,
            "y": year(r),
            "d": date_text(r),
            "a": artist(r),
            "c": "",
            "l": "Finnish National Gallery, Helsinki",
            "m": ", ".join(x.get("en") or x.get("fi") or "" for x in r.get("materials") or [] if x.get("en") or x.get("fi")),
            "s": "",
            "k": kind(r),
            "img": BASE + small,
            "big": BASE + big,
            "src": f"{BASE}/en/object/{r['objectId']}",
            "lic": "CC0",
            "u": u,
        }
        out.append(rec)
    out.sort(key=lambda x: (x["y"] is None, x["y"] or 0, x["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {len(out)} records to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
