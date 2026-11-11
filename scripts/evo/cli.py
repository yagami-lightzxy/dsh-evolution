"""Command-line interface for evolution."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .audit import fail_on, latest_audit, run_audit, write_audit
from .cleanup import apply_cleanup, cleanup_markdown, plan_cleanup
from .envcheck import run_envcheck, write_envcheck
from .scaffold import create_skill, verify_markdown, verify_skill
from .skill_health import run_skill_health, skill_health_markdown
from .skill_sources import (
    collect_entries,
    usage_index,
    plan_quarantine,
    quarantine,
    restore as restore_skill,
    sources_markdown,
    update_entry,
)
from .config import ensure_config, get, load_config, validate_config
from .discover import discover_all
from .frontmatter import load_skill, parse_frontmatter
from .evolve import run_evolution
from .fetch import fetch_skill
from .install import (
    TARGET_ORDER,
    doctor,
    install,
    install_report_markdown,
    uninstall,
    verify_safety_lock,
)
from .maintenance import (
    apply_plugin_updates,
    check_plugin_updates,
    maintenance_markdown,
    plugin_updates_markdown,
    run_maintenance,
)
from .metrics import ERROR_CLASSES, OUTCOMES, format_markdown as metrics_markdown
from .metrics import load as load_metrics
from .metrics import record as record_metric
from .metrics import stats as metrics_stats
from .paths import Paths, build_paths
from .recommend import format_recommendation, recommend
from .review import run_review
from .schedule import install_schedule, remove_schedule, schedule_status
from .security import scan_skill, summarize
from .snapshot import (
    create_snapshot,
    diff_snapshot,
    export_bundle,
    import_bundle,
    list_snapshots,
    load_manifest,
    prune_snapshots,
    rollback,
    verify_bundle,
)
from .util import file_lock, now_iso, now_stamp, read_text, sha256_file, write_json


def _emit(args: argparse.Namespace, data: Any, human: str = "") -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif human and not getattr(args, "quiet", False):
        print(human)


def _err(message: str) -> None:
    print(f"evolution: {message}", file=sys.stderr)


def _paths(args: argparse.Namespace) -> Paths:
    return build_paths(runtime_home=getattr(args, "home", None), os_home=getattr(args, "os_home", None))


def _cfg(paths: Paths) -> dict[str, Any]:
    cfg = load_config(paths, ensure=True)
    errors = validate_config(cfg)
    if errors:
        raise ValueError("invalid config: " + "; ".join(errors))
    return cfg


def _init(args: argparse.Namespace) -> int:
    paths = _paths(args)
    paths.ensure_runtime()
    ensure_config(paths, force=bool(args.force))
    cfg = load_config(paths)
    result = {
        "ok": True,
        "runtime_home": str(paths.runtime_home),
        "config": str(paths.config),
        "skill": str(paths.skill_dir),
        "version": cfg.get("schema"),
    }
    _emit(args, result, f"initialized: {paths.runtime_home}\nconfig: {paths.config}\nskill: {paths.skill_dir}")
    return 0


def _status(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    data = discover_all(cfg, paths)
    schedule = schedule_status(paths, cfg)
    latest = latest_audit(paths)
    review_latest = paths.state_dir / "review-latest.json"
    result = {
        "version": __version__,
        "generated_at": now_iso(),
        "paths": {
            "skill": str(paths.skill_dir),
            "runtime": str(paths.runtime_home),
            "reports": str(paths.reports_dir),
            "metrics": str(paths.metrics_file),
        },
        "counts": {
            "skills": data["skills"]["counts"]["skills"],
            "valid_skills": data["skills"]["counts"]["valid"],
            "skill_roots": data["skills"]["counts"]["roots"],
            "mcp": len(data["mcp"]["servers"]),
            "plugins": data["plugins"]["count"],
        },
        "schedule": schedule,
        "latest_audit": (latest or {}).get("generated_at"),
        "latest_review": review_latest.exists(),
        "safety_lock": verify_safety_lock(paths, cfg),
    }
    human = (
        f"evolution {__version__}\n"
        f"skill: {paths.skill_dir}\n"
        f"runtime: {paths.runtime_home}\n"
        f"skills: {result['counts']['skills']} ({result['counts']['valid_skills']} valid) · "
        f"MCP: {result['counts']['mcp']} · plugins: {result['counts']['plugins']}\n"
        f"schedule: {schedule.get('driver')} installed={schedule.get('installed')} ({schedule.get('detail')})\n"
        f"latest audit: {result['latest_audit'] or '—'}\n"
        f"safety lock: {'ok' if result['safety_lock'].get('ok') else result['safety_lock'].get('reason') or result['safety_lock'].get('changed')}"
    )
    _emit(args, result, human)
    return 0


def _doctor(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    result = doctor(paths, cfg)
    human_lines = [f"evolution doctor · {result['version']} · ok={result['ok']}", ""]
    for check in result["checks"]:
        mark = "PASS" if check["ok"] else f"FAIL({check['severity']})"
        human_lines.append(f"[{mark}] {check['name']}: {check['detail']}")
    human_lines.append("")
    human_lines.append("targets:")
    for target in result["targets"]:
        human_lines.append(f"  - {target['name']}: {target['kind']} ok={target['ok']} -> {target['path']}")
    _emit(args, result, "\n".join(human_lines))
    return 0 if result["ok"] else 1


def _capabilities(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    data = discover_all(cfg, paths)
    agent = getattr(args, "agent", "") or ""
    skills = [s.as_dict() for s in data["skills"]["skills"] if not agent or s.agent == agent]
    roots_serializable = [{**root, "path": str(root["path"])} for root in data["skills"]["roots"]]
    result = {
        "generated_at": now_iso(),
        "skills": skills,
        "skill_roots": roots_serializable,
        "mcp": data["mcp"]["servers"],
        "plugins": data["plugins"]["plugins"],
        "permissions": data["permissions"]["permissions"],
        "runtime": data["runtime"],
        "dependencies": data["dependencies"],
        "counts": {
            "skills": len(skills),
            "mcp": len(data["mcp"]["servers"]),
            "plugins": data["plugins"]["count"],
        },
    }
    if getattr(args, "json", False):
        _emit(args, result)
        return 0
    lines = [f"# 能力盘点 · {result['generated_at']}", ""]
    lines.append(f"- 技能 {len(skills)} · MCP {result['counts']['mcp']} · 插件 {result['counts']['plugins']}")
    lines.append("")
    lines.append("## 技能")
    lines.append("| 名称 | Agent | 版本 | 说明 |")
    lines.append("|---|---|---|---|")
    for s in skills[:80]:
        lines.append(f"| {s['name']} | {s['agent']} | {s['version'] or '—'} | {s['description'][:100]} |")
    if len(skills) > 80:
        lines.append(f"| … | … | … | 其余 {len(skills) - 80} 个 |")
    lines.append("")
    if data["mcp"]["servers"]:
        lines.append("## MCP")
        lines.append("| 名称 | 来源 | 命令/URL |")
        lines.append("|---|---|---|")
        for s in data["mcp"]["servers"]:
            lines.append(f"| {s['name']} | {Path(s['source']).name} | {s['command'] or s['url']} |")
        lines.append("")
    lines.append("## 技能根")
    for root in data["skills"]["roots"]:
        lines.append(f"- [{'x' if root['exists'] else ' '}] rank {root['rank']} · {root['agent']} · {root['path']} ({root.get('count', 0)})")
    _emit(args, result, "\n".join(lines))
    return 0


def _recommend(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    result = recommend(paths, cfg, args.task, limit=args.limit)
    _emit(args, result, format_recommendation(result))
    return 0


def _audit(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    scopes = [s for chunk in (args.scope or []) for s in chunk.split(",") if s]
    threshold = args.fail_on or get(cfg, "security.fail_on", "high")
    with file_lock(paths.lock_file, timeout=60):
        result = run_audit(paths, cfg, scopes=scopes or None, online=bool(args.online))
        md_path, json_path = write_audit(paths, result)
    summary = {
        "score": result["score"],
        "severity_counts": result["severity_counts"],
        "counts": result["counts"],
        "top_actions": result["next_actions"],
        "reports": {"markdown": str(md_path), "json": str(json_path)},
    }
    human = (
        f"审计完成：健康分 {result['score']}/100 · 发现 {result['counts']['findings']} "
        f"(critical {result['severity_counts'].get('critical', 0)}, high {result['severity_counts'].get('high', 0)})\n"
        f"报告：{md_path}\nJSON：{json_path}\n"
    )
    for item in result["next_actions"][:5]:
        human += f"- {item['severity'].upper()} {item['title']} → {item['recommendation']}\n"
    if getattr(args, "out", ""):
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix.lower() == ".json":
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        else:
            from .audit import audit_markdown

            out_path.write_text(audit_markdown(result), encoding="utf-8", newline="\n")
        summary["out"] = str(out_path)
    if getattr(args, "format", "md") == "json" and not getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _emit(args, summary, human)
    return 1 if fail_on(result, threshold) else 0


def _review(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    if args.days < 1:
        _err("--days must be >= 1")
        return 2
    maintenance = None
    if args.apply:
        with file_lock(paths.lock_file, timeout=120):
            maintenance = run_maintenance(
                paths,
                cfg,
                apply=True,
                online=bool(args.online),
                scopes=list(get(cfg, "maintenance.scope", ["skills", "plugins"])),
            )
    with file_lock(paths.lock_file, timeout=120):
        result = run_review(
            paths,
            cfg,
            week=args.week or "",
            online=bool(args.online),
            no_audit=bool(args.no_audit),
            days=int(args.days),
            maintenance=maintenance,
        )
    env = result.get("env") or {}
    summary = {
        "week": result["week"],
        "health": result["audit"]["score"],
        "env_score": env.get("score"),
        "env_status": env.get("status"),
        "severity_counts": result["audit"]["severity_counts"],
        "metrics": {k: result["metrics"].get(k) for k in ("n", "success_rate", "failure_rate", "cost_usd")},
        "top_actions": result["audit"]["next_actions"][:5],
        "schedule": result["schedule"],
        "reports": result["paths"],
    }
    if getattr(args, "print_report", False) and not getattr(args, "json", False):
        print(result["markdown"])
    else:
        human = (
            f"周审阅完成：{result['week']} · 健康分 {result['audit']['score']}/100 · "
            f"环境年代感 {env.get('score', '—')}/100 ({env.get('status', '—')}) · "
            f"发现 {result['audit']['counts']['findings']}\n"
            f"报告：{result['paths']['markdown']}\nJSON：{result['paths']['json']}\n"
        )
        for item in result["audit"]["next_actions"][:5]:
            human += f"- {item['severity'].upper()} {item['title']} → {item['recommendation']}\n"
        _emit(args, summary, human)
    if args.fail_on and fail_on(result["audit"], args.fail_on):
        return 1
    return 0


def _envcheck(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    result = run_envcheck(
        paths,
        cfg,
        online=bool(args.online),
        threshold_days=int(getattr(args, "days", 30) or 30),
    )
    md_path, json_path = write_envcheck(paths, result)
    if getattr(args, "print_report", False) and not getattr(args, "json", False):
        print(result["markdown"])
    else:
        human = (
            f"环境年代感体检 · {result['score']}/100 · {result['status'].upper()} · "
            f"{'联网' if result['online'] else '离线'}\n"
            f"报告：{md_path}\nJSON：{json_path}\n"
        )
        for check_name, check in result["checks"].items():
            human += f"[{check['status'].upper()}] {check_name}: {check['detail']}\n"
        for item in result["next_actions"][:5]:
            human += f"- {item['severity'].upper()} {item['title']} -> {item['recommendation']}\n"
        _emit(args, result, human)
    threshold = str(getattr(args, "fail_on", "none") or "none")
    if threshold != "none":
        rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
        worst = max((rank.get(str(f.get("severity")), 0) for f in result["findings"]), default=0)
        if worst >= rank.get(threshold, 99):
            return 1
    return 0


def _cleanup(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    plan = plan_cleanup(
        paths,
        cfg,
        days=int(args.days),
        grace_days=int(args.grace_days),
        include_backups=bool(args.include_backups),
        include_logs=not bool(args.no_logs),
    )
    result = None
    if args.apply:
        with file_lock(paths.lock_file, timeout=600):
            result = apply_cleanup(paths, plan)
    human = cleanup_markdown(plan, result)
    if not args.apply:
        human += "\n这是 dry-run；加 --apply 执行。\n"
    _emit(args, {"plan": plan, "result": result}, human)
    return 0 if not (result and result.get("errors")) else 1


def _health(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    result = run_skill_health(paths, cfg, stale_days=int(getattr(args, "days", 0) or 0) or None)
    if getattr(args, "print_report", False) and not getattr(args, "json", False):
        print(skill_health_markdown(result))
    else:
        counts = result["counts"]
        human = (
            f"技能健康：{counts['total']} 个（ok {counts['ok']} / warn {counts['warn']} / bad {counts['bad']}）\n"
            f"问题：{len(result['findings'])} 条，过期阈值 {result['stale_days']} 天\n"
        )
        for finding in result["findings"][:10]:
            human += f"- [{finding['severity']}] {finding['skill']} · {finding['kind']}：{finding['detail'][:120]}\n"
        _emit(args, result, human)
    threshold = str(getattr(args, "fail_on", "none") or "none")
    if threshold != "none":
        rank = {"high": 3, "medium": 2, "low": 1, "info": 0}
        worst = max((rank.get(str(f.get("severity")), 0) for f in result["findings"]), default=0)
        if worst >= rank.get(threshold, 99):
            return 1
    return 0


def _new(args: argparse.Namespace) -> int:
    paths = _paths(args)
    _cfg(paths)
    root = Path(args.dir).expanduser() if args.dir else paths.os_home / ".dsh" / "skills"
    result = create_skill(
        root,
        args.name,
        args.description,
        version=args.version,
        license=args.license,
        author=args.author,
        when_to_use=args.when_to_use,
        with_script=bool(args.with_script),
        with_tests=bool(args.with_tests),
        apply=bool(args.apply),
    )
    human = [f"新建技能：{result['name']} → {result['target']}"]
    for path in result.get("files", []):
        human.append(f"- {path}")
    if not result["ok"]:
        for item in result["errors"]:
            human.append(f"错误：{item}")
    elif not result.get("applied"):
        human.append("这是 dry-run；加 --apply 写盘。")
    else:
        human.append(f"下一步：{result.get('next', '')}")
    _emit(args, result, "\n".join(human))
    return 0 if result["ok"] else 1


def _verify(args: argparse.Namespace) -> int:
    paths = _paths(args)
    _cfg(paths)
    command: Any = (args.command or "").strip()
    if isinstance(command, str) and command.startswith("["):
        # JSON array form avoids every shell quoting trap: ["python", "-m", "unittest"]
        try:
            command = json.loads(command)
        except ValueError as exc:
            _err(f"--command JSON 解析失败: {exc}")
            return 2
    result = verify_skill(args.path, command=command, timeout=float(args.timeout))
    _emit(args, result, verify_markdown(result))
    return 0 if result["ok"] else 1


def _skills(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    protected = {str(x) for x in (get(cfg, "maintenance.protected_skills", []) or [])}
    entries = collect_entries(paths, cfg)

    if args.restore:
        data = restore_skill(paths, args.restore, apply=bool(args.apply))
        human = [f"还原 {args.restore}：{'已移动' if data.get('applied') else ('演练' if data.get('dry_run') else '失败')}"]
        for key in ("from", "to", "error"):
            if data.get(key):
                human.append(f"- {key}: {data[key]}")
        _emit(args, data, "\n".join(human))
        return 0 if data.get("applied") or data.get("dry_run") else 1

    if args.update:
        entry = next((e for e in entries if e["name"] == args.update), None)
        if entry is None:
            _err(f"找不到技能 {args.update}")
            return 2
        data = update_entry(paths, cfg, entry, apply=bool(args.apply))
        human = [f"更新 {args.update}：{'完成' if data.get('installed') else ('演练' if data.get('dry_run') else '失败')}"]
        if data.get("error"):
            human.append(f"- 错误：{data['error']}")
        if data.get("provenance"):
            human.append(f"- 来源记录：{data['provenance']}")
        _emit(args, data, "\n".join(human))
        return 0 if data.get("ok") else 1

    has_usage = bool(usage_index(paths))
    plan = (
        plan_quarantine(entries, days=int(args.days), protected=protected, usage_available=has_usage)
        if args.quarantine_unused
        else None
    )
    applied: list[dict[str, Any]] = []
    if plan is not None and args.apply:
        with file_lock(paths.lock_file, timeout=60):
            applied = [quarantine(paths, item, apply=True) for item in plan["candidates"]]
    human = sources_markdown(entries, plan)
    if plan is not None and not args.apply:
        human += "\n这是演练；加 --apply 执行隔离。\n"
    _emit(args, {"entries": entries, "plan": plan, "applied": applied}, human)
    return 0


def _maintain(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    scopes = [s for chunk in (args.scope or []) for s in chunk.split(",") if s]
    scopes = scopes or list(get(cfg, "maintenance.scope", ["skills", "plugins"]))
    apply = bool(args.apply) or (bool(get(cfg, "maintenance.auto_apply", False)) and not args.dry_run)
    online = bool(args.online) if args.online or args.offline else bool(get(cfg, "maintenance.online", True))
    if getattr(args, "max_actions", None):
        cfg.setdefault("maintenance", {})["max_actions"] = int(args.max_actions)
    with file_lock(paths.lock_file, timeout=120):
        result = run_maintenance(paths, cfg, apply=apply, online=online, scopes=scopes)
    human = maintenance_markdown(result)
    if not apply:
        human += "\n这是 dry-run；加 --apply（或设置 maintenance.auto_apply=true）执行。\n"
    _emit(args, result, human)
    return 0 if not result.get("apply_result", {}).get("errors") else 1


def _plugins(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    online = bool(args.online)
    if getattr(args, "apply", False):
        with file_lock(paths.lock_file, timeout=900):
            applied = apply_plugin_updates(
                paths,
                cfg,
                apply=True,
                include_major=bool(getattr(args, "include_major", False)),
                online=online,
                max_actions=int(getattr(args, "max_actions", 0) or 5),
            )
        _emit(args, applied, plugin_updates_markdown(applied))
        return 0 if not applied.get("errors") else 1
    result = check_plugin_updates(paths, cfg, online=online)
    human = [f"DSH 插件升级检查（online={online}）", ""]
    if result.get("candidates"):
        human.append("| 插件 | 当前 | 最新 | 风险 |")
        human.append("|---|---|---|---|")
        for item in result["candidates"]:
            human.append(f"| {item['name']} | {item['current']} | {item['latest']} | {item['risk']} |")
        human.append("")
        human.append("升级由 DSH `plugin_manager` 执行；跨 major 不自动升级。")
    else:
        human.append("无升级候选（或未联网）。")
    for item in result.get("errors", []):
        human.append(f"- 检查失败：{item['name']}: {item['error']}")
    _emit(args, result, "\n".join(human))
    return 0


def _fetch(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    with file_lock(paths.lock_file, timeout=120):
        result = fetch_skill(
            paths,
            cfg,
            args.source,
            ref=args.ref,
            subpath=args.path,
            name=args.name,
            apply=bool(args.apply),
            link_targets=not args.no_link,
            allow_risk=bool(args.allow_risk),
            allow_no_license=bool(args.allow_no_license),
        )
    human = (
        f"fetch: {result.get('name')} -> {result.get('target')}\n"
        f"apply={result.get('apply')} dry_run={result.get('dry_run')} "
        f"findings={result.get('findings')} high={result.get('high_findings')}\n"
        + (f"backup={result.get('backup')}\n" if result.get("backup") else "")
        + (f"linked={result.get('linked')}\n" if result.get("linked") else "")
    )
    _emit(args, result, human)
    return 0 if result.get("ok", True) else 1


def _evolve(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    with file_lock(paths.lock_file, timeout=180):
        result = run_evolution(
            paths,
            cfg,
            apply=bool(args.apply),
            bump=args.bump,
            release=bool(args.release),
            days=int(args.days),
            evaluate=bool(args.evaluate),
            force_bump=bool(args.force_bump),
        )
    diagnosis = result.get("diagnosis", {})
    human = [
        f"自我进化 · {diagnosis.get('generated_at', '')}",
        f"- 基线样本：{diagnosis.get('baseline', {}).get('n', 0)}；提案：{len(diagnosis.get('proposals', []))}",
    ]
    for proposal in diagnosis.get("proposals", []):
        human.append(f"- {proposal['area']}/{proposal['id']}：{proposal['change']}（{proposal['evidence']}）")
    if result.get("applied"):
        learned = sorted({p for item in result["applied"] for p in item["proposals"]})
        human.append(f"- 已写入学习规则：{', '.join(learned)}")
    if result.get("upgrade"):
        up = result["upgrade"]
        if up.get("ok"):
            human.append(f"- 自升级：{up['version_before']} → {up['version_after']}；bundle {up.get('bundle', {}).get('path')}")
        else:
            human.append(f"- 自升级失败：{up.get('error')}")
    elif args.apply:
        human.append("- 无新学习项，未触发版本升级。")
    if result.get("evaluation"):
        human.append(f"- 实验评估：{result['evaluation']}")
    _emit(args, result, "\n".join(human))
    return 0 if not (result.get("upgrade") and not result["upgrade"].get("ok")) else 1


def _record(args: argparse.Namespace) -> int:
    paths = _paths(args)
    _cfg(paths)  # validates config exists / dirs
    tags = [t for chunk in (args.tags or []) for t in chunk.split(",") if t]
    with file_lock(paths.lock_file, timeout=30):
        record = record_metric(
            paths,
            task=args.task,
            outcome=args.outcome,
            duration_s=args.duration,
            tokens=args.tokens,
            cost_usd=args.cost,
            tools=args.tools,
            interventions=args.interventions,
            retries=args.retries,
            quality=args.quality,
            error_class=args.error_class or "",
            notes=args.notes or "",
            tags=tags,
            agent=args.agent or "",
            model=args.model or "",
            skill=args.skill or "",
        )
    _emit(args, record, f"recorded: {record['task']} -> {record['outcome']} @ {record['ts']}\nmetrics: {paths.metrics_file}")
    return 0


def _stats(args: argparse.Namespace) -> int:
    paths = _paths(args)
    if args.days < 1:
        _err("--days must be >= 1")
        return 2
    records = load_metrics(paths, days=args.days)
    data = metrics_stats(records)
    human = f"任务指标（最近 {args.days} 天）\n" + metrics_markdown(data)
    _emit(args, data, human)
    return 0


def _snapshot(args: argparse.Namespace) -> int:
    paths = _paths(args)
    _cfg(paths)
    with file_lock(paths.lock_file, timeout=30):
        data = create_snapshot(paths, label=args.label or "manual")
    _emit(args, data, f"snapshot: {data['id']} ({data['file_count']} files)\npath: {paths.snapshots_dir / data['id']}")
    return 0


def _history(args: argparse.Namespace) -> int:
    paths = _paths(args)
    items = list_snapshots(paths)[: args.limit]
    lines = ["快照历史：", ""]
    for item in items:
        lines.append(f"- {item['id']} · {item['created_at']} · {item['label']} · {item['file_count']} files")
    _emit(args, {"snapshots": items}, "\n".join(lines))
    return 0


def _diff(args: argparse.Namespace) -> int:
    paths = _paths(args)
    data = diff_snapshot(paths, args.from_id, args.to)
    lines = [f"diff {data['from']} -> {data['to']} · {data['summary']}"]
    for key in data["added"]:
        lines.append(f"+ {key}")
    for key in data["removed"]:
        lines.append(f"- {key}")
    for item in data["changed"]:
        lines.append(f"~ {item['key']}")
    _emit(args, data, "\n".join(lines))
    return 0


def _rollback(args: argparse.Namespace) -> int:
    paths = _paths(args)
    _cfg(paths)
    with file_lock(paths.lock_file, timeout=60):
        data = rollback(paths, args.snapshot, apply=bool(args.apply))
    if not args.apply:
        lines = [f"rollback dry-run: {args.snapshot}", "将要恢复："]
        for item in data["plan"]:
            lines.append(f"- {item['scope']}:{item['path']} -> {item['target']}")
        lines.append("加 --apply 执行；执行前会自动创建 pre-rollback 快照。")
        _emit(args, data, "\n".join(lines))
        return 0
    lines = [
        f"rollback applied: {args.snapshot} all_ok={data.get('all_ok')}",
        f"pre-rollback snapshot: {data.get('pre_rollback_snapshot')}",
        f"backup: {data.get('backup_dir')}",
    ]
    _emit(args, data, "\n".join(lines))
    return 0 if data.get("all_ok") else 1


def _export(args: argparse.Namespace) -> int:
    paths = _paths(args)
    _cfg(paths)
    out = Path(args.out) if args.out else None
    with file_lock(paths.lock_file, timeout=60):
        data = export_bundle(paths, out=out, include_knowledge=bool(args.include_knowledge))
    _emit(args, data, f"exported: {data['path']}\nfiles={data['files']} size={data['size']} sha256={data['sha256']}")
    return 0


def _release(args: argparse.Namespace) -> int:
    paths = _paths(args)
    _cfg(paths)
    version = paths.version_file.read_text(encoding="utf-8").strip() if paths.version_file.exists() else __version__
    if args.version and args.version != version:
        _err(f"version mismatch: requested {args.version}, VERSION={version}")
        return 2
    out = Path(args.out) if args.out else (paths.releases_dir / f"evolution-{version}.zip")
    with file_lock(paths.lock_file, timeout=60):
        data = export_bundle(paths, out=out, include_knowledge=False)
    data.update({"kind": "release", "version": version, "release_path": str(out)})
    _emit(args, data, f"release: {out}\nfiles={data['files']} size={data['size']} sha256={data['sha256']}")
    return 0


def _import(args: argparse.Namespace) -> int:
    paths = _paths(args)
    _cfg(paths)
    archive = Path(args.archive)
    if args.verify_only:
        data = verify_bundle(archive)
        _emit(args, data, json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data["ok"] else 1
    with file_lock(paths.lock_file, timeout=60):
        data = import_bundle(
            paths,
            archive,
            dest=Path(args.dest) if args.dest else None,
            apply=bool(args.apply),
            force=bool(args.force),
        )
    _emit(args, data, json.dumps(data, ensure_ascii=False, indent=2))
    return 0 if data.get("ok") else 1


def _install(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    targets = [t for chunk in (args.targets or []) for t in chunk.split(",") if t] or list(TARGET_ORDER)
    with file_lock(paths.lock_file, timeout=60):
        data = install(
            paths,
            cfg,
            targets=targets,
            mode=args.mode,
            apply=bool(args.apply),
            force=bool(args.force),
            rules=not args.no_rules,
            rules_only=bool(args.rules_only),
            accept_safety_change=bool(args.accept_safety_change),
        )
        checks = doctor(paths, cfg) if args.apply and not args.rules_only else None
    human = install_report_markdown(data)
    if checks is not None:
        human += f"\ndoctor: {'PASS' if checks['ok'] else 'FAIL'} · failed={len(checks['failed'])}\n"
    _emit(args, {"install": data, "doctor": checks}, human)
    return 0 if data.get("ok") and (checks is None or checks.get("ok")) else 1


def _uninstall(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    if args.purge and not args.yes:
        _err("--purge 会删除 config/schedule 状态；需同时加 --yes")
        return 3
    targets = [t for chunk in (args.targets or []) for t in chunk.split(",") if t] or list(TARGET_ORDER)
    with file_lock(paths.lock_file, timeout=60):
        data = uninstall(paths, cfg, targets=targets, apply=bool(args.apply), purge=bool(args.purge))
    _emit(args, data, json.dumps(data, ensure_ascii=False, indent=2))
    return 0 if data.get("ok") else 1


def _schedule(args: argparse.Namespace) -> int:
    paths = _paths(args)
    cfg = _cfg(paths)
    action = args.schedule_action
    if action == "confirm":
        from .schedule import confirm_native_schedule

        with file_lock(paths.lock_file, timeout=30):
            data = confirm_native_schedule(
                paths,
                cfg,
                schedule_id=args.schedule_id,
                scheduled_at=getattr(args, "scheduled_at", ""),
            )
        _emit(args, data, f"native schedule confirmed: {data.get('agent_schedule_id')} at {data.get('scheduled_at')}")
        return 0
    if action == "status":
        data = schedule_status(paths, cfg)
        human = (
            f"driver={data.get('driver')} installed={data.get('installed')}\n"
            f"native recorded={data.get('native_recorded')} active={data.get('native_active')}\n"
            f"os installed={data.get('os', {}).get('installed')} ({data.get('os', {}).get('detail')})\n"
            f"{data.get('detail')}"
        )
        _emit(args, data, human)
        return 0
    if action in {"native", "install"}:
        driver = getattr(args, "driver", None) or "native"
        with file_lock(paths.lock_file, timeout=30):
            data = install_schedule(
                paths,
                cfg,
                driver=driver,
                apply=bool(args.apply),
                time_value=getattr(args, "time", None),
                day=getattr(args, "day", None),
            )
        if driver == "native":
            prompt = data.get("prompt", "")
            human = (
                "Agent 原生自动化（首选）\n"
                "在 DSH 中调用 schedule_create，title='evolution 全局审阅'，weekly 时间："
                f"{data.get('day')} {data.get('time')} Asia/Shanghai。\n"
                "Prompt:\n" + prompt + "\n"
                f"记录：{paths.schedule_state}（active={data.get('active')}）\n"
            )
            if not args.apply:
                human += "这是 dry-run；加 --apply 写入原生调度提示记录。\n"
        else:
            human = json.dumps(data, ensure_ascii=False, indent=2)
        _emit(args, data, human)
        return 0 if data.get("supported", True) else 1
    if action == "remove":
        driver = args.driver or "native"
        with file_lock(paths.lock_file, timeout=30):
            data = remove_schedule(paths, cfg, driver=driver, apply=bool(args.apply))
        _emit(args, data, json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    _err("unknown schedule action")
    return 2


def _knowledge(args: argparse.Namespace) -> int:
    paths = _paths(args)
    paths.ensure_runtime()
    target = paths.knowledge_dir / "lessons.md"
    action = args.knowledge_action
    if action == "add":
        if not args.title:
            _err("--title is required")
            return 2
        from .util import redact_secrets

        body = redact_secrets(args.body or "")
        safe_title = redact_secrets(args.title.strip())
        entry = f"\n## {now_stamp()[:8]} · {safe_title}\n\n{body.strip()}\n\n- tags: {', '.join(args.tags or [])}\n"
        if not args.apply:
            _emit(args, {"action": "dry-run", "target": str(target), "entry": entry}, entry)
            return 0
        with file_lock(paths.lock_file, timeout=30):
            existing = read_text(target) if target.exists() else "# 经验与教训\n"
            if args.title.strip() in existing and body.strip() and body.strip()[:80] in existing:
                _emit(args, {"action": "duplicate", "target": str(target)}, "duplicate: 标题与正文片段已存在，未写入。")
                return 0
            target.write_text(existing.rstrip("\n") + "\n" + entry, encoding="utf-8", newline="\n")
        _emit(args, {"action": "added", "target": str(target)}, f"added: {target}")
        return 0
    if action == "list":
        items = []
        if target.exists():
            for line in read_text(target).splitlines():
                if line.startswith("## "):
                    items.append(line[3:])
        _emit(args, {"file": str(target), "items": items}, "\n".join(items) if items else "（空）")
        return 0
    if action == "search":
        query = (args.query or "").lower()
        hits = []
        if target.exists():
            lines = read_text(target).splitlines()
            for idx, line in enumerate(lines):
                if query and query in line.lower():
                    hits.append({"line": idx + 1, "text": line[:300]})
        _emit(args, {"query": query, "hits": hits}, "\n".join(f"{h['line']}: {h['text']}" for h in hits) or "（无命中）")
        return 0
    _err("unknown knowledge action")
    return 2


def _validate(args: argparse.Namespace) -> int:
    paths = _paths(args)
    if args.bundle:
        data = verify_bundle(Path(args.bundle))
    else:
        path = Path(args.path) if args.path else paths.skill_md
        base = path.parent if path.name == "SKILL.md" else path
        if path.is_dir():
            path = path / "SKILL.md"
            base = path.parent
        skill = load_skill(path, base, "validate", 0)
        findings = scan_skill(skill)
        data = {
            "ok": skill.valid and not any(f["severity"] in {"critical", "high"} for f in findings),
            "skill": skill.as_dict(),
            "findings": findings,
            "severity_counts": summarize(findings),
            "version_match": skill.version == paths.version_file.read_text(encoding="utf-8").strip() if paths.version_file.exists() else None,
        }
    _emit(args, data, json.dumps(data, ensure_ascii=False, indent=2))
    return 0 if data.get("ok") else 1


def _selfcheck(args: argparse.Namespace) -> int:
    paths = _paths(args)
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "", severity: str = "high") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "severity": "info" if ok else severity})

    version = paths.version_file.read_text(encoding="utf-8").strip() if paths.version_file.exists() else ""
    add("VERSION", bool(version), version or "missing")
    text = read_text(paths.skill_md) if paths.skill_md.exists() else ""
    fm, _body, errors = parse_frontmatter(text)
    add("SKILL.md frontmatter", not errors and fm.get("name") == "evolution", "; ".join(errors), "high")
    add("name=evolution", fm.get("name") == "evolution", str(fm.get("name")))
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    add("version consistency", str(meta.get("version", "")) == version, f"SKILL={meta.get('version')} VERSION={version}", "medium")
    add("SKILL.md size", len(text.encode("utf-8")) < 256 * 1024, f"{len(text.encode('utf-8'))} bytes", "medium")
    for rel in (
        "README.md", "CHANGELOG.md", "LICENSE", "rules/principles.md", "rules/precedence.md",
        "rules/weekly-review.md", "rules/security.md", "rules/knowledge.md",
        "templates/AGENTS.block.md", "references/commands.md", "references/portability.md",
    ):
        add(rel, (paths.skill_dir / rel).exists(), str(paths.skill_dir / rel), "medium")
    block = read_text(paths.skill_dir / "templates" / "AGENTS.block.md") if (paths.skill_dir / "templates" / "AGENTS.block.md").exists() else ""
    add("AGENTS block markers", "<!-- EVOLUTION:BEGIN" in block and "<!-- EVOLUTION:END -->" in block, "", "high")
    py_errors: list[str] = []
    for path in (paths.skill_dir / "scripts").rglob("*.py"):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            py_errors.append(f"{path}: {exc}")
    add("python syntax", not py_errors, "; ".join(py_errors[:5]), "high")
    skill = load_skill(paths.skill_md, paths.skill_dir, "self", 0) if paths.skill_md.exists() else None
    findings = scan_skill(skill) if skill else []
    high = [f for f in findings if f["severity"] in {"critical", "high"}]
    add("self security scan", not high, "; ".join(f"{f['title']} @ {f['path']}" for f in high[:5]), "high")
    if args.strict:
        code = _run_tests(paths, verbose=False)
        add("tests", code == 0, f"unittest exit={code}", "high")
    result = {
        "ok": all(c["ok"] for c in checks),
        "version": version,
        "checks": checks,
        "findings": findings,
        "generated_at": now_iso(),
    }
    lines = [f"selfcheck · {version} · ok={result['ok']}", ""]
    for check in checks:
        lines.append(f"[{'PASS' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}")
    _emit(args, result, "\n".join(lines))
    return 0 if result["ok"] else 1


def _run_tests(paths: Paths, verbose: bool = False) -> int:
    tests = paths.skill_dir / "tests"
    if not tests.is_dir():
        _err(f"tests directory missing: {tests}")
        return 1
    args = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
    if verbose:
        args.append("-v")
    proc = subprocess.run(args, cwd=str(paths.skill_dir), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


def _test(args: argparse.Namespace) -> int:
    paths = _paths(args)
    return _run_tests(paths, verbose=bool(args.verbose))


def _prune(args: argparse.Namespace) -> int:
    paths = _paths(args)
    _cfg(paths)
    if args.days < 1:
        _err("--days must be >= 1")
        return 2
    result: dict[str, Any] = {"snapshots": None, "reports": None}
    with file_lock(paths.lock_file, timeout=30):
        if args.kind in {"snapshots", "all"}:
            result["snapshots"] = prune_snapshots(paths, days=args.days, apply=bool(args.apply))
        if args.kind in {"reports", "all"}:
            import datetime as _dt

            cutoff = _dt.datetime.now().timestamp() - args.days * 86400
            candidates = []
            for folder in (paths.audit_dir, paths.weekly_dir):
                if folder.is_dir():
                    for path in folder.glob("*"):
                        if path.is_file() and path.stat().st_mtime < cutoff:
                            candidates.append(str(path))
            if args.apply:
                for item in candidates:
                    Path(item).unlink(missing_ok=True)
            result["reports"] = {"days": args.days, "candidates": candidates, "applied": bool(args.apply)}
    _emit(args, result, json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _mcp(args: argparse.Namespace) -> int:
    from .mcp_server import run_mcp

    return run_mcp(_paths(args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evolution",
        description="全局 Agent 自优化与能力管理（默认离线、可回滚、最小权限）",
    )
    parser.add_argument("--home", help="runtime home (default ~/.agents/evolution or $EVOLUTION_HOME)")
    parser.add_argument("--os-home", help="simulated user home (for tests/migration)")
    parser.add_argument("--json", action="store_true", help="JSON output only")
    parser.add_argument("--quiet", action="store_true", help="suppress human output")
    parser.add_argument("--version", action="version", version=f"evolution {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create runtime dirs and default config")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_init)

    p = sub.add_parser("status", help="show version, paths, counts and schedule")
    p.set_defaults(func=_status)

    p = sub.add_parser("doctor", help="check install, rules, safety lock and schedule")
    p.set_defaults(func=_doctor)

    p = sub.add_parser("capabilities", help="inventory local skills/MCP/plugins")
    p.add_argument("--agent", default="")
    p.set_defaults(func=_capabilities)

    p = sub.add_parser("recommend", help="recommend existing capabilities for a task")
    p.add_argument("--task", required=True)
    p.add_argument("--limit", type=int, default=5)
    p.set_defaults(func=_recommend)

    p = sub.add_parser("audit", help="environment/security/hygiene audit")
    p.add_argument("--scope", action="append", help="repeatable or comma-separated: skills,mcp,plugins,permissions,runtime,dependencies,hygiene")
    p.add_argument("--online", action="store_true")
    p.add_argument("--format", choices=["md", "json"], default="md")
    p.add_argument("--out", default="")
    p.add_argument("--fail-on", choices=["none", "low", "medium", "high", "critical"], default=None)
    p.set_defaults(func=_audit)

    p = sub.add_parser("review", help="weekly review (audit + metrics + upgrades)")
    p.add_argument("--week", default="")
    p.add_argument("--online", action="store_true")
    p.add_argument("--no-audit", action="store_true")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--print-report", action="store_true")
    p.add_argument("--apply", action="store_true", help="run autonomous maintenance before auditing")
    p.add_argument("--fail-on", choices=["none", "low", "medium", "high", "critical"], default="none")
    p.set_defaults(func=_review)

    p = sub.add_parser("evolve", help="self-evolution: metrics -> learning -> versioned release")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--bump", choices=["none", "patch", "minor", "major"], default="none")
    p.add_argument("--release", action="store_true")
    p.add_argument("--force-bump", action="store_true")
    p.add_argument("--evaluate", action="store_true")
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(func=_evolve)

    p = sub.add_parser("maintain", help="autonomous dedupe/quarantine/upgrade planning")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--online", action="store_true")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--scope", action="append", help="skills,plugins (repeatable or comma-separated)")
    p.add_argument("--max-actions", type=int, default=0)
    p.set_defaults(func=_maintain)

    p = sub.add_parser("plugins", help="check DSH plugin upgrade candidates")
    p.add_argument("--online", action="store_true")
    p.add_argument("--apply", action="store_true", help="execute same-major upgrades via the host CLI")
    p.add_argument("--include-major", action="store_true", help="allow cross-major upgrades (off by default)")
    p.add_argument("--max-actions", type=int, default=5)
    p.set_defaults(func=_plugins)

    p = sub.add_parser("envcheck", help="environment currency check: stale runtime/tooling that caps the model")
    p.add_argument("--online", action="store_true")
    p.add_argument("--days", type=int, default=30, help="hygiene threshold in days")
    p.add_argument("--print-report", action="store_true")
    p.add_argument("--fail-on", choices=["none", "low", "medium", "high", "critical"], default="none")
    p.set_defaults(func=_envcheck)

    p = sub.add_parser("cleanup", help="declutter caches, stale reports/logs and (optionally) old backups")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--grace-days", type=int, default=7)
    p.add_argument("--include-backups", action="store_true")
    p.add_argument("--no-logs", action="store_true")
    p.set_defaults(func=_cleanup)

    p = sub.add_parser("skills", help="skill provenance, usage and reversible quarantine")
    p.add_argument("--days", type=int, default=90, help="unused threshold in days")
    p.add_argument("--quarantine-unused", action="store_true", help="list/apply quarantine for unused managed skills")
    p.add_argument("--restore", default="", help="restore a quarantined skill by name")
    p.add_argument("--update", default="", help="re-fetch one managed skill from its recorded source")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=_skills)

    p = sub.add_parser("health", help="skill integrity and staleness check")
    p.add_argument("--days", type=int, default=0, help="staleness threshold in days (0 = config/default)")
    p.add_argument("--print-report", action="store_true")
    p.add_argument("--fail-on", choices=["none", "low", "medium", "high"], default="none")
    p.set_defaults(func=_health)

    p = sub.add_parser("new", help="scaffold a new skill from an identified capability gap")
    p.add_argument("--name", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--dir", default="", help="destination root (default ~/.dsh/skills)")
    p.add_argument("--version", default="0.1.0")
    p.add_argument("--license", default="MIT")
    p.add_argument("--author", default="")
    p.add_argument("--when-to-use", dest="when_to_use", default="")
    p.add_argument("--with-script", action="store_true")
    p.add_argument("--with-tests", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=_new)

    p = sub.add_parser("verify", help="verify a skill: structure, scripts, references, optional smoke command")
    p.add_argument("--path", required=True)
    p.add_argument("--command", default="", help="smoke command to run inside the skill dir")
    p.add_argument("--timeout", type=float, default=120.0)
    p.set_defaults(func=_verify)

    p = sub.add_parser("fetch", help="download/install a skill from GitHub or zip")
    p.add_argument("--source", required=True, help="github:owner/repo, https://github.com/owner/repo, or local dir/zip")
    p.add_argument("--name", default="")
    p.add_argument("--ref", default="")
    p.add_argument("--path", default="", help="subdirectory containing SKILL.md")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--no-link", action="store_true")
    p.add_argument("--allow-risk", action="store_true")
    p.add_argument("--allow-no-license", action="store_true")
    p.set_defaults(func=_fetch)

    p = sub.add_parser("record", help="append a task result to metrics")
    p.add_argument("--task", required=True)
    p.add_argument("--outcome", required=True, choices=sorted(OUTCOMES))
    p.add_argument("--duration", type=float)
    p.add_argument("--tokens", type=int)
    p.add_argument("--cost", type=float)
    p.add_argument("--tools", type=int)
    p.add_argument("--interventions", type=int)
    p.add_argument("--retries", type=int)
    p.add_argument("--quality", type=float)
    p.add_argument("--error-class", choices=sorted(ERROR_CLASSES), default="")
    p.add_argument("--notes", default="")
    p.add_argument("--tags", action="append")
    p.add_argument("--agent", default="")
    p.add_argument("--model", default="")
    p.add_argument("--skill", default="")
    p.set_defaults(func=_record)

    p = sub.add_parser("stats", help="aggregate metrics")
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(func=_stats)

    p = sub.add_parser("snapshot", help="create a hash snapshot of managed files")
    p.add_argument("--label", default="manual")
    p.set_defaults(func=_snapshot)

    p = sub.add_parser("history", help="list snapshots")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=_history)

    p = sub.add_parser("diff", help="diff a snapshot against current/latest")
    p.add_argument("--from", dest="from_id", required=True)
    p.add_argument("--to", default="current")
    p.set_defaults(func=_diff)

    p = sub.add_parser("rollback", help="restore managed files from a snapshot")
    p.add_argument("--snapshot", required=True)
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=_rollback)

    p = sub.add_parser("export", help="export a portable bundle")
    p.add_argument("--out", default="")
    p.add_argument("--include-knowledge", action="store_true")
    p.set_defaults(func=_export)

    p = sub.add_parser("release", help="build a versioned release bundle")
    p.add_argument("--version", default="")
    p.add_argument("--out", default="")
    p.set_defaults(func=_release)

    p = sub.add_parser("import", help="verify/import a portable bundle")
    p.add_argument("--from", dest="archive", required=True)
    p.add_argument("--dest", default="")
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_import)

    p = sub.add_parser("install", help="link/copy skill into agent roots and write rules")
    p.add_argument("--targets", action="append", choices=list(TARGET_ORDER))
    p.add_argument("--mode", choices=["link", "copy"], default="link")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-rules", action="store_true")
    p.add_argument("--rules-only", action="store_true")
    p.add_argument("--accept-safety-change", action="store_true")
    p.set_defaults(func=_install)

    p = sub.add_parser("uninstall", help="remove managed links/rules")
    p.add_argument("--targets", action="append", choices=list(TARGET_ORDER))
    p.add_argument("--apply", action="store_true")
    p.add_argument("--purge", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=_uninstall)

    p = sub.add_parser("schedule", help="weekly schedule (agent-native first, OS fallback)")
    ssub = p.add_subparsers(dest="schedule_action", required=True)
    sp = ssub.add_parser("native", help="write agent-native prompt record")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=_schedule)
    sp = ssub.add_parser("install", help="install native or OS schedule")
    sp.add_argument("--driver", choices=["native", "os"], default="native")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--time", default=None)
    sp.add_argument("--day", default=None)
    sp.set_defaults(func=_schedule)
    sp = ssub.add_parser("confirm", help="record an existing agent-native reminder")
    sp.add_argument("--id", dest="schedule_id", required=True)
    sp.add_argument("--scheduled-at", default="")
    sp.set_defaults(func=_schedule)
    sp = ssub.add_parser("status", help="show schedule status")
    sp.set_defaults(func=_schedule)
    sp = ssub.add_parser("remove", help="remove schedule")
    sp.add_argument("--driver", choices=["native", "os"], default="native")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=_schedule)

    p = sub.add_parser("knowledge", help="append/list/search long-term lessons")
    ksub = p.add_subparsers(dest="knowledge_action", required=True)
    kp = ksub.add_parser("add")
    kp.add_argument("--title", required=True)
    kp.add_argument("--body", default="")
    kp.add_argument("--tags", action="append")
    kp.add_argument("--apply", action="store_true")
    kp.set_defaults(func=_knowledge)
    kp = ksub.add_parser("list")
    kp.set_defaults(func=_knowledge)
    kp = ksub.add_parser("search")
    kp.add_argument("--query", required=True)
    kp.set_defaults(func=_knowledge)

    p = sub.add_parser("validate", help="validate a skill or bundle")
    p.add_argument("--path", default="")
    p.add_argument("--bundle", default="")
    p.set_defaults(func=_validate)

    p = sub.add_parser("selfcheck", help="validate the evolution bundle itself")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=_selfcheck)

    p = sub.add_parser("test", help="run the evolution test suite")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=_test)

    p = sub.add_parser("prune", help="prune own snapshots/reports (dry-run default)")
    p.add_argument("--kind", choices=["snapshots", "reports", "all"], default="snapshots")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=_prune)

    p = sub.add_parser("mcp", help="run minimal MCP stdio server")
    p.set_defaults(func=_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
    except BrokenPipeError:
        return 0
    except (ValueError, FileNotFoundError, PermissionError, OSError) as exc:
        _err(f"{type(exc).__name__}: {exc}")
        return 2
    except Exception as exc:  # pragma: no cover
        _err(f"unexpected {type(exc).__name__}: {exc}")
        if os.environ.get("EVOLUTION_DEBUG"):
            import traceback

            traceback.print_exc()
        return 2
