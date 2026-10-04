#!/bin/sh
set -eu
# Uninstall code only. Reading data, caches and private backups are preserved.
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$BASE/tools/uninstall.py" "$@"
