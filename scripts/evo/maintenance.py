"""Autonomous weekly maintenance: dedupe skills, quarantine broken ones,
check plugin/skill upgrades, and process an install queue.

All destructive actions are snapshot-backed and each removed skill is preserved
in a backup/quarantine directory before the active copy is changed.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import get
from .discover import discover_skills
from .fetch import run_install_queue
from .frontmatter import Skill
from .install import create_directory_link
from .paths import Paths
from .snapshot import create_snapshot
from .util import now_iso, now_stamp, run_cmd, sha256_file, write_json

NPM_REGISTRY = "https://registry.npmjs.org"
USER_AGENT = "evolution-maintenance/1.1"


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _skill_dir(skill: Skill) -> Path:
    return skill.path.parent


def _canonical_root(paths: Paths) -> Path:
    return (paths.os_home / ".agents" / "skills").resolve()


def _choose_canonical(items: list[Skill], paths: Paths) -> Skill:
    canonical_root = _canonical_root(paths)
    for item in items:
        try:
            if _skill_dir(item).resolve() == canonical_root / item.name:
                return item
        except OSError:
            pass
    return sorted(items, key=lambda s: (s.rank, str(s.path)))[0]


def plan_skill_actions(paths: Paths, cfg: dict[str, Any]) -> dict[str, Any]:
    data = discover_skills(cfg, paths)
    skills: list[Skill] = data["skills"]
    protected = {str(x) for x in (get(cfg, "maintenance.protected_skills", []) or [])}
    protected.add("evolution")
    max_actions = int(get(cfg, "maintenance.max_actions", 30))
    actions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    groups: dict[tuple[str, str], list[Skill]] = defaultdict(list)
    for skill in skills:
        if skill.valid and skill.sha256:
            groups[(skill.name, skill.sha256)].append(skill)
    for (name, sha), items in sorted(groups.items()):
        if len(items) < 2 or name in protected:
            continue
        if any(item.path.name != "SKILL.md" for item in items):
            skipped.append({"action": "dedupe-link", "name": name, "reason": "flat .md skill; not auto-linked"})
            continue
        canonical = _choose_canonical(items, paths)
        canonical_dir = _skill_dir(canonical)
        for item in items:
            if item is canonical:
                continue
            target = _skill_dir(item)
            if _is_link(target):
                continue
            if (target / ".evolution-managed.json").is_file():
                continue
            if len(actions) >= max_actions:
                skipped.append({"action": "dedupe-link", "name": name, "reason": "max_actions reached"})
                continue
            actions.append(
                {
                    "action": "dedupe-link",
                    "name": name,
                    "target": str(target),
                    "canonical": str(canonical_dir),
                    "sha256": sha,
                    "agent": item.agent,
                    "reason": "same content in multiple skill roots",
                }
            )

    for skill in skills:
        if skill.valid or skill.name in protected:
            continue
        if skill.path.name != "SKILL.md":
            skipped.append({"action": "quarantine-invalid", "name": skill.name or skill.path.name, "reason": "flat .md skill; not auto-moved"})
            continue
        target = _skill_dir(skill)
        if _is_link(target):
            continue
        if len(actions) >= max_actions:
            skipped.append({"action": "quarantine-invalid", "name": skill.name or target.name, "reason": "max_actions reached"})
            continue
        actions.append(
            {
                "action": "quarantine-invalid",
                "name": skill.name or target.name,
                "target": str(target),
                "errors": skill.errors,
                "agent": skill.agent,
                "reason": "invalid frontmatter / unloadable skill",
            }
        )
    return {"skills": skills, "actions": actions, "skipped": skipped, "counts": {a["action"]: sum(1 for x in actions if x["action"] == a["action"]) for a in actions}}


def _semver(value: str) -> tuple[int, int, int] | None:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", str(value or "").strip())
    if not match:
        return None
    return tuple(int(x) for x in match.groups())  # type: ignore[return-value]


def _npm_latest(name: str) -> str | None:
    url = f"{NPM_REGISTRY}/{urllib.parse.quote(name, safe='')}/latest"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - fixed registry
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            value = str(data.get("version", "")).strip()
            if value:
                return value
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last_error = exc
            time.sleep(0.4)
    # Fallback to the npm CLI, which handles system proxy/TLS settings better.
    npm = shutil.which("npm") or "npm"
    code, out, _err = run_cmd([npm, "view", name, "version", "--json"], timeout=25)
    if code == 0:
        try:
            value = str(json.loads(out)).strip()
            if value:
                return value
        except (ValueError, TypeError):
            pass
    raise ValueError(f"cannot query npm for {name}: {last_error}")


def check_plugin_updates(paths: Paths, cfg: dict[str, Any], *, online: bool = False) -> dict[str, Any]:
    package = paths.os_home / ".dsh" / "profiles" / "desktop" / "package.json"
    if not package.is_file():
        return {"online": online, "candidates": [], "detail": "DSH desktop package.json not found"}
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"online": online, "candidates": [], "detail": f"cannot read package.json: {exc}"}
    deps = data.get("dependencies") or {}
    cache_path = paths.state_dir / "plugin-update-cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    if not isinstance(cache, dict):
        cache = {}
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for name, current in deps.items():
        if not isinstance(current, str) or not re.match(r"^\d+\.\d+\.\d+", current):
            continue
        latest = None
        if online:
            cached = cache.get(name)
            try:
                fetched = _dt.datetime.fromisoformat(str((cached or {}).get("fetched_at", "")).replace("Z", "+00:00"))
                age = (_dt.datetime.now().astimezone() - fetched).total_seconds()
            except (ValueError, TypeError):
                age = 10**9
            if isinstance(cached, dict) and age < 86400:
                latest = cached.get("latest")
            else:
                try:
                    latest = _npm_latest(name)
                    cache[name] = {"fetched_at": now_iso(), "latest": latest}
                except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
                    errors.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
        if latest:
            cur, new = _semver(current), _semver(latest)
            if cur and new and new > cur:
                candidates.append(
                    {
                        "name": name,
                        "current": current,
                        "latest": latest,
                        "major": new[0] != cur[0],
                        "risk": "major" if new[0] != cur[0] else "minor-patch",
                    }
                )
    if online:
        try:
            write_json(cache_path, cache)
        except OSError:
            pass
    return {"online": online, "candidates": candidates, "errors": errors, "checked": len(deps)}


def _backup_target(paths: Paths, target: Path, item: dict[str, Any], stamp: str) -> Path:
    folder = paths.backups_dir / f"maintenance-{stamp}" / str(item.get("agent", "unknown"))
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / target.name
    if dest.exists():
        dest = dest.with_name(dest.name + "-" + now_stamp())
    shutil.move(str(target), str(dest))
    return dest


def _write_quarantine(paths: Paths, target: Path, item: dict[str, Any], stamp: str) -> Path:
    dest = paths.state_dir / "quarantine" / stamp / str(item.get("agent", "unknown")) / target.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest = dest.with_name(dest.name + "-" + now_stamp())
    shutil.move(str(target), str(dest))
    write_json(
        dest / ".evolution-quarantine.json",
        {
            "schema": 1,
            "quarantined_at": now_iso(),
            "reason": item.get("reason", ""),
            "errors": item.get("errors", []),
            "original_path": str(target),
        },
    )
    return dest


def apply_plan(paths: Paths, plan: dict[str, Any]) -> dict[str, Any]:
    actions = plan.get("actions", [])
    result: dict[str, Any] = {"applied": [], "errors": [], "snapshot": None, "quarantined": []}
    if not actions:
        return result
    snapshot = create_snapshot(paths, label="pre-maintenance")
    result["snapshot"] = snapshot["id"]
    stamp = now_stamp()
    for item in actions:
        target = Path(item["target"])
        backup = None
        try:
            if item["action"] == "dedupe-link":
                if not target.exists():
                    result["errors"].append({"action": item["action"], "target": str(target), "error": "target missing"})
                    continue
                backup = _backup_target(paths, target, item, stamp)
                canonical = Path(item["canonical"])
                mode = create_directory_link(canonical, target)
                ok = (target / "SKILL.md").is_file() and sha256_file(target / "SKILL.md") == item.get("sha256")
                result["applied"].append(
                    {
                        "action": "dedupe-link",
                        "name": item["name"],
                        "target": str(target),
                        "canonical": str(canonical),
                        "mode": mode,
                        "backup": str(backup) if backup else "",
                        "verified": ok,
                    }
                )
            elif item["action"] == "quarantine-invalid":
                dest = _write_quarantine(paths, target, item, stamp)
                result["quarantined"].append({"name": item["name"], "from": str(target), "to": str(dest)})
                result["applied"].append({"action": "quarantine-invalid", "name": item["name"], "target": str(target), "quarantine": str(dest)})
        except Exception as exc:
            # A failed link must not leave the agent without an active skill.
            if backup is not None and not target.exists():
                try:
                    shutil.move(str(backup), str(target))
                except OSError:
                    pass
            result["errors"].append({"action": item["action"], "target": str(target), "error": f"{type(exc).__name__}: {exc}"})
    return result


def maintenance_markdown(result: dict[str, Any]) -> str:
    lines = [f"# evolution 自主维护 · {result.get('generated_at', '')}", ""]
    lines.append(f"- 应用：{result.get('applied')} · 计划动作：{len(result.get('plan', {}).get('actions', []))}")
    lines.append(f"- 快照：{result.get('snapshot') or '—'}")
    counts = result.get("plan", {}).get("counts", {})
    if counts:
        lines.append("- 动作：" + ", ".join(f"{k}={v}" for k, v in counts.items()))
    lines.append("")
    lines.append("## 已执行")
    for item in result.get("apply_result", {}).get("applied", []):
        lines.append(f"- {item['action']} · {item.get('name')} → {item.get('canonical') or item.get('quarantine')}")
    if not result.get("apply_result", {}).get("applied"):
        lines.append("- 无")
    lines.append("")
    lines.append("## 错误")
    for item in result.get("apply_result", {}).get("errors", []):
        lines.append(f"- {item.get('action')} {item.get('target')}: {item.get('error')}")
    if not result.get("apply_result", {}).get("errors"):
        lines.append("- 无")
    lines.append("")
    candidates = result.get("plugins", {}).get("candidates", [])
    lines.append("## 插件升级候选")
    if candidates:
        for item in candidates:
            lines.append(f"- {item['name']}: {item['current']} → {item['latest']} ({item['risk']})")
        lines.append("- 由 DSH `plugin_manager` 在周任务中执行；跨 major 不自动升级。")
    else:
        lines.append("- 无（或未联网检查）。")
    lines.append("")
    queue = result.get("install_queue", [])
    if queue:
        lines.append("## 按需下载")
        for item in queue:
            lines.append(f"- {item.get('name')}: {item.get('action') or item.get('error') or item.get('ok')}")
    return "\n".join(lines) + "\n"


def _dsh_launcher(paths: Paths, cfg: dict[str, Any]) -> Path | None:
    """Resolve the dsh launcher through envcheck so the logic lives in one place."""
    from .envcheck import _find_dsh_cli

    cli, _how = _find_dsh_cli(paths, cfg)
    return cli


def _installed_version(profile: Path, name: str) -> str:
    manifest = profile / "node_modules" / name / "package.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("version") or "")


def apply_plugin_updates(
    paths: Paths,
    cfg: dict[str, Any],
    *,
    apply: bool = False,
    include_major: bool = False,
    online: bool = True,
    max_actions: int = 5,
) -> dict[str, Any]:
    """Execute same-major plugin upgrades through the host CLI.

    Gates that are not negotiable:
      * cross-major is reported, never applied (the API surface may have moved);
      * link: and github: dependencies are untouched (they are not registry
        releases, and rewriting their spec would silently change the source);
      * a snapshot plus a package.json/lockfile backup is taken before the first
        write, and restored if any single upgrade fails verification.

    The runner never restarts the host. A plugin upgrade is only complete after
    DSH restarts, so requires_restart is part of the result rather than an
    afterthought.
    """
    paths.ensure_runtime()
    stamp = now_stamp()
    updates = check_plugin_updates(paths, cfg, online=online)
    candidates = list(updates.get("candidates") or [])
    plan: list[dict[str, Any]] = []
    for item in candidates:
        name = str(item.get("name") or "")
        entry = dict(item)
        if item.get("major") and not include_major:
            entry["action"] = "report-only"
            entry["why"] = "跨 major：只报告，不自动升级"
        else:
            entry["action"] = "upgrade" if apply else "would-upgrade"
        plan.append(entry)

    result: dict[str, Any] = {
        "schema": 1,
        "kind": "plugin-updates",
        "generated_at": now_iso(),
        "applied": False,
        "online": bool(online),
        "plan": plan,
        "upgraded": [],
        "errors": [],
        "skipped": [p for p in plan if p.get("action") == "report-only"],
        "snapshot": None,
        "backup_dir": "",
        "requires_restart": False,
        "checked": updates.get("checked", 0),
    }

    upgrades = [p for p in plan if p.get("action") == "upgrade"]
    if not apply:
        result["note"] = "dry-run：未修改任何文件"
        write_json(paths.state_dir / "plugin-updates-latest.json", result)
        return result
    if not upgrades:
        result["note"] = "没有可自动升级的同 major 候选"
        write_json(paths.state_dir / "plugin-updates-latest.json", result)
        return result

    cli = _dsh_launcher(paths, cfg)
    if cli is None:
        result["errors"].append({"stage": "precheck", "error": "找不到 dsh 启动器，无法执行升级"})
        result["note"] = "无法执行：缺少 dsh CLI"
        write_json(paths.state_dir / "plugin-updates-latest.json", result)
        return result

    profile_name = str(get(cfg, "paths.dsh_profile", "desktop") or "desktop")
    profile = paths.os_home / ".dsh" / "profiles" / profile_name
    managed = [profile / "package.json", profile / "pnpm-lock.yaml"]

    snapshot = create_snapshot(paths, label=f"pre-plugin-update-{stamp}")
    result["snapshot"] = snapshot.get("id") or snapshot.get("snapshot") or str(snapshot)[:120]
    backup_dir = paths.backups_dir / f"plugin-updates-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in managed:
        if path.is_file():
            try:
                shutil.copy2(path, backup_dir / path.name)
            except OSError:
                pass
    result["backup_dir"] = str(backup_dir)

    import sys as _sys

    def rollback(reason: str) -> None:
        for path in managed:
            backup = backup_dir / path.name
            if backup.is_file():
                try:
                    shutil.copy2(backup, path)
                except OSError:
                    pass
        argv = [str(cli), "plugin", "--profile", profile_name, "install"]
        if _sys.platform.startswith("win") and cli.suffix.lower() in {".cmd", ".bat"}:
            argv = ["cmd", "/c", *argv]
        run_cmd(argv, timeout=900, cwd=profile)
        result["errors"].append({"stage": "rollback", "error": reason})

    for item in upgrades[: max(1, int(max_actions))]:
        name = str(item["name"])
        target_version = str(item["latest"])
        before = _installed_version(profile, name)
        argv = [str(cli), "plugin", "--profile", profile_name, "add", f"{name}@{target_version}"]
        if _sys.platform.startswith("win") and cli.suffix.lower() in {".cmd", ".bat"}:
            argv = ["cmd", "/c", *argv]
        code, out, err = run_cmd(argv, timeout=900, cwd=profile)
        after = _installed_version(profile, name)
        ok = code == 0 and after == target_version
        record = {
            "name": name,
            "from": before,
            "to": after,
            "target": target_version,
            "exit": code,
            "ok": ok,
            "stderr": (err or "").strip()[:400],
            "stdout": (out or "").strip()[:400],
        }
        if ok:
            result["upgraded"].append(record)
        else:
            record["error"] = "版本未达到目标"
            result["errors"].append(record)
            rollback(f"{name} 升级失败：exit={code} before={before} after={after} target={target_version}")
            break

    result["applied"] = bool(result["upgraded"]) and not result["errors"]
    result["requires_restart"] = result["applied"]
    if result["requires_restart"]:
        result["note"] = "升级已写入依赖；重启 DSH 后才生效"
    elif result["errors"]:
        result["note"] = "升级失败并已回滚"
    else:
        result["note"] = "无升级写入"
    write_json(paths.state_dir / "plugin-updates-latest.json", result)
    out_dir = paths.reports_dir / "maintenance"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"plugin-updates-{stamp}.md").write_text(plugin_updates_markdown(result), encoding="utf-8", newline="\n")
    return result


def plugin_updates_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# 插件升级 · {result.get('generated_at', '')}")
    lines.append("")
    state = "已执行" if result.get("applied") else "演练"
    lines.append(f"- 状态：{state} · 检查 {result.get('checked', 0)} 个依赖 · 模式：{'联网' if result.get('online') else '离线'}")
    if result.get("snapshot"):
        lines.append(f"- 快照：{result.get('snapshot')} · 备份：{result.get('backup_dir') or '-'}")
    if result.get("requires_restart"):
        lines.append("- **需要重启 DSH 才会生效**")
    lines.append("")

    plan = result.get("plan") or []
    if plan:
        lines.append("## 计划")
        for item in plan:
            action = item.get("action")
            label = {"upgrade": "升级", "would-upgrade": "将升级", "report-only": "仅报告"}.get(str(action), str(action))
            tail = f" — {item.get('why')}" if item.get("why") else ""
            lines.append(f"- {label}：{item.get('name')} {item.get('current')} → {item.get('latest')}{tail}")
        lines.append("")

    if result.get("upgraded"):
        lines.append("## 已升级")
        for item in result["upgraded"]:
            lines.append(f"- {item['name']} {item.get('from')} → {item.get('to')}")
        lines.append("")

    if result.get("errors"):
        lines.append("## 失败与回滚")
        for item in result["errors"]:
            lines.append(f"- {item.get('stage') or item.get('name')}: {item.get('error')}")
        lines.append("")

    lines.append("## 边界")
    lines.append("- 只升级同 major 的注册表版本；跨 major、link:、github: 依赖不动。")
    lines.append("- 升级前记录快照并备份 package.json / pnpm-lock.yaml；单次失败即整体回滚。")
    lines.append("")
    return "\n".join(lines)


def run_maintenance(
    paths: Paths,
    cfg: dict[str, Any],
    *,
    apply: bool = False,
    online: bool = True,
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    paths.ensure_runtime()
    scopes = scopes or ["skills", "plugins"]
    plan = plan_skill_actions(paths, cfg) if "skills" in scopes else {"actions": [], "skipped": [], "skills": []}
    # Skill objects are for planning only; never serialize them into reports/state.
    plan.pop("skills", None)
    plugins = check_plugin_updates(paths, cfg, online=online) if "plugins" in scopes else {"candidates": [], "online": online}
    result: dict[str, Any] = {
        "schema": 1,
        "kind": "maintenance",
        "generated_at": now_iso(),
        "applied": bool(apply),
        "scopes": scopes,
        "plan": plan,
        "plugins": plugins,
        "install_queue": [],
        "apply_result": {"applied": [], "errors": [], "snapshot": None, "quarantined": []},
    }
    if apply:
        result["apply_result"] = apply_plan(paths, plan)
        result["snapshot"] = result["apply_result"].get("snapshot")
        if get(cfg, "maintenance.allow_download", True):
            result["install_queue"] = run_install_queue(paths, cfg, apply=True)
    else:
        # Dry-run queue: report what would be installed without downloading.
        for item in get(cfg, "maintenance.install_queue", []) or []:
            if isinstance(item, dict):
                result["install_queue"].append({"name": item.get("name"), "action": "would-install", "source": item.get("source")})
    result["path"] = str(paths.state_dir / "maintenance-latest.json")
    write_json(paths.state_dir / "maintenance-latest.json", result)
    md = paths.reports_dir / "maintenance"
    md.mkdir(parents=True, exist_ok=True)
    (md / f"maintenance-{now_stamp()}.md").write_text(maintenance_markdown(result), encoding="utf-8", newline="\n")
    return result