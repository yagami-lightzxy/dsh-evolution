"""Small stdlib-only utilities: paths, atomic IO, hashing, redaction, locking."""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

# ---------------------------------------------------------------------------
# Time / ids
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """Local timezone ISO-8601 timestamp with seconds precision."""
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def week_id(when: _dt.date | None = None) -> str:
    when = when or _dt.date.today()
    year, week, _ = when.isocalendar()
    return f"{year}-W{week:02d}"


def parse_iso(value: str) -> _dt.datetime | None:
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def expand_path(
    value: str | os.PathLike[str],
    base: Path | None = None,
    home: Path | None = None,
) -> Path:
    """Expand ``~`` and env vars; resolve relative paths against *base*.

    ``home`` overrides the operating-system home for ``~`` expansion, which is
    required for tests, migration and simulated agent homes.
    """
    text = str(value)
    if home is not None and (text == "~" or text.startswith("~/") or text.startswith("~\\")):
        text = str(home) + text[1:]
    text = os.path.expandvars(os.path.expanduser(text))
    p = Path(text)
    if not p.is_absolute() and base is not None:
        p = base / p
    # abspath (not resolve) keeps symlinked user roots visible while normalising.
    return Path(os.path.abspath(str(p)))


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def safe_join(root: Path, *parts: str) -> Path:
    """Join and refuse traversal outside *root*."""
    candidate = root.joinpath(*parts)
    resolved = Path(os.path.abspath(str(candidate)))
    root_abs = Path(os.path.abspath(str(root)))
    if not is_relative_to(resolved, root_abs) and resolved != root_abs:
        raise ValueError(f"path escapes root: {candidate}")
    return resolved


def same_path(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        try:
            return Path(os.path.abspath(str(a))) == Path(os.path.abspath(str(b)))
        except OSError:
            return False


# ---------------------------------------------------------------------------
# Atomic IO
# ---------------------------------------------------------------------------


def _newline_for(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def atomic_write_text(path: Path, text: str, newline: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nl = _newline_for(text) if newline is None else newline
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            if nl == "\r\n":
                text = text.replace("\r\n", "\n").replace("\n", "\r\n")
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            if os.path.exists(tmp):
                os.unlink(tmp)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            if os.path.exists(tmp):
                os.unlink(tmp)


def read_text(path: Path, max_bytes: int | None = None) -> str:
    data = path.read_bytes()
    if max_bytes is not None:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="replace")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(read_text(path))
    except (OSError, ValueError):
        return default


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_jsonl(path: Path, max_lines: int = 100_000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for idx, line in enumerate(fh):
                if idx >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    out.append(value)
    except OSError:
        return out
    return out


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path, max_bytes: int | None = None) -> str:
    digest = hashlib.sha256()
    remaining = max_bytes
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 256)
            if not chunk:
                break
            if remaining is not None:
                chunk = chunk[:remaining]
                remaining -= len(chunk)
            digest.update(chunk)
            if remaining is not None and remaining <= 0:
                break
    return digest.hexdigest()


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("openai/deepseek", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    (
        "generic-secret",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
            r"\s*[:=]\s*['\"]?([A-Za-z0-9_./+=-]{12,})['\"]?"
        ),
    ),
]

_PLACEHOLDER = re.compile(
    r"(?i)^(?:\$\{?[A-Z_]+\}?|<[^>]+>|your[-_ ]|example|placeholder|changeme|xxxx|todo|none|null|redacted)"
)


def _mask(value: str, kind: str) -> str:
    if len(value) <= 6:
        return f"[REDACTED:{kind}:{len(value)}]"
    return f"{value[:3]}…{value[-2:]}[REDACTED:{kind}:len={len(value)}]"


def _looks_like_code(value: str) -> bool:
    """Avoid treating env/config references as literal secrets."""
    value = value.strip()
    if not value:
        return True
    if re.search(r"[.()\[\]{}<>]", value):
        return True
    # ALL_CAPS identifiers are almost always environment/config references.
    if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", value):
        return True
    low = value.lower()
    return low.startswith(
        (
            "os.",
            "getenv",
            "environ",
            "self.",
            "args.",
            "config.",
            "settings.",
            "process.env",
            "none",
            "true",
            "false",
            "$",
            "{{",
            "<",
        )
    )


