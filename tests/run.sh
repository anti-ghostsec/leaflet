#!/bin/sh
# All of Leaflet's tests.   tests/run.sh [--live]
#   - document formats (Python, no extra packages)
#   - PDF page operations (needs node)
#   - with --live: the real window on a virtual screen (needs Xvfb, or a DISPLAY)
set -eu
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -p 'test_*.py'
if command -v node >/dev/null 2>&1; then
    node tests/test_pdfops.mjs
else
    echo "node isn't installed: skipping the PDF page operation tests" >&2
fi
if [ "${1:-}" = "--live" ]; then
    out=$(mktemp -d)
    if [ -n "${DISPLAY:-}" ]; then
        python3 tests/live_check.py "$out"
    elif command -v xvfb-run >/dev/null 2>&1; then
        xvfb-run -a -s '-screen 0 1280x900x24' python3 tests/live_check.py "$out"
    else
        echo "no DISPLAY and no xvfb-run: can't run the live check" >&2
        exit 1
    fi
    echo "live check output (screenshots, saved files): $out"
fi
