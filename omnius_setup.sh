#!/bin/sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=$(command -v python3)
else
  echo "python3 is required to install Omnius" >&2
  exit 1
fi

if [ -n "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/src:$PYTHONPATH"
else
  export PYTHONPATH="$REPO_ROOT/src"
fi

exec "$PYTHON_BIN" -m omnius.bootstrap "$REPO_ROOT" "$@"
