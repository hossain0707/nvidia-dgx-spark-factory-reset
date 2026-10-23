#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -x .venv/bin/python ]; then
    python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11 or newer required"'
    python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import spark_recovery, pycdlib, zstandard' 2>/dev/null; then
    .venv/bin/python -m pip install -e .
fi
exec .venv/bin/python -m spark_recovery "$@"
