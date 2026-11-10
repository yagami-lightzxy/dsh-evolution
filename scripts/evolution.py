#!/usr/bin/env python3
"""evolution — global agent evolution, audit and capability management.

Entry point. Run ``python scripts/evolution.py --help``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make ``evo`` importable when executed from any working directory.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evo.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
