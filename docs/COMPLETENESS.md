# Completeness research — finding every known work

Goal: for your topic and period, the collection must contain **every known surviving depiction of Christ that has
a freely licensed image online** (Wikimedia Commons, museum open access, or other public-domain/CC sources).

## Method

1. **Build the reference list first.** From scholarly and reference sources (Wikipedia lists and articles in all
   languages, museum catalogues, Commons category trees, Wikidata queries, specialised databases such as
   e-codices, Gallica, the British Library, Europeana, POP), enumerate the known works for your topic. Be
   systematic: walk the Commons category tree for your topic exhaustively (e.g. `Category:Catacomb of Priscilla`
   and every subcategory), and list every site/manuscript/object, not just famous ones.
2. **Check what we already have.** Search data/collection/c-1.json … c-11.json and c-undated.json (fields `t`, `l`,
   `i` = Commons file name, `q` = Wikidata id, `src`) and data/eras/*.json + data/eras/additions/*.json (featured
   works). Do not add duplicates. If an existing entry is the same work, note its key in your report instead.
3. **Add each missing work** with the best freely licensed image (prefer the whole work; one entry per work —
   for manuscripts, one entry per miniature that depicts Christ; for catacomb chambers, one entry per distinct
   painted scene of Christ; for coins, one entry per distinct type/issue, not per specimen).
4. **Hand-check while adding**: view the image, confirm Christ is depicted, verify the facts, and write the
   description exactly as in docs/ENRICH.md (summary, scripture, facts, links). Accuracy over quantity.

## Output (two files, same ids in both)

`data/sources/cur-<topic>.json` — JSON array of records:
```json
{"id": "cur:<topic>-<short-slug>", "t": "English title", "y": 350, "d": "c. 350",
 "a": "artist or ''", "c": "place made", "l": "current location", "m": "medium", "s": "style/period",
 "k": "fresco|mosaic|sarcophagus|ivory|manuscript|metalwork|coin|icon|sculpture|relief|textile|gold glass|other",
 "img": "https://upload.wikimedia.org/wikipedia/commons/thumb/<a>/<ab>/<File>/330px-<File>",
 "big": "https://upload.wikimedia.org/wikipedia/commons/thumb/<a>/<ab>/<File>/1280px-<File> (or original URL if narrower)",
 "src": "https://commons.wikimedia.org/wiki/File:<File> (or the museum object page)",
 "lic": "license short name", "u": ["subject labels as in scripts/subjects.py RULES / harvest SUBJECTS"],
 "wd": "Q… if the work has a Wikidata item (optional)"}
```
Get thumbnail URLs from the Commons API (`prop=imageinfo&iiprop=url|size|extmetadata&iiurlwidth=330`); only
standard widths 330/500/960/1280 work for `thumb` URLs.

`data/enrich/batches/cur-<topic>.json` — the hand-check entries (format in docs/ENRICH.md) with
`"status": "verified"` and the same `key` as the record id.

Validate both files with python3. Use a descriptive User-Agent
("DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)") and be gentle with servers.
