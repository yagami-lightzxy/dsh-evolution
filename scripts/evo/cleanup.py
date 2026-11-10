"""Declutter: reclaim space and reduce noise from leftovers.

A weekly evolution loop accumulates artifacts: byte-code caches, restored-file
backups, old reports, session logs, export bundles. None of them makes the next
task better, and together they slow every audit scan and blur what is current.

Deletion here is allowlist-only and paranoid:
  * only the patterns below -- never user-authored content;
  * nothing touched inside the grace window (default 7 days);
  * dry-run unless apply=True;
  * every removal is recorded with size, age and reason.

The point is not disk space. It is that an environment full of stale copies
makes "which one is real?" the dominant question.
"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

from .config import get
from .paths import Paths
from .util import human_bytes, now_iso, now_stamp, write_json

#: Directory names that only ever hold regenerable caches.
CACHE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}

#: Suffixes that are regenerable build output.
CACHE_FILE_SUFFIXES = {".pyc", ".pyo"}

#: Backup naming DSH and the plugin manager use when they restore a file.
BACKUP_PATTERN = re.compile(r"\.bak(?:-|$)|\.old$|\.orig$", re.I)

DEFAULT_GRACE_DAYS = 7


def _age_days(path: Path) -> float:
    try:
        return max(0.0, (time.time() - path.stat().st_mtime) / 86400.0)
    except OSError:
        return 0.0


def _size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
        return total
    except OSError:
        return 0


def _within(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def _items(
    paths: Paths,
    *,
    days: int,
    grace_days: int,
    include_backups: bool,
    include_logs: bool,
    retention_days: int = 180,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect deletion candidates and reasons for everything intentionally kept."""
    items: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []

    skill_root = paths.skill_dir
    runtime = paths.runtime_home
    dsh_home = paths.os_home / ".dsh"
    roots = [skill_root, runtime]

    def consider(path: Path, kind: str, reason: str, *, safe_roots: list[Path] | None = None) -> None:
        allowed = safe_roots or roots
        if not _within(path, allowed):
            kept.append({"path": str(path), "kind": kind, "why": "不在允许删除的根目录内"})
            return
        age = _age_days(path)
        if age < grace_days:
            kept.append({"path": str(path), "kind": kind, "why": f"仍在 {grace_days} 天宽限期内"})
            return
        items.append(
            {
                "path": str(path),
                "kind": kind,
                "bytes": _size(path),
                "age_days": round(age, 1),
                "reason": reason,
            }
        )

    # 1) Byte-code and tool caches: always regenerable.
    for root in (skill_root, runtime):
        if not root.is_dir():
            continue
        for child in root.rglob("*"):
            name = child.name
            if child.is_dir() and name in CACHE_DIR_NAMES:
                consider(child, "cache-dir", "字节码/工具缓存，可随时重建")
            elif child.is_file() and child.suffix.lower() in CACHE_FILE_SUFFIXES:
                consider(child, "cache-file", "字节码产物，可随时重建")

    # 2) Backups left behind by restores. Off by default: a fresh backup is a
    #    safety net, and only the user knows when it stopped being one.
    if include_backups and dsh_home.is_dir():
        for child in dsh_home.rglob("*"):
            if child.is_file() and BACKUP_PATTERN.search(child.name) and _age_days(child) >= days:
                consider(
                    child,
                    "backup",
                    f"超过 {days} 天的还原备份，当前配置已生效",
                    safe_roots=[dsh_home],
                )

    # 3) Evolution's own reports beyond the configured retention window.
    retention = max(30, int(retention_days or 180))
    for folder, kind, reason in (
        (paths.weekly_dir, "report", "超出保留期的周报"),
        (paths.audit_dir, "report", "超出保留期的审计报告"),
        (paths.reports_dir / "envcheck", "report", "超出保留期的环境体检报告"),
    ):
        if not folder.is_dir():
            continue
        for child in folder.glob("*"):
            if child.is_file() and _age_days(child) >= retention:
                consider(child, kind, reason, safe_roots=[paths.reports_dir])

    # 4) Evolution logs and exports.
    if include_logs:
        for folder, kind, reason in (
            (paths.logs_dir, "log", f"超过 {days} 天的运行日志"),
            (paths.exports_dir, "export", f"超过 {days} 天的导出包"),
        ):
            if not folder.is_dir():
                continue
            for child in folder.glob("*"):
                if _age_days(child) >= days:
                    consider(child, kind, reason, safe_roots=[runtime])

    # 5) Staging copies of this skill produced by upgrades.
    for staged in paths.cwd.glob("_evo_extract*"):
        consider(staged, "staging", "evolution 自升级的解压暂存目录", safe_roots=[paths.cwd])

    return items, kept


