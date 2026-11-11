#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$PROJECT_DIR/appearance.py" "$@"
