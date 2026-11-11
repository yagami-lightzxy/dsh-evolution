"""Hash snapshots, diff, rollback, portable export/import."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .frontmatter import parse_frontmatter
from .paths import Paths
from .util import (
    atomic_write_bytes,
    atomic_write_text,
    file_size,
    now_iso,
    now_stamp,
    read_json,
    sha256_bytes,
    sha256_file,
    slugify,
    write_json,
)

MANAGED_RUNTIME = [
    "config.json",
    "state/install-manifest.json",
    "state/agents-block.md",
    "state/safety.lock",
    "state/schedule.json",
]
MANAGED_SKILL = [
    "SKILL.md",
    "VERSION",
    "templates/AGENTS.block.md",
]


def _snapshot_id(label: str) -> str:
    return f"{now_stamp()}-{slugify(label or 'snapshot', 40)}"


def _managed_current(paths: Paths) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rel in MANAGED_RUNTIME:
        path = paths.runtime_home / rel
        if path.is_file():
            out[f"runtime:{rel}"] = {
                "scope": "runtime",
                "path": rel,
                "abs": str(path),
                "sha256": sha256_file(path),
                "size": file_size(path),
            }
    for rel in MANAGED_SKILL:
        path = paths.skill_dir / rel
        if path.is_file():
            out[f"skill:{rel}"] = {
                "scope": "skill",
                "path": rel,
                "abs": str(path),
                "sha256": sha256_file(path),
                "size": file_size(path),
            }
    # Snapshot every rule file, not just a fixed list.
    rules_dir = paths.skill_dir / "rules"
    if rules_dir.is_dir():
        for path in sorted(rules_dir.glob("*.md")):
            rel = f"rules/{path.name}"
            out[f"skill:{rel}"] = {
                "scope": "skill",
                "path": rel,
                "abs": str(path),
                "sha256": sha256_file(path),
                "size": file_size(path),
            }
    return out


def create_snapshot(paths: Paths, *, label: str = "snapshot") -> dict[str, Any]:
    paths.ensure_runtime()
    base_id = _snapshot_id(label)
    snapshot_id = base_id
    suffix = 1
    target = paths.snapshots_dir / snapshot_id
    while target.exists():
        snapshot_id = f"{base_id}-{suffix}"
        suffix += 1
        target = paths.snapshots_dir / snapshot_id
    files_dir = target / "files"
    files_dir.mkdir(parents=True, exist_ok=False)
    current = _managed_current(paths)
    entries: list[dict[str, Any]] = []
    for key, item in current.items():
        dest = files_dir / item["scope"] / item["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item["abs"], dest)
        entries.append({k: item[k] for k in ("scope", "path", "sha256", "size")})
    manifest = {
        "schema": 1,
        "kind": "snapshot",
        "id": snapshot_id,
        "label": label,
        "created_at": now_iso(),
        "version": __version__,
        "files": sorted(entries, key=lambda e: (e["scope"], e["path"])),
        "file_count": len(entries),
        "note": "回滚只恢复清单内文件；不删除清单外新增文件，避免误删。",
    }
    write_json(target / "manifest.json", manifest)
    return manifest


def load_manifest(paths: Paths, snapshot_id: str) -> dict[str, Any]:
    manifest_path = paths.snapshots_dir / snapshot_id / "manifest.json"
    data = read_json(manifest_path, default=None)
    if not isinstance(data, dict):
        raise FileNotFoundError(f"snapshot not found or invalid: {snapshot_id}")
    return data


def list_snapshots(paths: Paths) -> list[dict[str, Any]]:
    if not paths.snapshots_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(paths.snapshots_dir.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        manifest = read_json(child / "manifest.json", default=None)
        if isinstance(manifest, dict):
            out.append(
                {
                    "id": manifest.get("id", child.name),
                    "label": manifest.get("label", ""),
                    "created_at": manifest.get("created_at", ""),
                    "version": manifest.get("version", ""),
                    "file_count": manifest.get("file_count", len(manifest.get("files", []))),
                }
            )
    return out


def _manifest_keys(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in manifest.get("files", []):
        out[f"{item['scope']}:{item['path']}"] = item
    return out


def diff_snapshot(paths: Paths, from_id: str, to_id: str = "current") -> dict[str, Any]:
    before = load_manifest(paths, from_id)
    before_map = _manifest_keys(before)
    if to_id == "current":
        after_map = _managed_current(paths)
    else:
        after_map = _manifest_keys(load_manifest(paths, to_id))
    added = sorted(set(after_map) - set(before_map))
    removed = sorted(set(before_map) - set(after_map))
    changed: list[dict[str, Any]] = []
    same: list[str] = []
    for key in sorted(set(before_map) & set(after_map)):
        if before_map[key].get("sha256") != after_map[key].get("sha256"):
            changed.append(
                {
                    "key": key,
                    "before": before_map[key].get("sha256"),
                    "after": after_map[key].get("sha256"),
                }
            )
        else:
            same.append(key)
    return {
        "from": from_id,
        "to": to_id,
        "added": added,
        "removed": removed,
        "changed": changed,
        "same_count": len(same),
        "summary": f"+{len(added)} -{len(removed)} ~{len(changed)} ={len(same)}",
    }


def rollback(
    paths: Paths,
    snapshot_id: str,
    *,
    apply: bool = False,
    safety_snapshot: bool = True,
) -> dict[str, Any]:
    manifest = load_manifest(paths, snapshot_id)
    source_dir = paths.snapshots_dir / snapshot_id / "files"
    plan: list[dict[str, Any]] = []
    for item in manifest.get("files", []):
        scope, rel = item["scope"], item["path"]
        base = paths.runtime_home if scope == "runtime" else paths.skill_dir
        target = base / rel
        source = source_dir / scope / rel
        plan.append(
            {
                "scope": scope,
                "path": rel,
                "target": str(target),
                "exists": target.exists(),
                "source_exists": source.is_file(),
                "restore": source.is_file(),
            }
        )
    result: dict[str, Any] = {
        "snapshot": snapshot_id,
        "applied": False,
        "plan": plan,
        "missing_sources": [p["path"] for p in plan if not p["source_exists"]],
    }
    if not apply:
        result["dry_run"] = True
        return result
    if result["missing_sources"]:
        raise ValueError("snapshot is incomplete; missing: " + ", ".join(result["missing_sources"]))
    if safety_snapshot:
        result["pre_rollback_snapshot"] = create_snapshot(paths, label="pre-rollback")["id"]
    backup_dir = paths.backups_dir / f"rollback-{now_stamp()}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    restored: list[dict[str, Any]] = []
    for item, entry in zip(manifest.get("files", []), plan):
        target = Path(entry["target"])
        source = source_dir / item["scope"] / item["path"]
        if target.exists():
            backup = backup_dir / item["scope"] / item["path"]
            backup.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file():
                shutil.copy2(target, backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, source.read_bytes())
        restored.append(
            {
                "scope": item["scope"],
                "path": item["path"],
                "sha256": sha256_file(target),
                "expected": item.get("sha256"),
                "ok": sha256_file(target) == item.get("sha256"),
            }
        )
    result.update(
        {
            "applied": True,
            "restored": restored,
            "backup_dir": str(backup_dir),
            "all_ok": all(r["ok"] for r in restored),
        }
    )
    write_json(paths.state_dir / f"rollback-{now_stamp()}.json", result)
    return result


def prune_snapshots(paths: Paths, *, days: int = 90, apply: bool = False) -> dict[str, Any]:
    import datetime as _dt

    cutoff = _dt.datetime.now().timestamp() - days * 86400
    candidates: list[dict[str, Any]] = []
    for item in list_snapshots(paths):
        folder = paths.snapshots_dir / item["id"]
        try:
            mtime = folder.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            candidates.append(item)
    if apply:
        for item in candidates:
            shutil.rmtree(paths.snapshots_dir / item["id"], ignore_errors=True)
    return {"days": days, "candidates": candidates, "applied": apply, "count": len(candidates)}


# ---------------------------------------------------------------------------
# Export / import
# ---------------------------------------------------------------------------

_EXPORT_SKIP = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv", "dist", "build"}


def _zip_add_tree(zf: zipfile.ZipFile, root: Path, prefix: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXPORT_SKIP and not d.startswith(".pytest")]
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.name.endswith((".pyc", ".pyo")):
                continue
            rel = path.relative_to(root).as_posix()
            arc = f"{prefix}/{rel}" if prefix else rel
            data = path.read_bytes()
            zf.writestr(arc, data, compress_type=zipfile.ZIP_DEFLATED)
            entries.append({"path": arc, "sha256": sha256_bytes(data), "size": len(data)})
    return entries


def export_bundle(
    paths: Paths,
    *,
    out: Path | None = None,
    include_knowledge: bool = False,
) -> dict[str, Any]:
    paths.ensure_runtime()
    stamp = now_stamp()
    out = out or (paths.exports_dir / f"evolution-bundle-{stamp}.zip")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        entries.extend(_zip_add_tree(zf, paths.skill_dir, "skill"))
        if include_knowledge and paths.knowledge_dir.is_dir():
            entries.extend(_zip_add_tree(zf, paths.knowledge_dir, "knowledge"))
    manifest = {
        "schema": 1,
        "kind": "evolution-bundle",
        "name": "evolution",
        "version": __version__,
        "created_at": now_iso(),
        "source_skill_dir": str(paths.skill_dir),
        "include_knowledge": include_knowledge,
        "files": sorted(entries, key=lambda e: e["path"]),
        "file_count": len(entries),
    }
    # Add the manifest last so it is not part of its own hash list.
    with zipfile.ZipFile(out, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return {
        "path": str(out),
        "version": __version__,
        "files": len(entries),
        "size": out.stat().st_size,
        "sha256": sha256_file(out),
        "manifest": manifest,
    }


def _safe_zip_name(name: str) -> str:
    name = name.replace("\\", "/")
    if name.startswith("/") or name.startswith("../") or "/../" in name or name == "..":
        raise ValueError(f"unsafe zip path: {name}")
    if ":" in name.split("/")[0]:
        raise ValueError(f"absolute/drive path in zip: {name}")
    return name


def verify_bundle(path: Path, *, max_files: int = 3000, max_total: int = 200 * 1024 * 1024) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    with zipfile.ZipFile(path, "r") as zf:
        infos = zf.infolist()
        if len(infos) > max_files:
            raise ValueError(f"bundle has too many files: {len(infos)} > {max_files}")
        total = sum(i.file_size for i in infos)
        if total > max_total:
            raise ValueError(f"bundle is too large: {total} > {max_total}")
        names = [_safe_zip_name(i.filename) for i in infos]
        if "manifest.json" not in names:
            raise ValueError("manifest.json missing")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        if manifest.get("kind") != "evolution-bundle":
            raise ValueError("not an evolution bundle")
        if int(manifest.get("schema", 0)) != 1:
            raise ValueError("unsupported bundle schema")
        declared = {item["path"]: item for item in manifest.get("files", [])}
        if "skill/SKILL.md" not in declared:
            raise ValueError("skill/SKILL.md missing from manifest")
        errors: list[str] = []
        checked = 0
        info_by_name = {_safe_zip_name(info.filename): info for info in infos}
        for info in infos:
            name = _safe_zip_name(info.filename)
            if name == "manifest.json":
                continue
            if info.is_dir():
                continue
            if name not in declared:
                errors.append(f"undeclared file: {name}")
                continue
            data = zf.read(info_by_name[name])
            if sha256_bytes(data) != declared[name].get("sha256"):
                errors.append(f"hash mismatch: {name}")
            checked += 1
        skill_md = zf.read("skill/SKILL.md").decode("utf-8")
        fm, _body, fm_errors = parse_frontmatter(skill_md)
        if fm.get("name") != "evolution":
            errors.append("skill/SKILL.md name must be evolution")
        if fm_errors:
            errors.extend(f"frontmatter: {e}" for e in fm_errors)
        return {
            "ok": not errors,
            "path": str(path),
            "version": manifest.get("version"),
            "created_at": manifest.get("created_at"),
            "files_checked": checked,
            "errors": errors,
            "manifest": manifest,
        }


def import_bundle(
    paths: Paths,
    archive: Path,
    *,
    dest: Path | None = None,
    apply: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    report = verify_bundle(archive)
    if not report["ok"]:
        return {"ok": False, "applied": False, "verify": report}
    dest = Path(dest) if dest else paths.os_home / ".agents" / "skills"
    target = dest / "evolution"
    result: dict[str, Any] = {
        "ok": True,
        "applied": False,
        "target": str(target),
        "verify": {k: report[k] for k in ("version", "files_checked", "created_at")},
    }
    if not apply:
        result["dry_run"] = True
        result["would_replace"] = target.exists()
        return result
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="evolution-import-") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(archive, "r") as zf:
            for info in zf.infolist():
                name = _safe_zip_name(info.filename)
                if name == "manifest.json" or info.is_dir():
                    continue
                if not name.startswith("skill/"):
                    continue
                out = tmp_path / name
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(zf.read(info))
        source = tmp_path / "skill"
        if not (source / "SKILL.md").is_file():
            raise ValueError("bundle skill payload missing")
        if target.exists():
            if not force:
                result.update({"ok": False, "error": "target exists; use --force to replace (a backup is kept)"})
                return result
            backup = paths.backups_dir / f"import-{now_stamp()}"
            backup.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(backup / "evolution"))
            result["backup"] = str(backup / "evolution")
        shutil.copytree(source, target)
        marker = {
            "schema": 1,
            "name": "evolution",
            "version": report.get("version") or __version__,
            "managed_by": "evolution",
            "canonical": str(target).lower().endswith("\\.agents\\skills\\evolution") or str(target).lower().endswith("/.agents/skills/evolution"),
            "imported_at": now_iso(),
            "source_bundle": str(archive),
        }
        write_json(target / ".evolution-managed.json", marker)
    result.update({"applied": True, "ok": True})
    return result
