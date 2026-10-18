#!/usr/bin/env python3
"""Harvest openly licensed depictions of Christ from the Europeana Search API.

Runs many multilingual field-restricted searches (title / what / who, plus
Iconclass subject codes) restricted to TYPE:IMAGE, reusability=open (PD, CC0,
CC BY, CC BY-SA), media=true and thumbnail=true. Records from providers we
harvest directly (Rijksmuseum, Wikimedia) are excluded in the query. Each
candidate's title (translated to English via the record's English title or a
small multilingual glossary) is classified with subjects.classify plus the
shared filters from aic.py; Iconclass codes and subject keywords are used as
additional signals. Writes data/sources/europeana.json. Raw API responses are
cached under .cache/europeana/. Stdlib only.

Uses the documented public demo key "api2demo" unless EUROPEANA_KEY is set.
"""
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from subjects import classify  # noqa: E402,F401
from aic import NOT_CHRIST, decide  # noqa: E402  (shared filters)

CACHE = ROOT / ".cache" / "europeana"
OUT = ROOT / "data" / "sources" / "europeana.json"
API = "https://api.europeana.eu/record/v2/search.json"
KEY = os.environ.get("EUROPEANA_KEY", "api2demo")
DEBUG = bool(os.environ.get("EUROPEANA_DEBUG"))
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
MIN_INTERVAL = 0.5  # <= 2 req/s
ROWS = 100

# Providers harvested directly elsewhere (excluded in the query itself).
SKIP_PROVIDERS = ["Rijksmuseum", "Wikimedia Commons", "Wikimedia Foundation"]
# Natural-history providers whose specimens match collector names such as "Christ";
# excluded in the query to save requests.
NATURAL_HISTORY = [
    "Naturalis Biodiversity Center", "Royal Botanic Garden Edinburgh", "Meise Botanic Garden",
    "The Trustees of the Natural History Museum, London", "Royal Botanic Gardens Kew",
    "Berlin-Dahlem Botanical Garden and Botanical Museum", "Natural History Museum in Paris",
    "Herbarium WU, Institute of Botany", "Institute for Tropical Scientific Research",
]
# Natural-history / archival providers whose fuzzy hits are never artworks.
NON_ART_PROVIDERS = re.compile(
    r"life sciences|naturalis|botanic|herbari|natural history|natur(kunde|historisches)|biodiversity|"
    r"tropical scientific|zoolog|geolog|cartograph|naval museum|military history|"
    r"forest museum|science and technology|architecture", re.I)

# --- search terms -----------------------------------------------------------
# Searched as title:"..." and what:"..." (phrase queries).
TERMS = [
    # English
    "Christ", "Jesus", "Madonna", "Virgin and Child", "Madonna and Child", "Crucifixion",
    "Crucifix", "Crucified", "Pieta", "Pietà", "Nativity", "Holy Family", "Adoration of the Magi",
    "Adoration of the Shepherds", "Adoration of the Kings", "Epiphany", "Lamentation", "Entombment",
    "Deposition", "Descent from the Cross", "Resurrection", "Last Supper", "Ecce Homo",
    "Man of Sorrows", "Calvary", "Golgotha", "Transfiguration", "Ascension", "Flight into Egypt",
    "Rest on the Flight", "Last Judgment", "Last Judgement", "Emmaus", "Gethsemane",
    "Agony in the Garden", "Flagellation", "Crowning with Thorns", "Salvator Mundi", "Saviour",
    "Redeemer", "Pantocrator", "Christ in Majesty", "Sacred Heart", "Holy Face", "Veil of Veronica",
    "Saint Veronica", "Veronica's Veil", "Sudarium", "Sainte Véronique",
    "Good Shepherd", "Raising of Lazarus", "Noli me tangere", "Doubting Thomas", "Circumcision",
    "Presentation in the Temple", "Mystic Marriage", "Entry into Jerusalem", "Harrowing of Hell",
    "Anastasis", "Agnus Dei", "Deesis", "Holy Trinity", "Baptism of Christ", "Infant Christ",
    "Christ Child", "Holy Kinship", "Mother of God", "Theotokos", "Hodegetria", "Eleousa",
    "Mandylion", "Stations of the Cross", "Way of the Cross", "Carrying the Cross",
    "Wedding at Cana", "Sermon on the Mount", "Christ Blessing",
    # Latin
    "Christus", "Iesus", "Salvator", "Maiestas Domini", "Imago Pietatis", "Vera Icon", "Crucifixus",
    # German
    "Kreuzigung", "Kruzifix", "Gekreuzigte", "Maria mit Kind", "Maria mit dem Kind", "Muttergottes",
    "Mutter Gottes", "Madonna mit Kind", "Geburt Christi", "Anbetung der Könige", "Anbetung der Hirten",
    "Anbetung", "Heilige Familie", "Flucht nach Ägypten", "Beweinung", "Grablegung", "Kreuzabnahme",
    "Auferstehung", "Abendmahl", "Schmerzensmann", "Kreuztragung", "Ölberg", "Dornenkrönung",
    "Geißelung", "Himmelfahrt Christi", "Jüngstes Gericht", "Weltgericht", "Gnadenstuhl",
    "Taufe Christi", "Christkind", "Jesuskind", "Vesperbild", "Heiland", "Erlöser", "Schweißtuch",
    # French
    "Jésus", "Vierge à l'Enfant", "Vierge et l'Enfant", "Sainte Famille", "Adoration des Mages",
    "Adoration des bergers", "Nativité", "Fuite en Égypte", "Descente de croix", "Mise au tombeau",
    "Cène", "Résurrection", "Jugement dernier", "Christ en croix", "Enfant Jésus", "Sauveur",
    # Spanish / Portuguese / Catalan
    "Cristo", "Jesús", "Crucifixión", "Virgen con el Niño", "Virgen y el Niño", "Niño Jesús",
    "Sagrada Familia", "Adoración de los Reyes", "Adoración de los Magos", "Adoración de los pastores",
    "Huida a Egipto", "Descendimiento", "Santo Entierro", "Piedad", "Última Cena", "Nacimiento",
    "Crucificação", "Virgem com o Menino", "Menino Jesus", "Sagrada Família", "Crist", "Crucifixió",
    "Mare de Déu",
    # Italian
    "Gesù", "Crocifissione", "Crocifisso", "Madonna col Bambino", "Madonna con Bambino",
    "Madonna con il Bambino", "Sacra Famiglia", "Adorazione dei Magi", "Adorazione dei pastori",
    "Fuga in Egitto", "Deposizione", "Compianto", "Ultima Cena", "Cristo morto", "Resurrezione",
    "Gesù Bambino", "Natività", "Battesimo di Cristo", "Salvatore",
    # Dutch
    "Jezus", "Kruisiging", "Christus aan het kruis", "Maria met Kind", "Heilige Familie",
    "Aanbidding der koningen", "Aanbidding van de koningen", "Aanbidding der herders",
    "Aanbidding van de herders", "Vlucht naar Egypte", "Kruisafneming", "Bewening", "Graflegging",
    "Laatste Avondmaal", "Avondmaal", "Opstanding", "Hemelvaart", "Doop van Christus",
    "Laatste Oordeel", "Geboorte van Christus", "Kindje Jezus", "Kruisdraging",
    # Scandinavian / Finnish / Baltic
    "Kristus", "Korsfästelsen", "Korsfæstelsen", "Nattvarden", "Nadverden", "Jesusbarnet",
    "Ristiinnaulitseminen", "Jeesus", "Jėzus", "Nukryžiavimas", "Jēzus", "Krustā sišana",
    # Central / Eastern Europe
    "Chrystus", "Jezus Chrystus", "Ukrzyżowanie", "Matka Boska z Dzieciątkiem", "Ostatnia Wieczerza",
    "Pokłon Trzech Króli", "Zdjęcie z krzyża", "Złożenie do grobu", "Zmartwychwstanie",
    "Ježíš", "Ukřižování", "Madona s dítětem", "Panna Maria s dítětem", "Ježiš", "Ukrižovanie",
    "Madona s dieťaťom", "Krisztus", "Jézus", "Keresztrefeszítés", "Mária a gyermekkel",
    "Kristus na križu", "Križanje", "Marija z detetom", "Krist", "Isus", "Raspeće",
    "Hristos", "Iisus", "Răstignirea", "Maica Domnului", "Христос", "Иисус", "Распятие",
    "Богородица", "Χριστός", "Ιησούς", "Σταύρωση", "Παναγία", "Христос Пантократор",
]
EXTRA_QUERIES = [
    'who:"Jesus Christ"', 'who:"Jesus Christus"', 'who:"Christus"', 'who:"Jésus-Christ"',
    'who:"Gesù Cristo"', 'who:"Jesucristo"',
    # Iconclass notations (plain-text or URI subjects)
    "proxy_dc_subject:11D*", "proxy_dc_subject:11F4*", "proxy_dc_subject:*11F4*",
    "proxy_dc_subject:*iconclass.org/11D*", 'proxy_dc_subject:"http://iconclass.org/11D"',
    "proxy_dc_subject:73B*", "proxy_dc_subject:73C*", "proxy_dc_subject:73D*", "proxy_dc_subject:73E*",
    "proxy_dc_subject:*73B*", "proxy_dc_subject:*73C*", "proxy_dc_subject:*73D*", "proxy_dc_subject:*73E*",
    "skos_concept:*iconclass*",
]


