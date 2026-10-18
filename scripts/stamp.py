#!/usr/bin/env python3
"""Append content hashes to local CSS/JS references in the HTML pages (cache busting)."""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for page in ("index.html", "collection.html"):
    path = ROOT / page
    html = path.read_text()

    def stamp(m):
        attr, url = m.group(1), m.group(2)
        f = ROOT / url
        if not f.exists():
            return m.group(0)
        h = hashlib.sha1(f.read_bytes()).hexdigest()[:10]
        return f'{attr}="{url}?v={h}"'

    html = re.sub(r'(src|href)="(assets/[^"?]+\.(?:js|css))(?:\?v=[0-9a-f]+)?"', stamp, html)
    path.write_text(html)
    print("stamped", page)
