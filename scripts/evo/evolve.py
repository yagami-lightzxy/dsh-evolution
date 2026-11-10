"""Self-evolution loop: metrics -> hypotheses -> learning -> versioned release.

This is deliberately evidence-gated: low-risk learning notes can be appended
automatically, but a version bump only happens when the caller asks for one or
when a concrete non-metric hypothesis was applied. Every upgrade is snapshot
backed, tested, and reversible.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from . import __version__
from .config import get
from .envcheck import latest_envcheck
from .metrics import load as load_metrics
from .metrics import stats as metrics_stats
from .paths import Paths
from .snapshot import create_snapshot, export_bundle
from .util import append_jsonl, now_iso, now_stamp, read_json, read_jsonl, sha256_file, write_json

ERROR_ACTIONS = {
    "info-gap": "先检索官方文档/既有资料，再执行；记录来源与版本。",
    "tool-failure": "调用前检查工具可用性；失败后切换备用工具并记录错误，不循环重试。",
    "permission": "提前确认权限/审批；无法获得时明确说明影响，不静默降级。",
    "wrong-approach": "先复述验收标准，比较至少两个方案，再选择成本/风险更优者。",
    "insufficient-verification": "交付前按验收清单逐条检查；关键路径必须真实运行/测试，不以表面完成代替。",
    "external-dependency": "增加超时、重试与降级路径；外部服务不可用时给出替代方案。",
    "timeout": "拆分长任务并设置检查点；超时前保存中间结果。",
    "unknown": "记录复现步骤和证据，先定位根因再改方案。",
}


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(value or __version__))
    if not match:
        return (1, 0, 0)
    return tuple(int(x) for x in match.groups())  # type: ignore[return-value]


def bump_version(current: str, bump: str) -> str:
    major, minor, patch = _version_tuple(current)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return current


def _latest_audit(paths: Paths) -> dict[str, Any] | None:
    return read_json(paths.state_dir / "audit-latest.json", default=None)


def diagnose(paths: Paths, cfg: dict[str, Any], *, days: int = 30) -> dict[str, Any]:
    records = load_metrics(paths, days=days)
    stats = metrics_stats(records)
    audit = _latest_audit(paths)
    proposals: list[dict[str, Any]] = []
    if stats.get("n", 0) < 5:
        proposals.append(
            {
                "id": "instrumentation",
                "area": "metrics",
                "hypothesis": "指标样本不足，无法可靠区分策略优劣。",
                "change": "继续记录任务结果；样本达到 5 条前不调整行为策略。",
                "risk": "low",
                "auto_apply": False,
                "evidence": f"n={stats.get('n', 0)}",
            }
        )
    if stats.get("n", 0) >= 5:
        for error_class, count in (stats.get("error_classes") or {}).items():
            if count <= 0:
                continue
            proposals.append(
                {
                    "id": f"error-{error_class}",
                    "area": "task-policy",
                    "hypothesis": f"{error_class} 是当前主要失败根因（{count} 次）。",
                    "change": ERROR_ACTIONS.get(error_class, "记录复现步骤，先定位根因。"),
                    "risk": "low",
                    "auto_apply": True,
                    "evidence": f"error_class={error_class}, count={count}",
                }
            )
        if (stats.get("intervention_rate") or 0) > 0.3:
            proposals.append(
                {
                    "id": "reduce-interventions",
                    "area": "task-policy",
                    "hypothesis": "人工干预率偏高，说明初始信息或验收标准不足。",
                    "change": "任务开始前一次性补齐目标/约束/验收标准；减少中途确认。",
                    "risk": "low",
                    "auto_apply": True,
                    "evidence": f"intervention_rate={stats.get('intervention_rate')}",
                }
            )
        quality = stats.get("quality", {}).get("avg")
        if quality is not None and quality < 4.0:
            proposals.append(
                {
                    "id": "quality-gate",
                    "area": "verification",
                    "hypothesis": f"质量均分 {quality}/5，低于 4.0。",
                    "change": "交付前增加独立复核/关键路径测试；不以“看起来完成”结束。",
                    "risk": "low",
                    "auto_apply": True,
                    "evidence": f"quality_avg={quality}",
                }
            )
    maintenance = read_json(paths.state_dir / "maintenance-latest.json", default=None)
    if isinstance(maintenance, dict):
        applied_count = len((maintenance.get("apply_result") or {}).get("applied", []))
        if applied_count:
            proposals.append(
                {
                    "id": "dedupe-policy",
                    "area": "maintenance",
                    "hypothesis": f"已实际完成 {applied_count} 个重复技能去重，内容一致时 junction 比多副本更可维护。",
                    "change": "同内容技能保留 `~/.agents/skills` 规范源，其他位置改为 junction；替换前备份原目录。",
                    "risk": "low",
                    "auto_apply": True,
                    "evidence": f"maintenance applied={applied_count}",
                }
            )
        plugins = maintenance.get("plugins") or {}
        if plugins.get("checked") or plugins.get("candidates"):
            proposals.append(
                {
                    "id": "plugin-upgrade-policy",
                    "area": "maintenance",
                    "hypothesis": "插件版本检查已接入；跨 major 自动升级风险高于收益。",
                    "change": "周任务只自动升级同 major 且来源可信的插件；跨 major 只报告，升级前 snapshot。",
                    "risk": "low",
                    "auto_apply": True,
                    "evidence": f"plugin checked={plugins.get('checked')}, candidates={len(plugins.get('candidates') or [])}",
                }
            )
        if applied_count:
            proposals.append(
                {
                    "id": "maintenance-safety-policy",
                    "area": "maintenance",
                    "hypothesis": "自主删除/替换必须可恢复，否则一次误判会破坏工作环境。",
                    "change": "每次维护先 snapshot，并为每个被替换技能单独备份；链接失败时恢复原目录；不删除用户手写技能。",
                    "risk": "low",
                    "auto_apply": True,
                    "evidence": f"snapshot={maintenance.get('snapshot')}",
                }
            )
    if audit:
        counts = audit.get("counts") or {}
        ids = {item["id"] for item in proposals}
        if "dedupe-policy" not in ids and counts.get("skills") and not counts.get("duplicates"):
            proposals.append(
                {
                    "id": "dedupe-policy",
                    "area": "maintenance",
                    "hypothesis": f"当前 {counts.get('skills')} 个技能、duplicates=0，说明规范源优先策略有效。",
                    "change": "同内容技能保留 `~/.agents/skills` 规范源，其他位置改为 junction；替换前备份原目录。",
                    "risk": "low",
                    "auto_apply": True,
                    "evidence": f"audit skills={counts.get('skills')}, duplicates={counts.get('duplicates')}",
                }
            )
        if "plugin-upgrade-policy" not in ids and get(cfg, "maintenance.plugin_updates") == "auto":
            proposals.append(
                {
                    "id": "plugin-upgrade-policy",
                    "area": "maintenance",
                    "hypothesis": "插件升级已配置为自动检查；跨 major 自动升级风险高于收益。",
                    "change": "周任务只自动升级同 major 且来源可信的插件；跨 major 只报告，升级前 snapshot。",
                    "risk": "low",
                    "auto_apply": True,
                    "evidence": "maintenance.plugin_updates=auto",
                }
            )
        if "maintenance-safety-policy" not in ids and get(cfg, "maintenance.auto_apply"):
            proposals.append(
                {
                    "id": "maintenance-safety-policy",
                    "area": "maintenance",
                    "hypothesis": "自主删除/替换必须可恢复，否则一次误判会破坏工作环境。",
                    "change": "每次维护先 snapshot，并为每个被替换技能单独备份；链接失败时恢复原目录；不删除用户手写技能。",
                    "risk": "low",
                    "auto_apply": True,
                    "evidence": "maintenance.auto_apply=true",
                }
            )
    if audit:
        for finding in (audit.get("findings") or []):
            if finding.get("severity") in {"critical", "high"} and not finding.get("accepted"):
                proposals.append(
                    {
                        "id": f"audit-{finding.get('id', 'high')}",
                        "area": "security",
                        "hypothesis": f"审计发现：{finding.get('title')}",
                        "change": finding.get("recommendation", "人工审查该发现。"),
                        "risk": "high",
                        "auto_apply": False,
                        "evidence": finding.get("evidence", ""),
                    }
                )
    # Environment currency: an outdated environment silently caps what the
    # agent can do, so it belongs in the learning loop, not only in reports.
    env = latest_envcheck(paths)
    if isinstance(env, dict):
        for finding in env.get("findings") or []:
            if str(finding.get("severity")) not in {"critical", "high"}:
                continue
            proposals.append(
                {
                    "id": f"env-{finding.get('check', 'unknown')}-{finding.get('id', '')}".rstrip("-"),
                    "area": "environment",
                    "hypothesis": f"环境拖累：{finding.get('title')}",
                    "change": str(finding.get("recommendation") or "按发现项处理环境不一致。"),
                    "risk": "high",
                    "auto_apply": False,
                    "evidence": str(finding.get("evidence") or ""),
                }
            )
        if env.get("findings"):
            proposals.append(
                {
                    "id": "env-currency-policy",
                    "area": "environment",
                    "hypothesis": "环境（运行时/插件/模型通道）落后时，模型会在能力被削弱的条件下工作，且表现归因会指向模型。",
                    "change": "每周先运行 envcheck 再评估能力：插件被宿主拒绝加载时，先升级或卸载该插件，再讨论是否新增工具；模型通道必须显式配置 reasoningEffort。",
                    "risk": "low",
                    "auto_apply": True,
                    "evidence": f"envcheck score={env.get('score')}, status={env.get('status')}",
                }
            )

    # Deduplicate by id.
    seen: set[str] = set()
    unique = []
    for item in proposals:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        unique.append(item)
    return {"generated_at": now_iso(), "days": days, "baseline": stats, "audit_generated_at": (audit or {}).get("generated_at"), "proposals": unique}


def _append_learning(paths: Paths, proposals: list[dict[str, Any]], *, experiment_id: str) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    targets = [paths.knowledge_dir / "learned.md", paths.skill_dir / "rules" / "learned.md"]
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.exists() else (
            "# 自动学习规则\n\n由 `evolution evolve` 生成；仅记录有证据的长期改进，不写临时信息。\n"
        )
        additions: list[str] = []
        for proposal in proposals:
            # Stable marker: a rule is learned once, not once per weekly run.
            marker = f"<!-- evolve:{proposal['id']} -->"
            if marker in existing:
                continue
            additions.append(
                f"\n## {now_iso()[:10]} · {proposal['id']}\n"
                f"{marker}\n"
                f"- 观察：{proposal['hypothesis']}\n"
                f"- 调整：{proposal['change']}\n"
                f"- 证据：{proposal['evidence']}\n"
            )
        if additions:
            target.write_text(existing.rstrip("\n") + "\n" + "".join(additions), encoding="utf-8", newline="\n")
            applied.append({"path": str(target), "proposals": [p["id"] for p in proposals]})
    return applied


def _run_tests(paths: Paths) -> dict[str, Any]:
    tests = paths.skill_dir / "tests"
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=str(paths.skill_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    return {"ok": proc.returncode == 0, "exit": proc.returncode, "tail": (proc.stdout or proc.stderr or "")[-2000:]}


def _update_changelog(paths: Paths, new_version: str, changes: list[str]) -> None:
    path = paths.skill_dir / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8") if path.exists() else "# Changelog\n"
    if f"## [{new_version}]" in text:
        return
    heading = f"## [{new_version}] — {now_iso()[:10]}"
    entry = heading + "\n\n### Changed\n" + "".join(f"- {line}\n" for line in changes) + "\n"
    match = re.search(r"^## \[", text, re.MULTILINE)
    if match:
        text = text[: match.start()] + entry + "\n" + text[match.start() :]
    else:
        text = text.rstrip("\n") + "\n\n" + entry
    path.write_text(text, encoding="utf-8", newline="\n")


def self_upgrade(paths: Paths, *, bump: str = "patch", changes: list[str] | None = None) -> dict[str, Any]:
    paths.ensure_runtime()
    changes = changes or ["automatic self-evolution learning"]
    current = (paths.version_file.read_text(encoding="utf-8").strip() if paths.version_file.exists() else __version__)
    new_version = bump_version(current, bump)
    if new_version == current:
        return {"ok": False, "reason": "no version change", "version": current}
    snapshot = create_snapshot(paths, label=f"pre-self-upgrade-{new_version}")
    version_file = paths.version_file
    skill_file = paths.skill_md
    init_file = paths.skill_dir / "scripts" / "evo" / "__init__.py"
    changelog = paths.skill_dir / "CHANGELOG.md"
    originals = {
        version_file: version_file.read_bytes() if version_file.exists() else b"",
        skill_file: skill_file.read_bytes() if skill_file.exists() else b"",
        init_file: init_file.read_bytes() if init_file.exists() else b"",
        changelog: changelog.read_bytes() if changelog.exists() else b"",
    }
    try:
        version_file.write_text(new_version + "\n", encoding="utf-8", newline="\n")
        text = skill_file.read_text(encoding="utf-8")
        text = re.sub(r'(?m)^(\s*version:\s*")[^"]+(")', rf"\g<1>{new_version}\g<2>", text, count=1)
        skill_file.write_text(text, encoding="utf-8", newline="\n")
        init_text = init_file.read_text(encoding="utf-8")
        init_text = re.sub(r'(?m)^(__version__\s*=\s*")[^"]+(")', rf"\g<1>{new_version}\g<2>", init_text, count=1)
        init_file.write_text(init_text, encoding="utf-8", newline="\n")
        _update_changelog(paths, new_version, changes)
        tests = _run_tests(paths)
        if not tests["ok"]:
            raise RuntimeError("tests failed: " + tests["tail"])
        bundle = export_bundle(paths, out=paths.releases_dir / f"evolution-{new_version}.zip")
        result = {
            "ok": True,
            "version_before": current,
            "version_after": new_version,
            "snapshot": snapshot["id"],
            "tests": tests,
            "bundle": bundle,
            "changes": changes,
        }
        history_path = paths.state_dir / "evolution" / "versions.json"
        history = read_json(history_path, default=[]) or []
        history.append(
            {
                "version": new_version,
                "previous": current,
                "at": now_iso(),
                "changes": changes,
                "snapshot": snapshot["id"],
                "bundle": bundle["path"],
                "sha256": bundle["sha256"],
            }
        )
        write_json(history_path, history)
        return result
    except Exception as exc:
        for path, data in originals.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return {"ok": False, "version": current, "error": f"{type(exc).__name__}: {exc}", "snapshot": snapshot["id"]}


def evaluate_experiments(paths: Paths, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    exp_path = paths.state_dir / "evolution" / "experiments.jsonl"
    records = read_jsonl(exp_path)
    all_metrics = load_metrics(paths, days=None)
    results: list[dict[str, Any]] = []
    for exp in records:
        if exp.get("status") != "observing":
            continue
        created = exp.get("created_at", "")
        after = [m for m in all_metrics if str(m.get("ts", "")) > created]
        need = int(exp.get("evaluate_after", 5))
        if len(after) < need:
            results.append({"id": exp.get("id"), "status": "waiting", "samples": len(after), "need": need})
            continue
        stats = metrics_stats(after)
        baseline = exp.get("baseline", {})
        verdict = "keep"
        if baseline.get("success_rate") is not None and stats.get("success_rate", 0) + 0.05 < baseline["success_rate"]:
            verdict = "regress"
        results.append({"id": exp.get("id"), "status": verdict, "samples": len(after), "after": {"success_rate": stats.get("success_rate"), "quality": stats.get("quality")}, "baseline": baseline})
    return results


def run_evolution(
    paths: Paths,
    cfg: dict[str, Any],
    *,
    apply: bool = False,
    bump: str = "none",
    release: bool = True,
    days: int = 30,
    evaluate: bool = False,
    force_bump: bool = False,
) -> dict[str, Any]:
    diagnosis = diagnose(paths, cfg, days=days)
    auto = [p for p in diagnosis["proposals"] if p.get("auto_apply") and p.get("area") != "security"]
    applied: list[dict[str, Any]] = []
    experiment_id = "exp-" + now_stamp()
    if apply and auto:
        applied = _append_learning(paths, auto, experiment_id=experiment_id)
        if applied:
            append_jsonl(
                paths.state_dir / "evolution" / "experiments.jsonl",
                {
                    "id": experiment_id,
                    "created_at": now_iso(),
                    "baseline": diagnosis["baseline"],
                    "proposals": [p["id"] for p in auto],
                    "changes": [p["change"] for p in auto],
                    "status": "observing",
                    "evaluate_after": 5,
                    "version_before": paths.version_file.read_text(encoding="utf-8").strip(),
                },
            )
    upgrade = None
    if apply and bump in {"patch", "minor", "major"} and (applied or force_bump):
        upgrade = self_upgrade(paths, bump=bump, changes=[p["change"] for p in auto] or ["manual feature self-upgrade"])
    if release and upgrade and upgrade.get("ok"):
        # self_upgrade already produced a release zip in state/releases.
        upgrade["release"] = upgrade.get("bundle", {}).get("path")
    return {
        "schema": 1,
        "kind": "self-evolution",
        "generated_at": now_iso(),
        "diagnosis": diagnosis,
        "applied": applied,
        "experiment_id": experiment_id if applied else "",
        "upgrade": upgrade,
        "evaluation": evaluate_experiments(paths, cfg) if evaluate else [],
    }
