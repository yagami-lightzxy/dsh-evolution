"""Turn task history into small, reviewable improvement proposals."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import re
import json
from pathlib import Path
from typing import Any

from .scaffold import create_skill
from .metrics import load as load_metrics
from .metrics import stats as metrics_stats
from .paths import Paths
from .util import now_iso, write_json


def _label(record: dict[str, Any]) -> str:
    tags = [str(tag).strip() for tag in (record.get("tags") or []) if str(tag).strip()]
    return ", ".join(tags[:4]) or str(record.get("task") or "").strip()[:100]


def reflect(paths: Paths, *, days: int = 30, min_repeats: int = 3) -> dict[str, Any]:
    records = load_metrics(paths, days=days)
    failures: Counter[str] = Counter()
    workflows: Counter[str] = Counter()
    skills: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[str]] = defaultdict(list)
    for record in records:
        label = _label(record)
        if label:
            workflows[label] += 1
            examples[label].append(str(record.get("task") or "")[:160])
        error = str(record.get("error_class") or "").strip()
        if error:
            failures[error] += 1
        skill = str(record.get("skill") or "").strip()
        if skill:
            skills[skill][str(record.get("outcome") or "unknown")] += 1

    proposals: list[dict[str, Any]] = []
    for error, count in failures.most_common():
        if count < 2:
            continue
        proposals.append(
            {
                "kind": "repair-skill" if any(skills.values()) else "workflow-rule",
                "id": f"failure:{error}",
                "title": f"修复反复出现的 {error} 失败",
                "evidence": {"count": count, "samples": [str(r.get("task") or "")[:120] for r in records if r.get("error_class") == error][:5]},
                "next": "为该失败根因补充一个可验证步骤，并在留出任务上回归。",
            }
        )
    for label, count in workflows.most_common():
        if count < min_repeats:
            continue
        related = [r for r in records if _label(r) == label]
        success = sum(1 for r in related if r.get("outcome") == "success")
        skill_names = sorted({str(r.get("skill")) for r in related if str(r.get("skill") or "").strip()})
        if success < 2 and not skill_names:
            continue
        proposals.append(
            {
                "kind": "extract-skill" if not skill_names else "refine-skill",
                "id": f"workflow:{label}",
                "title": f"把重复工作流固化为技能：{label}",
                "evidence": {"count": count, "success": success, "skills": skill_names, "examples": examples[label][:5]},
                "next": "用 `new` 或编辑现有技能形成候选版本，再用 `evaluate` 的留出集门槛晋级。",
            }
        )
    result = {
        "schema": 1,
        "kind": "experience-reflection",
        "generated_at": now_iso(),
        "days": days,
        "metrics": metrics_stats(records),
        "proposals": proposals,
        "limits": ["反思只使用已记录的任务证据，不把缺失记录推断为失败或成功。"],
    }
    write_json(paths.reflection_file, result)
    return result


def reflection_markdown(result: dict[str, Any]) -> str:
    lines = [f"# 任务复盘 · {result.get('generated_at', '')}", "", f"- 数据窗口：{result.get('days')} 天 · 提案：{len(result.get('proposals', []))}", ""]
    for item in result.get("proposals", []):
        evidence = item.get("evidence") or {}
        lines.append(f"## {item.get('kind')} · {item.get('title')}")
        lines.append(f"- 证据：{evidence}")
        lines.append(f"- 下一步：{item.get('next')}")
        lines.append("")
    if not result.get("proposals"):
        lines.append("- 当前样本没有形成重复模式，继续记录任务结果。")
    return "\n".join(lines) + "\n"


def materialize_skills(paths: Paths, proposals: list[dict[str, Any]], *, apply: bool = False) -> list[dict[str, Any]]:
    """Create one small skill for each repeated successful workflow proposal."""
    root = paths.os_home / ".agents" / "skills"
    result: list[dict[str, Any]] = []
    for proposal in proposals:
        if proposal.get("kind") != "extract-skill":
            continue
        evidence = proposal.get("evidence") or {}
        examples = evidence.get("examples") or []
        label = str(proposal.get("title") or "workflow").split("：", 1)[-1].strip()
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:42]
        if not slug:
            slug = "workflow-" + hashlib.sha1(label.encode("utf-8")).hexdigest()[:8]
        name = f"learned-{slug}"
        description = f"Reusable workflow for {label}, extracted from repeated successful task evidence."
        created = create_skill(
            root,
            name,
            description,
            author="evolution",
            when_to_use=f"Use when the task matches {label}. Verify the result against the current task before relying on it.",
            apply=apply,
        )
        if created.get("applied"):
            folder = Path(str(created["target"]))
            (folder / ".evolution-source.json").write_text(
                json.dumps({"schema": 1, "name": name, "source": {"kind": "experience", "proposal": proposal.get("id")}, "created_at": now_iso()}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            skill_md = folder / "SKILL.md"
            body = skill_md.read_text(encoding="utf-8")
            body += "\n## 复盘证据\n\n" + "\n".join(f"- {item}" for item in examples[:5]) + "\n"
            skill_md.write_text(body, encoding="utf-8", newline="\n")
        result.append(created)
    return result
