"""Install, link, sync global rules, doctor and uninstall."""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import ensure_config, get, resolve_install_targets, resolve_rules_paths, save_config
from .frontmatter import load_skill, parse_frontmatter
from .paths import Paths
from .snapshot import create_snapshot
from .util import (
    atomic_write_text,
    copy_file,
    ensure_dir,
    file_size,
    now_iso,
    now_stamp,
    read_json,
    read_text,
    same_path,
    sha256_file,
    sha256_text,
    write_json,
)

TARGET_ORDER = ("dsh", "agents", "claude", "codex")
COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv", "dist", "build", "*.pyc")
_SKIP_PARTS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv", "dist", "build"}


def tree_signature(root: Path) -> str:
    """Stable content signature for copy targets (detects stale copies)."""
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*")):
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        if path.is_file() and path.suffix != ".pyc":
            rel = path.relative_to(root).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(sha256_file(path).encode("ascii"))
    return digest.hexdigest()


def _read_version(paths: Paths) -> str:
    try:
        return paths.version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return __version__


def _marker(path: Path) -> dict[str, Any] | None:
    data = read_json(path / ".evolution-managed.json", default=None)
    return data if isinstance(data, dict) else None


def target_status(target: Path, source: Path) -> dict[str, Any]:
    if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
        try:
            resolved = target.resolve()
        except OSError:
            resolved = target
        return {
            "kind": "link",
            "path": str(target),
            "resolves_to": str(resolved),
            "points_to_source": same_path(resolved, source),
        }
    if same_path(target, source):
        return {"kind": "canonical", "path": str(target), "resolves_to": str(target)}
    if not target.exists() and not target.is_symlink():
        return {"kind": "absent", "path": str(target)}
    if target.is_dir():
        marker = _marker(target)
        return {
            "kind": "copy" if marker else "foreign",
            "path": str(target),
            "version": (marker or {}).get("version", ""),
            "source_hash": (marker or {}).get("source_hash", ""),
            "marker": marker,
        }
    return {"kind": "foreign-file", "path": str(target)}


def _run_mklink(target: Path, source: Path) -> bool:
    if os.name != "nt":
        return False
    ensure_dir(target.parent)
    if target.exists() or target.is_symlink():
        return False
    try:
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            shell=False,
        )
        return proc.returncode == 0 and target.exists()
    except (OSError, subprocess.SubprocessError):
        return False


def create_directory_link(source: Path, target: Path) -> str:
    """Create a junction (Windows) or symlink (POSIX); return mode used."""
    ensure_dir(target.parent)
    if target.exists() or target.is_symlink():
        raise FileExistsError(str(target))
    if os.name == "nt":
        if _run_mklink(target, source):
            return "junction"
    try:
        os.symlink(str(source), str(target), target_is_directory=True)
        return "symlink"
    except OSError:
        pass
    # Last resort: copy (still managed, but updates require reinstall).
    shutil.copytree(source, target, ignore=COPY_IGNORE)
    _write_marker(target, __version__, tree_signature(source))
    return "copy"


def _write_marker(target: Path, version: str = "", source_hash: str = "") -> None:
    write_json(
        target / ".evolution-managed.json",
        {
            "schema": 1,
            "name": "evolution",
            "version": version or __version__,
            "source_hash": source_hash,
            "managed_by": "evolution",
            "installed_at": now_iso(),
        },
    )


def _remove_managed(target: Path) -> None:
    if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
        try:
            target.unlink()
        except OSError:
            os.rmdir(target)
    elif target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()


def _backup_existing(paths: Paths, target: Path, tag: str) -> Path | None:
    if not (target.exists() or target.is_symlink()):
        return None
    folder = paths.backups_dir / f"{tag}-{now_stamp()}"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / target.name
    if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
        dest.write_text(f"link -> {os.readlink(target)}", encoding="utf-8")
        _remove_managed(target)
    else:
        shutil.move(str(target), str(dest))
    return dest


