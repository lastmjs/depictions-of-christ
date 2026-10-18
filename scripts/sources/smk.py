#!/usr/bin/env python3
"""Harvest public-domain depictions of Christ from SMK (Statens Museum for Kunst).

Runs many Danish and English keyword searches against the keyless SMK API
(https://api.smk.dk/api/v1/art/search/, filtered to public_domain + has_image),
keeps records whose English title (or the Danish title mapped to English
keywords) passes subjects.classify plus the shared filters in aic.py, and
writes data/sources/smk.json. Raw API responses are cached under .cache/smk/.
Stdlib only.
"""
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from subjects import classify  # noqa: E402,F401
from aic import NOT_CHRIST, decide  # noqa: E402  (shared filters)

CACHE = ROOT / ".cache" / "smk"
OUT = ROOT / "data" / "sources" / "smk.json"
API = "https://api.smk.dk/api/v1/art/search/"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
MIN_INTERVAL = 0.3
ROWS = 1000

QUERIES = [
    # Danish
    "Kristus", "Kristi", "Jesus", "Jesu", "Jesusbarnet", "Kristusbarnet", "Barnet", "Madonna",
    "Madonnaen", "Maria med barnet", "Jomfru Maria", "Maria", "Den hellige familie", "Hellig familie",
    "Kristi fødsel", "Jesu fødsel", "Krybben", "Hyrdernes tilbedelse", "Hyrderne", "Kongernes tilbedelse",
    "De hellige tre konger", "De vise mænd", "Helligtrekonger", "Omskærelse", "Fremstillingen i templet",
    "Flugten til Ægypten", "Flugten til Egypten", "Hvilen på flugten", "Den tolvårige Jesus",
    "Kristi dåb", "Jesu dåb", "Fristelse", "Brylluppet i Kana", "Bjergprædikenen", "Lazarus",
    "Forklarelse", "Forklarelsen på bjerget", "Indtog i Jerusalem", "Tempelrensning", "Nadver",
    "Nadveren", "Den sidste nadver", "Fodvaskning", "Getsemane", "Gethsemane", "Olivenbjerget",
    "Judaskys", "Judas", "Tilfangetagelse", "Pilatus", "Kajfas", "Hudstrygning", "Hudstrygningen",
    "Tornekroning", "Tornekronet", "Spot", "Bespottelse", "Ecce Homo", "Korsbæring", "Korset",
    "Veronika", "Svededug", "Korsfæstelse", "Korsfæstelsen", "Korsfæstede", "Krucifiks", "Golgata",
    "Calvaria", "Nedtagelse", "Korsnedtagelse", "Begrædelse", "Begrædelsen", "Pietà", "Pieta",
    "Gravlæggelse", "Gravlæggelsen", "Kristi legeme", "Den døde Kristus", "Smertensmand",
    "Smertensmanden", "Nedfart", "Opstandelse", "Opstandelsen", "Den opstandne", "Noli me tangere",
    "Emmaus", "Thomas' vantro", "Den vantro Thomas", "Himmelfart", "Kristi himmelfart",
    "Dommedag", "Den yderste dom", "Verdensdommer", "Marias kroning", "Majestas Domini", "Pantokrator",
    "Salvator Mundi", "Frelser", "Frelseren", "Verdens frelser", "Den gode hyrde", "Jesu hjerte",
    "Treenighed", "Treenigheden", "Nådestol", "Guds lam", "Den samaritanske kvinde",
    "Lad de små børn", "Martha og Maria", "Skattepengen", "Apostle", "Disciple", "Passion",
    "Lidelseshistorie", "Altertavle", "Alterbillede", "Ikon",
    # English
    "Christ", "Virgin and Child", "Madonna and Child", "Holy Family", "Crucifixion", "Nativity",
    "Adoration", "Magi", "Lamentation", "Entombment", "Deposition", "Resurrection", "Last Supper",
    "Man of Sorrows", "Flagellation", "Transfiguration", "Ascension", "Last Judgment", "Baptism",
    "Flight into Egypt", "Saviour", "Redeemer", "Trinity", "Christ Child", "Infant",
]

