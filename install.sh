#!/usr/bin/env sh
# evolution installer (default dry-run).
# Usage:
#   ./install.sh                 # show plan
#   ./install.sh --apply         # link into agent roots + write global rules
#   EVOLUTION_PYTHON=/usr/bin/python3 ./install.sh --apply
set -eu

SKILL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY="${EVOLUTION_PYTHON:-python3}"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "evolution: Python 3.11+ not found (set EVOLUTION_PYTHON)" >&2
  exit 2
fi

exec "$PY" "$SKILL_DIR/scripts/evolution.py" install "$@"
