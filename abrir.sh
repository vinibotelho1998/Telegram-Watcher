#!/bin/sh
set -eu
dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -x "$dir/.venv/bin/python" ]; then
    printf '%s\n' 'Execute instalar-linux.sh para preparar o ambiente.' >&2
    exit 1
fi
umask 077
if [ -d "$dir/.browsers" ]; then
    export PLAYWRIGHT_BROWSERS_PATH="$dir/.browsers"
fi
exec "$dir/.venv/bin/python" "$dir/app.py" "$@"
