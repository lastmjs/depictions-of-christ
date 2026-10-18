#!/usr/bin/env python3
"""Harvest Open Content (public-domain) depictions of Christ from the J. Paul Getty Museum.

Uses the Getty Museum Collection Linked Art data (https://data.getty.edu/museum/collection/):
  1. a SPARQL query lists every HumanMadeObject with all of its labels (paged, cached);
  2. labels are classified locally (subjects.classify via aic.decide plus walters.refine);
  3. each candidate's Linked Art record is fetched (cached under .cache/getty/objects/)
     and kept only when the museum owns it, it has an image, and its rights
     statement is "Public Domain" / "No Copyright" (Getty Open Content).
Manuscript leaves and miniatures are separate objects at the Getty and are included.
Writes data/sources/getty.json. Stdlib only.
"""
import csv
import hashlib
import io
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
from walters import refine, kind_from  # noqa: E402

CACHE = ROOT / ".cache" / "getty"
OUT = ROOT / "data" / "sources" / "getty.json"
SPARQL = "https://data.getty.edu/museum/collection/sparql"
UA = "DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)"
MIN_INTERVAL = 0.25
PAGE = 50000

LABEL_QUERY = """PREFIX crm: <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?o ?l WHERE { ?o a crm:E22_Human-Made_Object ; rdfs:label ?l . } ORDER BY ?o ?l LIMIT %d OFFSET %d"""

# Cheap prefilter before classify(): any label mentioning a Christ-related word.
PREFILTER = re.compile(
    r"christ|jesus|jesu|cristo|madonna|virgin|vierge|maria|mary|our lady|holy family|nativity|adoration|magi|"
    r"crucif|calvary|golgotha|piet[àa]|lament|entomb|deposition|descent|resurrect|ascension|transfig|baptism|"
    r"supper|emmaus|gethsemane|agony|flagell|scourg|thorns|ecce homo|man of sorrows|salvator|saviou?r|redeemer|"
    r"pantocrator|pantokrator|majesty|deesis|trinity|veronica|sudarium|holy face|mandylion|good shepherd|lazarus|"
    r"cana|noli me|thomas|circumcision|presentation|doctors|mystic marriage|jerusalem|money ?changers|betrayal|"
    r"judas|pilate|mocking|carrying the cross|bearing the cross|stations|harrowing|limbo|anastasis|lamb of god|"
    r"agnus dei|holy women|three marys|sermon|samaritan|adulteress|tribute money|judg(e)?ment|flight into|"
    r"rest on the flight|shepherds|epiphany|sacred heart|throne of mercy|coronation of the virgin|passion|"
    r"infant|child|bambino|theotokos|hodegetria|eleousa|kreuz|cristo|ihs|dead christ|sepulch|tomb|pentecost",
    re.I)

_last = [0.0]


def get(url, accept="application/json", timeout=120, binary=False):
    delay = 2.0
    for attempt in range(8):
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504):
                ra = e.headers.get("Retry-After")
                time.sleep(float(ra) if ra and ra.isdigit() else delay)
                delay = min(delay * 2, 120)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(delay)
            delay = min(delay * 2, 120)
    raise RuntimeError(f"giving up on {url[:200]}")


def all_labels():
    """{object_uri: [labels]} for every Getty HumanMadeObject (SPARQL, paged, cached)."""
    labels = {}
    offset = 0
    while True:
        path = CACHE / "labels" / f"{offset:07d}.csv"
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            q = LABEL_QUERY % (PAGE, offset)
            text = get(SPARQL + "?" + urllib.parse.urlencode({"query": q}), accept="text/csv", timeout=600)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        rows = list(csv.reader(io.StringIO(text)))[1:]
        for row in rows:
            if len(row) >= 2:
                labels.setdefault(row[0], []).append(row[1])
        print(f"labels offset {offset}: {len(rows)} rows, {len(labels)} objects", file=sys.stderr)
        if len(rows) < PAGE:
            break
        offset += PAGE
    return labels


def record(uri):
    key = uri.rsplit("/", 1)[-1]
    path = CACHE / "objects" / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    text = get(uri, accept="application/ld+json, application/json")
    data = json.loads(text) if text else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


ACC_PAREN = re.compile(r"\s*\((\d+\.[A-Z]{1,3}\.[\w.]+|\d{2,4}\.[A-Z]{1,4}\.\d[\w.]*)\)")


def strip_title(label):
    """'The Crucifixion (Ms. Ludwig V 7 (83.MG.82), fol. 11v)' -> 'The Crucifixion (Ms. Ludwig V 7, fol. 11v)';
    'Head of Christ (84.XT.1)' -> 'Head of Christ'."""
    label = ACC_PAREN.sub("", label or "")
    return re.sub(r"\s+", " ", label).strip()


def subject_title(t):
    """Title without a trailing manuscript/folio reference, for classification."""
    return re.sub(r"\s*\((Ms\.|MS\.|Leaf|fol\.|Ms |Ludwig)[^()]*(\([^()]*\)[^()]*)*\)\s*$", "", t).strip()


def texts(rec, label_rx):
    out = []
    for r in rec.get("referred_to_by") or []:
        if re.search(label_rx, r.get("_label") or "", re.I) and r.get("content"):
            out.append(r["content"].strip())
    return out


