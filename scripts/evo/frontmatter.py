"""Tiny YAML-frontmatter reader for SKILL.md (no PyYAML dependency).

Supports the subset used by Agent Skills: scalar keys, quoted scalars, booleans,
numbers, simple lists, one-level nested maps and ``|``/``>`` block scalars.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .util import sha256_file

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    root: Path
    agent: str
    rank: int
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    errors: list[str] = field(default_factory=list)
    sha256: str = ""
    size: int = 0
    mtime: float = 0.0

    @property
    def version(self) -> str:
        value = self.frontmatter.get("version")
        if value is None:
            meta = self.frontmatter.get("metadata")
            if isinstance(meta, dict):
                value = meta.get("version")
        return str(value).strip() if value is not None else ""

    @property
    def license(self) -> str:
        value = self.frontmatter.get("license")
        if value is None:
            meta = self.frontmatter.get("metadata")
            if isinstance(meta, dict):
                value = meta.get("license")
        return str(value).strip() if value is not None else ""

    @property
    def valid(self) -> bool:
        return not self.errors and bool(self.name) and bool(self.description)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "root": str(self.root),
            "agent": self.agent,
            "rank": self.rank,
            "version": self.version,
            "license": self.license,
            "errors": list(self.errors),
            "sha256": self.sha256,
            "size": self.size,
            "mtime": self.mtime,
        }


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        quote = value[0]
        inner = value[1:-1]
        if quote == '"':
            return (
                inner.replace("\\\\", "\\")
                .replace('\\"', '"')
                .replace("\\n", "\n")
                .replace("\\t", "\t")
            )
        return inner.replace("''", "'")
    return value


def _scalar(value: str) -> Any:
    raw = value.strip()
    if raw == "":
        return None
    low = raw.lower()
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    if low in {"null", "none", "~"}:
        return None
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part) for part in inner.split(",")]
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return _strip_quotes(raw)
    if re.fullmatch(r"-?\d+", raw):
        try:
            return int(raw)
        except ValueError:
            return raw
    if re.fullmatch(r"-?\d+\.\d+", raw):
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str, list[str]]:
    """Return ``(frontmatter, body, errors)``."""
    errors: list[str] = []
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, ["missing YAML frontmatter (first line must be ---)"]

    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() in {"---", "..."}:
            end = idx
            break
    if end is None:
        return {}, text, ["unterminated YAML frontmatter"]

    fm_lines = lines[1:end]
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    # Group top-level keys with their raw continuation blocks. We deliberately
    # ignore unknown/nested YAML instead of rejecting it: a skill that DSH's
    # full YAML loader accepts must not be reported as invalid by a lightweight
    # audit parser. Only required fields and the skill name grammar are enforced.
    entries: list[tuple[str, list[str]]] = []
    current_key: str | None = None
    current_block: list[str] = []
    for raw in fm_lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if indent == 0:
            match = re.match(r"^([A-Za-z0-9_.-]+):(.*)$", stripped)
            if match:
                if current_key:
                    entries.append((current_key, current_block))
                current_key = match.group(1)
                current_block = [match.group(2).strip()]
                continue
            if stripped.startswith("-") and current_key:
                current_block.append(stripped)
                continue
            # Tolerant continuation of a plain scalar (some cards use it).
            if current_key:
                current_block.append(stripped)
            continue
        if current_key:
            current_block.append(raw.rstrip())

    if current_key:
        entries.append((current_key, current_block))

    def text_block(first: str, rest: list[str]) -> str:
        if first in {"|", "|-", "|+", ">", ">-", ">+"}:
            non_empty = [len(x) - len(x.lstrip(" ")) for x in rest if x.strip()]
            dedent = min(non_empty) if non_empty else 0
            cleaned = [x[dedent:] if len(x) >= dedent else "" for x in rest]
            value = "\n".join(cleaned).strip("\n")
            if first.startswith(">"):
                value = re.sub(r"(?<!\n)\n(?!\n)", " ", value)
            return value
        if not first:
            return " ".join(x.strip() for x in rest if x.strip()).strip()
        value = _scalar(first)
        if isinstance(value, str) and rest:
            value = " ".join([value] + [x.strip() for x in rest if x.strip()]).strip()
        return str(value) if value is not None else ""

    data: dict[str, Any] = {}
    for key, block in entries:
        first = block[0] if block else ""
        rest = block[1:]
        if key in {"name", "version", "license"}:
            data[key] = _scalar(first)
        elif key in {"description", "whenToUse", "when_to_use"}:
            data[key] = text_block(first, rest)
        elif key == "metadata":
            meta: dict[str, Any] = {}
            for line in block:
                nested = re.match(r"^\s+(version|license|name|description):\s*(.*)$", line)
                if nested:
                    meta[nested.group(1)] = _scalar(nested.group(2))
            data[key] = meta
        # Unknown keys are intentionally ignored.

    name = str(data.get("name", "")).strip()
    description = str(data.get("description", "")).strip()
    if not name:
        errors.append("missing required field: name")
    elif not NAME_RE.match(name):
        errors.append("name must be kebab-case (a-z0-9 and hyphens)")
    if not description:
        errors.append("missing required field: description")
    return data, body, errors


def load_skill(path: Path, root: Path, agent: str, rank: int) -> Skill:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return Skill(
            name="",
            description="",
            path=path,
            root=root,
            agent=agent,
            rank=rank,
            errors=[f"read failed: {exc}"],
        )
    data, body, errors = parse_frontmatter(text)
    try:
        stat = path.stat()
        size = stat.st_size
        mtime = stat.st_mtime
    except OSError:
        size = len(text.encode("utf-8", errors="replace"))
        mtime = 0.0
    return Skill(
        name=str(data.get("name", "")).strip(),
        description=str(data.get("description", "")).strip(),
        path=path,
        root=root,
        agent=agent,
        rank=rank,
        frontmatter=data,
        body=body,
        errors=errors,
        sha256=sha256_file(path),
        size=size,
        mtime=mtime,
    )