# Danish -> English keyword mapping (applied to lowercased Danish titles before classify()).
DA_EN = [
    (r"\bmarias? himmelfart\b|\bmariæ himmelfart\b|\bjomfru marias himmelfart\b", "assumption"),
    (r"\b(kristi |jesu )?himmelfart\b", "ascension"),
    (r"\bden ubesmittede undfangelse\b", "immaculate conception"),
    (r"\bbebudelse(n)?\b|\bmariæ bebudelse\b", "annunciation"),
    (r"\bmarias besøg hos elisabeth?\b|\bmariæ besøgelse\b", "visitation"),
    (r"\b(jesus|kristus)barn(et)?\b|\bjesus-?barnet\b|\bkristus-?barnet\b|\bbarnet jesus\b", "christ child"),
    (r"\b(kristi|jesu) fødsel\b|\bkrybben\b|\bjulekrybbe\b|\bjulenat(ten)?\b|\bfødselen\b", "nativity"),
    (r"\bhyrdernes tilbedelse\b|\bhyrderne tilbeder\b", "adoration of the shepherds"),
    (r"\bbebudelsen for hyrderne\b|\bforkyndelsen for hyrderne\b", "annunciation to the shepherds"),
    (r"\b(kongernes|de hellige tre kongers|de tre kongers|de vise mænds|magiernes) tilbedelse\b|\bde hellige tre konger\b|\bhelligtrekonger\b|\bde vise mænd\b|\bde tre vise\b", "adoration of the magi"),
    (r"\bomskærelse(n)?\b|\bjesu omskærelse\b", "circumcision"),
    (r"\bfremstilling(en)? i templet\b|\bjesu fremstilling\b|\bkyndelmisse\b", "presentation in the temple"),
    (r"\bhvile(n)? på flugten\b|\bhvil(en|e)? under flugten\b", "rest on the flight"),
    (r"\bflugte(n)? til (æ|e)gypten\b|\bhjemkomsten fra (æ|e)gypten\b", "flight into egypt"),
    (r"\b(den )?hellige familie\b|\bhellig familie\b", "holy family"),
    (r"\bden tolvårige jesus\b|\bjesus (blandt|i templet blandt) de skriftkloge\b|\bjesus i templet\b|\bden 12-årige jesus\b", "christ among the doctors"),
    (r"\bmystiske? (bryllup|forlovelse|formæling)\b|\bkatarinas (mystiske )?(bryllup|forlovelse|formæling)\b", "mystic marriage"),
    (r"\b(kristi|jesu) dåb\b|\b(kristus|jesus) døbes\b|\bdåben i jordan\b", "baptism of christ"),
    (r"\b(kristi|jesu) fristelse\b|\b(kristus|jesus) i ørkenen\b|\bfristelsen i ørkenen\b", "temptation of christ"),
    (r"\bbryllup(pet)? i kana\b", "wedding at cana"),
    (r"\bbjergprædiken(en)?\b", "sermon on the mount"),
    (r"\blazarus'? opvækkelse\b|\bopvækkelse(n)? af lazarus\b|\bjairus'? datter\b|\bjairi datter\b|\benken i nain\b", "raising of lazarus"),
    (r"\bdet underfulde fiskedræt\b|\bpeters fiskedræt\b", "miraculous draught"),
    (r"\bbespisning(en)?\b|\bbrødunder(et)?\b", "feeding of the five thousand"),
    (r"\b(kristus|jesus) går på vandet\b|\bgående på vandet\b", "walking on water"),
    (r"\b(kristus|jesus) helbreder\b|\bhelbredelse(n)? af\b", "healing of the"),
    (r"\bforklarelse(n)?( på bjerget)?\b|\b(kristi|jesu) forklarelse\b|\btransfiguration(en)?\b", "transfiguration"),
    (r"\b(kristi|jesu) indtog i jerusalem\b|\bindtoget i jerusalem\b|\bpalmesøndag\b", "entry into jerusalem"),
    (r"\btempelrensning(en)?\b|\buddrivelse(n)? af (kræmmerne|pengevekslerne|købmændene)\b|\bpengevekslerne\b|\bkræmmerne\b", "cleansing of the temple"),
    (r"\b(den )?sidste nadver\b|\bden hellige nadver\b|\bnadverens indstiftelse\b|\bnadver(en)?\b", "last supper"),
    (r"\bfodvaskning(en)?\b|\b(kristus|jesus) vasker\b", "christ washing the disciples feet"),
    (r"\bgetsemane\b|\bgethsemane\b|\boliebjerget\b|\bolivenbjerget\b|\b(kristus|jesus) i (urtegården|haven)\b|\burtegården\b|\b(kristi|jesu) bøn i\b|\bsjælekamp(en)?\b", "agony in the garden"),
    (r"\bjudaskys(set)?\b|\bjudas'? kys\b|\bjudas forråder\b|\bforræderiet\b", "kiss of judas"),
    (r"\b(kristi|jesu) tilfangetagelse\b|\btilfangetagelse(n)?\b|\b(kristus|jesus) tages til fange\b", "arrest of christ"),
    (r"\b(kristus|jesus) for (pilatus|kajfas|annas|herodes|ypperstepræsten)\b", "christ before pilate"),
    (r"\bpilatus (to|vasker)\w*\b", "pilate washing"),
    (r"\bhudstrygning(en)?\b|\bhudstrøget\b|\bgeissel\w*\b|\b(kristus|jesus) ved (søjlen|piskestøtten|martersøjlen)\b|\bpiskning(en)?\b", "flagellation"),
    (r"\btornekron\w*\b", "crowning with thorns"),
    (r"\bbespottelse(n)?\b|\b(kristus|jesus) (be)?spottes\b|\bspottelse(n)? af\b|\bhånelse(n)?\b", "mocking of christ"),
    (r"\b(kristus|jesus) bærer korset\b|\bkorsbæring(en)?\b|\bkorsbæreren\b|\bvejen til golgata\b|\bkorsvej(en)?\b|\bkorsvejsstation\w*\b|\bkorsets bæren\b", "christ carrying the cross"),
    (r"\bveronikas (svededug|dug|klæde)\b|\bsvededug(en)?\b|\bveronika(s)? (med|holder)\b", "veil of veronica"),
    (r"\bkorsfæstelse(n)?\b|\b(kristus|jesus) på korset\b|\bkorsfæstes\b|\bkorsfæstet\b", "crucifixion"),
    (r"\bden korsfæstede\b|\bkorsfæstede (kristus|frelser)\b", "crucified"),
    (r"\bkrucifiks\w*\b", "crucifix"),
    (r"\bgolgata\b|\bcalvaria\w*\b|\bkalvarie\w*\b", "calvary"),
    (r"\bnedtagelse(n)?( af korset| fra korset)?\b|\bkorsnedtagelse(n)?\b|\bnedtagningen\b", "descent from the cross"),
    (r"\bbegrædelse(n)?\b|\bklagen over\b|\bbegrædt\b", "lamentation"),
    (r"\bpi[eé]t[àa]\b", "pietà"),
    (r"\bgravlæggelse(n)?\b|\b(kristi|jesu) begravelse\b", "entombment"),
    (r"\bden døde kristus\b|\bdøde kristus\b|\b(kristi|jesu) (legeme|lig)\b|\b(kristus|jesus) i graven\b", "dead christ"),
    (r"\bsmertensmand(en)?\b|\bsmerternes mand\b", "man of sorrows"),
    (r"\b(kristi|jesu) nedfart\b|\bnedfart(en)? til (helvede|dødsriget)\b|\b(kristus|jesus) i forhelvede\b|\bforhelvede\b", "christ in limbo"),
    (r"\b(kristi|jesu) opstandelse\b|\bopstandelse(n)?\b|\bden opstandne( kristus| frelser)?\b|\bopstanden\b", "resurrection of christ"),
    (r"\bkvinderne ved (graven|den tomme grav)\b|\bde tre marier\b|\bde hellige kvinder ved graven\b", "holy women at the tomb"),
    (r"\bemmaus\b", "emmaus"),
    (r"\b(den )?vantro thomas\b|\bthomas'? vantro\b|\btvivlende thomas\b", "incredulity of thomas"),
    (r"\b(kristus|jesus) åbenbarer sig\b|\b(kristus|jesus) viser sig\b|\b(kristi|jesu) åbenbaring\b", "christ appearing to"),
    (r"\bden yderste dom\b|\bdommedag(en)?\b|\bverdensdommer(en)?\b|\bdet sidste? dom\b", "last judgment"),
    (r"\bmarias kroning\b|\bmariæ kroning\b|\bkroningen af maria\b|\bjomfru marias kroning\b", "coronation of the virgin"),
    (r"\b(kristus|jesus) i majestæt\b|\btronende kristus\b|\bkristus på tronen\b|\bvelsignende kristus\b|\bden velsignende kristus\b|\bkristus velsigner\b", "christ in majesty"),
    (r"\b(kristi|jesu) hoved\b|\b(kristi|jesu) ansigt\b|\bkristushoved\b|\bportræt af (kristus|jesus)\b|\bbuste af (kristus|jesus)\b|\b(kristi|jesu) åsyn\b|\bkristus i profil\b", "holy face"),
    (r"\bjesu hjerte\b|\bdet hellige hjerte\b", "sacred heart"),
    (r"\bden gode hyrde\b|\bgode hyrde\b", "good shepherd"),
    (r"\b(den hellige )?treenighed(en)?\b|\bnådestol(en)?\b", "trinity"),
    (r"\b(jomfru )?maria med (jesus)?barnet\b|\bmadonna(en)? med (jesus)?barnet\b|\bmaria og barnet\b|\bmadonna(en)? med barn\b|\bmaria med barn\b|\bmadonna og barn\b|\bmaria med jesus\b|\bguds moder\b|\bgudsmoder(en)?\b|\btronende madonna\b|\bmadonnaen\b", "virgin and child"),
    (r"\b(kristus|jesus) og den samaritanske kvinde\b|\bden samaritanske kvinde\b|\bkvinden ved brønden\b|\bden ægteskabsbryderske\b|\bhorkvinden\b|\bden kananæiske kvinde\b", "christ and the woman"),
    (r"\blad de små børn\b|\b(kristus|jesus) velsigner (de små )?børnene?\b|\bbørnevennen\b", "suffer the little children"),
    (r"\b(martha og maria|maria og martha|marta og maria)\b", "mary and martha"),
    (r"\bskattepenge(n)?\b|\bskatten til kejseren\b", "tribute money"),
    (r"\b(kristus|jesus) prædiker\b|\b(kristus|jesus) underviser\b|\b(kristi|jesu) prædiken\b", "christ preaching"),
    (r"\bfrelser(en)?\b|\bverdens frelser\b|\bforløser(en)?\b", "saviour"),
    (r"\bguds lam\b", "lamb of god"),
    (r"\bkristus\b|\bkristi\b|\bkristum\b", "christ"),
    (r"\bjesus\b|\bjesu\b", "jesus"),
]
DA_EN = [(re.compile(rx, re.I), en) for rx, en in DA_EN]
# "<event> af <someone other than Christ>" must not be read as the Christ event.
OTHER_EVENT = re.compile(
    r"\b(gravlæggelse|begrædelse|fødsel|dåb|himmelfart|korsfæstelse|opstandelse|hudstrygning|"
    r"martyrdød|bespottelse|nedtagelse|kroning)(n|en)? af (?!(kristus|jesus|kristi|jesu|frelseren|herren|"
    r"vor herre|korset)\b)", re.I)