def production_info(rec):
    p = rec.get("produced_by") or {}
    parts = [p] + list(p.get("part") or [])
    names, display, begin, end = [], "", None, None
    for q in parts:
        for r in q.get("referred_to_by") or []:
            lab = r.get("_label") or ""
            if re.search(r"Producer\) Name$|Producer Name$", lab) and r.get("content"):
                names.append(r["content"].strip())
        if not names:
            for c in q.get("carried_out_by") or []:
                if c.get("_label"):
                    names.append(c["_label"].strip())
        ts = q.get("timespan") or {}
        for n in ts.get("identified_by") or []:
            if n.get("content") and not display:
                display = n["content"].strip()
        b, e = ts.get("begin_of_the_begin"), ts.get("end_of_the_end")
        if b and begin is None:
            begin = b
        if e and end is None:
            end = e
    return list(dict.fromkeys(names)), display, begin, end


def yr(s):
    if not s:
        return None
    m = re.match(r"(-?)0*(\d+)-", s)
    if not m:
        return None
    return -int(m.group(2)) if m.group(1) else int(m.group(2))


def year(begin, end):
    s, e = yr(begin), yr(end)
    if s is None and e is None:
        return None
    if s is None or e is None:
        return s if s is not None else e
    return int(round((s + e) / 2)) if 0 <= e - s <= 300 else s


def rights(rec):
    out = []
    for r in rec.get("subject_to") or []:
        for n in r.get("identified_by") or []:
            if n.get("content"):
                out.append(n["content"])
        for c in r.get("classified_as") or []:
            out.append(c.get("id") or "")
    for r in rec.get("referred_to_by") or []:
        if re.search(r"License|Rights", r.get("_label") or "") and r.get("content"):
            out.append(r["content"])
    return out


def image_base(rec):
    for rep in rec.get("representation") or []:
        u = rep.get("id") or ""
        m = re.match(r"(https://media\.getty\.edu/iiif/image/[0-9a-f-]+)/", u)
        if m:
            return m.group(1)
    return None


def homepage(rec):
    for s in rec.get("subject_of") or []:
        if any((c.get("_label") or "") == "Web Page" for c in s.get("classified_as") or []):
            return s.get("id")
    return None


def kind(rec, title):
    types = " ".join(c.get("_label") or "" for c in rec.get("classified_as") or [])
    otype = " ".join(texts(rec, r"^Object Type$"))
    med = " ".join(texts(rec, r"^Materials Description$"))
    if re.search(r"Manuscript", types) or re.search(r"\b(Ms\.|fol\.)", title) or \
            re.search(r"^(Leaf|Folio|Cutting|Miniature|Initial|Bifolium|Codex|Book of Hours)", otype, re.I):
        return "manuscript"
    if re.search(r"Photograph", types) or re.search(r"photograph|albumen|gelatin silver|salted paper", f"{otype} {med}", re.I):
        return "photograph"
    if re.search(r"Prints", types):
        return "print"
    if re.search(r"Drawings", types):
        return "drawing"
    if re.search(r"Paintings", types):
        return "icon" if re.search(r"\bicon\b", title, re.I) else "painting"
    if re.search(r"Sculpture", types):
        return kind_from(med) if re.search(r"ivory|enamel", med, re.I) else "sculpture"
    return kind_from(otype, types, med)


def main():
    labels = all_labels()
    cands = {}
    for uri, ls in labels.items():
        best = None
        for l in ls:
            if not PREFILTER.search(l):
                continue
            t = strip_title(l)
            u = refine(t, decide(subject_title(t)))
            if u and (best is None or len(t) < len(best[0])):
                best = (t, u)
        if best:
            cands[uri] = best
    print(f"{len(cands)} label candidates of {len(labels)} objects", file=sys.stderr)

    out, rights_seen, skipped = [], {}, {"rights": 0, "image": 0, "owner": 0}
    for i, (uri, (t0, u0)) in enumerate(sorted(cands.items())):
        if i % 250 == 0:
            print(f"  {i}/{len(cands)} fetched, {len(out)} kept", file=sys.stderr)
        rec = record(uri)
        if not rec:
            continue
        owners = " ".join(o.get("_label") or "" for o in rec.get("current_owner") or [])
        if owners and "Getty" not in owners:
            skipped["owner"] += 1
            continue
        rs = rights(rec)
        key = " / ".join(sorted(set(x for x in rs if not x.startswith("http"))))
        rights_seen[key] = rights_seen.get(key, 0) + 1
        if not any(re.search(r"^(Public Domain|No Copyright)$", x) for x in rs):
            skipped["rights"] += 1
            continue
        base = image_base(rec)
        if not base:
            skipped["image"] += 1
            continue
        title = strip_title(rec.get("_label") or t0)
        u = refine(title, decide(subject_title(title))) or u0
        names, display, begin, end = production_info(rec)
        a = "; ".join(n for n in names if not re.search(r"^unknown\b", n, re.I))
        rec_out = {
            "id": "getty:" + uri.rsplit("/", 1)[-1],
            "t": title,
            "y": year(begin, end),
            "d": display,
            "a": a,
            "c": "; ".join(texts(rec, r"^Culture Statement$")) or "; ".join(texts(rec, r"^Place Created$")),
            "l": "J. Paul Getty Museum",
            "m": "; ".join(texts(rec, r"^Materials Description$")),
            "s": "",
            "k": kind(rec, title),
            "img": f"{base}/full/!600,600/0/default.jpg",
            "big": f"{base}/full/!1600,1600/0/default.jpg",
            "src": homepage(rec) or uri,
            "lic": "CC0",
            "u": u,
        }
        out.append(rec_out)
    print("rights statements among candidates:", json.dumps(rights_seen, indent=1), file=sys.stderr)
    print("skipped:", skipped, file=sys.stderr)
    out.sort(key=lambda x: (x["y"] is None, x["y"] or 0, x["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {len(out)} records (of {len(cands)} candidates) to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
