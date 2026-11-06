#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
if command -v uv >/dev/null 2>&1; then
    [ -x .venv/bin/python ] || uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python -r requirements.txt -r requirements-autobuy.txt
else
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt -r requirements-autobuy.txt
fi
PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers" .venv/bin/python -m playwright install chromium
