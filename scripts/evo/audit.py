"""Environment, capability, security and hygiene audit."""
from __future__ import annotations

import datetime as _dt
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import __version__
from .config import get
from .discover import discover_all
from .frontmatter import Skill
from .paths import Paths
from .security import (
    risk_score,
    scan_mcp,
    scan_permissions,
    scan_skill,
    severity_rank,
    summarize,
)
from .util import (
    md_cell,
    now_iso,
    now_stamp,
    platform_label,
    read_text,
    sha256_text,
)

ALL_SCOPES = ["skills", "mcp", "plugins", "permissions", "runtime", "dependencies", "hygiene"]
MAX_SKILL_ROWS = 60


def _add(
    findings: list[dict[str, Any]],
    *,
    area: str,
    severity: str,
    title: str,
    evidence: str,
    recommendation: str,
    path: str = "",
    line: int | None = None,
    kind: str = "",
) -> None:
    findings.append(
        {
            "area": area,
            "severity": severity,
            "title": title,
            "evidence": evidence[:500],
            "recommendation": recommendation,
            "path": path,
            "line": line,
            "kind": kind,
        }
    )


def _jaccard(a: str, b: str) -> float:
    def tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9\u4e00-\u9fff]{2,}", text.lower()))

    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _requirements_findings(skills: list[Skill], findings: list[dict[str, Any]]) -> None:
    for skill in skills:
        base = skill.path.parent if skill.path.name == "SKILL.md" else skill.path.parent
        req = base / "requirements.txt"
        if not req.is_file() or req.stat().st_size > 512 * 1024:
            continue
        text = read_text(req, max_bytes=512 * 1024)
        unpinned = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            if any(op in line for op in ("==", ">=", "<=", "~=", "!=", "@")):
                continue
            unpinned.append(line.split()[0])
        if unpinned:
            _add(
                findings,
                area="dependencies",
                severity="low",
                title=f"{skill.name}: requirements 未固定版本",
                evidence=", ".join(unpinned[:12]) + ("…" if len(unpinned) > 12 else ""),
                recommendation="长期环境锁定版本并校验哈希；一次性环境可记录不固定原因。",
                path=str(req),
            )


def _hygiene(paths: Paths, findings: list[dict[str, Any]], *, context: str = "audit") -> dict[str, Any]:
    info: dict[str, Any] = {}
    snapshots = sorted(paths.snapshots_dir.glob("*")) if paths.snapshots_dir.is_dir() else []
    reports = list(paths.weekly_dir.glob("*.md")) if paths.weekly_dir.is_dir() else []
    audits = list(paths.audit_dir.glob("*.md")) if paths.audit_dir.is_dir() else []
    info["snapshots"] = len([p for p in snapshots if p.is_dir()])
    info["weekly_reports"] = len(reports)
    info["audit_reports"] = len(audits)
    info["knowledge_files"] = len(list(paths.knowledge_dir.glob("*.md"))) if paths.knowledge_dir.is_dir() else 0
    if info["snapshots"] > 30:
        _add(
            findings,
            area="hygiene",
            severity="low",
            title="快照数量偏多",
            evidence=f"{info['snapshots']} 个快照",
            recommendation="保留最近 90 天与关键节点；旧快照归档而非直接删除。",
            path=str(paths.snapshots_dir),
        )
    if info["weekly_reports"] == 0 and context != "review":
        _add(
            findings,
            area="hygiene",
            severity="info",
            title="尚无每周审阅报告",
            evidence="reports/weekly 为空",
            recommendation="运行 `evolution review`，并考虑安装周调度。",
            path=str(paths.weekly_dir),
        )
    return info


