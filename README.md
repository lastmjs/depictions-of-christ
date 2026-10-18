# The Face of Christ

**Depictions of Jesus through the centuries** — a fully static website, hosted on GitHub Pages:
<https://lastmjs.github.io/depictions-of-christ/>

The site has two parts:

- **The Journey** (`index.html`): a guided, chronological walk through seven eras of Christian art, from the
  third-century catacombs to the present. Each featured work is shown in full with its date, place of creation,
  current location, medium, style, the scripture it depicts, notable facts and links for further reading.
- **The Complete Collection** (`collection.html`): every catalogued, freely licensed artwork depicting Christ that
  we can find — tens of thousands of paintings, icons, mosaics, sculptures, prints, manuscripts and more —
  arranged by century, searchable and filterable, each with a detail view and links to its sources.

## Sources and licensing

- Featured images come from [Wikimedia Commons](https://commons.wikimedia.org/) (public domain or Creative Commons);
  each is credited with its author and license. Works still in copyright are described and linked, not reproduced.
- The collection is harvested from [Wikidata](https://www.wikidata.org/) (artworks that depict Jesus or a scene in
  which he appears and have a Commons image) and from museum open-access programs (public domain / CC0), deduplicated
  against Wikidata.

## Building

Everything is plain HTML, CSS and JavaScript; the Python scripts (standard library + Pillow) only regenerate data.

```bash
python3 scripts/build.py      # resolve featured images via the Commons API, write assets/img + assets/js/data.js
python3 scripts/sources/*.py  # (optional) refresh museum open-access sources into data/sources/
python3 scripts/harvest.py    # harvest Wikidata, merge sources, write data/collection/
python3 scripts/stamp.py      # cache-bust asset URLs in the HTML
```

Curated entries live in `data/eras/*.json` (schema in `data/SCHEMA.md`); era introductions in `data/eras.json`.

To preview locally: `python3 -m http.server` and open <http://localhost:8000>.
