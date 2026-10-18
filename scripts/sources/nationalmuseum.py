#!/usr/bin/env python3
"""Harvest depictions of Christ from Nationalmuseum (Stockholm) via its open API.

The keyless API at https://api.nationalmuseum.se/api/objects has no working
search parameter, so the whole collection (~210k records) is paged through
(limit=100). Each page is cached under .cache/nationalmuseum/ slimmed to the
records that carry an IIIF image. Records whose English title (or the Swedish
title translated to English keywords) passes subjects.classify plus the shared
filters in aic.py are written to data/sources/nationalmuseum.json.
Only images marked "Public Domain" or "CC BY SA" by the museum are kept.
Stdlib only.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from subjects import classify  # noqa: E402,F401
from aic import NOT_CHRIST, decide  # noqa: E402  (shared filters)

CACHE = ROOT / ".cache" / "nationalmuseum"
OUT = ROOT / "data" / "sources" / "nationalmuseum.json"
API = "https://api.nationalmuseum.se/api/objects"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
MIN_INTERVAL = 0.4  # between request starts, all workers combined
WORKERS = 3
LIMIT = 100

_last = [0.0]
_rate = threading.Lock()


def http_json(url):
    delay = 2.0
    for attempt in range(10):
        with _rate:
            wait = MIN_INTERVAL - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode())
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


def has_image(it):
    return bool(it.get("iiif")) or any((a.get("type") == "IIIF") for a in it.get("assets") or [])


KEEP = ("id", "inventory_number", "iiif", "iiif_license", "title", "technique_material", "category",
        "object_type", "dating", "geografical_reference", "motive_category")


def slim(it):
    s = {k: it.get(k) for k in KEEP}
    s["actors"] = [{"actor_full_name": a.get("actor_full_name"), "actor_role": a.get("actor_role"),
                    "relation_type": a.get("relation_type"), "actor_nationality": a.get("actor_nationality")}
                   for a in it.get("actors") or []]
    s["assets"] = [{"type": a.get("type"), "image_license": a.get("image_license"),
                    "image_copyright": a.get("image_copyright")} for a in it.get("assets") or []]
    return s


lock = threading.Lock()


def page(n):
    path = CACHE / f"page-{n:05d}.json"
    if path.exists():
        return json.loads(path.read_text())
    data = http_json(f"{API}?limit={LIMIT}&page={n}")["data"]
    out = {"paging": data["paging"], "items": [slim(it) for it in data["items"] if has_image(it)]}
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False))
    tmp.replace(path)
    return out


def harvest():
    first = page(1)
    total = first["paging"]["total_pages"]
    results = {1: first["items"]}
    done = [0]

    def work(n):
        items = page(n)["items"]
        with lock:
            results[n] = items
            done[0] += 1
            if done[0] % 100 == 0:
                print(f"{done[0]}/{total} pages", file=sys.stderr)

    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(work, range(2, total + 1)))
    return [it for n in sorted(results) for it in results[n]]


# --- Swedish -> English keyword mapping (applied to lowercased Swedish titles) ---
SV_EN = [
    (r"\bmarie? (himmelsfärd|upptagande)\b|\bmarias (himmelsfärd|upptagning)\b", "assumption"),
    (r"\b(kristi )?himmelsfärd\b", "ascension"),
    (r"\bobefläckade? avelse\b", "immaculate conception"),
    (r"\bbebådelse(n)?\b|\bmarie bebådelse\b", "annunciation"),
    (r"\bmarias besök hos elisabet\b|\bvisitation(en)?\b", "visitation"),
    (r"\b(kristus|jesus)barnet\b|\bjesusbarn(et)?\b|\bkristusbarn(et)?\b|\bjesus som barn\b", "christ child"),
    (r"\bkristi födelse\b|\bjesu födelse\b|\bjulkrubba\b|\bkrubban?\b|\bjulnatten\b", "nativity"),
    (r"\bherdarnas tillbedjan\b|\bherdarna tillbeder\b|\bherdarnas tillbedan\b", "adoration of the shepherds"),
    (r"\bbebådelsen för herdarna\b|\bherdarnas bebådelse\b", "annunciation to the shepherds"),
    (r"\bkonungarnas tillbedjan\b|\bde (tre )?vise männens tillbedjan\b|\bde vise männen\b|\bde tre vise\b|\btrettondag\w*\b|\bkonungarnas tillbedan\b|\btre konungar\w*\b", "adoration of the magi"),
    (r"\bomskärelse(n)?\b", "circumcision"),
    (r"\bframställande(t|n)? i templet\b|\bjesu frambärande\b|\bkyndelsmäss\w*\b|\bframbärandet i templet\b", "presentation in the temple"),
    (r"\bvila (under|på) flykten\b|\bvilan under flykten\b", "rest on the flight"),
    (r"\bflykt(en)? till egypten\b|\båterkomst(en)? från egypten\b", "flight into egypt"),
    (r"\bden heliga familjen\b|\bheliga familjen\b", "holy family"),
    (r"\b(den )?tolvårige jesus\b|\bjesus (bland|i templet bland) de skriftlärde\b|\bjesus i templet\b", "christ among the doctors"),
    (r"\bmystiska? (bröllop|förlovning)\b|\bkatarinas (mystiska )?förlovning\b", "mystic marriage"),
    (r"\b(kristi|jesu) dop\b|\b(kristus|jesus) döps\b", "baptism of christ"),
    (r"\b(kristi|jesu) frestelse\b|\b(kristus|jesus) i öknen\b|\bfrestelsen i öknen\b", "temptation of christ"),
    (r"\bbröllopet i kana\b", "wedding at cana"),
    (r"\bbergspredikan\b", "sermon on the mount"),
    (r"\blasarus uppväckelse\b|\buppväckandet av lasarus\b|\blasarus uppväcks\b|\bjairi dotter\b", "raising of lazarus"),
    (r"\bunderbara fiskafänget\b|\bdet underbara fisket\b", "miraculous draught"),
    (r"\bbespisningen\b|\bbrödunder\w*\b", "feeding of the five thousand"),
    (r"\b(kristus|jesus) går på vattnet\b", "walking on water"),
    (r"\b(kristus|jesus) botar\b|\b(kristus|jesus) helar\b|\bhelandet av\b", "healing of the"),
    (r"\bförklaring(en)? på berget\b|\b(kristi|jesu) förklaring\b|\btransfiguration(en)?\b", "transfiguration"),
    (r"\b(kristi|jesu) intåg i jerusalem\b|\bintåget i jerusalem\b|\bpalmsöndag\w*\b", "entry into jerusalem"),
    (r"\btempelrensning(en)?\b|\b(månglarna|köpmännen) (drivs|fördrivs|jagas) ut\b|\bmånglarna\b", "cleansing of the temple"),
    (r"\b(den )?sista måltiden\b|\bnattvarden\b|\bnattvardens instiftelse\b|\bheliga nattvarden\b", "last supper"),
    (r"\bfottvagning(en)?\b|\b(kristus|jesus) tvår\b", "christ washing the disciples feet"),
    (r"\bgetsemane\b|\boljeberget\b|\b(kristus|jesus) i örtagården\b|\bi örtagården\b|\bkristi ångest\b", "agony in the garden"),
    (r"\bjudaskyssen\b|\bjudas kyss\b|\bjudas förråder\b|\bförräderiet\b", "kiss of judas"),
    (r"\b(kristi|jesu) tillfångatagande\b|\b(kristus|jesus) tillfångatas\b|\btillfångatagandet\b", "arrest of christ"),
    (r"\b(kristus|jesus) inför (pilatus|kaifas|hannas|herodes|översteprästen)\b", "christ before pilate"),
    (r"\bpilatus tvår\b", "pilate washing"),
    (r"\bgissling(en)?\b|\bgisslandet\b|\b(kristus|jesus) vid (pelaren|gisselpelaren)\b|\bgisslad\b", "flagellation"),
    (r"\btörnekröning(en)?\b|\btörnekron\w*\b", "crowning with thorns"),
    (r"\bhånandet\b|\b(kristus|jesus) hånas\b|\b(kristi|jesu) hånande\b|\bbegabbelse(n)?\b", "mocking of christ"),
    (r"\b(kristus|jesus) bär korset\b|\bkorsbärning(en)?\b|\bkorsbäraren\b|\bvägen till golgata\b|\bkorsvägen\b|\bkorsvägsstation\w*\b", "christ carrying the cross"),
    (r"\bveronikas svetteduk\b|\bsvetteduk(en)?\b|\bveronikas duk\b", "veil of veronica"),
    (r"\bkorsfästelse(n)?\b|\b(kristus|jesus) på korset\b|\bkorsfästningen\b|\bkorsfäst\b|\bkorsfäste\b", "crucifixion"),
    (r"\bden korsfäste\b|\bkorsfäste kristus\b", "crucified"),
    (r"\bkrucifix\w*\b", "crucifix"),
    (r"\bgolgata\b|\bkalvarieberget\b|\bkalvarie\w*\b", "calvary"),
    (r"\bnedtagning(en)? från korset\b|\bkorsnedtagning(en)?\b|\bnedtagandet från korset\b", "descent from the cross"),
    (r"\bbegråtande(t)?\b|\bklagan över\b|\bbegråtandet av kristus\b", "lamentation"),
    (r"\bpi[eé]t[àa]\b", "pietà"),
    (r"\bgravläggning(en)?\b|\b(kristi|jesu) begravning\b", "entombment"),
    (r"\bden döde kristus\b|\bdöde kristus\b|\b(kristi|jesu) lik\b|\b(kristus|jesus) i graven\b", "dead christ"),
    (r"\bsmärtomannen\b|\bsmärtornas man\b|\bsmärtans man\b", "man of sorrows"),
    (r"\bnedstigning(en)? till dödsriket\b|\b(kristus|jesus) i dödsriket\b|\bhelvetesfärd\w*\b", "christ in limbo"),
    (r"\b(kristi|jesu) uppståndelse\b|\buppståndelsen\b|\bden uppståndne( kristus)?\b|\bden uppståndne frälsaren\b", "resurrection of christ"),
    (r"\bkvinnorna vid (graven|den tomma graven)\b|\bde tre mariorna\b", "holy women at the tomb"),
    (r"\bemmaus\b", "emmaus"),
    (r"\btvivlande tomas\b|\btomas tvivel\b|\bden tvivlande tomas\b|\btomas otro\b", "incredulity of thomas"),
    (r"\b(kristus|jesus) uppenbarar sig\b|\b(kristus|jesus) visar sig\b", "christ appearing to"),
    (r"\byttersta domen\b|\bden yttersta domen\b|\bdomedag(en)?\b", "last judgment"),
    (r"\bmarias kröning\b|\bmarie kröning\b|\bkröningen av maria\b", "coronation of the virgin"),
    (r"\b(kristus|jesus) i majestät\b|\btronande kristus\b|\bkristus på tronen\b", "christ in majesty"),
    (r"\b(kristi|jesu) huvud\b|\b(kristi|jesu) ansikte\b|\bkristushuvud\b|\bporträtt av (kristus|jesus)\b|\bbyst av (kristus|jesus)\b|\b(kristi|jesu) anlete\b", "holy face"),
    (r"\bjesu hjärta\b|\bheliga hjärtat\b", "sacred heart"),
    (r"\bden gode herden\b|\bgoda herden\b", "good shepherd"),
    (r"\b(den )?heliga treenigheten\b|\btreenighet(en)?\b|\bnådastol(en)?\b", "trinity"),
    (r"\bmaria med (jesus)?barnet\b|\bmadonna med (jesus)?barnet\b|\bjungfru(n)? maria med barnet\b|\bmaria och barnet\b|\bmadonnan med barnet\b|\bmadonnan\b|\bmaria med barn\b|\bmadonna med barn\b", "virgin and child"),
    (r"\b(kristus|jesus) och den samaritiska kvinnan\b|\bsamaritiska kvinnan\b|\bäktenskapsbryterskan\b", "christ and the woman"),
    (r"\blåten barnen komma\b|\b(kristus|jesus) välsignar barnen\b", "suffer the little children"),
    (r"\b(marta och maria|maria och marta)\b", "mary and martha"),
    (r"\bskatt(e)?penningen\b", "tribute money"),
    (r"\b(kristus|jesus) predikar\b|\b(kristus|jesus) undervisar\b", "christ preaching"),
    (r"\bfrälsaren\b|\bfrälsare\b|\bvärldens frälsare\b|\bförlossaren\b", "saviour"),
    (r"\bguds lamm\b", "lamb of god"),
    (r"\bkristus\b|\bkristi\b|\bkristum\b", "christ"),
    (r"\bjesus\b|\bjesu\b", "jesus"),
]
SV_EN = [(re.compile(rx, re.I), en) for rx, en in SV_EN]
# "<event> av <someone other than Christ>" must not be read as the Christ event.
OTHER_EVENT = re.compile(
    r"\b(gravläggning|begråtande|födelse|dop|himmelsfärd|korsfästelse|uppståndelse|gissling|"
    r"martyrdöd|hånande)(n|t|en)? (av|över) (?!(kristus|jesus|kristi|jesu|frälsaren|herren)\b)", re.I)
SV_OTHER = re.compile(r"^(den )?(helige?|heliga|sankt|s:t|st\.?) \w+$|\bhelgonet\b", re.I)


def translate(sv):
    t = OTHER_EVENT.sub("xx ", sv.lower())
    for rx, en in SV_EN:
        t = rx.sub(f" {en} ", t)
    return re.sub(r"\s+", " ", t).strip()


def tx(d, *langs):
    d = d or {}
    for lang in langs:
        v = (d.get(lang) or "").strip()
        if v:
            return v
    return ""


KIND = [
    ("icon", r"\bikon"),
    ("manuscript", r"miniatyr|manuskript|handskrift"),
    ("print", r"grafik|gravyr|kopparstick|etsning|träsnitt|litografi|mezzotint|akvatint|grafiskt blad|kopparstick|stålstick|xylografi|gravure|engraving|etching|woodcut|lithograph"),
    ("drawing", r"teckning|skiss|akvarell|blyerts|krita|lavering|gouache|pastell|tusch|drawing|watercolour|chalk"),
    ("painting", r"måleri|målning|olja|tempera|oil|fresk|miniatyrmåleri"),
    ("photograph", r"fotografi|photograph"),
    ("sculpture", r"skulptur|relief|staty|byst|skulpterad|marmor|terrakotta|sculpture|brons|alabaster|gips"),
    ("textile", r"textil|broderi|gobeläng|vävnad|tapet|sammet|siden|textile"),
    ("ceramic", r"keramik|porslin|fajans|stengods|lergods|ceramic|porcelain"),
    ("glass", r"\bglas\b|glass"),
    ("enamel", r"emalj|enamel"),
    ("ivory", r"elfenben|ivory"),
    ("metalwork", r"silver|guld|metall|tenn|mässing|koppar|järn|medalj|mynt|metal"),
    ("book", r"\bbok\b|volym|bound"),
]


def kind(it):
    cat = tx(it.get("category"), "sv", "en").lower()
    ot = tx(it.get("object_type"), "sv", "en").lower()
    tm = tx(it.get("technique_material"), "sv", "en").lower()
    for text in (ot, cat, tm):
        for k, rx in KIND:
            if text and re.search(rx, text):
                return k
    return "other"


def dates(it):
    for d in it.get("dating") or []:
        e, l = d.get("date_earliest"), d.get("date_latest")
        if e or l:
            e = e or l
            l = l or e
            y = int(round((e + l) / 2)) if 0 <= l - e <= 300 else int(e)
            return y, tx(d.get("date"), "en", "sv")
    return None, ""


def artist(it):
    """(name, actor) of the main maker; prefers the artist role over e.g. publishers."""
    actors = [a for a in it.get("actors") or [] if (a.get("actor_full_name") or "").strip()]
    actors.sort(key=lambda a: 0 if (a.get("actor_role") or "").lower() in ("konstnär", "artist") else 1)
    if actors:
        return actors[0]["actor_full_name"].strip(), actors[0]
    return "", None


LIC_OK = {"public domain": "Public domain", "cc by sa": "CC BY-SA", "cc0": "CC0", "cc by": "CC BY"}


def image(it):
    """(iiif base, licence label) for the record's main image, or (None, None)."""
    lic = (it.get("iiif_license") or {}).get("license") or ""
    base = it.get("iiif")
    if base and lic.lower() in LIC_OK:
        return base.replace("http://", "https://").rstrip("/"), LIC_OK[lic.lower()]
    return None, None