def detect_secrets(text: str, limit: int = 50) -> list[dict[str, Any]]:
    """Detect credential-shaped strings; never return the full secret."""
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for kind, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if kind == "generic-secret" and match.lastindex:
                value = match.group(1)
                if _PLACEHOLDER.match(value) or _looks_like_code(value):
                    continue
            key = (kind, match.start())
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    "kind": kind,
                    "start": match.start(),
                    "preview": _mask(value, kind),
                    "length": len(value),
                }
            )
            if len(found) >= limit:
                return found
    return found


def redact_secrets(text: str) -> str:
    redacted = text
    for kind, pattern in SECRET_PATTERNS:
        def repl(match: re.Match[str], _kind: str = kind) -> str:
            value = match.group(0)
            if _kind == "generic-secret" and match.lastindex:
                value = match.group(1)
                if _PLACEHOLDER.match(value) or _looks_like_code(value):
                    return match.group(0)
            return _mask(value, _kind)

        redacted = pattern.sub(repl, redacted)
    return redacted


def redact_paths(text: str, home: Path | None = None) -> str:
    """Optionally shorten the user's home path in reports."""
    if home is None:
        return text
    try:
        return text.replace(str(home), "~")
    except Exception:
        return text


# ---------------------------------------------------------------------------
# Markdown / text
# ---------------------------------------------------------------------------


def md_cell(value: Any, limit: int = 160) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def slugify(text: str, limit: int = 60) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip()).strip("-._")
    return (text[:limit] or "item").lower()


def human_bytes(num: int | float) -> str:
    size = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024.0 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TiB"


def percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, min(len(ordered), int(round((pct / 100.0) * len(ordered) + 0.5))))
    return ordered[rank - 1]


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return False


# ---------------------------------------------------------------------------
# Subprocess
# ---------------------------------------------------------------------------


def run_cmd(
    args: Sequence[str],
    timeout: float = 15.0,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run without a shell; never raises for non-zero exit."""
    try:
        proc = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd else None,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


class LockTimeout(RuntimeError):
    pass


@contextlib.contextmanager
def file_lock(path: Path, timeout: float = 30.0, stale_seconds: float = 600.0) -> Iterator[None]:
    """Cross-platform single-instance advisory lock.

    A lock file older than *stale_seconds* is considered abandoned and removed
    once, then the acquisition retries. Active locks are never stolen.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    start = time.monotonic()
    locked = False
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except (OSError, IOError):
                if time.monotonic() - start >= timeout:
                    try:
                        age = time.time() - path.stat().st_mtime
                    except OSError:
                        age = 0
                    if age > stale_seconds:
                        with contextlib.suppress(OSError):
                            path.unlink()
                    raise LockTimeout(f"could not acquire lock: {path}")
                time.sleep(0.15)
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({"pid": os.getpid(), "ts": now_iso()}) + "\n")
        fh.flush()
        yield
    finally:
        if locked:
            with contextlib.suppress(OSError):
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


# ---------------------------------------------------------------------------
# FS helpers
# ---------------------------------------------------------------------------


def iter_files(
    root: Path,
    *,
    max_files: int = 5000,
    max_depth: int = 6,
    skip_names: Iterable[str] = (),
    skip_dirs: Iterable[str] = (".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache"),
) -> Iterator[Path]:
    """Bounded, symlink-safe-ish recursive file walk (no follow of dir symlinks)."""
    skip_names = set(skip_names)
    skip_dirs = set(skip_dirs)
    root = Path(root)
    if not root.exists():
        return
    count = 0
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        depth = len(current.parts) - base_depth
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
        if depth >= max_depth:
            dirnames[:] = []
        for name in filenames:
            if name in skip_names or name.startswith("."):
                continue
            yield current / name
            count += 1
            if count >= max_files:
                return


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def remove_path(path: Path) -> None:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def platform_label() -> str:
    return f"{sys.platform} ({os.name})"