TITLE_ONLY = {"Mare de Déu", "Nacimiento", "Anbetung", "Piedad", "Salvatore", "Sauveur", "Heiland",
              "Maica Domnului", "Παναγία", "Богородица"}


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def queries():
    out, seen = [], set()
    for t in TERMS:
        t = t.replace('"', "")
        if fold(t) in seen:  # Europeana folds diacritics: Jesus == Jésus == Jesús
            continue
        seen.add(fold(t))
        out.append(f'title:"{t}"')
        if t not in TITLE_ONLY:
            out.append(f'what:"{t}"')
    out += EXTRA_QUERIES
    return list(dict.fromkeys(out))


# --- API --------------------------------------------------------------------
_last = [0.0]
KEEP_LANGS = {"def", "en", "zxx"}
KEEP = {"id", "title", "dcTitleLangAware", "dcCreator", "dcCreatorLangAware", "dcDescription",
        "dcSubjectLangAware", "dcTypeLangAware", "edmConcept", "edmConceptPrefLabelLangAware",
        "edmAgentLabelLangAware", "edmTimespanLabelLangAware", "year", "dataProvider", "provider",
        "rights", "edmIsShownAt", "edmIsShownBy", "edmPreview", "guid", "country", "type",
        "edmDatasetName", "dcLanguage", "previewNoDistribute", "edmPlaceLabelLangAware",
        "edmAgent", "edmAgentLabel", "dcContributorLangAware"}


def slim(it):
    """Drop bulky fields (and non-English vocabulary translations) before caching."""
    out = {}
    for k, v in it.items():
        if k not in KEEP:
            continue
        if k in ("edmConceptPrefLabelLangAware", "edmTimespanLabelLangAware", "edmPlaceLabelLangAware",
                 "edmAgentLabelLangAware") and isinstance(v, dict):
            v = {lang: x for lang, x in v.items() if lang in KEEP_LANGS}
        if k == "dcDescription":
            v = [x[:600] for x in v[:3]]
        out[k] = v
    return out



def fetch(params):
    url = API + "?" + urllib.parse.urlencode(params, doseq=True)
    key = hashlib.sha1(url.replace(KEY, "KEY").encode()).hexdigest()
    path = CACHE / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    delay = 5.0
    for attempt in range(10):
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read().decode())
            if not data.get("success", True):
                raise RuntimeError(f"API error: {data.get('error')}")
            data["items"] = [slim(it) for it in data.get("items") or []]
            CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False))
            return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                ra = e.headers.get("Retry-After")
                time.sleep(float(ra) if ra and ra.isdigit() else delay)
                delay = min(delay * 2, 300)
                continue
            if e.code in (400, 401, 403):
                body = e.read().decode(errors="replace")[:300]
                raise RuntimeError(f"HTTP {e.code} for {url}: {body}")
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            time.sleep(delay)
            delay = min(delay * 2, 300)
    raise RuntimeError(f"giving up on {url}")


def search(query):
    cursor = "*"
    qf = ["TYPE:IMAGE"] + [f'-DATA_PROVIDER:"{p}"' for p in SKIP_PROVIDERS + NATURAL_HISTORY]
    total = None
    while cursor:
        data = fetch({"wskey": KEY, "query": query, "qf": qf, "reusability": "open",
                      "media": "true", "thumbnail": "true", "rows": ROWS, "profile": "rich",
                      "cursor": cursor})
        if total is None:
            total = data.get("totalResults", 0)
        items = data.get("items") or []
        yield from items
        cursor = data.get("nextCursor")
        if not items:
            break


def harvest():
    seen = {}
    hits = Counter()
    for q in queries():
        n = 0
        for it in search(q):
            iid = it.get("id")
            if not iid:
                continue
            seen.setdefault(iid, it)
            hits[iid] += 1
            n += 1
        print(f"{q}: {n} hits, {len(seen)} unique", file=sys.stderr)
    return seen



