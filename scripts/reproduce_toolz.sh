#!/usr/bin/env sh
# Reproduce the toolz column of docs/RESULTS.md.
# Clones toolz at the exact commit the published numbers came from, gives it its own
# venv with pytest, and runs the same audit. Takes 10+ minutes on a laptop.
# Usage: sh scripts/reproduce_toolz.sh [output-dir]   (needs git, python >= 3.11, suite-auditor)
set -eu
TOOLZ_COMMIT=451af60dec590a6010e2babdbf391ea8f815122f
dest=targets/toolz
out=${1:-out/toolz}

if [ ! -d "$dest/.git" ]; then
    git clone --quiet https://github.com/pytoolz/toolz.git "$dest"
fi
git -C "$dest" fetch --quiet origin "$TOOLZ_COMMIT" 2>/dev/null || true
git -C "$dest" checkout --quiet "$TOOLZ_COMMIT"

if [ ! -d "$dest/.venv" ]; then
    python -m venv "$dest/.venv"
    if [ -x "$dest/.venv/bin/python" ]; then py="$dest/.venv/bin/python"; else py="$dest/.venv/Scripts/python.exe"; fi
    "$py" -m pip install --quiet pytest
fi

suite-auditor audit "$dest" --test toolz/tests --per-function 5 -j 4 --out "$out"
echo "compare $out/audit.json with docs/audit-toolz.json"
