"""Which installed skills came from where, and which are actually still used?

Deleting an unused skill is not something this module does. Two reasons:

  * the host does not report skill invocations, so "unused" is a guess built
    from age and the metrics ledger -- strong enough to nominate a skill for
    quarantine, never strong enough to destroy it;
  * deletion is irreversible. Quarantine is not.

So the loop is: nominate -> move to a dated quarantine folder with a manifest ->
restore on demand. A skill written by hand (no provenance file) is never
nominated, because there is no upstream to reinstall it from.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .config import get
from .discover import discover_skills
from .paths import Paths
from .util import now_iso, now_stamp, read_json, read_text, write_json

PROVENANCE_FILE = ".evolution-source.json"
QUARANTINE_MANIFEST = ".evolution-quarantine.json"
DEFAULT_UNUSED_DAYS = 90


def _age_days(mtime: float) -> float:
    return max(0.0, (time.time() - mtime) / 86400.0) if mtime else 0.0


def provenance_of(skill_dir: Path) -> dict[str, Any] | None:
    data = read_json(skill_dir / PROVENANCE_FILE, default=None)
    return data if isinstance(data, dict) else None


def usage_index(paths: Paths) -> dict[str, dict[str, Any]]:
    """Skill name -> {count, last}. Empty when no metrics were ever recorded."""
    path = paths.metrics_file
    if not path.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in read_text(path, max_bytes=16 * 1024 * 1024).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        name = str(record.get("skill") or "").strip()
        if not name:
            continue
        slot = out.setdefault(name, {"count": 0, "last": ""})
        slot["count"] += 1
        ts = str(record.get("ts") or "")
        if ts > slot["last"]:
            slot["last"] = ts
    return out


def collect_entries(paths: Paths, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Every discovered skill, enriched with provenance, age and usage."""
    data = discover_skills(cfg, paths)
    usage = usage_index(paths)
    protected = {str(x) for x in (get(cfg, "maintenance.protected_skills", []) or [])}
    entries: list[dict[str, Any]] = []
    for skill in data["skills"]:
        folder = skill.path.parent
        prov = provenance_of(folder)
        source = (prov or {}).get("source") or {}
        used = usage.get(skill.name) or {}
        entries.append(
            {
                "name": skill.name,
                "path": str(folder),
                "agent": skill.agent,
                "rank": skill.rank,
                "version": skill.version,
                "age_days": round(_age_days(skill.mtime), 1),
                "managed": prov is not None,
                "source": source if isinstance(source, dict) else {},
                "installed_at": str((prov or {}).get("installed_at") or ""),
                "used_count": int(used.get("count") or 0),
                "last_used": str(used.get("last") or ""),
                "protected": skill.name in protected,
            }
        )
    return entries


def plan_quarantine(
    entries: list[dict[str, Any]],
    *,
    days: int = DEFAULT_UNUSED_DAYS,
    protected: set[str] | None = None,
    usage_available: bool = True,
) -> dict[str, Any]:
    """Nominate skills to quarantine. Hand-written skills are never nominated.

    usage_available=False means no usage ledger exists at all. In that state
    "no usage record" says nothing about a skill, so nothing is nominated:
    an empty ledger must not read as "everything is unused".
    """
    protected = protected or set()
    candidates: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    if not usage_available:
        return {
            "candidates": [],
            "kept": [{"name": str(e["name"]), "why": "没有任何使用记录数据，无法判断是否闲置"} for e in entries],
            "days": days,
            "usage_available": False,
        }
    for entry in entries:
        name = str(entry["name"])
        if entry.get("protected") or name in protected:
            kept.append({"name": name, "why": "受保护（protected_skills）"})
            continue
        if not entry.get("managed"):
            kept.append({"name": name, "why": "手写技能，没有来源可回装"})
            continue
        if int(entry.get("used_count") or 0) > 0 or entry.get("last_used"):
            kept.append({"name": name, "why": f"有使用记录（{entry.get('used_count')} 次）"})
            continue
        age = float(entry.get("age_days") or 0)
        if age < days:
            kept.append({"name": name, "why": f"仅 {age:.0f} 天未动（阈值 {days}）"})
            continue
        candidates.append(
            {
                "name": name,
                "path": entry["path"],
                "age_days": age,
                "source": entry.get("source") or {},
                "reason": f"来自外部安装、{age:.0f} 天未修改、无使用记录",
            }
        )
    return {"candidates": candidates, "kept": kept, "days": days, "usage_available": True}


