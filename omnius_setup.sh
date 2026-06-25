#!/bin/sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    candidate_path=$(command -v "$candidate")
    if "$candidate_path" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      PYTHON_BIN=$candidate_path
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "Python 3.11 or newer is required to install Omnius" >&2
  exit 1
fi

if [ -n "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$REPO_ROOT/src:$PYTHONPATH"
else
  export PYTHONPATH="$REPO_ROOT/src"
fi

exec "$PYTHON_BIN" -m omnius.bootstrap "$REPO_ROOT" "$@"
