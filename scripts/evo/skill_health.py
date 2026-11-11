"""Skill integrity and currency checks.

The user's real fear is not a missing tool -- it is a skill that still looks
installed while its instructions point at files and scripts that no longer
exist. Such a skill does not fail; it silently teaches the model a workflow the
machine can no longer run. Nothing else in the audit looks inside the skill.

What is checked here:
  * references: markdown links and code-span paths that must exist on disk;
  * scripts: every .py compiles, every .json parses, every shell/ps1 file is non-empty;
  * metadata: name/disclaimer sanity, version presence, description length;
  * age: how long since the skill last changed, against a configured threshold;
  * context cost: SKILL.md size.

Checks are read-only and bounded: no network, no execution of skill code.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .config import get
from .discover import discover_skills
from .frontmatter import Skill
from .paths import Paths
from .util import md_cell, now_iso, read_text

DEFAULT_STALE_DAYS = 180
MIN_DESCRIPTION = 20
MAX_SKILL_BYTES = 24 * 1024
MAX_FILES = 400

#: Directory prefixes whose mentions in a skill body are meant to be real paths.
PATH_PREFIXES = ("scripts/", "references/", "rules/", "templates/", "tests/", "assets/", "docs/")

#: Extensions we can validate without running anything.
PY_SUFFIX = {".py"}
JSON_SUFFIX = {".json"}
TEXT_SUFFIX = {".sh", ".ps1", ".cmd", ".bat"}

LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


def _age_days(mtime: float) -> float | None:
    if not mtime:
        return None
    return max(0.0, (__import__("time").time() - mtime) / 86400.0)


def _strip_fences(text: str) -> str:
    """Drop fenced code blocks: examples there are illustrative, not paths."""
    return re.sub(r"~~~.*?~~~", "", re.sub(r"\x60\x60\x60.*?\x60\x60\x60", "", text, flags=re.S), flags=re.S)


def local_references(body: str) -> list[str]:
    """Collect skill-relative paths that the body claims exist."""
    text = _strip_fences(body)
    refs: list[str] = []
    for match in LINK_RE.finditer(text):
        target = match.group(1).strip()
        if not target or target.startswith(("http://", "https://", "#", "mailto:", "//")):
            continue
        target = target.split("#", 1)[0].strip()
        if target and not target.startswith("/"):
            refs.append(target)
    # Code spans: split on backticks and keep the odd segments.
    parts = text.split(chr(96))
    for index in range(1, len(parts), 2):
        span = parts[index].strip()
        if not span or " " in span or "(" in span:
            continue
        if span.startswith(PATH_PREFIXES) or span in {"SKILL.md", "VERSION", "CHANGELOG.md", "README.md"}:
            refs.append(span)
    seen: set[str] = set()
    out: list[str] = []
    for ref in refs:
        cleaned = ref.replace("\\", "/").lstrip("./")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def _skill_dir(skill: Skill) -> Path:
    return skill.path.parent


def _check_files(skill: Skill) -> list[dict[str, Any]]:
    """Compile/parse everything a skill ships, so a broken helper is visible."""
    issues: list[dict[str, Any]] = []
    root = _skill_dir(skill)
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        count += 1
        if count > MAX_FILES:
            issues.append({"kind": "too-many-files", "severity": "low", "detail": f"文件数超过 {MAX_FILES}，停止扫描"})
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 1024 * 1024:
            continue
        suffix = path.suffix.lower()
        rel = path.relative_to(root).as_posix()
        if suffix in PY_SUFFIX:
            try:
                compile(read_text(path), str(path), "exec")
            except (SyntaxError, ValueError, OSError) as exc:
                issues.append({"kind": "broken-script", "severity": "high", "detail": f"{rel}: {type(exc).__name__}: {exc}".strip(), "path": rel})
        elif suffix in JSON_SUFFIX:
            try:
                json.loads(read_text(path))
            except (ValueError, OSError) as exc:
                issues.append({"kind": "broken-config", "severity": "high", "detail": f"{rel}: {exc}", "path": rel})
        elif suffix in TEXT_SUFFIX and size == 0:
            issues.append({"kind": "empty-script", "severity": "medium", "detail": f"{rel}: 空文件", "path": rel})
    return issues


def inspect_skill(skill: Skill, *, stale_days: int = DEFAULT_STALE_DAYS) -> dict[str, Any]:
    """Per-skill report: status, age, version and every issue found."""
    issues: list[dict[str, Any]] = []
    root = _skill_dir(skill)

    if not skill.valid:
        issues.append({"kind": "invalid", "severity": "high", "detail": "; ".join(skill.errors) or "frontmatter 无效"})
    if len(skill.description.strip()) < MIN_DESCRIPTION:
        issues.append({"kind": "weak-description", "severity": "medium", "detail": f"description 仅 {len(skill.description.strip())} 字符，模型可能无法判断何时该用"})
    if not skill.version and not (root / "VERSION").is_file():
        issues.append({"kind": "no-version", "severity": "low", "detail": "既无 frontmatter version 也无 VERSION 文件，无法判断新旧"})
    if skill.size > MAX_SKILL_BYTES:
        issues.append({"kind": "oversized", "severity": "low", "detail": f"SKILL.md {skill.size} 字节，超出 {MAX_SKILL_BYTES}，每轮都在花上下文"})

    age = _age_days(skill.mtime)
    if age is not None:
        if age > stale_days * 2:
            issues.append({"kind": "stale", "severity": "high", "detail": f"{age:.0f} 天未更新（阈值 {stale_days}）"})
        elif age > stale_days:
            issues.append({"kind": "stale", "severity": "medium", "detail": f"{age:.0f} 天未更新（阈值 {stale_days}）"})

    missing: list[str] = []
    for ref in local_references(skill.body):
        if not (root / ref).exists() and not (root / ref.split("/")[0]).exists():
            missing.append(ref)
    if missing:
        issues.append(
            {
                "kind": "broken-reference",
                "severity": "high",
                "detail": "正文引用了不存在的路径：" + ", ".join(missing[:8]),
            }
        )

    issues.extend(_check_files(skill))

    has_scripts = (root / "scripts").is_dir()
    has_tests = (root / "tests").is_dir() or (root / "test").is_dir()
    if has_scripts and not has_tests:
        issues.append({"kind": "no-tests", "severity": "info", "detail": "带脚本但没有 tests/，改动后无法自证"})

    order = {"high": 3, "medium": 2, "low": 1, "info": 0}
    worst = max((order.get(str(i["severity"]), 0) for i in issues), default=-1)
    status = "ok" if worst <= 0 else ("bad" if worst >= 3 else "warn")
    return {
        "name": skill.name,
        "path": str(skill.path),
        "agent": skill.agent,
        "rank": skill.rank,
        "version": skill.version,
        "size": skill.size,
        "age_days": round(age, 1) if age is not None else None,
        "status": status,
        "issues": issues,
    }


def run_skill_health(
    paths: Paths,
    cfg: dict[str, Any],
    *,
    stale_days: int | None = None,
    max_skills: int = 200,
) -> dict[str, Any]:
    """Inspect every discovered skill and summarise what is rotting."""
    paths.ensure_runtime()
    threshold = int(stale_days if stale_days is not None else get(cfg, "envcheck.skill_stale_days", DEFAULT_STALE_DAYS) or DEFAULT_STALE_DAYS)
    data = discover_skills(cfg, paths)
    reports: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for skill in data["skills"][:max_skills]:
        report = inspect_skill(skill, stale_days=threshold)
        reports.append(report)
        for issue in report["issues"]:
            if issue["severity"] == "info":
                continue
            findings.append(
                {
                    "skill": report["name"],
                    "path": report["path"],
                    "kind": issue["kind"],
                    "severity": issue["severity"],
                    "detail": issue["detail"],
                }
            )
    counts = {"total": len(reports), "ok": 0, "warn": 0, "bad": 0}
    for report in reports:
        counts[report["status"]] = counts.get(report["status"], 0) + 1
    sev: dict[str, int] = {}
    for finding in findings:
        sev[finding["severity"]] = sev.get(finding["severity"], 0) + 1
    return {
        "schema": 1,
        "kind": "skill-health",
        "generated_at": now_iso(),
        "stale_days": threshold,
        "counts": counts,
        "severity_counts": sev,
        "skills": reports,
        "findings": findings,
        "roots": data["counts"],
    }


def skill_health_markdown(result: dict[str, Any]) -> str:
    counts = result["counts"]
    lines = [f"# 技能健康 · {result['generated_at']}", ""]
    lines.append(f"- 共 {counts['total']} 个技能：ok {counts['ok']} / warn {counts['warn']} / bad {counts['bad']}，过期阈值 {result['stale_days']} 天")
    sev = result.get("severity_counts") or {}
    if sev:
        lines.append("- 问题分布：" + " / ".join(f"{k} {v}" for k, v in sorted(sev.items())))
    lines.append("")
    if result["findings"]:
        lines.append("## 问题")
        for finding in result["findings"]:
            lines.append(f"- [{finding['severity']}] {finding['skill']} · {finding['kind']}：{md_cell(finding['detail'], 200)}")
        lines.append("")
    lines.append("## 明细")
    lines.append("| 技能 | 状态 | 版本 | 年龄(天) | 大小 | 问题 |")
    lines.append("|---|---|---|---|---|---|")
    for report in result["skills"]:
        issues = ", ".join(str(i["kind"]) for i in report["issues"]) or "-"
        lines.append(
            f"| {md_cell(report['name'])} | {report['status']} | {md_cell(report['version'] or '-')} | "
            f"{report['age_days'] if report['age_days'] is not None else '-'} | {report['size']} | {md_cell(issues, 80)} |"
        )
    lines.append("")
    lines.append("## 边界")
    lines.append("- 只做静态检查：能发现引用失效、脚本编译失败、元数据缺失与长期未动，无法判断内容是否在语义上过时。")
    lines.append("- 不执行技能自带的脚本；需要运行验证时用 verify --command 显式指定。")
    return "\n".join(lines)
