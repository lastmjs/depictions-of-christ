#!/usr/bin/env bash
# Rebuild everything and commit all generated outputs together, so pages and data never go out of sync.
# Usage: scripts/publish.sh "commit message"
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/harvest.py
python3 scripts/build.py
python3 scripts/stamp.py
git add index.html collection.html map.html work.html assets data/collection data/map data/eras data/enrich/batches data/sources scripts docs README.md
git commit -m "${1:-Rebuild site data}"
git push