def plan_cleanup(
    paths: Paths,
    cfg: dict[str, Any],
    *,
    days: int = 30,
    grace_days: int = DEFAULT_GRACE_DAYS,
    include_backups: bool = False,
    include_logs: bool = True,
) -> dict[str, Any]:
    """Build a deletion plan without touching disk."""
    paths.ensure_runtime()
    days = max(1, int(days))
    items, kept = _items(
        paths,
        days=days,
        grace_days=max(0, int(grace_days)),
        include_backups=bool(include_backups),
        include_logs=bool(include_logs),
        retention_days=max(30, int(get(cfg, "review.retention_days", 180) or 180)),
    )
    items.sort(key=lambda item: (-int(item["bytes"]), item["path"]))
    totals: dict[str, int] = {}
    for item in items:
        totals[item["kind"]] = totals.get(item["kind"], 0) + int(item["bytes"])
    return {
        "schema": 1,
        "kind": "cleanup-plan",
        "generated_at": now_iso(),
        "days": days,
        "grace_days": max(0, int(grace_days)),
        "include_backups": bool(include_backups),
        "items": items,
        "kept": kept[:40],
        "counts": {kind: sum(1 for i in items if i["kind"] == kind) for kind in sorted({i["kind"] for i in items})},
        "total_bytes": sum(int(i["bytes"]) for i in items),
        "total_human": human_bytes(sum(int(i["bytes"]) for i in items)),
    }


def apply_cleanup(paths: Paths, plan: dict[str, Any]) -> dict[str, Any]:
    """Execute a plan. Returns what was removed and everything that failed."""
    removed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    freed = 0
    for item in plan.get("items", []):
        path = Path(str(item["path"]))
        if not path.exists():
            continue
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        freed += int(item.get("bytes") or 0)
        removed.append({"path": str(path), "kind": item.get("kind"), "bytes": int(item.get("bytes") or 0)})
    result = {
        "schema": 1,
        "kind": "cleanup-result",
        "generated_at": now_iso(),
        "removed": removed,
        "errors": errors,
        "freed_bytes": freed,
        "freed_human": human_bytes(freed),
    }
    write_json(paths.state_dir / "cleanup-latest.json", result)
    return result


def cleanup_markdown(plan: dict[str, Any], result: dict[str, Any] | None = None) -> str:
    lines: list[str] = []
    lines.append(f"# 断舍离 · {plan.get('generated_at', '')}")
    lines.append("")
    lines.append(
        f"- 候选 {len(plan.get('items', []))} 项 · 合计 {plan.get('total_human')} · "
        f"宽限期 {plan.get('grace_days')} 天 · 阈值 {plan.get('days')} 天"
    )
    lines.append(f"- 模式：{'已执行' if result else '演练（dry-run）'}")
    if result:
        lines.append(f"- 已删除 {len(result.get('removed', []))} 项，释放 {result.get('freed_human')}；错误 {len(result.get('errors', []))}")
    lines.append("")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in plan.get("items", []):
        grouped.setdefault(str(item.get("kind")), []).append(item)
    if grouped:
        lines.append("## 候选")
        for kind, entries in sorted(grouped.items()):
            lines.append(f"### {kind}（{len(entries)} 项）")
            for item in entries[:10]:
                lines.append(f"- {item['path']} · {human_bytes(int(item.get('bytes') or 0))} · {item['age_days']} 天 · {item['reason']}")
            if len(entries) > 10:
                lines.append(f"- …… 另有 {len(entries) - 10} 项")
            lines.append("")

    if plan.get("kept"):
        lines.append("## 有意保留")
        for item in plan["kept"][:10]:
            lines.append(f"- {item['path']} — {item['why']}")
        lines.append("")

    if result and result.get("errors"):
        lines.append("## 失败")
        for item in result["errors"]:
            lines.append(f"- {item['path']}: {item['error']}")
        lines.append("")

    lines.append("## 边界")
    lines.append("- 只删除缓存、过期报告/日志/导出与（可选）还原备份；不删除技能正文、配置与用户手写文件。")
    lines.append("- 宽限期内的文件一律保留；需要强制清理时调低 grace。")
    lines.append("")
    return "\n".join(lines)