# --- normalisation ------------------------------------------------------------
# Multilingual phrases -> English, applied before subjects.classify (which is
# English-centric). Longer / more specific phrases first.
GLOSSARY = [
    # Madonna and Child
    (r"\b(maria|marie|mari[ae]|madonna|muttergottes|mutter gottes|gottesmutter|vierge|virgen|virgem|"
     r"madona|panna maria|matka boska|matka bo[zż]a|marija|m[aá]ria|maica domnului|mare de d[eé]u|"
     r"onze lieve vrouw|nossa senhora|nuestra se[nñ]ora|notre[- ]dame|jungfrau)\b[^.;]{0,30}?"
     r"\b(mit|met|med|col|con|avec|à|a|com|z|ze|s|cu|with|und|en|et|e|y|og|och)\b[^.;]{0,12}?"
     r"\b(dem |het |le |la |el |o |il |l')?(kind|kinde|kindje|jesuskind|christuskind|enfant|bambino|"
     r"ni[nñ]o|menino|dzieci[aą]tkiem|d[ií]t[eě]tem|die[tť]a[tť]om|ježíškem|detetom|gyermekkel|"
     r"pruncul|jezuskem|infant|child|barnet|barn|nen|infante)\b", "Virgin and Child"),
    (r"\bmadonna\b", "Madonna"),
    (r"\b(gottesmutter|muttergottes|mutter gottes|matka boska|matka bo[zż]a|bogorodica|богородица|"
     r"παναγία|theotokos|maica domnului)\b", "Mother of God"),
    # Christ Child
    (r"\b(christkind|jesuskind|jesuskindes|christuskind|kindje jezus|jezuskind|enfant j[eé]sus|"
     r"ni[nñ]o jes[uú]s|menino jesus|ges[uù] bambino|jesusbarnet|jezuli|dzieci[aą]tko jezus)\b", "Christ Child"),
    # Scenes
    (r"\b(kreuzigung|kruisiging|crucifixi[oó]n?|crocifissione|crucifica[cç][aã]o|ukrzy[zż]owanie|"
     r"ukři[zž]ování|ukri[zž]ovanie|keresztrefesz[ií]t[eé]s|kri[zž]anje|raspe[cć]e|r[aă]stignirea|"
     r"korsf[aæä]stelsen|korsf[aæ]stelse|ristiinnaulitseminen|nukry[zž]iavimas|krust[aā] si[sš]ana|"
     r"σταύρωση|распятие|crucifixió)\b", "Crucifixion"),
    (r"\b(christus am kreuz|christus aan het kruis|christ en croix|cristo en la cruz|cristo in croce|"
     r"chrystus na krzy[zż]u|kristus na k[rř][ií][zž]i|kristus p[aå] korset)\b", "Christ on the Cross"),
    (r"\b(kruzifix|kruzifixus|crocifisso|crucifijo|krucifiks|krucifix|krzy[zż]|cristo crucificado|"
     r"cristo crocifisso|gekreuzigte[rn]?|crucifixus)\b", "Crucifix"),
    (r"\b(geburt christi|geboorte van christus|geburt jesu|nativit[eéà]|nacimiento de cristo|"
     r"natividad|nascita di ges[uù]|bo[zż]e narodzenie|narozen[ií] p[aá]n[eě]|christi geburt|"
     r"kerstmis|geboorte)\b", "Nativity"),
    (r"\b(anbetung der (heiligen )?(drei )?k[oö]nige|anbetung der magier|aanbidding (der|van de) "
     r"(drie )?koningen|aanbidding der wijzen|adoration des mages|adoraci[oó]n de los (reyes|magos)|"
     r"adorazione dei magi|pok[lł]on (trzech )?kr[oó]li|klan[eě]n[ií] t[rř][ií] kr[aá]l[uů]|"
     r"heilige drei k[oö]nige|drei k[oö]nige|epiphanie)\b", "Adoration of the Magi"),
    (r"\b(anbetung der hirten|aanbidding (der|van de) herders|adoration des bergers|"
     r"adoraci[oó]n de los pastores|adorazione dei pastori|pok[lł]on pasterzy)\b", "Adoration of the Shepherds"),
    (r"\b(heilige familie|sainte famille|sagrada fam[ií]lia|sacra famiglia|[sś]wi[eę]ta rodzina|"
     r"svat[aá] rodina|heliga familjen|hellige familie)\b", "Holy Family"),
    (r"\b(flucht nach [aä]gypten|vlucht naar egypte|fuite en [eé]gypte|huida a egipto|"
     r"fuga in egitto|ucieczka do egiptu|fuga para o egipto)\b", "Flight into Egypt"),
    (r"\b(ruhe auf der flucht|rust op de vlucht|repos pendant la fuite|riposo (durante la|nella) fuga|"
     r"descanso en la huida)\b", "Rest on the Flight"),
    (r"\b(beschneidung (christi|jesu)|besnijdenis|circoncision|circuncisi[oó]n|circoncisione)\b", "Circumcision"),
    (r"\b(darbringung (im tempel|jesu|christi)|opdracht in de tempel|pr[eé]sentation au temple|"
     r"presentaci[oó]n en el templo|presentazione al tempio)\b", "Presentation in the Temple"),
    (r"\b(der zw[oö]lfj[aä]hrige jesus im tempel|jesus im tempel|christus im tempel)\b", "Christ among the Doctors"),
    (r"\b(taufe (christi|jesu)|doop van christus|bapt[eê]me du christ|bautismo de cristo|"
     r"battesimo di cristo|chrzest chrystusa|kristi d[aå]b)\b", "Baptism of Christ"),
    (r"\b(versuchung christi|verzoeking van christus|tentation du christ)\b", "Temptation of Christ"),
    (r"\b(hochzeit zu kana|bruiloft (te|van) kana|noces de cana|bodas de can[aá]|nozze di cana)\b", "Wedding at Cana"),
    (r"\b(verkl[aä]rung christi|transfiguratie|transfigurazione|transfiguraci[oó]n|verheerlijking)\b", "Transfiguration"),
    (r"\b(einzug in jerusalem|intocht in jeruzalem|entr[eé]e (du christ )?[aà] j[eé]rusalem|"
     r"entrada en jerusal[eé]n|ingresso a gerusalemme|wjazd do jerozolimy)\b", "Entry into Jerusalem"),
    (r"\b(tempelreinigung|vertreibung der h[aä]ndler|chasse des marchands|"
     r"expulsi[oó]n de los mercaderes|cacciata dei mercanti)\b", "Cleansing of the Temple"),
    (r"\b(auferweckung des lazarus|opwekking van lazarus|r[eé]surrection de lazare|"
     r"resurrecci[oó]n de l[aá]zaro|resurrezione di lazzaro)\b", "Raising of Lazarus"),
    (r"\b(letzte[sn]? abendmahl|laatste avondmaal|avondmaal|derni[eè]re c[eè]ne|[uú]ltima cena|"
     r"ultima cena|ostatnia wieczerza|posledn[ií] ve[cč]e[rř]e|nattvarden|nadverden|santa cena|cenacolo)\b", "Last Supper"),
    (r"\b(fu[sß]waschung|voetwassing|lavement des pieds|lavatorio)\b", "Christ washing the disciples' feet"),
    (r"\b([oö]lberg|christus am [oö]lberg|hof van olijven|olijfberg|jardin des oliviers|"
     r"huerto de los olivos|orto degli ulivi|getsemani|gethsemane|ogr[oó]jec)\b", "Agony in the Garden"),
    (r"\b(gefangennahme (christi|jesu)|judaskuss|kus van judas|baiser de judas|beso de judas|"
     r"bacio di giuda|pojmanie chrystusa|gevangenneming)\b", "Betrayal of Christ"),
    (r"\b(geißelung|gei[sß]elung|gesel(ing|ling)|flagellation|flagelaci[oó]n|flagellazione|"
     r"biczowanie)\b", "Flagellation"),
    (r"\b(dornenkr[oö]nung|doornenkroning|couronnement d'[eé]pines|coronaci[oó]n de espinas|"
     r"incoronazione di spine|cierniem ukoronowanie|koronowanie cierniem)\b", "Crowning with Thorns"),
    (r"\b(verspottung (christi|jesu)|bespotting|d[eé]rision du christ|outrages)\b", "Mocking of Christ"),
    (r"\b(christus vor pilatus|christus voor pilatus|christ devant pilate|cristo ante pilato|"
     r"cristo davanti a pilato)\b", "Christ before Pilate"),
    (r"\b(kreuztragung|kruisdraging|portement de (la )?croix|camino del calvario|"
     r"salita al calvario|andata al calvario|cristo con la cruz a cuestas|niesienie krzy[zż]a|"
     r"droga krzy[zż]owa|kreuzweg(station)?|chemin de croix|via crucis|v[ií]a crucis|kruisweg(statie)?|"
     r"kalvarienberg)\b", "Carrying the Cross"),
    (r"\b(schwei[sß]tuch|zweetdoek|sainte face|santa faz|volto santo|vera icon)\b", "Holy Face"),
    (r"\b(kreuzabnahme|kruisafneming|descente de (la )?croix|descendimiento|deposizione|"
     r"deposici[oó]n|zdj[eę]cie z krzy[zż]a|sn[eě]t[ií] z k[rř][ií][zž]e)\b", "Descent from the Cross"),
    (r"\b(beweinung|bewening|d[eé]ploration|lamentation|llanto sobre cristo|compianto|"
     r"op[lł]akiwanie)\b", "Lamentation"),
    (r"\b(piet[aà]|vesperbild|piedad|piedade|pieta)\b", "Pietà"),
    (r"\b(grablegung|graflegging|mise au tombeau|santo entierro|entierro de cristo|sepoltura di cristo|"
     r"deposizione nel sepolcro|z[lł]o[zż]enie do grobu|ulo[zž]en[ií] do hrobu)\b", "Entombment"),
    (r"\b(toter christus|toten christus|dode christus|christ mort|cristo muerto|cristo morto|"
     r"cristo yacente|christus im grab)\b", "Dead Christ"),
    (r"\b(schmerzensmann|man van smarten|homme de douleurs|var[oó]n de dolores|uomo dei dolori|"
     r"chrystus frasobliwy|frasobliwy|ecce homo)\b", "Man of Sorrows"),
    (r"\b(h[oö]llenfahrt|abstieg in die vorh[oö]lle|nederdaling ter helle|descente aux limbes|"
     r"descenso al limbo|discesa al limbo)\b", "Harrowing of Hell"),
    (r"\b(auferstehung( christi)?|opstanding( van christus)?|verrijzenis|r[eé]surrection( du christ)?|"
     r"resurrecci[oó]n( de cristo)?|resurrezione( di cristo)?|zmartwychwstanie|zmrtv[yý]chvst[aá]n[ií]|"
     r"uppst[aå]ndelsen|ylösnousemus|vaskrsenje|învierea)\b", "Resurrection of Christ"),
    (r"\b(frauen am grabe|vrouwen bij het graf|saintes femmes au tombeau)\b", "Holy Women at the Tomb"),
    (r"\b(emma[uü]s|emaús|emmaüs)\b", "Supper at Emmaus"),
    (r"\b(ungl[aä]ubige[rn]? thomas|ongelovige thomas|incr[eé]dulit[eé] de (saint )?thomas|"
     r"incredulidad de santo tom[aá]s|incredulit[aà] di (san )?tommaso)\b", "Incredulity of Thomas"),
    (r"\b(himmelfahrt (christi|jesu)|christi himmelfahrt|hemelvaart( van christus)?|ascension du christ|"
     r"ascensi[oó]n( de cristo| del se[nñ]or)?|ascensione( di cristo)?|wniebowst[aą]pienie)\b", "Ascension"),
    (r"\b(j[uü]ngste[sn]? gericht|weltgericht|laatste oordeel|jugement dernier|juicio final|"
     r"giudizio universale|s[aą]d ostateczny|posledn[ií] soud|judecata de apoi|"
     r"strašn[ií] soud|dommedag)\b", "Last Judgment"),
    (r"\b(gnadenstuhl|genadestoel|tr[oô]ne de gr[aâ]ce|heilige dreifaltigkeit|dreifaltigkeit|"
     r"drie[eë]enheid|sainte trinit[eé]|sant[ií]sima trinidad|santissima trinit[aà]|tr[oó]jca [sś]wi[eę]ta)\b", "Holy Trinity"),
    (r"\b(herz[- ]jesu|heiligstes herz|heilig hart|sacr[eé][- ]c(œ|oe)ur|sagrado coraz[oó]n|sacro cuore|"
     r"naj[sś]wi[eę]tsze serce)\b", "Sacred Heart"),
    (r"\b(guter hirte|gute hirte|goede herder|bon pasteur|buen pastor|buon pastore|dobry pasterz)\b", "Good Shepherd"),
    (r"\b(lamm gottes|lam gods|agneau de dieu|cordero de dios|agnello di dio)\b", "Lamb of God"),
    (r"\b(segnende[rn]? christus|christus segnend|christ b[eé]nissant|cristo benedicente|"
     r"cristo bendiciendo)\b", "Christ Blessing"),
    (r"\b(weltenrichter|weltenherrscher|christus als weltenrichter)\b", "Christ in Majesty"),
    (r"\b(marienkr[oö]nung|kr[oö]nung mari[aeä]|couronnement de la vierge|coronaci[oó]n de la virgen|"
     r"incoronazione della vergine)\b", "Coronation of the Virgin"),
    (r"\b(verk[uü]ndigung|annonciation|anunciaci[oó]n|annunciazione|boodschap aan maria|zwiastowanie)\b", "Annunciation"),
    (r"\b(heimsuchung|visitation|visitaci[oó]n|visitazione|nawiedzenie)\b", "Visitation"),
    (r"\b(mari[aä]e? himmelfahrt|himmelfahrt mari[aeä]|assomption|asunci[oó]n|assunzione|"
     r"tenhemelopneming|wniebowzi[eę]cie)\b", "Assumption"),
    (r"\b(christus|christi|christo|christum|kristus|krisztus|chrystus|hristos|christos|krist|crist|cristo|cristu|kristu|"
     r"христос|χριστός|χριστος|j[eé]sus|jes[uú]s|jezus|ježíš|ježiš|jézus|jeesus|jėzus|jēzus|isus|"
     r"iisus|иисус|ιησούς|ιησους|ges[uù]|iesus|jesu|jesum|heiland|erl[oö]ser|sauveur|salvatore|"
     r"salvador)\b", "Christ"),
    (r"(?<!\w)(hl|st|sv|[sś]w|s)\.(?=\s)|\b(heilige[rn]?|sankt|saint|sainte|san|santa|santo|sint|[sś]wi[eę]ty|svat[yý])\b", "Saint"),
]
GLOSSARY = [(re.compile(rx, re.I), en) for rx, en in GLOSSARY]


