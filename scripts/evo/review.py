"""Weekly review: audit + metrics + upgrade candidates + report."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__
from .audit import audit_markdown, latest_audit, run_audit
from .envcheck import run_envcheck, write_envcheck
from .config import get
from .metrics import format_markdown as metrics_markdown
from .metrics import load as load_metrics
from .metrics import stats as metrics_stats
from .paths import Paths
from .schedule import schedule_status
from .util import md_cell, now_iso, now_stamp, platform_label, read_json, write_json

GITHUB_API = "https://api.github.com"


def _github_json(url: str, timeout: float = 12.0) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"evolution/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed GitHub API
        return json.loads(response.read().decode("utf-8", errors="replace"))


def check_upgrades(paths: Paths, cfg: dict[str, Any], *, online: bool = False) -> dict[str, Any]:
    cache_path = paths.state_dir / "upgrade-cache.json"
    cache = read_json(cache_path, default={})
    if not isinstance(cache, dict):
        cache = {}
    now = now_iso()
    results: list[dict[str, Any]] = []
    for item in cfg.get("upgrade_watch", []):
        if not isinstance(item, dict):
            continue
        repo = str(item.get("repo", "")).strip()
        kind = str(item.get("kind", "github"))
        entry: dict[str, Any] = {
            "kind": kind,
            "repo": repo,
            "note": item.get("note", ""),
            "current_version": item.get("current_version", ""),
            "status": "skipped",
        }
        if kind != "github" or not repo:
            entry["status"] = "unsupported"
            results.append(entry)
            continue
        if not online:
            entry["status"] = "offline"
            entry["detail"] = "未联网；运行 review --online 检查"
            results.append(entry)
            continue
        cache_key = f"github:{repo}"
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            fetched = cached.get("fetched_at", "")
            try:
                import datetime as _dt

                fetched_at = _dt.datetime.fromisoformat(str(fetched).replace("Z", "+00:00"))
                age = (_dt.datetime.now().astimezone() - fetched_at).total_seconds()
            except (ValueError, TypeError):
                age = 10**9
            if age < 86400:
                results.append({**entry, **cached.get("data", {}), "status": "cached"})
                continue
        try:
            meta = _github_json(f"{GITHUB_API}/repos/{repo}")
            latest = None
            try:
                latest = _github_json(f"{GITHUB_API}/repos/{repo}/releases/latest")
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
            data = {
                "repo": repo,
                "stars": meta.get("stargazers_count"),
                "archived": bool(meta.get("archived")),
                "pushed_at": meta.get("pushed_at"),
                "open_issues": meta.get("open_issues_count"),
                "license": (meta.get("license") or {}).get("spdx_id"),
                "latest_release": (latest or {}).get("tag_name") or (latest or {}).get("name"),
                "latest_release_at": (latest or {}).get("published_at"),
                "html_url": meta.get("html_url"),
            }
            entry.update(data)
            entry["status"] = "ok"
            entry["recommendation"] = _upgrade_recommendation(data, item)
            cache[cache_key] = {"fetched_at": now, "data": data}
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            entry["status"] = "error"
            entry["detail"] = f"{type(exc).__name__}: {exc}"
        results.append(entry)
    if online:
        try:
            write_json(cache_path, cache)
        except OSError:
            pass
    return {"online": online, "items": results, "checked_at": now}


def _upgrade_recommendation(data: dict[str, Any], item: dict[str, Any]) -> str:
    if data.get("archived"):
        return "reject/替代：仓库已归档，不建议新增依赖。"
    license_id = str(data.get("license") or "").upper()
    if license_id in {"GPL-3.0", "AGPL-3.0"}:
        return "审慎：强 copyleft 许可证，确认分发/使用方式。"
    current = str(item.get("current_version") or "")
    latest = str(data.get("latest_release") or "")
    if current and latest and current != latest:
        return f"候选：{current} → {latest}；先快照、灰度、回归，再决定是否升级。"
    if latest:
        return f"观察：最新 {latest}；无明确收益不升级。"
    return "观察：无 release 信息；检查 commit/issue 后再评估。"


def run_review(
    paths: Paths,
    cfg: dict[str, Any],
    *,
    week: str = "",
    online: bool = False,
    no_audit: bool = False,
    days: int = 30,
    maintenance: dict[str, Any] | None = None,
    envcheck_enabled: bool = True,
) -> dict[str, Any]:
    paths.ensure_runtime()
    week = week or _current_week()
    if not re.fullmatch(r"\d{4}-W\d{2}", week):
        raise ValueError("week must look like 2026-W45")
    audit = latest_audit(paths) if no_audit else run_audit(paths, cfg, online=online, context="review")
    if audit is None:
        audit = run_audit(paths, cfg, online=online, context="review")
    if not no_audit:
        # Keep the evidence pointer in the weekly report truthful: review must
        # persist the audit it actually used instead of leaving a stale file.
        write_json(paths.state_dir / "audit-latest.json", audit)
    records = load_metrics(paths, days=days)
    stats = metrics_stats(records)
    upgrades = check_upgrades(paths, cfg, online=online)
    # Environment currency: the weekly loop must notice when the tooling itself
    # fell behind the model, not just when a task went wrong.
    env = run_envcheck(paths, cfg, online=online) if envcheck_enabled else None
    if env is not None:
        write_envcheck(paths, env)
    schedule = schedule_status(paths, cfg)
    knowledge_files = sorted(paths.knowledge_dir.glob("*.md")) if paths.knowledge_dir.is_dir() else []
    result = {
        "schema": 1,
        "kind": "weekly-review",
        "week": week,
        "generated_at": now_iso(),
        "version": __version__,
        "platform": platform_label(),
        "online": bool(online),
        "audit": audit,
        "metrics": stats,
        "upgrades": upgrades,
        "env": ({k: v for k, v in env.items() if k != "markdown"} if env else None),
        "schedule": schedule,
        "maintenance": maintenance,
        "knowledge": {
            "files": len(knowledge_files),
            "last_modified": max((p.stat().st_mtime for p in knowledge_files), default=0),
        },
        "limits": list(audit.get("limits", [])),
    }
    result["markdown"] = weekly_markdown(result)
    md_path = paths.weekly_dir / f"{week}.md"
    json_path = paths.weekly_dir / f"{week}.json"
    md_path.write_text(result["markdown"], encoding="utf-8", newline="\n")
    write_json(json_path, {k: v for k, v in result.items() if k != "markdown"})
    write_json(paths.state_dir / "review-latest.json", {k: v for k, v in result.items() if k != "markdown"})
    result["paths"] = {"markdown": str(md_path), "json": str(json_path)}
    _update_index(paths)
    return result


def _current_week() -> str:
    import datetime as _dt

    year, week, _ = _dt.date.today().isocalendar()
    return f"{year}-W{week:02d}"


def weekly_markdown(result: dict[str, Any]) -> str:
    audit = result["audit"]
    stats = result["metrics"]
    sev = audit.get("severity_counts", {})
    counts = audit.get("counts", {})
    lines: list[str] = []
    lines.append(f"# 每周进化审阅 · {result['week']}")
    lines.append("")
    lines.append(f"- 生成：{result['generated_at']} · 版本：{result['version']} · 平台：{result['platform']}")
    lines.append(f"- 模式：{'联网' if result['online'] else '离线'} · 审计健康分：**{audit.get('score')}/100** · 数据窗口：{stats.get('n', 0)} 个任务")
    lines.append(
        f"- 发现：{counts.get('findings', 0)} 条（critical {sev.get('critical', 0)} / high {sev.get('high', 0)} / "
        f"medium {sev.get('medium', 0)} / low {sev.get('low', 0)} / info {sev.get('info', 0)}；"
        f"已接受 {counts.get('accepted', 0)}）"
    )
    lines.append("")

    lines.append("## 结论")
    top = audit.get("next_actions", [])[:3]
    if top:
        for item in top:
            lines.append(f"- **{item['severity'].upper()}** {item['title']} → {item['recommendation']}")
    else:
        lines.append("- 未发现需要立即处理的高/中风险问题。")
    if stats.get("n", 0) == 0:
        lines.append("- 指标数据不足：先用 `evolution record` 记录任务结果，否则性能结论只能留空。")
    elif stats.get("success_rate", 0) < 0.7:
        lines.append(f"- 成功率偏低（{stats['success_rate']:.1%}）：优先看失败根因与验收缺口。")
    lines.append("")

    if audit.get("accepted_findings"):
        lines.append("## 已接受风险")
        for item in audit["accepted_findings"]:
            lines.append(
                f"- {item['id']} · {item['severity']} · {item['title']} — 理由：{item.get('reason', '')} "
                f"（接受于 {item.get('accepted_at', '—')}）"
            )
        lines.append("")

    maint = result.get("maintenance")
    lines.append("## 自主维护")
    if not maint:
        lines.append("- 本次未执行维护；需要自动优化时运行 `review --apply` 或 `maintain --apply`。")
    else:
        apply_result = maint.get("apply_result", {})
        actions = maint.get("plan", {}).get("actions", [])
        lines.append(
            f"- 计划 {len(actions)} 个动作；已执行 {len(apply_result.get('applied', []))}；"
            f"错误 {len(apply_result.get('errors', []))}；快照 {maint.get('snapshot') or '—'}。"
        )
        for item in apply_result.get("applied", [])[:10]:
            lines.append(f"- {item.get('action')} · {item.get('name')} → {item.get('canonical') or item.get('quarantine')}")
        if apply_result.get("errors"):
            for item in apply_result["errors"][:5]:
                lines.append(f"- 错误：{item.get('action')} {item.get('target')}: {item.get('error')}")
        plugin_candidates = maint.get("plugins", {}).get("candidates", [])
        if plugin_candidates:
            lines.append("- 插件升级候选：" + ", ".join(f"{p['name']} {p['current']}→{p['latest']}" for p in plugin_candidates))
            lines.append("- 插件由 DSH `plugin_manager` 执行；跨 major 不自动升级。")
        if maint.get("install_queue"):
            lines.append("- 按需下载：" + ", ".join(str(x.get("name")) for x in maint["install_queue"]))
    lines.append("")

    lines.append("## 环境审计")
    lines.append(
        f"- 技能 {counts.get('valid_skills', 0)} 有效 / {counts.get('invalid_skills', 0)} 无效；"
        f"MCP {counts.get('mcp_servers', 0)}；插件 {counts.get('plugins', 0)}；重复 {counts.get('duplicates', 0)}。"
    )
    runtime = audit.get("runtime", {})
    lines.append(
        f"- Python {runtime.get('python')} · Node {runtime.get('node')} · "
        f"运行时目录可写：{runtime.get('runtime_writable')}。"
    )
    if audit.get("next_actions"):
        lines.append("- 详表见文末链接；本节只保留趋势与决策点。")
    lines.append("")

    lines.append("## 环境年代感")
    env = result.get("env") or {}
    if not env:
        lines.append("- 本次未执行环境体检。")
    else:
        lines.append(
            f"- 分数 **{env.get('score')}/100** · 状态 **{str(env.get('status', '')).upper()}** · "
            f"发现 critical {env.get('severity_counts', {}).get('critical', 0)} / "
            f"high {env.get('severity_counts', {}).get('high', 0)} / "
            f"medium {env.get('severity_counts', {}).get('medium', 0)}"
        )
        route = env.get("route") or {}
        if route:
            lines.append(
                f"- 模型通道：{route.get('provider') or '?'}/{route.get('model') or '?'}"
                f" effort={route.get('reasoning_effort') or '(未设置)'}"
            )
        for item in (env.get("next_actions") or [])[:5]:
            lines.append(f"- **{str(item['severity']).upper()}** {item['title']} → {item['recommendation']}")
        for limit in (env.get("limits") or [])[:3]:
            lines.append(f"- 边界：{limit}")
    lines.append("")

    lines.append("## 断舍离")
    duplicates = audit.get("duplicates", [])
    if duplicates:
        for dup in duplicates[:10]:
            action = "镜像，保留权威源" if dup.get("same_content") else "内容冲突，需确定权威版本"
            lines.append(f"- `{dup['name']}` ×{dup['count']}：{action}（{', '.join(dup.get('agents', []))}）")
    else:
        lines.append("- 未发现同名重复技能。")
    stale = [f for f in audit.get("findings", []) if f.get("area") == "hygiene"]
    for finding in stale[:6]:
        lines.append(f"- {finding['title']}：{finding['recommendation']}")
    lines.append("- 第三方组件只给建议，不自动删除；自有报告/快照按保留策略清理。")
    lines.append("")

    lines.append("## 更新升级")
    up = result["upgrades"]
    if not up.get("online"):
        lines.append("- 本次离线，未检查远端版本。需要时运行 `review --online`。")
    else:
        lines.append("| 仓库 | 最新 release | 最近推送 | 许可证 | 建议 |")
        lines.append("|---|---|---|---|---|")
        for item in up.get("items", []):
            lines.append(
                f"| {md_cell(item.get('repo'))} | {md_cell(item.get('latest_release') or '—')} | "
                f"{md_cell(item.get('pushed_at') or '—')} | {md_cell(item.get('license') or '—')} | "
                f"{md_cell(item.get('recommendation') or item.get('detail') or item.get('status'), 160)} |"
            )
    lines.append("- 默认只观察；升级前 snapshot + 兼容性验证 + 回滚演练。")
    lines.append("")

    lines.append("## 性能与成本")
    lines.append(metrics_markdown(stats).rstrip())
    if stats.get("n", 0) and stats.get("error_classes"):
        lines.append("")
        lines.append("失败根因分布：" + ", ".join(f"{k}={v}" for k, v in stats["error_classes"].items()))
    if stats.get("n", 0) and stats.get("top_tags"):
        lines.append("高频标签：" + ", ".join(f"{k}={v}" for k, v in stats["top_tags"].items()))
    lines.append("")

    lines.append("## 架构与流程")
    suggestions = _architecture_suggestions(audit, stats, result["schedule"])
    for item in suggestions:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## 已执行改进")
    lines.append("- 本次审阅只生成报告与指标聚合，未修改技能/配置/权限；如需修复，按优先动作逐项执行并验证。")
    lines.append("")

    lines.append("## 风险与未检查项")
    for item in result.get("limits", []):
        lines.append(f"- {item}")
    if not up.get("online"):
        lines.append("- 未检查新版本/安全公告/GitHub 活跃度。")
    lines.append("")

    lines.append("## 下周动作")
    actions = []
    for item in audit.get("next_actions", [])[:3]:
        actions.append(f"{item['title']} → {item['recommendation']}")
    if stats.get("n", 0) < 5:
        actions.append("开始用 `evolution record` 记录任务结果（至少 5 条），否则性能趋势不可信。")
    if not result["schedule"].get("installed"):
        actions.append("用 Agent 原生自动化创建周任务（`schedule_create`），或运行 `schedule native --apply` 生成提示。")
    if not actions:
        actions.append("保持现状；下周复查趋势与新增风险。")
    for idx, action in enumerate(actions[:5], start=1):
        lines.append(f"{idx}. {action}")
    lines.append("")

    lines.append("## 证据")
    lines.append(f"- 审计 JSON：`state/audit-latest.json`（生成于 {audit.get('generated_at')}）")
    lines.append(f"- 周报 JSON：`reports/weekly/{result['week']}.json`")
    lines.append(f"- 指标文件：`state/metrics.jsonl`（本次窗口 {stats.get('n', 0)} 条）")
    lines.append(f"- 调度：{result['schedule'].get('detail', '未知')}")
    lines.append("")
    return "\n".join(lines)


def _architecture_suggestions(
    audit: dict[str, Any], stats: dict[str, Any], schedule: dict[str, Any]
) -> list[str]:
    out: list[str] = []
    findings = audit.get("findings", [])
    if any(f.get("kind") in {"duplicate-conflict", "duplicate-mirror"} for f in findings):
        out.append("合并重复技能：保留一个权威源，其余用链接/同步，减少路由歧义与维护成本。")
    if any(f.get("kind") == "invalid-skill" for f in findings):
        out.append("先修复无效技能（frontmatter），否则它们不会出现在 Agent 目录中。")
    if any(f.get("area") == "permission" and f.get("severity") == "high" for f in findings):
        out.append("权限过大：日常任务改 workspace-write/read-only，按需审批；记录例外。")
    if stats.get("n", 0) == 0:
        out.append("建立最小指标闭环：每条任务记录 outcome/耗时/Token/干预/失败根因，周审阅才可量化。")
    elif stats.get("intervention_rate") is not None and stats["intervention_rate"] > 0.3:
        out.append("人工干预率偏高：把重复澄清/修复固化为规则、模板或脚本。")
    if not schedule.get("installed"):
        out.append("周审阅尚未自动触发：优先用 Agent 原生自动化，避免依赖外部计划任务。")
    if not out:
        out.append("架构未见明显问题；保持当前工具组合，避免无收益的升级/重构。")
    return out


def _update_index(paths: Paths) -> None:
    lines = ["# evolution 报告索引", "", f"更新时间：{now_iso()}", "", "## 每周审阅"]
    for path in sorted(paths.weekly_dir.glob("*.md"), reverse=True)[:30]:
        lines.append(f"- [{path.name}](weekly/{path.name})")
    lines += ["", "## 环境体检"]
    env_dir = paths.reports_dir / "envcheck"
    for path in sorted(env_dir.glob("*.md"), reverse=True)[:10] if env_dir.is_dir() else []:
        lines.append(f"- [{path.name}](envcheck/{path.name})")
    lines += ["", "## 审计"]
    for path in sorted(paths.audit_dir.glob("*.md"), reverse=True)[:20]:
        lines.append(f"- [{path.name}](audit/{path.name})")
    lines.append("")
    (paths.reports_dir / "index.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
