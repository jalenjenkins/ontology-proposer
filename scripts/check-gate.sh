#!/bin/sh
# Run from any directory. Set PYTHON to use a virtual environment interpreter.
set -eu
if [ "$#" -ne 1 ]; then
    printf 'Usage: %s SYNTHEA_DUCKDB\n' "$0" >&2
    exit 2
fi
database="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
cd "$(dirname "$0")/.."
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
"${PYTHON:-python3}" -m ontology --db "$database" --store "$scratch/review.sqlite" gate-compare > "$scratch/report.json"
"${PYTHON:-python3}" - "$scratch/report.json" <<'PY'
import json
import sys

with open(sys.argv[1]) as stream:
    reports = json.load(stream)
for name, report in reports.items():
    target, full = report["targeted"], report["full"]
    print(f"{name:20} {target['checked']:2}/{full['checked']:2} plans  "
          f"projection {target['projection_ms']:7.1f} ms  "
          f"query {target['query_ms']:5.1f} ms  missed {len(report['missed'])}")
    if report["missed"] or target["blocked"] != full["blocked"]:
        raise SystemExit("Targeted and full replay disagreed")
grant = reports["unreferenced_grant"]["targeted"]
if grant["checked"] or not grant["blocked"] or not grant["permission_changes"]:
    raise SystemExit("Unreferenced permission grant was not blocked independently of replay")
print("Unreferenced grant blocked with zero saved-plan executions.")
PY
