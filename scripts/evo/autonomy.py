"""Single improvement loop for environment, skills, tools, and learning."""
from __future__ import annotations

from typing import Any

from .audit import run_audit
from .config import get
from .envcheck import run_envcheck, write_envcheck
from .evolve import run_evolution
from .experience import materialize_skills, reflect
from .paths import Paths
from .skill_health import run_skill_health
from .skill_sources import collect_entries, plan_quarantine, purge_quarantine, quarantine, usage_index
from .maintenance import run_maintenance
from .util import now_iso, write_json
from .updater import apply_self_update, check_self_update


def plan_improvement(paths: Paths, cfg: dict[str, Any], *, days: int = 30, online: bool = False) -> dict[str, Any]:
    env = run_envcheck(paths, cfg, online=online)
    write_envcheck(paths, env)
    audit = run_audit(paths, cfg, online=online, context="improve")
    write_json(paths.state_dir / "audit-latest.json", audit)
    health = run_skill_health(paths, cfg)
    maintenance = run_maintenance(paths, cfg, apply=False, online=online, scopes=list(get(cfg, "maintenance.scope", ["skills", "plugins"])))
    reflection = reflect(paths, days=days, min_repeats=int(get(cfg, "autonomy.min_repeats", 3)))
    evolution = run_evolution(paths, cfg, apply=False, bump="none", release=False, days=days)
    self_update = check_self_update(paths, cfg, online=online) if bool(get(cfg, "autonomy.self_update", True)) else {"status": "disabled"}
    entries = collect_entries(paths, cfg)
    unused = plan_quarantine(
        entries,
        days=int(get(cfg, "autonomy.unused_days", 90)),
        protected={str(x) for x in (get(cfg, "maintenance.protected_skills", []) or [])},
        usage_available=bool(usage_index(paths)),
        retire_failure_rate=float(get(cfg, "autonomy.retire_failure_rate", 0.7)),
        min_failures=int(get(cfg, "autonomy.min_failures", 3)),
    )
    actions = list(maintenance.get("plan", {}).get("actions", []))
    actions.extend({"action": "quarantine-unused", **item} for item in unused.get("candidates", []))
    report = {
        "schema": 1,
        "kind": "improvement-plan",
        "generated_at": now_iso(),
        "version": evolution.get("schema", 1),
        "environment": {k: v for k, v in env.items() if k != "markdown"},
        "audit": audit,
        "health": health,
        "maintenance": maintenance,
        "reflection": reflection,
        "evolution": evolution,
        "self_update": self_update,
        "skills": {"entries": entries, "unused": unused},
        "actions": actions,
        "limits": [
            "工具更新候选由宿主和包管理器报告，安装仍通过已配置的维护队列执行。",
            "没有使用遥测时，不把缺失记录当作闲置证据。",
        ],
    }
    write_json(paths.autonomy_file, report)
    return report


def apply_improvement(paths: Paths, cfg: dict[str, Any], plan: dict[str, Any], *, online: bool = False, purge: bool = False) -> dict[str, Any]:
    self_update = apply_self_update(
        paths,
        cfg,
        online=online,
        apply=bool(get(cfg, "autonomy.self_update", True)),
        allow_major=bool(get(cfg, "autonomy.allow_major_update", False)),
    )
    maintenance = run_maintenance(
        paths,
        cfg,
        apply=True,
        online=online,
        scopes=list(get(cfg, "maintenance.scope", ["skills", "plugins"])),
    )
    quarantined = []
    for item in (plan.get("skills", {}).get("unused", {}).get("candidates", []) or []):
        quarantined.append(quarantine(paths, item, apply=True))
    evolution = run_evolution(paths, cfg, apply=True, bump="none", release=False, days=int(plan.get("reflection", {}).get("days", 30)))
    generated = materialize_skills(
        paths,
        plan.get("reflection", {}).get("proposals", []),
        apply=bool(get(cfg, "autonomy.create_skills", True)),
    )
    purged = purge_quarantine(
        paths,
        days=int(get(cfg, "autonomy.purge_after_days", 365)),
        apply=bool(purge),
    )
    result = {
        "schema": 1,
        "kind": "improvement-run",
        "generated_at": now_iso(),
        "maintenance": maintenance,
        "self_update": self_update,
        "quarantined": quarantined,
        "evolution": evolution,
        "generated_skills": generated,
        "purged": purged,
        "plan": str(paths.autonomy_file),
    }
    write_json(paths.state_dir / "improvement-latest.json", result)
    return result


def improvement_markdown(plan: dict[str, Any], result: dict[str, Any] | None = None) -> str:
    env = plan.get("environment") or {}
    lines = [f"# 自主改进闭环 · {plan.get('generated_at', '')}", "", f"- 环境：{env.get('score', '—')}/100 {str(env.get('status', '')).upper()}", f"- 维护动作：{len(plan.get('actions', []))}", f"- 复盘提案：{len(plan.get('reflection', {}).get('proposals', []))}", ""]
    for action in plan.get("actions", [])[:20]:
        lines.append(f"- {action.get('action')} · {action.get('name') or action.get('target') or ''} · {action.get('reason') or ''}")
    if result:
        lines.extend(["", f"## 已执行", f"- 自更新：{result.get('self_update', {}).get('version') or result.get('self_update', {}).get('check', {}).get('status', '—')}", f"- 技能隔离：{len(result.get('quarantined', []))}", f"- 新建技能：{len([x for x in result.get('generated_skills', []) if x.get('applied')])}", f"- 清理隔离区：{len(result.get('purged', {}).get('purged', []))}", f"- 维护错误：{len(result.get('maintenance', {}).get('apply_result', {}).get('errors', []))}"])
    if not plan.get("actions"):
        lines.append("- 当前没有需要执行的维护动作。")
    return "\n".join(lines) + "\n"
