"""Configuration loading, defaults and path resolution."""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

from .paths import Paths
from .util import expand_path, now_iso, read_json, write_json

DEFAULT_CONFIG: dict[str, Any] = {
    "schema": 1,
    "agent_name": "dsh",
    "profile": "balanced",
    "paths": {
        "dsh_home": "~/.dsh",
        "agents_home": "~/.agents",
        "claude_home": "~/.claude",
        "codex_home": "~/.codex",
    },
    "skill_roots": [
        {"path": "<cwd>/.dsh/skills", "rank": 100, "agent": "dsh-project"},
        {"path": "<cwd>/.agents/skills", "rank": 200, "agent": "shared-project"},
        {"path": "~/.dsh/skills", "rank": 400, "agent": "dsh"},
        {"path": "~/.agents/skills", "rank": 500, "agent": "shared"},
        {"path": "~/.claude/skills", "rank": 700, "agent": "claude"},
        {"path": "~/.codex/skills", "rank": 700, "agent": "codex"},
    ],
    "install_targets": {
        "dsh": "~/.dsh/skills/evolution",
        "agents": "~/.agents/skills/evolution",
        "claude": "~/.claude/skills/evolution",
        "codex": "~/.codex/skills/evolution",
    },
    "rules": {
        "agents_md": "~/.codex/AGENTS.md",
        "agents_shared_md": "~/.agents/AGENTS.md",
        "dsh_agents_md": "~/.dsh/AGENTS.md",
        "claude_md": "~/.claude/CLAUDE.md",
        "block_template": "templates/AGENTS.block.md",
        "begin_marker": "<!-- EVOLUTION:BEGIN",
        "end_marker": "<!-- EVOLUTION:END -->",
    },
    "review": {
        "driver": "native",
        "day": "SUN",
        "time": "20:00",
        "retention_days": 180,
        "snapshot_retention_days": 90,
        "metrics_retention_days": 365,
        "task_name": "DSH-Evolution-WeeklyReview",
    },
    "security": {
        "online": False,
        "max_scan_bytes": 1048576,
        "max_files_per_skill": 400,
        "fail_on": "high",
        "allow_safety_change": False,
        "accepted_risks": [],
    },
    "metrics": {
        "enabled": True,
        "redact_task_labels": False,
    },
    "maintenance": {
        "enabled": True,
        "auto_apply": True,
        "scope": ["skills", "plugins"],
        "dedupe_exact": True,
        "quarantine_invalid": True,
        "max_actions": 30,
        "online": True,
        "allow_download": True,
        "install_queue": [],
        "require_license": False,
        "protected_skills": ["evolution"],
        "plugin_updates": "auto",
        "backup_retention_days": 90,
    },
    "autonomy": {
        "enabled": True,
        "create_skills": True,
        "min_repeats": 3,
        "unused_days": 90,
        "purge_after_days": 365,
        "retire_failure_rate": 0.7,
        "min_failures": 3,
        "self_update": True,
        "self_update_repo": "yagami-lightzxy/dsh-evolution",
        "allow_major_update": False,
    },
    "upgrade_watch": [
        {"kind": "github", "repo": "anthropics/skills", "note": "Agent Skills reference"},
        {"kind": "github", "repo": "openai/codex", "note": "Codex CLI"},
        {"kind": "github", "repo": "modelcontextprotocol/servers", "note": "MCP reference servers"},
    ],
    "ignored_paths": [
        "**/node_modules/**",
        "**/.git/**",
        "**/__pycache__/**",
        "**/.venv/**",
        "**/dist/**",
        "**/build/**",
    ],
}

_PROTECTED_KEYS = {"schema", "paths", "install_targets", "rules", "security"}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(paths: Paths, *, ensure: bool = False) -> dict[str, Any]:
    paths.ensure_runtime()
    data = read_json(paths.config, default=None)
    if data is None:
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        if ensure:
            cfg["created_at"] = now_iso()
            write_json(paths.config, cfg)
        return cfg
    if not isinstance(data, dict):
        raise ValueError(f"config is not a JSON object: {paths.config}")
    merged = _deep_merge(DEFAULT_CONFIG, data)
    merged["_loaded_from"] = str(paths.config)
    return merged


def ensure_config(paths: Paths, *, force: bool = False) -> Path:
    paths.ensure_runtime()
    if paths.config.exists() and not force:
        return paths.config
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["created_at"] = now_iso()
    cfg["schema"] = 1
    write_json(paths.config, cfg)
    return paths.config


def save_config(paths: Paths, cfg: dict[str, Any]) -> None:
    clean = {k: v for k, v in cfg.items() if not str(k).startswith("_")}
    write_json(paths.config, clean)


def resolve_skill_roots(cfg: dict[str, Any], paths: Paths) -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in cfg.get("skill_roots", []):
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict) or not item.get("path"):
            continue
        raw = str(item["path"]).replace("<cwd>", str(paths.cwd))
        p = expand_path(raw, base=paths.os_home, home=paths.os_home)
        key = str(p).lower() if str(p).startswith("\\\\") else str(p)
        if key in seen:
            continue
        seen.add(key)
        roots.append(
            {
                "path": p,
                "rank": int(item.get("rank", 999)),
                "agent": str(item.get("agent", "unknown")),
                "exists": p.is_dir(),
            }
        )
    roots.sort(key=lambda r: (r["rank"], str(r["path"])))
    return roots


def resolve_install_targets(cfg: dict[str, Any], paths: Paths) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for name, raw in (cfg.get("install_targets") or {}).items():
        # An empty string disables a target: without this, the merged defaults
        # always re-enable cross-agent targets on machines that only run DSH.
        if not raw:
            continue
        out[str(name)] = expand_path(str(raw), base=paths.os_home, home=paths.os_home)
    return out


def resolve_rules_paths(cfg: dict[str, Any], paths: Paths) -> dict[str, Path]:
    rules = cfg.get("rules") or {}
    out: dict[str, Path] = {}
    for key in ("agents_md", "agents_shared_md", "dsh_agents_md", "claude_md", "block_template"):
        raw = rules.get(key)
        if raw:
            if key == "block_template" and not str(raw).startswith(("~", "/", "\\")) and not Path(str(raw)).is_absolute():
                out[key] = paths.skill_dir / str(raw)
            else:
                out[key] = expand_path(str(raw), base=paths.os_home, home=paths.os_home)
    return out


def get(cfg: dict[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def validate_config(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(cfg.get("skill_roots"), list):
        errors.append("skill_roots must be a list")
    if not isinstance(cfg.get("install_targets"), dict):
        errors.append("install_targets must be an object")
    fail_on = get(cfg, "security.fail_on", "high")
    if fail_on not in {"none", "low", "medium", "high", "critical"}:
        errors.append("security.fail_on must be none|low|medium|high|critical")
    for key in ("day", "time"):
        if not get(cfg, f"review.{key}"):
            errors.append(f"review.{key} is required")
    return errors


def warn(message: str) -> None:
    print(f"[evolution] warning: {message}", file=sys.stderr)