def englishize(s):
    s = s or ""
    for rx, en in GLOSSARY:
        s = rx.sub(en, s)
    return s


def first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def lang(d, prefer=("en",), fallback=True):
    """First value from a LangAware dict, preferring the given languages."""
    if not isinstance(d, dict):
        return None
    for l in prefer:
        if d.get(l):
            return d[l][0]
    if fallback:
        for l, vals in d.items():
            for v in vals:
                if v and not v.startswith("http"):
                    return v
    return None


def clean(s):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s.strip(" .;,")


RIGHTS = [
    (r"publicdomain/zero", "CC0"),
    (r"publicdomain/mark", "Public domain"),
    (r"licenses/by-sa/([\d.]+)", "CC BY-SA {0}"),
    (r"licenses/by/([\d.]+)", "CC BY {0}"),
]


def license_name(url):
    for rx, name in RIGHTS:
        m = re.search(rx, url or "")
        if m:
            return name.format(*m.groups())
    return None


# Iconclass notations that imply Christ is shown.
ICONCLASS = [
    (re.compile(r"^11D1"), ["Christ Child"]),
    (re.compile(r"^11D3"), ["Crucifixion"]),
    (re.compile(r"^11D"), ["Jesus Christ"]),
    (re.compile(r"^11F4"), ["Madonna and Child"]),
    (re.compile(r"^11C2"), ["Throne of Mercy"]),
    (re.compile(r"^73B1"), ["Nativity"]),
    (re.compile(r"^73B2"), ["Adoration of the Shepherds"]),
    (re.compile(r"^73B5"), ["Adoration of the Magi"]),
    (re.compile(r"^73B6"), ["Presentation in the Temple"]),
    (re.compile(r"^73B7"), ["Flight into Egypt"]),
    (re.compile(r"^73B8"), ["Holy Family"]),
    (re.compile(r"^73B9"), ["Christ among the Doctors"]),
    (re.compile(r"^73C1"), ["Baptism"]),
    (re.compile(r"^73C"), ["Jesus Christ"]),
    (re.compile(r"^73D2"), ["Last Supper"]),
    (re.compile(r"^73D31"), ["Agony in the Garden"]),
    (re.compile(r"^73D3"), ["Betrayal & Arrest"]),
    (re.compile(r"^73D35"), ["Ecce Homo"]),
    (re.compile(r"^73D4"), ["Carrying the Cross"]),
    (re.compile(r"^73D6"), ["Crucifixion"]),
    (re.compile(r"^73D7"), ["Lamentation"]),
    (re.compile(r"^73D"), ["Jesus Christ"]),
    (re.compile(r"^73E1"), ["Resurrection"]),
    (re.compile(r"^73E"), ["Jesus Christ"]),
]
ICONCLASS_CODE = re.compile(r"(?:iconclass\.org/(?:rkd/)?|^)\s*((?:11[CDF]F?|73[BCDE])[0-9A-Z()+]*)", re.I)
# 73D1x (Entry into Jerusalem etc.) is fine; 73A (Mary's early life) and 11F
# other than 11F4 (Mary alone) are not used. 73E7x+ is Pentecost / Mary's death.
ICONCLASS_SKIP = re.compile(r"^(73E[4-9]|11F[^4]|11C1|11C3)", re.I)


