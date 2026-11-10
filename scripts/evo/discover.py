"""Local capability discovery: skills, MCP servers, plugins, permissions, runtime."""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from .config import resolve_skill_roots
from .frontmatter import Skill, load_skill
from .paths import Paths
from .util import expand_path, file_size, human_bytes, now_iso, read_json, read_text, run_cmd, sha256_text

try:  # Python 3.11+
    import tomllib  # type: ignore
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore


def _safe_scan(root: Path) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(root) as it:
            return list(it)
    except (OSError, PermissionError):
        return []


def discover_skills(cfg: dict[str, Any], paths: Paths) -> dict[str, Any]:
    roots = resolve_skill_roots(cfg, paths)
    skills: list[Skill] = []
    errors: list[str] = []
    seen_paths: set[str] = set()
    for root_info in roots:
        root: Path = root_info["path"]
        root_info["count"] = 0
        if not root.is_dir():
            continue
        for entry in _safe_scan(root):
            name = entry.name
            if name.startswith("."):
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=True)
                is_file = entry.is_file(follow_symlinks=True)
            except OSError:
                continue
            skill_path: Path | None = None
            if is_dir:
                candidate = Path(entry.path) / "SKILL.md"
                if candidate.is_file():
                    skill_path = candidate
            elif is_file and name.lower().endswith(".md"):
                skill_path = Path(entry.path)
            if skill_path is None:
                continue
            key = str(skill_path.resolve()).lower()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            skill = load_skill(skill_path, root, str(root_info["agent"]), int(root_info["rank"]))
            if not skill.name and skill_path.name.lower() == "skill.md":
                skill.name = skill_path.parent.name
            if not skill.name and skill_path.suffix.lower() == ".md":
                skill.name = skill_path.stem
            skills.append(skill)
            root_info["count"] += 1
            if skill.errors:
                errors.append(f"{skill_path}: {'; '.join(skill.errors)}")
    skills.sort(key=lambda s: (s.name, s.rank, str(s.path)))
    return {
        "skills": skills,
        "roots": roots,
        "errors": errors,
        "counts": {
            "skills": len(skills),
            "valid": sum(1 for s in skills if s.valid),
            "invalid": sum(1 for s in skills if not s.valid),
            "roots": len([r for r in roots if r["exists"]]),
        },
    }


def _mcp_from_object(data: Any, source: Path) -> list[dict[str, Any]]:
    servers: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return servers
    containers: list[Any] = []
    for key in ("mcpServers", "mcp_servers", "servers"):
        if isinstance(data.get(key), dict):
            containers.append(data[key])
    mcp = data.get("mcp")
    if isinstance(mcp, dict):
        for key in ("servers", "mcpServers", "mcp_servers"):
            if isinstance(mcp.get(key), dict):
                containers.append(mcp[key])
    for container in containers:
        for name, spec in container.items():
            if not isinstance(spec, dict):
                continue
            command = spec.get("command")
            args = spec.get("args") if isinstance(spec.get("args"), list) else []
            env = spec.get("env") if isinstance(spec.get("env"), dict) else {}
            url = spec.get("url") or spec.get("endpoint")
            servers.append(
                {
                    "name": str(name),
                    "source": str(source),
                    "command": " ".join(str(x) for x in ([command] if isinstance(command, str) else (command or []))) if command else "",
                    "args": [str(x) for x in args],
                    "url": str(url) if url else "",
                    "env_keys": sorted(str(k) for k in env.keys()),
                    "transport": str(spec.get("type") or spec.get("transport") or ("http" if url else "stdio")),
                    "raw_keys": sorted(str(k) for k in spec.keys()),
                    "hash": sha256_text(json.dumps(spec, ensure_ascii=False, sort_keys=True)),
                }
            )
    return servers


def _mcp_from_json(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or file_size(path) > 8 * 1024 * 1024:
        return []
    data = read_json(path, default=None)
    return _mcp_from_object(data, path)


def _mcp_from_toml(path: Path) -> list[dict[str, Any]]:
    if tomllib is None or not path.is_file() or file_size(path) > 4 * 1024 * 1024:
        return []
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError):
        return []
    return _mcp_from_object(data, path)