def install_target(
    paths: Paths,
    name: str,
    target: Path,
    source: Path,
    *,
    mode: str = "link",
    apply: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    status = target_status(target, source)
    result: dict[str, Any] = {"name": name, "target": str(target), "mode": mode, "status": status}
    if status["kind"] == "canonical":
        result.update({"action": "skip", "reason": "target is the canonical source"})
        return result
    if status["kind"] == "link" and status.get("points_to_source"):
        result.update({"action": "skip", "reason": "already linked to source"})
        return result
    if (
        status["kind"] == "copy"
        and status.get("version") == _read_version(paths)
        and status.get("source_hash") == tree_signature(source)
    ):
        result.update({"action": "skip", "reason": "managed copy is current"})
        return result
    if status["kind"] in {"foreign", "foreign-file"} and not force:
        result.update({"action": "conflict", "reason": "target exists and is not managed by evolution; use --force after review"})
        return result
    if not apply:
        result.update({"action": "would-install", "dry_run": True})
        return result

    if target.exists() or target.is_symlink():
        result["backup"] = str(_backup_existing(paths, target, f"install-{name}"))
    ensure_dir(target.parent)
    if mode == "copy":
        shutil.copytree(source, target, ignore=COPY_IGNORE)
        _write_marker(target, _read_version(paths), tree_signature(source))
        result.update({"action": "installed", "installed_mode": "copy"})
    else:
        try:
            used = create_directory_link(source, target)
        except Exception as exc:  # fallback to copy on link errors
            shutil.copytree(source, target, ignore=COPY_IGNORE)
            _write_marker(target, _read_version(paths), tree_signature(source))
            used = "copy"
            result["link_error"] = f"{type(exc).__name__}: {exc}"
        result.update({"action": "installed", "installed_mode": used})
    result["resolved"] = str(target.resolve())
    return result


def _block_text(paths: Paths, cfg: dict[str, Any]) -> str:
    rules = resolve_rules_paths(cfg, paths)
    template = rules.get("block_template") or (paths.skill_dir / "templates" / "AGENTS.block.md")
    text = read_text(template)
    if not text.strip():
        raise ValueError(f"empty AGENTS block template: {template}")
    return text


def upsert_managed_block(
    paths: Paths,
    agents_md: Path,
    block: str,
    *,
    begin_marker: str,
    end_marker: str,
    apply: bool = False,
) -> dict[str, Any]:
    block = block.strip() + "\n"
    if begin_marker not in block or end_marker not in block:
        raise ValueError("block template must contain both markers")
    existing = ""
    if agents_md.exists():
        existing = read_text(agents_md)
    begin_count = existing.count(begin_marker)
    end_count = existing.count(end_marker)
    if begin_count != end_count:
        raise ValueError(f"malformed managed block in {agents_md}: begin={begin_count}, end={end_count}")
    if begin_count > 1:
        raise ValueError(f"multiple managed blocks in {agents_md}; refusing to edit")
    if begin_count == 1:
        pattern = re.compile(re.escape(begin_marker) + r".*?" + re.escape(end_marker), re.DOTALL)
        replacement = block.rstrip("\n")
        new_text = pattern.sub(lambda _m: replacement, existing)
        # Keep one trailing newline when the block ends the file; avoid growing
        # one blank line on every ``install --rules-only``.
        if new_text.endswith(replacement):
            new_text = new_text.rstrip("\n") + "\n"
        action = "updated"
    else:
        sep = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
        new_text = existing + sep + block
        action = "appended"
    result = {
        "path": str(agents_md),
        "action": action if apply else f"would-{action}",
        "dry_run": not apply,
        "block_sha256": sha256_text(block),
    }
    if not apply:
        return result
    ensure_dir(agents_md.parent)
    if existing:
        backup = paths.backups_dir / f"rules-{now_stamp()}" / (agents_md.name or "AGENTS.md")
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(existing, encoding="utf-8", newline="")
        result["backup"] = str(backup)
    newline = "\r\n" if "\r\n" in existing else "\n"
    atomic_write_text(agents_md, new_text, newline=newline)
    paths.agents_block_snapshot.write_text(block, encoding="utf-8", newline="\n")
    result["written"] = True
    return result


def remove_managed_block(
    paths: Paths,
    agents_md: Path,
    *,
    begin_marker: str,
    end_marker: str,
    apply: bool = False,
) -> dict[str, Any]:
    if not agents_md.exists():
        return {"path": str(agents_md), "action": "missing"}
    existing = read_text(agents_md)
    if existing.count(begin_marker) != 1 or existing.count(end_marker) != 1:
        raise ValueError(f"malformed managed block in {agents_md}")
    pattern = re.compile(r"\n?" + re.escape(begin_marker) + r".*?" + re.escape(end_marker) + r"\n?", re.DOTALL)
    new_text = pattern.sub("\n", existing).strip("\n") + "\n"
    if not apply:
        return {"path": str(agents_md), "action": "would-remove", "dry_run": True}
    backup = paths.backups_dir / f"rules-remove-{now_stamp()}" / (agents_md.name or "AGENTS.md")
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(existing, encoding="utf-8", newline="")
    atomic_write_text(agents_md, new_text, newline="\r\n" if "\r\n" in existing else "\n")
    return {"path": str(agents_md), "action": "removed", "backup": str(backup)}


def _safety_hashes(paths: Paths, cfg: dict[str, Any]) -> dict[str, str]:
    files = {
        "rules/security.md": paths.skill_dir / "rules" / "security.md",
        "rules/precedence.md": paths.skill_dir / "rules" / "precedence.md",
        "templates/AGENTS.block.md": paths.skill_dir / "templates" / "AGENTS.block.md",
    }
    hashes = {name: sha256_file(path) for name, path in files.items() if path.is_file()}
    hashes["config.security"] = sha256_text(
        json_dumps_sorted(
            {
                "allow_safety_change": get(cfg, "security.allow_safety_change", False),
                "fail_on": get(cfg, "security.fail_on", "high"),
            }
        )
    )
    return hashes


def json_dumps_sorted(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def write_safety_lock(paths: Paths, cfg: dict[str, Any]) -> dict[str, Any]:
    lock = {
        "schema": 1,
        "created_at": now_iso(),
        "version": __version__,
        "hashes": _safety_hashes(paths, cfg),
        "note": "安全内核哈希：变更后必须显式接受（install --accept-safety-change）并记录原因。",
    }
    write_json(paths.safety_lock, lock)
    return lock


def verify_safety_lock(paths: Paths, cfg: dict[str, Any]) -> dict[str, Any]:
    lock = read_json(paths.safety_lock, default=None)
    if not isinstance(lock, dict):
        return {"ok": False, "reason": "safety lock missing", "changed": [], "unknown": []}
    current = _safety_hashes(paths, cfg)
    old = lock.get("hashes") or {}
    changed = [name for name in current if old.get(name) != current[name]]
    unknown = [name for name in current if name not in old]
    return {"ok": not changed and not unknown, "changed": changed, "unknown": unknown, "created_at": lock.get("created_at")}


def _write_shims(paths: Paths) -> dict[str, str]:
    script = paths.skill_dir / "scripts" / "evolution.py"
    cmd = paths.bin_dir / "evolution.cmd"
    sh = paths.bin_dir / "evolution"
    cmd.write_text(
        "@echo off\r\n"
        f'"{sys.executable}" "{script}" %*\r\n',
        encoding="utf-8",
        newline="",
    )
    sh.write_text(
        "#!/usr/bin/env sh\n"
        f'exec "{sys.executable}" "{script}" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    try:
        sh.chmod(sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
    return {"cmd": str(cmd), "sh": str(sh)}


def install(
    paths: Paths,
    cfg: dict[str, Any],
    *,
    targets: list[str] | None = None,
    mode: str = "link",
    apply: bool = False,
    force: bool = False,
    rules: bool = True,
    rules_only: bool = False,
    accept_safety_change: bool = False,
) -> dict[str, Any]:
    paths.ensure_runtime()
    ensure_config(paths)
    source = paths.skill_dir
    version = _read_version(paths)
    skill = load_skill(source / "SKILL.md", source, "canonical", 0)
    if not skill.valid or skill.name != "evolution":
        raise ValueError(f"invalid canonical skill: {skill.errors}")
    if skill.version and skill.version != version:
        raise ValueError(f"SKILL.md version {skill.version} != VERSION {version}")
    selected = targets or list(TARGET_ORDER)
    unknown = [t for t in selected if t not in TARGET_ORDER]
    if unknown:
        raise ValueError(f"unknown targets: {', '.join(unknown)}")
    target_map = resolve_install_targets(cfg, paths)
    safety = verify_safety_lock(paths, cfg)
    if apply and (safety.get("changed") or safety.get("unknown")) and not accept_safety_change:
        return {
            "ok": False,
            "applied": False,
            "error": "安全内核规则自上次接受后已变更；审阅后加 --accept-safety-change 再安装。",
            "safety": safety,
        }
    snapshot = create_snapshot(paths, label="pre-install") if apply else None
    result: dict[str, Any] = {
        "ok": True,
        "applied": bool(apply),
        "version": version,
        "source": str(source),
        "mode": mode,
        "targets": [],
        "rules": [],
        "snapshot": (snapshot or {}).get("id"),
        "safety": safety,
        "dry_run": not apply,
    }
    if not rules_only:
        for name in selected:
            target = target_map.get(name)
            if target is None:
                result["targets"].append({"name": name, "action": "missing-target"})
                continue
            item = install_target(paths, name, target, source, mode=mode, apply=apply, force=force)
            result["targets"].append(item)
            if item.get("action") == "conflict":
                result["ok"] = False
    if rules:
        block = _block_text(paths, cfg)
        rule_paths = resolve_rules_paths(cfg, paths)
        # Write the same managed block to Codex, shared AGENTS, and DSH AGENTS.
        for key in ("agents_md", "agents_shared_md", "dsh_agents_md"):
            path = rule_paths.get(key)
            if path is None:
                continue
            try:
                item = upsert_managed_block(
                    paths,
                    path,
                    block,
                    begin_marker=str(get(cfg, "rules.begin_marker", "<!-- EVOLUTION:BEGIN")),
                    end_marker=str(get(cfg, "rules.end_marker", "<!-- EVOLUTION:END -->")),
                    apply=apply,
                )
                item["key"] = key
                result["rules"].append(item)
            except Exception as exc:
                result["rules"].append({"key": key, "path": str(path), "error": f"{type(exc).__name__}: {exc}"})
                result["ok"] = False
    if apply:
        result["shims"] = _write_shims(paths)
        if rules_only:
            # Still refresh the safety lock when rules were explicitly changed.
            result["safety_lock"] = write_safety_lock(paths, cfg)
        else:
            result["safety_lock"] = write_safety_lock(paths, cfg)
        manifest = {
            "schema": 1,
            "name": "evolution",
            "version": version,
            "installed_at": now_iso(),
            "source": str(source),
            "mode": mode,
            "targets": result["targets"],
            "rules": result["rules"],
            "shims": result.get("shims", {}),
            "snapshot": result.get("snapshot"),
        }
        write_json(paths.install_manifest, manifest)
    return result


def uninstall(
    paths: Paths,
    cfg: dict[str, Any],
    *,
    targets: list[str] | None = None,
    apply: bool = False,
    purge: bool = False,
) -> dict[str, Any]:
    selected = targets or list(TARGET_ORDER)
    target_map = resolve_install_targets(cfg, paths)
    source = paths.skill_dir
    result: dict[str, Any] = {"ok": True, "applied": bool(apply), "targets": [], "rules": [], "purged": False}
    for name in selected:
        target = target_map.get(name)
        if target is None:
            continue
        status = target_status(target, source)
        item = {"name": name, "target": str(target), "status": status}
        if status["kind"] == "canonical":
            item.update({"action": "skip", "reason": "canonical source"})
        elif status["kind"] == "link" and status.get("points_to_source"):
            item["action"] = "would-remove" if not apply else "remove"
            if apply:
                _remove_managed(target)
                item["action"] = "removed"
        elif status["kind"] == "copy":
            item["action"] = "would-remove" if not apply else "remove"
            if apply:
                _remove_managed(target)
                item["action"] = "removed"
        else:
            item.update({"action": "skip", "reason": "not managed by evolution"})
        result["targets"].append(item)
    rule_paths = resolve_rules_paths(cfg, paths)
    for key in ("agents_md", "agents_shared_md", "dsh_agents_md"):
        path = rule_paths.get(key)
        if path is None or not path.exists():
            continue
        try:
            item = remove_managed_block(
                paths,
                path,
                begin_marker=str(get(cfg, "rules.begin_marker", "<!-- EVOLUTION:BEGIN")),
                end_marker=str(get(cfg, "rules.end_marker", "<!-- EVOLUTION:END -->")),
                apply=apply,
            )
            item["key"] = key
            result["rules"].append(item)
        except Exception as exc:
            result["rules"].append({"key": key, "path": str(path), "error": f"{type(exc).__name__}: {exc}"})
    if purge and apply:
        purge_paths = [paths.config, paths.schedule_state]
        for path in purge_paths:
            if path.exists():
                path.unlink()
        result["purged"] = True
    return result


def doctor(paths: Paths, cfg: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "", severity: str = "high") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "severity": severity if not ok else "info"})

    add("runtime_dir", paths.runtime_home.is_dir(), str(paths.runtime_home))
    add("config", paths.config.is_file(), str(paths.config))
    add("skill_md", paths.skill_md.is_file(), str(paths.skill_md))
    skill = load_skill(paths.skill_md, paths.skill_dir, "canonical", 0) if paths.skill_md.is_file() else None
    add("frontmatter", bool(skill and skill.valid), "; ".join(skill.errors) if skill else "missing SKILL.md")
    add("version", bool(skill and skill.version == _read_version(paths)), f"SKILL={getattr(skill, 'version', '?')} VERSION={_read_version(paths)}", "medium")
    add("python>=3.11", sys.version_info >= (3, 11), sys.version.split()[0])
    rules_dir = paths.skill_dir / "rules"
    add("rules", rules_dir.is_dir() and len(list(rules_dir.glob("*.md"))) >= 5, str(rules_dir), "medium")
    add("tests", (paths.skill_dir / "tests").is_dir(), str(paths.skill_dir / "tests"), "medium")

    target_map = resolve_install_targets(cfg, paths)
    target_rows: list[dict[str, Any]] = []
    for name in TARGET_ORDER:
        target = target_map.get(name)
        if target is None:
            continue
        status = target_status(target, paths.skill_dir)
        ok = status["kind"] in {"canonical", "link", "copy"} and (
            status["kind"] != "link" or status.get("points_to_source")
        )
        stale = False
        if status["kind"] == "copy":
            stale = status.get("source_hash") != tree_signature(paths.skill_dir)
            ok = ok and not stale
        target_rows.append({"name": name, **status, "ok": ok, "stale": stale})
        detail = f"{status['kind']} -> {target}" + (" (stale copy; reinstall)" if stale else "")
        add(f"target:{name}", ok, detail, "medium" if stale else "high")
    rule_paths = resolve_rules_paths(cfg, paths)
    rules_rows: list[dict[str, Any]] = []
    block = ""
    if (rule_paths.get("block_template") or Path()).is_file():
        block = read_text(rule_paths["block_template"]).strip()
    for key in ("agents_md", "agents_shared_md", "dsh_agents_md"):
        path = rule_paths.get(key)
        if path is None:
            continue
        text = read_text(path) if path.exists() else ""
        count = text.count(str(get(cfg, "rules.begin_marker", "<!-- EVOLUTION:BEGIN")))
        ok = count == 1
        rules_rows.append({"key": key, "path": str(path), "exists": path.exists(), "blocks": count, "ok": ok})
        add(f"rules:{key}", ok, f"{path} blocks={count}", "medium")
    safety = verify_safety_lock(paths, cfg)
    add("safety_lock", safety.get("ok", False), "; ".join(safety.get("changed", [])) or "ok", "medium")
    schedule_info: dict[str, Any] = {}
    try:
        from .schedule import schedule_status

        schedule_info = schedule_status(paths, cfg)
        add("schedule", bool(schedule_info.get("installed")), str(schedule_info.get("detail", "")), "low")
    except Exception as exc:
        add("schedule", False, f"{type(exc).__name__}: {exc}", "low")
    failed = [c for c in checks if not c["ok"] and c["severity"] in {"high", "medium"}]
    return {
        "ok": not failed,
        "checks": checks,
        "targets": target_rows,
        "rules": rules_rows,
        "safety": safety,
        "schedule": schedule_info,
        "failed": failed,
        "generated_at": now_iso(),
        "version": _read_version(paths),
    }


def install_report_markdown(report: dict[str, Any]) -> str:
    lines = [f"# evolution 安装报告", ""]
    lines.append(f"- 版本：{report.get('version')} · 应用：{report.get('applied')} · 成功：{report.get('ok')}")
    if report.get("snapshot"):
        lines.append(f"- 快照：{report['snapshot']}")
    lines.append("")
    lines.append("## 目标")
    for item in report.get("targets", []):
        lines.append(f"- {item.get('name')}: {item.get('action')} ({item.get('target')})")
    lines.append("")
    lines.append("## 全局规则")
    for item in report.get("rules", []):
        lines.append(f"- {item.get('key')}: {item.get('action', item.get('error'))} ({item.get('path')})")
    if report.get("error"):
        lines.append("")
        lines.append(f"**错误**：{report['error']}")
    lines.append("")
    return "\n".join(lines)