def iconclass_subjects(subjects):
    out = []
    for s in subjects:
        m = ICONCLASS_CODE.search(s.strip())
        if not m:
            continue
        code = re.sub(r"^11([CDF])F", r"11\1", m.group(1).upper())  # 11FF4 (Eastern) == 11F4
        if ICONCLASS_SKIP.match(code):
            continue
        for rx, labels in ICONCLASS:
            if rx.match(code):
                for l in labels:
                    if l not in out:
                        out.append(l)
                break
    if len(out) > 1 and "Jesus Christ" in out:
        out.remove("Jesus Christ")
    return out


def all_strings(d):
    out = []
    if isinstance(d, dict):
        for vals in d.values():
            out += [v for v in vals if isinstance(v, str)]
    elif isinstance(d, list):
        out += [v for v in d if isinstance(v, str)]
    return out


CHILD_WORD = re.compile(r"kind|child|infant|enfant|bambino|ni[nñ]o|menino|pruncul|dzieci|d[ií]t[eě]|"
                        r"die[tť]a|gyermek|deteto|barn|jesus|jezus|christus|christ", re.I)
NO_CHILD = re.compile(r"\b(ohne|without|sans|senza|sin|zonder|utan|bez)\b", re.I)
# Annunciation-type titles that only mention Christ's (future) birth.
ANNUNCIATION = re.compile(r"\b(annunciation(?! to the shepherds)|visitation|assumption|dormition|pentecost|"
                          r"conception)\b", re.I)
CHRIST_FUTURE = re.compile(r"\b(christ'?s birth|birth of christ|christ'?s coming|incarnation of christ|"
                           r"mother of christ|conceive[sd]? christ)\b", re.I)
