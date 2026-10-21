#!/usr/bin/env bash
# Consumer test: installs minigraf into a new virtual environment, as a user
# does, and runs the README quick start and the consumer contract.
#
#   run.sh local <dist-dir> <version>   from built wheels (dist-wheel-* artifacts)
#   run.sh registry <version>           `pip install minigraf==<version>` from PyPI
#
# PYTHON picks the interpreter (default python3); the manylinux2014 leg sets it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || PYTHON=python

# wait_for <seconds> <command...>: run the command every 30 s until it succeeds.
wait_for() {
  local limit="$1"; shift
  local start=$SECONDS
  until "$@"; do
    if (( SECONDS - start >= limit )); then
      echo "::error::gave up after ${limit}s waiting for: $*"
      return 1
    fi
    sleep 30
  done
}

# Resolve the dist directory before leaving the caller's directory.
if [ "${1:-}" = local ]; then DIST="$(cd "$2" && pwd)"; fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
"$PYTHON" -m venv venv
if [ -x venv/bin/python ]; then PY=venv/bin/python; else PY=venv/Scripts/python.exe; fi
"$PY" -m pip install --quiet --upgrade pip

case "${1:-}" in
  local)
    VERSION="$3"
    # Only the built wheels: --no-index keeps PyPI out of it.
    "$PY" -m pip install --no-index --find-links "$DIST" "minigraf==$VERSION"
    ;;
  registry)
    VERSION="$2"
    on_pypi() {
      curl -sf "https://pypi.org/pypi/minigraf/$VERSION/json" -o /dev/null
    }
    wait_for 600 on_pypi
    "$PY" -m pip install --no-cache-dir --only-binary :all: "minigraf==$VERSION"
    ;;
  *)
    echo "usage: $0 local <dist-dir> <version> | registry <version>" >&2
    exit 2
    ;;
esac

GOT=$("$PY" -c "import importlib.metadata as m; print(m.version('minigraf'))")
[ "$GOT" = "$VERSION" ] || { echo "::error::installed minigraf is $GOT, not $VERSION"; exit 1; }
echo "installed minigraf $GOT"

cp "$HERE/quickstart.py" "$HERE/contract.py" .
"$PY" -I quickstart.py
mkdir db
"$PY" -I contract.py db
