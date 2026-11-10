"""Download and install a skill from a GitHub repo or portable zip.

The downloader never executes third-party code. It applies size/path/zip-slip
limits, parses SKILL.md, runs the static scanner, checks provenance/license and
backs up an existing target before replacement.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import get
from .frontmatter import NAME_RE, load_skill, parse_frontmatter
from .install import COPY_IGNORE, _backup_existing, create_directory_link
from .paths import Paths
from .security import scan_skill
from .util import now_iso, now_stamp, sha256_file, write_json

MAX_DOWNLOAD = 60 * 1024 * 1024
MAX_FILES = 4000
MAX_EXTRACT = 150 * 1024 * 1024
USER_AGENT = "evolution-skill-fetch/1.1"


@dataclass
class Source:
    kind: str
    owner: str = ""
    repo: str = ""
    ref: str = ""
    subpath: str = ""
    url: str = ""
    local: str = ""


def parse_source(source: str, *, ref: str = "", subpath: str = "") -> Source:
    raw = (source or "").strip()
    if not raw:
        raise ValueError("source is required")
    if raw.startswith("github:"):
        rest = raw[len("github:") :].strip().strip("/")
        parts = [p for p in rest.split("/") if p]
        if len(parts) < 2:
            raise ValueError("github source must be github:owner/repo")
        return Source(kind="github", owner=parts[0], repo=parts[1].removesuffix(".git"), ref=ref, subpath=subpath or "/".join(parts[2:]))
    if raw.startswith("https://github.com/"):
        parsed = urllib.parse.urlparse(raw)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            raise ValueError("invalid GitHub URL")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        path = subpath
        ref_value = ref
        if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
            ref_value = ref_value or parts[3]
            path = path or "/".join(parts[4:])
        return Source(kind="github", owner=owner, repo=repo, ref=ref_value, subpath=path)
    local = Path(os.path.expandvars(os.path.expanduser(raw)))
    if local.exists():
        return Source(kind="local", local=str(local))
    if raw.lower().startswith(("http://", "https://")) and raw.lower().endswith((".zip", ".tar.gz")):
        return Source(kind="url", url=raw)
    raise ValueError("unsupported source: use github:owner/repo, https://github.com/owner/repo, or a local directory/zip")


def _download(url: str, dest: Path) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - allowlisted source
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_DOWNLOAD:
            raise ValueError(f"download too large: {length} bytes")
        data = response.read(MAX_DOWNLOAD + 1)
        if len(data) > MAX_DOWNLOAD:
            raise ValueError("download exceeded size limit")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def _github_zip(source: Source, dest: Path) -> Path:
    refs = [source.ref] if source.ref else []
    refs += ["HEAD", "main", "master"]
    last_error: Exception | None = None
    for ref in dict.fromkeys(refs):
        for url in (
            f"https://codeload.github.com/{source.owner}/{source.repo}/zip/refs/heads/{ref}",
            f"https://codeload.github.com/{source.owner}/{source.repo}/zip/refs/tags/{ref}",
            f"https://codeload.github.com/{source.owner}/{source.repo}/zip/{ref}",
        ):
            try:
                return _download(url, dest)
            except Exception as exc:  # try next ref/URL
                last_error = exc
    raise ValueError(f"cannot download GitHub archive for {source.owner}/{source.repo}: {last_error}")


def _safe_extract(archive: Path, dest: Path) -> None:
    with zipfile.ZipFile(archive, "r") as zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise ValueError(f"archive has too many files: {len(infos)}")
        total = sum(i.file_size for i in infos)
        if total > MAX_EXTRACT:
            raise ValueError(f"archive expands to too much data: {total}")
        for info in infos:
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or name.startswith("../") or "/../" in name or name == "..":
                raise ValueError(f"unsafe path in archive: {name}")
            target = dest / name
            if not str(target.resolve()).lower().startswith(str(dest.resolve()).lower()):
                raise ValueError(f"path escapes extraction dir: {name}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)


def _find_skill_dir(root: Path, subpath: str = "") -> Path:
    if subpath:
        candidate = root / subpath
        if (candidate / "SKILL.md").is_file():
            return candidate
        raise FileNotFoundError(f"SKILL.md not found under {subpath}")
    matches: list[Path] = []
    for path in root.rglob("SKILL.md"):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        matches.append(path.parent)
    if not matches:
        raise FileNotFoundError("archive contains no SKILL.md")
    matches.sort(key=lambda p: (len(p.relative_to(root).parts), str(p)))
    shallowest = [p for p in matches if len(p.relative_to(root).parts) == len(matches[0].relative_to(root).parts)]
    if len(shallowest) > 1:
        names = ", ".join(p.name for p in shallowest[:6])
        raise ValueError(f"multiple top-level skills found ({names}); pass --path")
    return matches[0]


def _license_present(skill_dir: Path, frontmatter: dict[str, Any]) -> bool:
    if frontmatter.get("license"):
        return True
    if isinstance(frontmatter.get("metadata"), dict) and frontmatter["metadata"].get("license"):
        return True
    return any((skill_dir / name).is_file() for name in ("LICENSE", "LICENSE.txt", "COPYING", "NOTICE"))


def install_from_directory(
    paths: Paths,
    cfg: dict[str, Any],
    skill_dir: Path,
    *,
    source_meta: dict[str, Any],
    name: str = "",
    apply: bool = False,
    link_targets: bool = True,
    allow_risk: bool = False,
    allow_no_license: bool = False,
) -> dict[str, Any]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"SKILL.md missing in {skill_dir}")
    skill = load_skill(skill_md, skill_dir, "fetch", 0)
    target_name = name.strip() or skill.name
    if not target_name or not NAME_RE.match(target_name):
        raise ValueError("skill name must be kebab-case")
    if skill.name and name and skill.name != name:
        raise ValueError(f"frontmatter name {skill.name!r} != requested name {name!r}")
    if not skill.valid:
        raise ValueError("invalid skill frontmatter: " + "; ".join(skill.errors))
    findings = scan_skill(skill)
    high = [f for f in findings if f.get("severity") in {"critical", "high"}]
    if high and not allow_risk:
        raise ValueError("refusing install: high-risk findings (use --allow-risk after review): " + "; ".join(f["title"] for f in high[:5]))
    require_license = bool(get(cfg, "maintenance.require_license", True))
    fm, _body, _errors = parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
    if require_license and not _license_present(skill_dir, fm) and not allow_no_license:
        raise ValueError("refusing install: no license declared (use --allow-no-license after review)")
    dest_root = paths.os_home / ".agents" / "skills"
    dest = dest_root / target_name
    report: dict[str, Any] = {
        "ok": True,
        "name": target_name,
        "source": source_meta,
        "target": str(dest),
        "version": skill.version,
        "license": skill.license,
        "findings": len(findings),
        "high_findings": len(high),
        "apply": bool(apply),
    }
    if not apply:
        report["dry_run"] = True
        report["would_replace"] = dest.exists()
        return report
    dest_root.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        report["backup"] = str(_backup_existing(paths, dest, "fetch"))
    shutil.copytree(skill_dir, dest, ignore=COPY_IGNORE)
    provenance = {
        "schema": 1,
        "name": target_name,
        "source": source_meta,
        "version": skill.version,
        "license": skill.license,
        "installed_at": now_iso(),
        "skill_sha256": sha256_file(dest / "SKILL.md"),
        "findings": findings[:50],
    }
    write_json(dest / ".evolution-source.json", provenance)
    linked: list[dict[str, Any]] = []
    if link_targets:
        for agent_root in (paths.os_home / ".dsh" / "skills", paths.os_home / ".claude" / "skills", paths.os_home / ".codex" / "skills"):
            target = agent_root / target_name
            if target.exists() or target.is_symlink():
                linked.append({"path": str(target), "action": "skip-existing"})
                continue
            try:
                mode = create_directory_link(dest, target)
                linked.append({"path": str(target), "action": mode})
            except Exception as exc:
                linked.append({"path": str(target), "action": "error", "error": f"{type(exc).__name__}: {exc}"})
    report.update({"installed": True, "linked": linked, "provenance": str(dest / ".evolution-source.json")})
    return report


def fetch_skill(
    paths: Paths,
    cfg: dict[str, Any],
    source: str,
    *,
    ref: str = "",
    subpath: str = "",
    name: str = "",
    apply: bool = False,
    link_targets: bool = True,
    allow_risk: bool = False,
    allow_no_license: bool = False,
) -> dict[str, Any]:
    parsed = parse_source(source, ref=ref, subpath=subpath)
    with tempfile.TemporaryDirectory(prefix="evolution-fetch-") as tmp:
        tmp_path = Path(tmp)
        if parsed.kind == "local":
            local = Path(parsed.local)
            if local.is_dir():
                skill_dir = _find_skill_dir(local, subpath)
                return install_from_directory(
                    paths, cfg, skill_dir,
                    source_meta={"kind": "local", "path": str(local), "ref": ref},
                    name=name, apply=apply, link_targets=link_targets,
                    allow_risk=allow_risk, allow_no_license=allow_no_license,
                )
            archive = local
        elif parsed.kind == "github":
            archive = _github_zip(parsed, tmp_path / "source.zip")
        else:
            archive = _download(parsed.url, tmp_path / "source.zip")
        if archive.suffix.lower() != ".zip":
            raise ValueError("only zip archives and local directories are supported")
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()
        _safe_extract(archive, extract_dir)
        skill_dir = _find_skill_dir(extract_dir, subpath)
        return install_from_directory(
            paths, cfg, skill_dir,
            source_meta={"kind": parsed.kind, "source": source, "ref": ref or parsed.ref, "subpath": subpath},
            name=name, apply=apply, link_targets=link_targets,
            allow_risk=allow_risk, allow_no_license=allow_no_license,
        )


def run_install_queue(paths: Paths, cfg: dict[str, Any], *, apply: bool = False) -> list[dict[str, Any]]:
    queue = get(cfg, "maintenance.install_queue", []) or []
    results: list[dict[str, Any]] = []
    if not isinstance(queue, list):
        return results
    for item in queue:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        source = str(item.get("source", "")).strip()
        if not name or not source:
            results.append({"ok": False, "name": name, "error": "name/source required"})
            continue
        existing = paths.os_home / ".agents" / "skills" / name
        if existing.exists():
            results.append({"ok": True, "name": name, "action": "skip", "reason": "already installed"})
            continue
        try:
            results.append(
                fetch_skill(
                    paths, cfg, source,
                    ref=str(item.get("ref", "")),
                    subpath=str(item.get("path", "")),
                    name=name,
                    apply=apply,
                    link_targets=bool(item.get("link", True)),
                    allow_risk=bool(item.get("allow_risk", False)),
                    allow_no_license=bool(item.get("allow_no_license", False)),
                )
            )
        except Exception as exc:
            results.append({"ok": False, "name": name, "error": f"{type(exc).__name__}: {exc}"})
    return results