# Europeana-specific false positives (names, non-religious senses of scene words).
EXTRA_NOT = re.compile(
    r"^[^,]{2,40},\s*([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+\s+)*Jes[uú]s(\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)*\.?\s*$|"
    r"\b(Mar[ií]a|Jos[eé]|Juan|Emilio|Carlos|Luis|Manuel|Antonio|Francisco)\s+Jes[uú]s\b|"
    r"\bJes[uú]s\s+(de\s+|del\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+(y\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b(?!.*\b(cristo|christ|nazaret))|"
    r"(?i:\b(carrer|calle|rua|rue|via|strasse|straße|plaza|pla[cç]a) (de )?(del )?(jes[uú]s|cristo|crist)\b|"
    r"\bdeposition of (a )?wreath|\bwreaths?\b|\bindustrial deposition|\bdeposition \d|"
    r"\bascension (day|island|thursday)|\bhimmelfahrtstag|\bvatertag|"
    r"\b(corpus christi|fronleichnam)\b(?!.*\b(crucif|christ (on|with)|madonna))|"
    r"\b(olive tree|reise|travel|journey to|hospital|szpital|ziekenhuis|krankenhaus|ospedale|"
    r"plano|mapa|map of|carte de|karte von|plattegrond|gottesacker)\b|\bd\.\s?g\.\s|^\W*i\.?h\.?s\.?\W*$)", re.U)
MOTHER_OF_GOD = re.compile(r"\b(mother of god|virgin hodegetria|our lady of (vladimir|kazan|"
                           r"czestochowa|cz[eę]stochowa|tikhvin|smolensk|perpetual help))\b", re.I)
MOG_NOT = re.compile(r"\b(dormition|assumption|protection|pokrov|intercession|sorrow\w*|mourning|"
                     r"annunciation|nativity of the mother|presentation of the mother|joy of all|"
                     r"praying|orans|oranta|burning bush|unburnt)\b", re.I)
PHOTO_SUBJECT = re.compile(r"\b(christ|jesus|madonna|virgin|child|crucif\w*|piet[aà]|ecce homo|holy family|"
                           r"nativity|adoration|lamentation|entombment|last supper|descent from the cross|"
                           r"deposition (from the cross|of christ)|"
                           r"man of sorrows|agony|emmaus|flagellation|salvator|pantocrator|deesis|"
                           r"last judgment|trinity|good shepherd|noli me tangere|holy face)\b", re.I)
CHRIST_AGENT = re.compile(r"^(jesus( christus| christ| kristus)?|christus|jesus von nazareth|"
                          r"j[eé]sus[- ]christ|ges[uù] cristo|jesucristo|jezus chrystus|"
                          r"kristus|krisztus|isus hristos)$", re.I)



# --- object type ------------------------------------------------------------------
KIND_RULES = [
    ("photograph", r"photograph|photo\b|photography|\bfotos?\b|fotograf|glasnegatief|\bdia\b|"
                   r"fotografia|negativ|slides? \(photo|gelatin silver|albumen|"
                   r"glass plate negative|negatives?\b|lantern slide|postcard|picture postcard|daguerreotype|"
                   r"film negative|transparenc"),
    ("manuscript", r"manuscript|illumination|miniature \(paint|book of hours|psalter|codex|breviar|missal|"
                   r"illuminated"),
    ("icon", r"\bicons? \(|\bicon\b"),
    ("stained glass", r"stained glass|glass painting|leaded glass"),
    ("print", r"\bprints?\b|engraving|etching|woodcut|lithograph|mezzotint|aquatint|drypoint|"
              r"xylograph|linocut|intaglio|chromolithograph|holy card|devotional (print|image|card)|"
              r"broadside|single-sheet|graphic art|2d graphics|\bgrafik|\bgrafika|graafika|graphics|"
              r"gravure|grabado|estampa|incisione|\bstampa\b|kupferstich|radierung|holzschnitt|"
              r"skolplansch|plansch|wall chart|material gr[aá]fico|bilderbogen|andachtsbild|prent"),
    ("drawing", r"drawing|sketch|zeichnung|kresba|rysunek|dessin|dibujo|disegno|tekening|teckning|"
                r"joonistus|watercolou?r|gouache|pastel|chalk|charcoal|study \(|pen and ink"),
    ("painting", r"painting|oil on|canvas|tempera|panel painting|altarpiece|easel|fresco|mural|retable|"
                 r"gem[aä]lde|malerei|ma[lľ]ba|\bmaal\b|schilderij|schildering|peinture|pintura|dipinto|"
                 r"m[aå]lning|altertavle|altartavla|hinterglas"),
    ("sculpture", r"sculpture|statue|statuette|relief|figurine|bust|carving|crucifix|corpus|plaster cast|"
                  r"terracotta|wood carving|skulptur|plastik|rondsculptuur|beeldhouw|\bfigur\b|scultura|"
                  r"escultura|vesperbild|kruzifix|krucifix|socha|rze[zź]ba"),
    ("textile", r"textile|tapestr|embroider|vestment|chasuble|lace|banner|cope\b|antependium"),
    ("metalwork", r"metalwork|goldsmith|silverwork|medal|coin|plaquette|enamel|jewel|pendant|bronze"),
    ("ceramic", r"ceramic|porcelain|faience|majolica|maiolica|earthenware|stoneware|tile"),
    ("book", r"\bbooks?\b|monograph|printed matter|incunabul|periodical|newspaper|pamphlet|text\b|"
             r"letters? \(|documents?\b|manuscripts? \(document|charters?|archival"),
    ("map", r"\bmaps?\b|cartograph|atlas"),
    ("specimen", r"specimen|herbari|botany|zoology|fossil"),
    ("architecture", r"architecture|building|church interior|architectural"),
]
KIND_RULES = [(k, re.compile(rx, re.I)) for k, rx in KIND_RULES]
ART_KINDS = {"manuscript", "icon", "stained glass", "print", "drawing", "painting", "sculpture",
             "textile", "metalwork", "ceramic"}


def type_labels(it):
    labels = []
    for k in ("edmConceptPrefLabelLangAware",):
        d = it.get(k) or {}
        labels += d.get("en", [])
    d = it.get("dcTypeLangAware") or {}
    labels += [v for v in all_strings(d) if not v.startswith("http")]
    return labels


def kinds(it):
    text = " | ".join(type_labels(it))
    return [k for k, rx in KIND_RULES if rx.search(text)]


# Fallback kind for providers whose records carry no usable type.
PROVIDER_KIND = [
    (re.compile(r"herzog august|albertina|kupferstich|graphische sammlung", re.I), "print"),
    (re.compile(r"\barchiv|archives?\b|fotothek|photo|foto", re.I), "photograph"),
]


def pick_kind(ks):
    """Choose the most specific artwork kind; reproduction photographs stay 'photograph'."""
    if "photograph" in ks:
        return "photograph"
    for k, _ in KIND_RULES:
        if k in ks and k in ART_KINDS:
            return k
    for k in ("book", "map", "specimen", "architecture"):
        if k in ks:
            return k
    return "other"


def item_kind(it, ks):
    k = pick_kind(ks)
    if k == "other":
        dp = first(it.get("dataProvider")) or ""
        for rx, pk in PROVIDER_KIND:
            if rx.search(dp):
                return pk
    return k