GENERIC = re.compile(r"^(volym|grafiskt blad|teckning|skiss|studie|utan titel|ornament|blad|untitled)\b", re.I)


NM_NOT = re.compile(r"\bancestors of christ\b|\b(kristi|kristus) förfäder\b|\bchristmas art\b|\bjulkort\b", re.I)
LATEST_YEAR = 1925  # later works need an explicit "Public Domain" mark


def subjects_for(it):
    en = tx(it.get("title"), "en")
    sv = tx(it.get("title"), "sv")
    a, _ = artist(it)
    for t in (en, sv):
        if t and (NOT_CHRIST.search(t) or NM_NOT.search(t)):
            return [], en or sv
    u = []
    if en and en != sv:
        u = decide(en, "", a)
    if not u and sv:
        tr = translate(sv)
        if not SV_OTHER.search(sv):
            u = decide(tr, "", a)
    title = en if (en and en != sv) else sv
    return u, title


def main():
    items = harvest()
    print(f"{len(items)} records with images", file=sys.stderr)
    out, seen = [], set()
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        base, lic = image(it)
        if not base:
            continue
        u, title = subjects_for(it)
        if not u:
            continue
        y, d = dates(it)
        latest = max([x.get("date_latest") or x.get("date_earliest") or 0 for x in it.get("dating") or []] or [0])
        if latest > LATEST_YEAR and lic != "Public domain":
            continue
        a, actor = artist(it)
        rec = {
            "id": f"nationalmuseum:{it['id']}",
            "t": title,
            "y": y,
            "d": d,
            "a": a,
            "c": "",
            "l": "Nationalmuseum, Stockholm",
            "m": tx(it.get("technique_material"), "en", "sv"),
            "s": "",
            "k": kind(it),
            "img": f"{base}/full/!600,600/0/default.jpg",
            "big": f"{base}/full/!1600,1600/0/default.jpg",
            "src": f"https://collection.nationalmuseum.se/en/collection/item/{it['id']}/",
            "lic": lic,
            "u": u,
        }
        out.append(rec)
    out.sort(key=lambda x: (x["y"] is None, x["y"] or 0, x["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {len(out)} records to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
