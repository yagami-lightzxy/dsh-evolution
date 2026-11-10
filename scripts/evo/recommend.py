"""Cheap local capability recommendation (no LLM, no network)."""
from __future__ import annotations

import re
from typing import Any

from .config import resolve_skill_roots
from .discover import discover_all
from .paths import Paths


def _tokens(text: str) -> set[str]:
    text = (text or "").lower()
    words = set(re.findall(r"[a-z0-9_+.-]{2,}", text))
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    words.update("".join(cjk[i : i + 2]) for i in range(max(0, len(cjk) - 1)))
    return words


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def recommend(paths: Paths, cfg: dict[str, Any], task: str, *, limit: int = 5) -> dict[str, Any]:
    data = discover_all(cfg, paths)
    query = _tokens(task)
    results: list[dict[str, Any]] = []
    for skill in data["skills"]["skills"]:
        if not skill.valid:
            continue
        score = 0.0
        reasons: list[str] = []
        name_tokens = _tokens(skill.name.replace("-", " "))
        if skill.name and skill.name.lower() in task.lower():
            score += 5.0
            reasons.append("名称命中")
        elif name_tokens & query:
            score += 2.5
            reasons.append("名称词元命中")
        overlap = _overlap(query, _tokens(skill.description))
        if overlap > 0:
            score += min(3.0, overlap * 4.0)
            reasons.append(f"描述相似 {overlap:.0%}")
        if skill.agent in {"shared", "dsh", "canonical"}:
            score += 0.2
        if score > 0:
            results.append(
                {
                    "kind": "skill",
                    "name": skill.name,
                    "score": round(score, 2),
                    "reason": "; ".join(reasons) or "弱匹配",
                    "path": str(skill.path),
                    "agent": skill.agent,
                    "description": skill.description[:240],
                }
            )
    for server in data["mcp"]["servers"]:
        score = 0.0
        reasons = []
        if server["name"].lower() in task.lower():
            score += 4.0
            reasons.append("MCP 名称命中")
        overlap = _overlap(query, _tokens(server["name"] + " " + server.get("command", "")))
        if overlap > 0:
            score += overlap * 2.0
            reasons.append("MCP 词元匹配")
        if score > 0:
            results.append(
                {
                    "kind": "mcp",
                    "name": server["name"],
                    "score": round(score, 2),
                    "reason": "; ".join(reasons),
                    "path": server["source"],
                    "description": (server.get("command") or server.get("url") or "")[:240],
                }
            )
    for plugin in data["plugins"]["plugins"]:
        if plugin["name"].lower() in task.lower():
            results.append(
                {
                    "kind": "plugin",
                    "name": plugin["name"],
                    "score": 3.0,
                    "reason": "插件名称命中",
                    "path": plugin["source"],
                    "description": plugin.get("kind", ""),
                }
            )
    results.sort(key=lambda x: (-x["score"], x["kind"], x["name"]))
    top = results[: max(1, limit)]
    best = top[0]["score"] if top else 0.0
    return {
        "task": task,
        "matches": top,
        "best_score": best,
        "gap": best < 2.0,
        "message": (
            "已有较强候选，优先复用并验证适配性。"
            if best >= 3.0
            else ("有弱候选，先看说明；不足再检索/补全。" if best >= 2.0 else "未发现强匹配；考虑检索成熟方案或补全能力（先获授权）。")
        ),
        "counts": {
            "skills": len(data["skills"]["skills"]),
            "mcp": len(data["mcp"]["servers"]),
            "plugins": data["plugins"]["count"],
            "roots": len(resolve_skill_roots(cfg, paths)),
        },
        "limits": [
            "本地关键词/词元匹配，不做语义向量；中文按二字切分，可能漏匹配。",
            "未验证候选的实际可用性、许可证与安全；需进一步审查。",
        ],
    }


def format_recommendation(result: dict[str, Any]) -> str:
    lines = [f"任务：{result['task']}", f"结论：{result['message']}", ""]
    if not result["matches"]:
        lines.append("未发现候选。下一步：确认能力缺口 → 检索官方/成熟项目 → 评估后安装（默认 dry-run）。")
        return "\n".join(lines) + "\n"
    lines.append("| 类型 | 名称 | 分数 | 理由 | 位置 |")
    lines.append("|---|---|---|---|---|")
    for item in result["matches"]:
        lines.append(
            f"| {item['kind']} | {item['name']} | {item['score']} | {item['reason']} | `{item['path']}` |"
        )
    lines.append("")
    lines.append("下一步：优先复用最高分候选；若适配性不足，记录缺口再检索/补全。")
    return "\n".join(lines) + "\n"