# Photographs of buildings, places, events and people (not of an artwork).
BUILDING = re.compile(
    r"\b(church|chiesa|iglesia|igreja|kirche|kerk|kirke|kyrka|ko[sś]ci[oó][lł]|kostel|templom|biserica|"
    r"[eé]glise|cathedral|cattedrale|catedral|kathedrale|dom|duomo|basilica|basilika|chapel|cappella|"
    r"capilla|kapelle|kapel|chapelle|kaplica|monaster\w*|kloster|klooster|convent|convento|abbey|abbazia|"
    r"abtei|santuario|sanctuary|wallfahrt\w*|parochie|parish|pfarrei|pfarrkirche|parroquia|parrocchia|"
    r"stiftskirche|m[uü]nster|minster|sn\.|socken)\b", re.I)
SCENE = re.compile(
    r"\b(pilgrimage|procession|prozession|processi[oó]n|processione|festa|fiesta|feast|fest|street|"
    r"strasse|straße|via|calle|rue|square|piazza|plaza|platz|village|dorf|town|city|panorama|ansicht|"
    r"view|vista|veduta|gezicht op|exterior|exteri[eö]r|fa[cç]ade|fassade|facciata|tower|turm|torre|"
    r"bridge|br[uü]cke|ponte|puente|monument|denkmal|monumento|cemetery|friedhof|cimitero|cementerio|"
    r"interior|interi[eö]r|innenansicht|innenraum|interieur|hochaltar|retablo mayor|wayside|bildstock|"
    r"wegkreuz|feldkreuz|flurkreuz|marterl|calvaire|hill|mountain|statue of|bildnis|exhibition|"
    r"ausstellung|mostra|exposici[oó]n|inauguration|einweihung|inzegening|onthulling|blessing of|"
    r"consecration|mass|messe|messa|misa|novena|hospital|krankenhaus|ospedale|hotel|restaurant|"
    r"bahnhof|cinema|theater|theatre|teatro|film|movie|actor|schauspieler|attore|passion play|"
    r"passionsspiel\w*|oberammergau|tableau vivant|lebendes bild|nativity scene|krippenspiel|"
    r"servizio fotografico|serie fotografica|photo reportage|photographic reportage|reportage|"
    r"fotoreportage|viering|feier|jubileum|jubil[aä]um|zwembad|fontana|colonna|column|s[aä]ule|ruins?|ruine|candidatura|"
    r"mount(ain)? of piet[aà]|monte di piet[aà]|monte de piedad|madonna di campiglio)\b", re.I)
NON_ARTWORK_TITLE = re.compile(BUILDING.pattern + "|" + SCENE.pattern, re.I)
# Titles describing a book/text rather than a picture.
BOOK_TITLE = re.compile(
    r"\b(leben jesu|vita christi|life of (jesus|christ)|imitatio(ne)? christi|imitation of christ|"
    r"nachfolge christi|navolging van christus|gebetbuch|gebedenboek|prayer ?book|catechism|katechismus|"
    r"predigt|sermon(?! on the mount)|sermons|preek|hymn|lied|gesangbuch|liederbuch|evangeliar|"
    r"bible|bibel|bijbel|biblia|bibbia|testament|epistle|brief|letter|lettre|carta|"
    r"history of|geschichte|historie|treatise|tractatus|opera omnia|dissertation|disputatio|"
    r"oratio|oration|edition|ausgabe|uitgave|volume|band\b|deel\b|tome|vol\.|pars\b|"
    r"title page|titelblatt|titelpagina|frontispiece|frontispiz|titlepage)\b", re.I)


def artist(it):
    # creator URIs -> labels via the parallel edmAgent / edmAgentLabel arrays
    agents = dict(zip(it.get("edmAgent") or [], [first(list(x.values())) if isinstance(x, dict) else x
                                                 for x in (it.get("edmAgentLabel") or [])]))
    vals = []
    d = it.get("dcCreatorLangAware") or {}
    for l in ["en", "def"] + [k for k in d if k not in ("en", "def")]:
        vals += d.get(l, [])
    vals += it.get("dcCreator") or []
    for v in vals:
        if not v:
            continue
        if v.startswith("http"):
            v = agents.get(v) or ""
            if not v:
                continue
        v = re.sub(r"\s*\((k[uü]nstler|hersteller|künstler/hersteller|autor|author|artist|maker|"
                   r"zeichner|stecher|verleger|drucker|fotograf|photographer|creator|editor|"
                   r"inventor|invenit|sculpsit|fecit|excudit)[^)]*\)\s*", " ", v, flags=re.I)
        v = re.sub(r"\s*\([^)]*(\d{3,4}|formschn|stecher|maler|zeichner|verleger|drucker|"
                   r"werkstatt|workshop|nachfolger|umkreis|schule|school|circle|follower|after|nach)[^)]*\)",
                   " ", v, flags=re.I)
        v = clean(v)
        if re.fullmatch(r"[\w-]*\d{3,}[\w-]*", v or ""):
            continue
        if v and not re.match(r"^(unknown|unbekannt|anonym\w*|onbekend|inconnu|desconocido|ignoto|"
                              r"n\.?n\.?|n/a|-)$", v, re.I):
            return v
    return ""


CENTURY = re.compile(r"^(\d{1,2})(st|nd|rd|th) century", re.I)


def dating(it):
    years = []
    for y in it.get("year") or []:
        m = re.match(r"^-?\d{1,4}$", str(y).strip())
        if m:
            years.append(int(y))
    ts = it.get("edmTimespanLabelLangAware") or {}
    raw = [v for v in (ts.get("zxx") or []) + (ts.get("def") or []) if not v.startswith("http")]
    en = ts.get("en") or []
    d = raw[0] if raw else (en[0] if en else "")
    if years:
        y = years[0]
        if len(set(years)) > 1 and 0 <= max(years) - min(years) <= 300:
            y = round((min(years) + max(years)) / 2)
        return y, d or (str(years[0]) if len(set(years)) == 1 else f"{min(years)}–{max(years)}")
    for v in raw:
        m = re.search(r"\b(\d{3,4})\b", v)
        if m:
            return int(m.group(1)), v
    for v in en:
        m = CENTURY.match(v)
        if m:
            return (int(m.group(1)) - 1) * 100 + 50, v
    return None, d


def item_id(it):
    return it["id"].lstrip("/")


def page_url(it):
    return "https://www.europeana.eu/item/" + item_id(it)


def thumb(it):
    u = first(it.get("edmPreview"))
    if not u:
        return None
    # Ask the thumbnail service for the larger (400px) rendition.
    if "api.europeana.eu/thumbnail/v2/" in u and "size=" not in u:
        u += "&size=w400"
    return u