def _apply_acceptances(findings: list[dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Mark explicitly accepted risks without hiding them from the report.

    Rules live in ``config.security.accepted_risks`` and must carry a reason.
    Critical findings cannot be accepted; an optional ``expires_at`` date lets
    an exception lapse automatically.
    """
    rules = get(cfg, "security.accepted_risks", []) or []
    if not isinstance(rules, list):
        return []
    accepted: list[dict[str, Any]] = []
    today = _dt.date.today().isoformat()
    for finding in findings:
        if finding.get("severity") == "critical":
            continue
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            reason = str(rule.get("reason", "")).strip()
            if not reason:
                continue
            expires = str(rule.get("expires_at", "")).strip()
            if expires and expires < today:
                continue
            if rule.get("kind") and str(rule["kind"]) != str(finding.get("kind", "")):
                continue
            if rule.get("area") and str(rule["area"]) != str(finding.get("area", "")):
                continue
            needle = str(rule.get("title_contains", "")).strip()
            if needle and needle not in str(finding.get("title", "")):
                continue
            finding["accepted"] = True
            finding["accept_reason"] = reason
            finding["accepted_at"] = str(rule.get("accepted_at", today))
            accepted.append(finding)
            break
    return accepted


def run_audit(
    paths: Paths,
    cfg: dict[str, Any],
    *,
    scopes: list[str] | None = None,
    online: bool = False,
    context: str = "audit",
) -> dict[str, Any]:
    scopes = list(dict.fromkeys(scopes or ALL_SCOPES))
    unknown = [s for s in scopes if s not in ALL_SCOPES]
    if unknown:
        raise ValueError(f"unknown audit scope(s): {', '.join(unknown)}")
    data = discover_all(cfg, paths)
    skills: list[Skill] = data["skills"]["skills"]
    findings: list[dict[str, Any]] = []

    # ---- skills ----
    duplicates: list[dict[str, Any]] = []
    mirror_groups: list[dict[str, Any]] = []
    scanned_content: set[tuple[str, str]] = set()
    if "skills" in scopes:
        by_name: dict[str, list[Skill]] = defaultdict(list)
        for skill in skills:
            if skill.name:
                by_name[skill.name].append(skill)
        for name, items in sorted(by_name.items()):
            if len(items) < 2:
                continue
            hashes = {item.sha256 for item in items if item.sha256}
            same = len(hashes) <= 1
            duplicates.append(
                {
                    "name": name,
                    "count": len(items),
                    "same_content": same,
                    "paths": [str(item.path) for item in items],
                    "agents": [item.agent for item in items],
                }
            )
            if same:
                mirror_groups.append(
                    {
                        "name": name,
                        "count": len(items),
                        "agents": [item.agent for item in items],
                    }
                )
            else:
                _add(
                    findings,
                    area="skills",
                    severity="high",
                    title=f"同名技能内容冲突：{name}",
                    evidence=f"{len(items)} 个位置内容不同：{', '.join(item.agent for item in items)}",
                    recommendation="确定权威版本，删除或归档其余副本；否则不同 Agent 行为会不一致。",
                    path="; ".join(str(item.path) for item in items),
                    kind="duplicate-conflict",
                )
        for skill in skills:
            if not skill.valid:
                _add(
                    findings,
                    area="skills",
                    severity="high",
                    title=f"技能无效或无法加载：{skill.name or skill.path.name}",
                    evidence="; ".join(skill.errors[:4]) or "missing name/description",
                    recommendation="修复 frontmatter（name 必须 kebab-case，description 必填）。",
                    path=str(skill.path),
                    kind="invalid-skill",
                )
                continue
            if skill.size > 512 * 1024:
                _add(
                    findings,
                    area="skills",
                    severity="medium",
                    title=f"技能正文过大：{skill.name}",
                    evidence=f"{skill.size / 1024:.0f} KiB",
                    recommendation="把细节移入 references/，SKILL.md 保持精简，降低上下文成本。",
                    path=str(skill.path),
                    kind="oversized-skill",
                )
            scan_key = (skill.name, skill.sha256)
            if scan_key in scanned_content:
                continue
            scanned_content.add(scan_key)
            findings.extend(scan_skill(skill, max_files=int(get(cfg, "security.max_files_per_skill", 400)), max_bytes=int(get(cfg, "security.max_scan_bytes", 1024 * 1024))))
        # Near-duplicate descriptions (only for valid, different names)
        valid = [s for s in skills if s.valid]
        for i, a in enumerate(valid):
            for b in valid[i + 1 :]:
                if a.name == b.name or not a.description or not b.description:
                    continue
                score = _jaccard(a.description, b.description)
                if score >= 0.92 and a.name != b.name:
                    _add(
                        findings,
                        area="skills",
                        severity="low",
                        title=f"技能描述高度相似：{a.name} / {b.name}",
                        evidence=f"Jaccard={score:.2f}",
                        recommendation="确认是否职责重叠；合并或明确触发边界，避免路由歧义。",
                        path=f"{a.path}; {b.path}",
                        kind="near-duplicate",
                    )
                    break
        if mirror_groups:
            _add(
                findings,
                area="skills",
                severity="info",
                title=f"{len(mirror_groups)} 组同名技能镜像",
                evidence=", ".join(f"{g['name']}×{g['count']}" for g in mirror_groups[:24])
                + ("…" if len(mirror_groups) > 24 else ""),
                recommendation="保留一个权威源，其余使用链接/同步；确认镜像的更新策略，避免手工多副本漂移。",
                kind="duplicate-mirror",
            )
        _requirements_findings(valid, findings)
        missing_license: list[str] = []
        for skill in valid:
            base = skill.path.parent
            if skill.license:
                continue
            if any((base / name).exists() for name in ("LICENSE", "LICENSE.txt", "COPYING")):
                continue
            missing_license.append(skill.name)
        if missing_license:
            _add(
                findings,
                area="license",
                severity="info",
                title=f"{len(missing_license)} 个技能未声明许可证",
                evidence=", ".join(missing_license[:24]) + ("…" if len(missing_license) > 24 else ""),
                recommendation="为自建/再分发技能补充 LICENSE 或 frontmatter license；仅内部使用的第三方技能记录来源与许可。",
                kind="license-missing",
            )

    # ---- MCP ----
    mcp_findings: list[dict[str, Any]] = []
    if "mcp" in scopes:
        for server in data["mcp"]["servers"]:
            mcp_findings.extend(scan_mcp(server))
        findings.extend(mcp_findings)
        by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for server in data["mcp"]["servers"]:
            by_name[server["name"]].append(server)
        for name, items in by_name.items():
            if len(items) > 1 and len({item.get("command") or item.get("url") for item in items}) > 1:
                _add(
                    findings,
                    area="mcp",
                    severity="medium",
                    title=f"MCP 同名不同实现：{name}",
                    evidence=f"{len(items)} 个定义",
                    recommendation="保留一个权威配置；不同客户端如需差异，明确命名与用途。",
                    path="; ".join(item["source"] for item in items),
                    kind="mcp-duplicate",
                )

    # ---- plugins ----
    if "plugins" in scopes and not data["plugins"]["plugins"]:
        _add(
            findings,
            area="plugins",
            severity="info",
            title="未发现可审计的插件配置",
            evidence="检查路径：~/.dsh/profiles/*/package.json, ~/.claude/plugins, ~/.codex/plugins",
            recommendation="如使用插件，补充配置路径到 config.json 以便审阅。",
        )

    # ---- permissions ----
    if "permissions" in scopes:
        findings.extend(scan_permissions(data["permissions"]["permissions"]))
        if not data["permissions"]["permissions"]:
            _add(
                findings,
                area="permission",
                severity="info",
                title="未发现权限配置文件",
                evidence="可能尚未配置，或路径不在默认列表",
                recommendation="确认 Agent 权限预设；长期 danger-full-access 应改为按需。",
            )

    # ---- runtime ----
    runtime = data["runtime"]
    if "runtime" in scopes:
        if not runtime.get("runtime_writable"):
            _add(
                findings,
                area="runtime",
                severity="high",
                title="运行时目录不可写",
                evidence=str(paths.runtime_home),
                recommendation="修复权限或通过 EVOLUTION_HOME 指向可写目录。",
            )
        try:
            py = tuple(int(x) for x in str(runtime.get("python", "0")).split(".")[:2])
        except ValueError:
            py = (0, 0)
        if py < (3, 11):
            _add(
                findings,
                area="runtime",
                severity="high",
                title="Python 版本过低",
                evidence=f"{runtime.get('python')}（需要 >=3.11）",
                recommendation="升级到 Python 3.11+，或使用 DSH 自带运行时。",
            )
        disk = runtime.get("disk") or {}
        if disk.get("free") is not None and disk["free"] < 1024 * 1024 * 1024:
            _add(
                findings,
                area="runtime",
                severity="medium",
                title="磁盘剩余空间不足 1 GiB",
                evidence=f"{disk['free'] / (1024**3):.2f} GiB free",
                recommendation="清理旧报告/快照/缓存，或迁移运行时目录。",
            )

    # ---- hygiene ----
    hygiene = _hygiene(paths, findings, context=context) if "hygiene" in scopes else {}

    # ---- schedule / latest review ----
    schedule_info: dict[str, Any] = {}
    try:
        from .schedule import schedule_status

        schedule_info = schedule_status(paths, cfg)
    except Exception as exc:  # pragma: no cover - defensive
        schedule_info = {"installed": None, "detail": f"schedule check failed: {exc}"}
    if "hygiene" in scopes and schedule_info.get("supported") and not schedule_info.get("installed"):
        _add(
            findings,
            area="hygiene",
            severity="low",
            title="每周审阅尚未安装调度",
            evidence=str(schedule_info.get("detail", "not installed")),
            recommendation="运行 `evolution schedule install --apply`，或保留手动审阅并在日历提醒。",
        )

    # ---- online upgrade checks are part of review, not audit ----
    limits: list[str] = []
    if not online:
        limits.append("离线审计：未检查网络上的新版本、漏洞公告与仓库活跃度。")
    limits.append("静态扫描不等于运行沙箱；未执行被审代码，无法发现运行时行为。")
    limits.append("指标数据来自自愿 record，未采集完整会话；成功率不代表所有任务。")

    # ---- finalize ----
    findings.sort(key=lambda f: (severity_rank(f["severity"]), f["area"], f["title"]))
    for idx, finding in enumerate(findings, start=1):
        finding["id"] = f"A-{idx:03d}"
    accepted_findings = _apply_acceptances(findings, cfg)
    active_findings = [f for f in findings if not f.get("accepted")]
    severity_counts = summarize(active_findings)
    # Health score: critical findings dominate; a few high findings should not
    # collapse the score to zero (that hides the difference between 1 and 20).
    penalty = (
        severity_counts.get("critical", 0) * 30
        + severity_counts.get("high", 0) * 10
        + severity_counts.get("medium", 0) * 1.5
        + severity_counts.get("low", 0) * 0.1
    )
    score = 0 if severity_counts.get("critical") else max(0, round(100 - min(85, penalty)))
    next_actions = [
        {
            "id": f["id"],
            "severity": f["severity"],
            "title": f["title"],
            "recommendation": f["recommendation"],
        }
        for f in active_findings
        if f["severity"] in {"critical", "high", "medium"}
    ][:6]
    skill_roots_serializable = [
        {**root, "path": str(root["path"])} for root in data["skills"]["roots"]
    ]
    result = {
        "schema": 1,
        "kind": "audit",
        "generated_at": now_iso(),
        "version": __version__,
        "platform": platform_label(),
        "scopes": scopes,
        "online": bool(online),
        "counts": {
            "skills": len(skills),
            "valid_skills": sum(1 for s in skills if s.valid),
            "invalid_skills": sum(1 for s in skills if not s.valid),
            "skill_roots": len([r for r in data["skills"]["roots"] if r["exists"]]),
            "mcp_servers": len(data["mcp"]["servers"]),
            "plugins": data["plugins"]["count"],
            "permission_findings": len(data["permissions"]["permissions"]),
            "duplicates": len(duplicates),
            "findings": len(findings),
            "accepted": len(accepted_findings),
            "active_findings": len(active_findings),
        },
        "severity_counts": severity_counts,
        "accepted_findings": [
            {
                "id": f["id"],
                "severity": f["severity"],
                "title": f["title"],
                "reason": f.get("accept_reason", ""),
                "accepted_at": f.get("accepted_at", ""),
            }
            for f in accepted_findings
        ],
        "score": score,
        "skills": [s.as_dict() for s in skills],
        "skill_roots": skill_roots_serializable,
        "duplicates": duplicates,
        "findings": findings,
        "mcp": data["mcp"]["servers"],
        "mcp_sources": data["mcp"]["sources"],
        "plugins": data["plugins"]["plugins"],
        "permissions": data["permissions"]["permissions"],
        "runtime": runtime,
        "dependencies": data["dependencies"],
        "hygiene": hygiene,
        "schedule": schedule_info,
        "limits": limits,
        "next_actions": next_actions,
        "discovery_errors": data["skills"]["errors"],
    }
    return result


def audit_markdown(result: dict[str, Any]) -> str:
    counts = result["counts"]
    sev = result["severity_counts"]
    lines: list[str] = []
    lines.append(f"# 环境与能力审计 · {result['generated_at']}")
    lines.append("")
    lines.append(f"- 版本：{result['version']} · 平台：{result['platform']}")
    lines.append(
        f"- 健康分：**{result['score']}/100** · 发现：{counts['findings']} "
        f"(active: critical {sev.get('critical', 0)} / high {sev.get('high', 0)} / medium {sev.get('medium', 0)} / "
        f"low {sev.get('low', 0)} / info {sev.get('info', 0)}；已接受 {counts.get('accepted', 0)})"
    )
    lines.append(
        f"- 技能：{counts['valid_skills']} 有效 / {counts['invalid_skills']} 无效 · 根：{counts['skill_roots']} · "
        f"MCP：{counts['mcp_servers']} · 插件：{counts['plugins']} · 重复：{counts['duplicates']}"
    )
    lines.append(f"- 范围：{', '.join(result['scopes'])} · 在线：{'是' if result['online'] else '否'}")
    lines.append("")

    lines.append("## 结论与优先动作")
    if result["next_actions"]:
        for item in result["next_actions"]:
            lines.append(f"- **{item['severity'].upper()} {item['id']}** {item['title']} → {item['recommendation']}")
    else:
        lines.append("- 未发现需要立即处理的高/中风险项。")
    lines.append("")

    lines.append("## 主要发现")
    if not result["findings"]:
        lines.append("未发现显著问题。")
    else:
        lines.append("| ID | 级别 | 领域 | 发现 | 证据 | 建议 |")
        lines.append("|---|---|---|---|---|---|")
        for f in result["findings"][:80]:
            evidence = f["evidence"]
            if f.get("line"):
                evidence += f" (L{f['line']})"
            sev_label = f["severity"] + (" (accepted)" if f.get("accepted") else "")
            lines.append(
                f"| {f['id']} | {sev_label} | {md_cell(f['area'])} | {md_cell(f['title'])} | "
                f"{md_cell(evidence, 120)} | {md_cell(f['recommendation'], 160)} |"
            )
        if len(result["findings"]) > 80:
            lines.append(f"| … | … | … | 其余 {len(result['findings']) - 80} 条省略 | … | … |")
    lines.append("")

    if result.get("accepted_findings"):
        lines.append("## 已接受风险")
        for item in result["accepted_findings"]:
            lines.append(
                f"- {item['id']} · {item['severity']} · {item['title']} — 理由：{item.get('reason', '')} "
                f"（接受于 {item.get('accepted_at', '—')}）"
            )
        lines.append("")

    lines.append("## 技能清单")
    if result["skills"]:
        lines.append("| 技能 | Agent | Rank | 版本 | 许可证 | 状态 | 路径 |")
        lines.append("|---|---|---|---|---|---|---|")
        for s in result["skills"][:MAX_SKILL_ROWS]:
            status = "ok" if not s["errors"] else "invalid: " + "; ".join(s["errors"])[:60]
            lines.append(
                f"| {md_cell(s['name'] or '?')} | {md_cell(s['agent'])} | {s['rank']} | "
                f"{md_cell(s['version'] or '—')} | {md_cell(s['license'] or '—')} | {md_cell(status, 80)} | "
                f"{md_cell(s['path'], 100)} |"
            )
        if len(result["skills"]) > MAX_SKILL_ROWS:
            lines.append(f"| … | … | … | … | … | 其余 {len(result['skills']) - MAX_SKILL_ROWS} 个省略 | … |")
    else:
        lines.append("未发现技能。")
    lines.append("")

    if result["mcp"]:
        lines.append("## MCP 服务")
        lines.append("| 名称 | 来源 | 传输 | 命令/URL | 环境变量名 |")
        lines.append("|---|---|---|---|---|")
        for s in result["mcp"]:
            target = s.get("command") or s.get("url") or "—"
            lines.append(
                f"| {md_cell(s['name'])} | {md_cell(Path(s['source']).name)} | {md_cell(s['transport'])} | "
                f"{md_cell(target, 100)} | {md_cell(', '.join(s.get('env_keys') or []) or '—')} |"
            )
        lines.append("")

    if result["permissions"]:
        lines.append("## 权限配置")
        lines.append("| 来源 | 键 | 值 | 风险 |")
        lines.append("|---|---|---|---|")
        for p in result["permissions"]:
            lines.append(f"| {md_cell(Path(p['source']).name)} | {md_cell(p['key'])} | {md_cell(p['value'], 80)} | {p['severity']} |")
        lines.append("")

    runtime = result["runtime"]
    lines.append("## 运行环境")
    lines.append(f"- OS：{runtime.get('os')}")
    lines.append(f"- Python：{runtime.get('python')} ({runtime.get('python_impl')}) · Node：{runtime.get('node')} · Git：{runtime.get('git')}")
    disk = runtime.get("disk") or {}
    if disk:
        lines.append(f"- 磁盘：{disk['free'] / (1024**3):.1f} GiB free / {disk['total'] / (1024**3):.1f} GiB total")
    lines.append(f"- 运行时目录：`{runtime.get('runtime_home')}` · 可写：{runtime.get('runtime_writable')}")
    lines.append("")

    lines.append("## 限制与未检查项")
    for item in result["limits"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## 证据")
    lines.append(f"- 审计 JSON：`state/audit-latest.json`")
    lines.append(f"- 技能根：{', '.join(str(r['path']) for r in result['skill_roots'])}")
    if result.get("discovery_errors"):
        lines.append("- 发现错误：")
        for err in result["discovery_errors"][:20]:
            lines.append(f"  - {err}")
    lines.append("")
    return "\n".join(lines)


def write_audit(paths: Paths, result: dict[str, Any]) -> tuple[Path, Path]:
    paths.ensure_runtime()
    stamp = now_stamp()
    md_path = paths.audit_dir / f"audit-{stamp}.md"
    json_path = paths.audit_dir / f"audit-{stamp}.json"
    md_path.write_text(audit_markdown(result), encoding="utf-8", newline="\n")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    latest = paths.state_dir / "audit-latest.json"
    latest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    _update_index(paths)
    return md_path, json_path


def latest_audit(paths: Paths) -> dict[str, Any] | None:
    path = paths.state_dir / "audit-latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _update_index(paths: Paths) -> None:
    rows: list[str] = ["# evolution 报告索引", "", f"更新时间：{now_iso()}", "", "## 审计"]
    for path in sorted(paths.audit_dir.glob("*.md"), reverse=True)[:20]:
        rows.append(f"- [{path.name}](audit/{path.name})")
    rows += ["", "## 每周审阅"]
    for path in sorted(paths.weekly_dir.glob("*.md"), reverse=True)[:20]:
        rows.append(f"- [{path.name}](weekly/{path.name})")
    rows.append("")
    (paths.reports_dir / "index.md").write_text("\n".join(rows), encoding="utf-8", newline="\n")


def fail_on(result: dict[str, Any], threshold: str) -> bool:
    threshold = (threshold or "none").lower()
    if threshold == "none":
        return False
    order = {"low": 4, "medium": 3, "high": 2, "critical": 1}
    limit = order.get(threshold, 2)
    for finding in result["findings"]:
        if finding.get("accepted"):
            continue
        rank = {"critical": 1, "high": 2, "medium": 3, "low": 4, "info": 5}.get(finding["severity"], 9)
        if rank <= limit:
            return True
    return False
