# Hand-checking and enriching collection entries

Every work in the Complete Collection is reviewed **individually**. No templates, no bulk-generated text.
Each reviewer handles a small batch (from `scripts/worklist.py`) and writes one result per work.

## For every work in your batch

1. **Look at the image.** Download the thumbnail (`img`, or the Commons thumb URL given) into your scratchpad
   and view it. Confirm what is actually depicted.
2. **Confirm Christ is depicted** (as child, adult, crucified, risen, enthroned, symbolically as the Lamb only if
   the work is clearly a depiction of Christ, etc.). If not — e.g. a saint alone, the Virgin alone, a church
   building, a text page, an Annunciation with no Christ — mark it `rejected` with a short reason.
3. **Check for duplicates** within your batch and against obvious same-work entries (same object photographed
   twice, same object from two sources). Mark the weaker one `rejected` with `"dup_of": "<key>"`.
4. **Verify the catalogue facts** against the holding institution's object page (the `src` link, the Wikidata item,
   Wikipedia, or other reliable sources): title (give a clear **English** title; keep the original in
   `original_title` if it differs), artist/attribution, date, place of creation, current location, medium, style.
   Record only fields you are changing.
5. **Write the entry** — the same quality as the featured works:
   - `summary`: 2–4 sentences, warm, reverent and scholarly: what is shown, how, and why it matters.
     Describe what is actually visible in the image. Never invent details.
   - `scripture`: the passage(s) depicted, e.g. "Luke 2:1–20" (omit only if no passage applies, e.g. a devotional
     portrait — then you may use a relevant verse with a note like "John 14:9 (theme)").
   - `facts`: 1–3 genuinely interesting, verified facts about this specific work (or, if little is known about it,
     about its type, maker or tradition — clearly framed as such).
   - `links`: useful further-reading links that you have confirmed exist (object page, Wikipedia, etc.).
   - `notes`: anything uncertain or disputed (optional).
6. **Accuracy over completeness.** If something cannot be verified, say less, or say "attributed", "probably",
   "traditionally". Never fabricate inscriptions, provenance, anecdotes or dimensions.

## Output

Write a JSON array to the file named in your instructions (`data/enrich/batches/<batch>.json`), one object per
work in the batch, in the batch's order:

```json
{
  "key": "Q12345 | met:123 | wga:... (exactly as given)",
  "status": "verified | corrected | rejected",
  "reject_reason": "only when rejected",
  "dup_of": "only for duplicates",
  "set": {"t": "English title", "original_title": "...", "a": "...", "d": "c. 1450", "y": 1450,
          "c": "place made", "l": "current location", "m": "medium", "s": "style", "k": "painting"},
  "summary": "...",
  "scripture": "...",
  "facts": ["...", "..."],
  "links": [{"label": "Museum page", "url": "https://..."}],
  "notes": "..."
}
```

`set` contains only corrected fields (empty object if nothing changed). Use a descriptive User-Agent
("DepictionsOfChristSite/1.0 (https://github.com/lastmjs/depictions-of-christ)") and be gentle with servers.
Validate the file with `python3 -c "import json;json.load(open(PATH))"`.