def discover_mcp(cfg: dict[str, Any], paths: Paths) -> dict[str, Any]:
    home = paths.os_home
    candidates: list[tuple[Path, str]] = [
        (home / ".dsh" / "mcp.json", "json"),
        (home / ".agents" / "mcp.json", "json"),
        (home / ".claude.json", "json"),
        (home / ".claude" / "settings.json", "json"),
        (home / ".claude" / "mcp.json", "json"),
        (home / ".codex" / "config.toml", "toml"),
        (home / ".cursor" / "mcp.json", "json"),
        (paths.cwd / ".mcp.json", "json"),
        (paths.cwd / ".dsh" / "mcp.json", "json"),
        (paths.cwd / ".agents" / "mcp.json", "json"),
    ]
    for folder in (home / ".dsh" / "mcp", home / ".dsh" / "mcps"):
        if folder.is_dir():
            for child in sorted(folder.glob("*.json")):
                candidates.append((child, "json"))
    servers: list[dict[str, Any]] = []
    sources: list[str] = []
    for path, kind in candidates:
        if not path.exists():
            continue
        found = _mcp_from_toml(path) if kind == "toml" else _mcp_from_json(path)
        if found:
            sources.append(str(path))
            servers.extend(found)
    # Deduplicate by name+command+url, keeping the first source.
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for server in servers:
        key = (server["name"], server["command"], server["url"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(server)
    return {"servers": deduped, "sources": sources}


def discover_plugins(cfg: dict[str, Any], paths: Paths) -> dict[str, Any]:
    home = paths.os_home
    plugins: list[dict[str, Any]] = []
    profiles = home / ".dsh" / "profiles"
    for profile_name in ("desktop", "web"):
        pkg = profiles / profile_name / "package.json"
        data = read_json(pkg, default=None)
        if isinstance(data, dict):
            for name, version in (data.get("dependencies") or {}).items():
                plugins.append(
                    {
                        "name": str(name),
                        "version": str(version),
                        "source": str(pkg),
                        "kind": "dsh-profile-dependency",
                        "scope": profile_name,
                    }
                )
        patch = profiles / profile_name / "cordis.patch.yml"
        if patch.is_file():
            text = read_text(patch, max_bytes=512 * 1024)
            import re

            for match in re.finditer(r"^\s*name:\s*[\"']?([^\"'\n]+)", text, re.M):
                name = match.group(1).strip()
                if name.startswith("@"):
                    plugins.append(
                        {
                            "name": name,
                            "version": "",
                            "source": str(patch),
                            "kind": "cordis-entry",
                            "scope": profile_name,
                        }
                    )
    for base, kind in ((home / ".claude" / "plugins", "claude-plugin"), (home / ".codex" / "plugins", "codex-plugin")):
        if base.is_dir():
            for child in sorted(base.iterdir()):
                if child.is_dir():
                    plugins.append(
                        {
                            "name": child.name,
                            "version": "",
                            "source": str(child),
                            "kind": kind,
                            "scope": "user",
                        }
                    )
    # Deduplicate by name+source.
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in plugins:
        key = (item["name"], item["source"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return {"plugins": out, "count": len(out)}


def discover_permissions(cfg: dict[str, Any], paths: Paths) -> dict[str, Any]:
    home = paths.os_home
    results: list[dict[str, Any]] = []
    # DSH profile preset
    patch = home / ".dsh" / "profiles" / "desktop" / "cordis.patch.yml"
    if patch.is_file():
        import re

        text = read_text(patch, max_bytes=512 * 1024)
        for match in re.finditer(r"defaultPreset:\s*([A-Za-z0-9_-]+)", text):
            results.append(
                {
                    "source": str(patch),
                    "key": "defaultPreset",
                    "value": match.group(1),
                    "severity": "high" if match.group(1) in {"danger-full-access", "full-access"} else "info",
                }
            )
    # Codex config
    codex_cfg = home / ".codex" / "config.toml"
    if tomllib is not None and codex_cfg.is_file():
        try:
            with codex_cfg.open("rb") as fh:
                data = tomllib.load(fh)
            for key in ("approval_policy", "sandbox_mode", "model", "model_provider"):
                if key in data:
                    value = data.get(key)
                    severity = "high" if key == "approval_policy" and value == "never" else "info"
                    if key == "sandbox_mode" and value in {"danger-full-access", "danger_full_access"}:
                        severity = "high"
                    results.append(
                        {
                            "source": str(codex_cfg),
                            "key": key,
                            "value": str(value),
                            "severity": severity,
                        }
                    )
        except (OSError, ValueError):
            pass
    # Claude settings
    for name in ("settings.json", "settings.local.json"):
        path = home / ".claude" / name
        data = read_json(path, default=None)
        if isinstance(data, dict) and isinstance(data.get("permissions"), dict):
            perms = data["permissions"]
            if perms.get("defaultMode"):
                results.append(
                    {
                        "source": str(path),
                        "key": "permissions.defaultMode",
                        "value": str(perms["defaultMode"]),
                        "severity": "high" if str(perms["defaultMode"]).lower() in {"bypasspermissions", "danger"} else "info",
                    }
                )
            allow = perms.get("allow")
            if isinstance(allow, list) and allow:
                broad = [x for x in allow if str(x) in {"Bash(*)", "Bash", "*"}]
                results.append(
                    {
                        "source": str(path),
                        "key": "permissions.allow",
                        "value": f"{len(allow)} rules" + (f"; broad: {', '.join(map(str, broad))}" if broad else ""),
                        "severity": "high" if broad else "info",
                    }
                )
    return {"permissions": results, "count": len(results)}


def discover_runtime(cfg: dict[str, Any], paths: Paths) -> dict[str, Any]:
    node = shutil.which("node")
    git = shutil.which("git")
    node_version = run_cmd([node, "--version"], timeout=5)[1].strip() if node else ""
    git_version = run_cmd([git, "--version"], timeout=5)[1].strip() if git else ""
    try:
        usage = shutil.disk_usage(str(paths.runtime_home))
        disk = {"total": usage.total, "used": usage.used, "free": usage.free}
    except OSError:
        disk = {}
    try:
        paths.state_dir.mkdir(parents=True, exist_ok=True)
        probe = paths.state_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    return {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "python_impl": platform.python_implementation(),
        "python_exe": sys.executable,
        "node": node_version or "not found",
        "node_path": node or "",
        "git": git_version or "not found",
        "disk": disk,
        "runtime_writable": writable,
        "runtime_home": str(paths.runtime_home),
        "skill_dir": str(paths.skill_dir),
        "cwd": str(paths.cwd),
        "now": now_iso(),
    }


def discover_dependencies(cfg: dict[str, Any], skills: list[Skill]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for skill in skills:
        base = skill.path.parent if skill.path.name == "SKILL.md" else skill.path.parent
        if not base.is_dir():
            continue
        for name in ("requirements.txt", "pyproject.toml", "package.json"):
            candidate = base / name
            if candidate.is_file() and file_size(candidate) <= 512 * 1024:
                files.append(
                    {
                        "skill": skill.name,
                        "path": str(candidate),
                        "file": name,
                        "size": file_size(candidate),
                    }
                )
    return {"dependency_files": files, "count": len(files)}


def discover_all(cfg: dict[str, Any], paths: Paths) -> dict[str, Any]:
    skills = discover_skills(cfg, paths)
    return {
        "generated_at": now_iso(),
        "skills": skills,
        "mcp": discover_mcp(cfg, paths),
        "plugins": discover_plugins(cfg, paths),
        "permissions": discover_permissions(cfg, paths),
        "runtime": discover_runtime(cfg, paths),
        "dependencies": discover_dependencies(cfg, skills["skills"]),
    }