def quarantine(paths: Paths, item: dict[str, Any], *, apply: bool = False) -> dict[str, Any]:
    """Move one skill into a dated quarantine folder. Reversible."""
    source = Path(str(item["path"]))
    stamp = now_stamp()
    dest = paths.state_dir / "quarantine" / stamp / source.name
    report = {"name": item.get("name"), "from": str(source), "to": str(dest), "applied": False}
    if not source.exists():
        report["error"] = "source missing"
        return report
    if not apply:
        report["dry_run"] = True
        return report
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    write_json(
        dest / QUARANTINE_MANIFEST,
        {
            "schema": 1,
            "name": item.get("name"),
            "from": str(source),
            "quarantined_at": now_iso(),
            "reason": item.get("reason", ""),
            "source": item.get("source") or {},
            "restore": f"evolution skills --restore {item.get('name')} --apply",
        },
    )
    report.update({"applied": True, "restore": f"evolution skills --restore {item.get('name')} --apply"})
    return report


def _latest_quarantine(paths: Paths, name: str) -> tuple[Path, dict[str, Any]] | None:
    root = paths.state_dir / "quarantine"
    if not root.is_dir():
        return None
    for folder in sorted(root.iterdir(), reverse=True):
        candidate = folder / name
        manifest = read_json(candidate / QUARANTINE_MANIFEST, default=None)
        if isinstance(manifest, dict):
            return candidate, manifest
    return None


def restore(paths: Paths, name: str, *, apply: bool = False) -> dict[str, Any]:
    found = _latest_quarantine(paths, name)
    if not found:
        return {"name": name, "found": False, "error": "quarantine 中没有该技能"}
    folder, manifest = found
    target = Path(str(manifest.get("from") or ""))
    report = {"name": name, "found": True, "from": str(folder), "to": str(target), "applied": False}
    if target.exists():
        report["error"] = "目标位置已存在同名技能；先移走它再恢复"
        return report
    if not apply:
        report["dry_run"] = True
        return report
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(target))
    report["applied"] = True
    return report


def update_entry(paths: Paths, cfg: dict[str, Any], entry: dict[str, Any], *, apply: bool = False) -> dict[str, Any]:
    """Re-fetch one managed skill from its recorded source."""
    from .fetch import fetch_skill

    source = entry.get("source") or {}
    raw = str(source.get("source") or "").strip()
    if not raw:
        return {"name": entry.get("name"), "ok": False, "error": "没有记录来源，无法更新"}
    try:
        report = fetch_skill(
            paths,
            cfg,
            raw,
            ref=str(source.get("ref") or ""),
            subpath=str(source.get("subpath") or ""),
            name=str(entry.get("name") or ""),
            apply=apply,
            link_targets=True,
            allow_risk=False,
            allow_no_license=False,
        )
    except (ValueError, OSError, FileNotFoundError) as exc:
        return {"name": entry.get("name"), "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    report["ok"] = True
    return report


def sources_markdown(entries: list[dict[str, Any]], plan: dict[str, Any] | None = None) -> str:
    lines = ["# 技能来源与使用情况", ""]
    managed = sum(1 for e in entries if e.get("managed"))
    lines.append(f"- 共 {len(entries)} 个技能，其中 {managed} 个有外部来源记录，{len(entries) - managed} 个手写")
    lines.append("")
    lines.append("| 技能 | 来源 | 版本 | 年龄(天) | 使用次数 | 最近使用 | 保护 |")
    lines.append("|---|---|---|---|---|---|---|")
    for entry in entries:
        source = entry.get("source") or {}
        label = str(source.get("source") or ("手写" if not entry.get("managed") else "未知"))
        lines.append(
            f"| {entry['name']} | {label[:48]} | {entry.get('version') or '-'} | {entry.get('age_days')} | "
            f"{entry.get('used_count')} | {entry.get('last_used') or '-'} | {'是' if entry.get('protected') else '-'} |"
        )
    lines.append("")
    if plan is not None:
        lines.append("## 隔离候选")
        if plan["candidates"]:
            for item in plan["candidates"]:
                lines.append(f"- {item['name']}：{item['reason']}（可还原：evolution skills --restore {item['name']} --apply）")
        else:
            lines.append("- 无。")
        lines.append("")
        if plan.get("kept"):
            lines.append("## 未纳入隔离")
            for item in plan["kept"]:
                lines.append(f"- {item['name']}：{item['why']}")
            lines.append("")
    lines.append("## 边界")
    lines.append("- 本工具不删除技能：只做隔离，且可用 --restore 还原。")
    lines.append("- 没有来源记录的手写技能永不进入隔离候选。")
    lines.append("- 使用次数来自 evolution 的任务记录；宿主不提供技能调用遥测，没有记录不等于真的没用。")
    return "\n".join(lines)
