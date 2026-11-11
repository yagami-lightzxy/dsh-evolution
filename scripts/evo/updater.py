"""Check and install a newer evolution release from GitHub."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from . import __version__
from .paths import Paths
from .scaffold import verify_skill
from .snapshot import create_snapshot
from .util import now_iso, now_stamp, write_json

DEFAULT_REPO = "yagami-lightzxy/dsh-evolution"


def version_tuple(value: str) -> tuple[int, int, int]:
    raw = str(value or "").strip().lstrip("v")
    parts = raw.split(".")
    try:
        return tuple(int(parts[index]) for index in range(3))  # type: ignore[return-value]
    except (IndexError, ValueError):
        return (0, 0, 0)


def _github_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"evolution/{__version__}"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - fixed GitHub endpoint
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("GitHub response is not an object")
    return data


def _latest_git_tag(repo: str) -> str:
    proc = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", f"https://github.com/{repo}.git"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "git ls-remote failed").strip())
    tags = [line.rsplit("refs/tags/", 1)[-1].strip() for line in proc.stdout.splitlines() if "refs/tags/v" in line]
    if not tags:
        raise ValueError("repository has no version tags")
    return max(tags, key=version_tuple)


def check_self_update(paths: Paths, cfg: dict[str, Any], *, online: bool = False) -> dict[str, Any]:
    local = paths.version_file.read_text(encoding="utf-8").strip() if paths.version_file.is_file() else __version__
    repo = str((cfg.get("autonomy") or {}).get("self_update_repo") or DEFAULT_REPO)
    result: dict[str, Any] = {"repo": repo, "current": local, "online": online, "status": "offline" if not online else "checking"}
    if not online:
        result["detail"] = "联网后检查 GitHub Release"
        return result
    try:
        try:
            release = _github_json(f"https://api.github.com/repos/{repo}/releases/latest")
            latest = str(release.get("tag_name") or release.get("name") or "").strip()
            release_url = release.get("html_url") or ""
            published_at = release.get("published_at") or ""
        except Exception:
            latest = _latest_git_tag(repo)
            release_url = f"https://github.com/{repo}/releases/tag/{latest}"
            published_at = ""
        result.update({"latest": latest, "release_url": release_url, "published_at": published_at})
        if version_tuple(latest) <= version_tuple(local):
            result.update({"status": "current", "detail": "已是最新 Release"})
            return result
        major_change = version_tuple(latest)[0] != version_tuple(local)[0]
        result.update({"status": "major" if major_change else "available", "major": major_change, "detail": f"可更新到 {latest}"})
        return result
    except Exception as exc:
        result.update({"status": "error", "detail": f"{type(exc).__name__}: {exc}"})
        return result


def apply_self_update(paths: Paths, cfg: dict[str, Any], *, online: bool = False, apply: bool = False, allow_major: bool = False) -> dict[str, Any]:
    check = check_self_update(paths, cfg, online=online)
    result: dict[str, Any] = {"check": check, "applied": False, "snapshot": "", "backup": ""}
    if check.get("status") in {"offline", "current", "error"}:
        return result
    if not apply:
        result["dry_run"] = True
        return result
    if check.get("major") and not allow_major:
        result["detail"] = "跨 major 只报告，不自动更新"
        return result
    if (paths.skill_dir / ".git").exists():
        result["detail"] = "当前是源码 checkout，保留工作区，由 git pull 管理"
        return result
    repo = str(check["repo"])
    tag = str(check["latest"])
    archive_url = f"https://github.com/{repo}/archive/refs/tags/{tag}.zip"
    paths.ensure_runtime()
    snapshot = create_snapshot(paths, label=f"pre-self-update-{tag}")
    backup = paths.backups_dir / f"self-update-{now_stamp()}"
    suffix = 1
    while backup.exists():
        backup = paths.backups_dir / f"self-update-{now_stamp()}-{suffix}"
        suffix += 1
    shutil.copytree(paths.skill_dir, backup)
    with tempfile.TemporaryDirectory(prefix="evolution-update-") as tmp:
        archive = Path(tmp) / "release.zip"
        request = urllib.request.Request(archive_url, headers={"User-Agent": f"evolution/{__version__}"})
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed GitHub archive endpoint
            archive.write_bytes(response.read())
        extract = Path(tmp) / "extract"
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extract)
        candidates = [path for path in extract.iterdir() if path.is_dir() and (path / "SKILL.md").is_file()]
        if len(candidates) != 1:
            result.update({"snapshot": snapshot["id"], "backup": str(backup), "detail": "Release 中没有唯一的 SKILL.md 根目录"})
            return result
        candidate = candidates[0]
        verified = verify_skill(candidate)
        if not verified.get("ok"):
            result.update({"snapshot": snapshot["id"], "backup": str(backup), "detail": "候选技能验收失败", "verification": verified})
            return result
        env = dict(os.environ)
        env["EVOLUTION_HOME"] = str(Path(tmp) / "runtime")
        proc = subprocess.run(
            [sys.executable, str(candidate / "scripts" / "evolution.py"), "selfcheck", "--strict"],
            cwd=str(candidate), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240, env=env,
        )
        if proc.returncode != 0:
            result.update({"snapshot": snapshot["id"], "backup": str(backup), "detail": "候选版本 selfcheck 失败", "verification_output": (proc.stdout or proc.stderr)[-4000:]})
            return result
        shutil.rmtree(paths.skill_dir)
        shutil.copytree(candidate, paths.skill_dir)
    result.update({"applied": True, "snapshot": snapshot["id"], "backup": str(backup), "version": tag, "updated_at": now_iso()})
    write_json(paths.state_dir / "self-update-latest.json", result)
    return result


def self_update_markdown(result: dict[str, Any]) -> str:
    check = result.get("check") or {}
    lines = ["# evolution 自更新", "", f"- 当前：{check.get('current', '?')} · 最新：{check.get('latest', '—')} · 状态：{check.get('status', '—')}"]
    if result.get("applied"):
        lines.append(f"- 已更新：{result.get('version')} · 快照：{result.get('snapshot')} · 备份：{result.get('backup')}")
    elif result.get("dry_run"):
        lines.append("- 发现新版本，这是 dry-run；加 --apply 才会安装")
    elif result.get("detail"):
        lines.append(f"- 说明：{result['detail']}")
    elif check.get("detail"):
        lines.append(f"- 说明：{check['detail']}")
    return "\n".join(lines) + "\n"