# Titles naming a non-Christ main subject ("Den hellige Hieronymus", "Apostlen Peter").
DA_OTHER = re.compile(r"^(den )?(hellige|sankt|skt\.?|st\.?) \w+$|^(apostlen|profeten|evangelisten) \w+$", re.I)


def translate(da):
    t = OTHER_EVENT.sub("xx ", da.lower())
    for rx, en in DA_EN:
        t = rx.sub(f" {en} ", t)
    return re.sub(r"\s+", " ", t).strip()


_last = [0.0]


def fetch(params):
    url = API + "?" + urllib.parse.urlencode(params)
    key = hashlib.sha1(url.encode()).hexdigest()
    path = CACHE / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    delay = 2.0
    for attempt in range(8):
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode())
            CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False))
            return data
        except urllib.error.HTTPError as e:
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


def search(term):
    offset = 0
    while True:
        data = fetch({"keys": term, "filters": "[public_domain:true],[has_image:true]",
                      "offset": offset, "rows": ROWS, "lang": "en"})
        rows = data.get("items") or []
        yield from rows
        offset += ROWS
        if not rows or offset >= data.get("found", 0):
            break


def titles(r):
    en, da = [], []
    for t in r.get("titles") or []:
        s = (t.get("title") or "").strip()
        if not s:
            continue
        lang = (t.get("language") or "").lower()
        typ = (t.get("type") or "").lower()
        if typ in ("serietitel",):  # series titles describe the series, not the sheet
            continue
        (en if lang in ("engelsk", "english") else da).append(s)
    return en, da


