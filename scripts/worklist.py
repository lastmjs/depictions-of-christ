#!/usr/bin/env python3
"""Emit a batch of collection works that still need hand-checking.

Usage: python3 scripts/worklist.py <batch-name> [--centuries 1-7] [--limit 30] [--offset 0]
Writes data/enrich/worklists/<batch-name>.json and prints its path.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLL = ROOT / "data" / "collection"
COMMONS = "https://upload.wikimedia.org/wikipedia/commons/"


def key(w):
    return w.get("q") or w.get("id")


def done_keys():
    keys = set()
    for f in (ROOT / "data" / "enrich" / "batches").glob("*.json"):
        try:
            keys |= {e["key"] for e in json.loads(f.read_text())}
        except Exception:
            pass
    return keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--centuries", default="1-21")
    ap.add_argument("--undated", action="store_true")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--offset", type=int, default=0)
    a = ap.parse_args()
    lo, hi = map(int, a.centuries.split("-"))
    files = [COLL / f"c-{c}.json" for c in range(lo, hi + 1)]
    if a.undated:
        files = [COLL / "c-undated.json"]
    done = done_keys()
    todo = []
    for f in files:
        if f.exists():
            todo += [w for w in json.loads(f.read_text()) if key(w) not in done]
    batch = todo[a.offset:a.offset + a.limit]
    out = []
    for w in batch:
        e = {"key": key(w), **{k: v for k, v in w.items() if k not in ("h",)}}
        if w.get("i"):
            from urllib.parse import quote
            n = quote(w["i"])
            e["thumb"] = f"{COMMONS}thumb/{w['h'][0]}/{w['h']}/{n}/500px-{n}"
            e["file_page"] = f"https://commons.wikimedia.org/wiki/File:{n}"
            e["wikidata"] = f"https://www.wikidata.org/wiki/{w['q']}" if w.get("q") else None
        else:
            e["thumb"] = w.get("img")
        out.append(e)
    path = ROOT / "data" / "enrich" / "worklists" / f"{a.name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(path, len(out), f"(remaining in range: {len(todo)})")


if __name__ == "__main__":
    main()
