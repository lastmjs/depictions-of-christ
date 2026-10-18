# Depiction entry schema

Each era file in `data/eras/` is a JSON array of entries:

```json
{
  "id": "kebab-case-unique-id",
  "title": "Christ Pantocrator",
  "artist": "Unknown Byzantine iconographer",
  "date": "c. 550",                 // display string
  "year": 550,                      // integer used for chronological sorting (negative not needed)
  "created": "Constantinople (likely)",   // where the work was made
  "location": "Saint Catherine's Monastery, Sinai, Egypt",  // where it is today (or "Lost")
  "medium": "Encaustic on wood panel",
  "form": "Icon",                   // one of: Painting, Fresco, Mosaic, Icon, Sculpture, Relief, Manuscript, Stained Glass, Metalwork, Textile, Print, Drawing, Photograph, Monument
  "style": "Byzantine",
  "summary": "2–4 sentences: what the work shows and why it matters.",
  "facts": ["Interesting fact 1", "Interesting fact 2", "Interesting fact 3"],
  "scripture": "Matthew 17:1–9",    // optional, the passage depicted
  "commons": "File:Exact Commons file name.jpg",   // or null if no freely-licensed image exists
  "links": [
    {"label": "Wikipedia", "url": "https://en.wikipedia.org/wiki/..."},
    {"label": "Museum page", "url": "https://..."}
  ]
}
```