IMG_EXT = re.compile(r"\.(jpe?g|png|gif|tiff?|webp|jp2)(\?|$)|/iiif/|/full/|download\.php|"
                     r"/images?/|getimage|image\?|/digitalobject|/media/|/files/|/bilder?/", re.I)
NOT_IMG = re.compile(r"\.(pdf|mp4|mp3|html?|xml|json|zip)(\?|$)|/manifest", re.I)


def big_image(it):
    u = first(it.get("edmIsShownBy"))
    if u and not NOT_IMG.search(u):
        return u
    return None


def classify_item(it):
    """Return (subjects, english_title, original_title, reason) or (None, ...)."""
    tl = it.get("dcTitleLangAware") or {}
    en_title = clean(lang(tl, ("en",), fallback=False) or "")
    orig = clean(lang(tl, ("def",)) or first(it.get("title")) or "")
    title = en_title or orig
    a = artist(it)
    texts = [en_title] if en_title else []
    texts.append(englishize(orig))
    u = []
    for tx in texts:
        if tx and not NOT_CHRIST.search(tx):
            u = decide(tx, "", a)
            if u:
                break
    if not u:
        for tx in texts:
            if tx and MOTHER_OF_GOD.search(tx) and not MOG_NOT.search(tx) and not NO_CHILD.search(tx):
                u = ["Madonna and Child"]
                break
    if any(NOT_CHRIST.search(tx) or EXTRA_NOT.search(tx) for tx in texts + [orig] if tx):
        return None, title, orig, "not-christ"
    for tx in texts:
        if tx and ANNUNCIATION.search(tx) and not re.search(
                r"\b(christ|jesus|child|infant)\b", CHRIST_FUTURE.sub(" ", tx), re.I):
            return None, title, orig, "annunciation"
    reason = "title"
    subj = all_strings(it.get("dcSubjectLangAware"))
    if not u:
        ic = iconclass_subjects(subj)
        if ic:
            u, reason = ic, "iconclass"
    if not u:
        # explicit subject keywords ("Kreuzigung Christi", "Jesus Christus")
        for s_ in subj:
            if s_.startswith("http"):
                continue
            if CHRIST_AGENT.match(clean(s_)):
                u, reason = ["Jesus Christ"], "subject"
                break
            if NON_ARTWORK_TITLE.search(s_) or NOT_CHRIST.search(s_):
                continue
            got = classify(englishize(s_))
            if "Madonna and Child" in got and (not CHILD_WORD.search(s_) or NO_CHILD.search(s_)):
                got = [g for g in got if g not in ("Madonna and Child", "Christ Child")]
            if got and got != ["Jesus Christ"]:
                u, reason = got, "subject"
                break
    if not u:
        ag = all_strings(it.get("edmAgentLabelLangAware"))
        if any(CHRIST_AGENT.match(clean(x)) for x in ag):
            u, reason = ["Jesus Christ"], "agent"
    if u == ["Circumcision"] and not any(re.search(r"\b(christ|jesus|child|infant)\b", tx, re.I)
                                         for tx in texts if tx):
        return None, title, orig, "not-christ"
    return (u or None), title, orig, reason


SAINT_TITLE = re.compile(r"^(Saint|St\.?)\b(?!.*\b(Christ|Child|Virgin and Child|Holy Family)\b)", re.I)


def build(it):
    """Return (record, None) or (None, rejection reason)."""
    dp = clean(first(it.get("dataProvider")) or "")
    if any(p.lower() in dp.lower() for p in SKIP_PROVIDERS + NATURAL_HISTORY) or "wikimedia" in dp.lower():
        return None, "skip-provider"
    if NON_ART_PROVIDERS.search(dp):
        return None, "non-art-provider"
    lic = license_name(first(it.get("rights")))
    if not lic:
        return None, "license"
    if it.get("previewNoDistribute"):
        return None, "no-preview"
    small = thumb(it)
    if not small:
        return None, "no-image"
    u, title, orig, reason = classify_item(it)
    if not u:
        return None, "no-match"
    eng = englishize(orig)
    ks = kinds(it)
    k = item_kind(it, ks)
    if k in ("specimen", "map", "architecture"):
        return None, f"kind-{k}"
    if k == "book" and (reason != "title" or u == ["Jesus Christ"]):
        return None, "kind-book"
    probe = f"{title} | {eng}"
    if BOOK_TITLE.search(probe) and k not in ("print", "drawing", "painting", "sculpture", "icon"):
        return None, "book-title"
    if k in ("photograph", "other", "book") and SCENE.search(probe):
        return None, "photo-scene"
    generic = not [x for x in u if x not in ("Jesus Christ", "Madonna and Child")]
    if k in ("photograph", "other", "book") and BUILDING.search(probe):
        # "Church of the Resurrection" names a building, not a picture.
        stripped = re.sub(BUILDING.pattern + r"(\s+\S+){0,4}", " ", probe, flags=re.I)
        if generic or not classify(stripped):
            return None, "photo-building"
    if k == "photograph" and (reason != "title" or u == ["Jesus Christ"] or not PHOTO_SUBJECT.search(eng + " " + title) or (
            u == ["Madonna and Child"] and not CHILD_WORD.search(re.sub(r"(?i)madonna", "", eng)))):
        return None, "photo-weak"
    if reason in ("subject", "agent") and SAINT_TITLE.search(englishize(title)):
        return None, "saint-title"
    y, d = dating(it)
    rec = {
        "id": f"europeana:{item_id(it)}",
        "t": title or orig,
        "y": y,
        "d": d,
        "a": artist(it),
        "c": "",
        "l": dp,
        "m": "",
        "s": "",
        "k": k,
        "img": small,
        "big": big_image(it) or small,
        "src": first(it.get("edmIsShownAt")) or page_url(it),
        "lic": lic,
        "u": u,
    }
    if DEBUG:
        rec["why"] = reason
    if orig and title and orig != title:
        rec["ot"] = orig
    return rec, None


def main():
    seen = harvest()
    out, rej, keys = [], Counter(), set()
    for it in seen.values():
        rec, why = build(it)
        if not rec:
            rej[why] += 1
            continue
        # The same object is sometimes ingested twice (different datasets).
        key = (rec["l"], rec["t"].lower(), rec["big"])
        if key in keys:
            rej["duplicate"] += 1
            continue
        keys.add(key)
        out.append(rec)
    out.sort(key=lambda x: (x["y"] is None, x["y"] or 0, x["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {len(out)} records (of {len(seen)} candidates) to {OUT}", file=sys.stderr)
    print("rejected:", dict(rej.most_common()), file=sys.stderr)
    print("providers:", Counter(r["l"] for r in out).most_common(25), file=sys.stderr)
    print("kinds:", Counter(r["k"] for r in out).most_common(), file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "harvest":
        harvest()
    else:
        main()