def series_titles(r):
    return [(t.get("title") or "").strip() for t in r.get("titles") or []
            if (t.get("type") or "").lower() == "serietitel" and t.get("title")]


KIND = [
    ("icon", r"\bicon"),
    ("manuscript", r"manuscript|illuminat|miniature"),
    ("painting", r"^painting|oil painting|altarpiece|panel painting|tempera"),
    ("print", r"print|engraving|etching|woodcut|lithograph|mezzotint|aquatint|drypoint|stipple|chiaroscuro"),
    ("drawing", r"drawing|sketch|study|watercolou?r|chalk|pen|gouache|pastel|graphite|pencil"),
    ("photograph", r"photograph"),
    ("sculpture", r"sculpture|statue|relief|bust|cast|figure|plaster|marble|bronze|terracotta|statuette|group"),
    ("textile", r"textile|tapestry|embroider"),
    ("ceramic", r"ceramic|porcelain|faience"),
    ("glass", r"\bglass"),
    ("enamel", r"enamel"),
    ("ivory", r"ivory"),
    ("metalwork", r"medal|coin|silver|gold|metal"),
    ("book", r"\bbook\b"),
]


def kind(r):
    names = [(o.get("name") or "").lower() for o in r.get("object_names") or []]
    tech = " ".join(r.get("techniques") or []).lower()
    for text in names + [tech]:
        for k, rx in KIND:
            if text and re.search(rx, text):
                return k
    return "other"


