"""evolution runtime package (stdlib only)."""
from __future__ import annotations

from pathlib import Path

__version__ = "1.4.0"
SCHEMA_VERSION = 1


def skill_dir() -> Path:
    """Return the skill bundle root (the directory containing SKILL.md)."""
    return Path(__file__).resolve().parents[2]