def year(r):
    for d in r.get("production_date") or []:
        s, e = d.get("start"), d.get("end")
        ys = int(s[:4]) if s and re.match(r"-?\d{4}", s) else None
        ye = int(e[:4]) if e and re.match(r"-?\d{4}", e) else None
        if ys is None and ye is None:
            continue
        if ys is None or ye is None:
            return ys if ys is not None else ye
        return int(round((ys + ye) / 2)) if 0 <= ye - ys <= 300 else ys
    return None


def date_text(r):
    for d in r.get("production_date") or []:
        if d.get("period"):
            return d["period"]
    return ""


def artist(r):
    a = [x for x in r.get("artist") or [] if x]
    if a:
        return a[0]
    for p in r.get("production") or []:
        if p.get("creator"):
            return p["creator"]
    return ""


def culture(r):
    for p in r.get("production") or []:
        if p.get("creator_nationality"):
            return p["creator_nationality"]
    return ""


def decide_record(r):
    en, da = titles(r)
    a = artist(r)
    for t in en + da:
        if NOT_CHRIST.search(t):
            return [], (en or da or [""])[0]
    for t in en:
        u = decide(t, "", a)
        if u:
            return u, t
    for t in da:
        if DA_OTHER.search(t):
            continue
        u = decide(translate(t), "", a)
        if u:
            return u, (en[0] if en else t)
    return [], (en or da or [""])[0]


def main():
    seen = {}
    for term in QUERIES:
        n = 0
        for r in search(term):
            seen.setdefault(r.get("object_number") or r["id"], r)
            n += 1
        print(f"{term!r}: {n} hits, {len(seen)} unique", file=sys.stderr)

    out = []
    for r in seen.values():
        if not r.get("public_domain") or not r.get("has_image"):
            continue
        iiif = r.get("image_iiif_id")
        if iiif:
            small = f"{iiif}/full/!600,600/0/default.jpg"
            big = f"{iiif}/full/!1600,1600/0/default.jpg"
        elif r.get("image_thumbnail"):
            small = big = r["image_thumbnail"]
        else:
            continue
        u, title = decide_record(r)
        if not u:
            continue
        rec = {
            "id": f"smk:{r['object_number']}",
            "t": title,
            "y": year(r),
            "d": date_text(r),
            "a": artist(r),
            "c": culture(r),
            "l": "Statens Museum for Kunst, Copenhagen",
            "m": ", ".join(r.get("techniques") or []),
            "s": "",
            "k": kind(r),
            "img": small,
            "big": big,
            "src": r.get("frontend_url") or f"https://open.smk.dk/artwork/image/{r['object_number']}",
            "lic": "Public domain",
            "u": u,
        }
        out.append(rec)
    out.sort(key=lambda x: (x["y"] is None, x["y"] or 0, x["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {len(out)} records (of {len(seen)} candidates) to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
