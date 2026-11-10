"""Local task metrics ledger (JSONL) and aggregation."""
from __future__ import annotations

import datetime as _dt
from collections import Counter
from pathlib import Path
from typing import Any

from .paths import Paths
from .util import (
    now_iso,
    parse_iso,
    percentile,
    redact_secrets,
    read_jsonl,
    append_jsonl,
)

OUTCOMES = {"success", "partial", "failed", "blocked", "rolled_back"}
ERROR_CLASSES = {
    "info-gap",
    "tool-failure",
    "permission",
    "wrong-approach",
    "insufficient-verification",
    "external-dependency",
    "timeout",
    "unknown",
}


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def record(
    paths: Paths,
    *,
    task: str,
    outcome: str,
    duration_s: float | None = None,
    tokens: int | None = None,
    cost_usd: float | None = None,
    tools: int | None = None,
    interventions: int | None = None,
    retries: int | None = None,
    quality: float | None = None,
    error_class: str = "",
    notes: str = "",
    tags: list[str] | None = None,
    agent: str = "",
    model: str = "",
    skill: str = "",
) -> dict[str, Any]:
    outcome = (outcome or "").strip().lower()
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(OUTCOMES)}")
    if not task or not str(task).strip():
        raise ValueError("task label is required")
    if duration_s is not None and duration_s < 0:
        raise ValueError("duration_s must be >= 0")
    if tokens is not None and tokens < 0:
        raise ValueError("tokens must be >= 0")
    if cost_usd is not None and cost_usd < 0:
        raise ValueError("cost_usd must be >= 0")
    if quality is not None and not (0 <= quality <= 5):
        raise ValueError("quality must be between 0 and 5")
    if error_class and error_class not in ERROR_CLASSES:
        raise ValueError(f"error_class must be one of {sorted(ERROR_CLASSES)}")
    record = {
        "ts": now_iso(),
        "task": redact_secrets(str(task).strip())[:200],
        "outcome": outcome,
        "duration_s": _number(duration_s),
        "tokens": int(tokens) if tokens is not None else None,
        "cost_usd": _number(cost_usd),
        "tools": int(tools) if tools is not None else None,
        "interventions": int(interventions) if interventions is not None else None,
        "retries": int(retries) if retries is not None else None,
        "quality": _number(quality),
        "error_class": error_class,
        "notes": redact_secrets(str(notes))[:1000],
        "tags": [str(t)[:40] for t in (tags or [])][:20],
        "agent": str(agent)[:80],
        "model": str(model)[:80],
        "skill": str(skill)[:80],
        "schema": 1,
    }
    append_jsonl(paths.metrics_file, record)
    return record


def load(paths: Paths, days: int | None = None) -> list[dict[str, Any]]:
    records = read_jsonl(paths.metrics_file)
    if days is None:
        return records
    cutoff = _dt.datetime.now().astimezone() - _dt.timedelta(days=days)
    selected: list[dict[str, Any]] = []
    for item in records:
        when = parse_iso(str(item.get("ts", "")))
        if when is not None and when >= cutoff:
            selected.append(item)
    return selected


def _values(records: list[dict[str, Any]], key: str) -> list[float]:
    out: list[float] = []
    for item in records:
        value = _number(item.get(key))
        if value is not None:
            out.append(value)
    return out


def stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    if n == 0:
        return {
            "n": 0,
            "sufficient": False,
            "message": "无指标数据；先用 record 记录任务结果。",
        }
    outcomes = Counter(str(item.get("outcome", "unknown")) for item in records)
    duration = _values(records, "duration_s")
    tokens = _values(records, "tokens")
    costs = _values(records, "cost_usd")
    quality = _values(records, "quality")
    interventions = _values(records, "interventions")
    retries = _values(records, "retries")
    error_classes = Counter(str(item.get("error_class")) for item in records if item.get("error_class"))
    tags = Counter(tag for item in records for tag in (item.get("tags") or []))
    agents = Counter(str(item.get("agent")) for item in records if item.get("agent"))
    success = outcomes.get("success", 0)
    partial = outcomes.get("partial", 0)
    failed = outcomes.get("failed", 0) + outcomes.get("blocked", 0)
    rolled = outcomes.get("rolled_back", 0) + sum(1 for item in records if item.get("rolled_back") is True)
    return {
        "n": n,
        "sufficient": n >= 5,
        "outcomes": dict(outcomes),
        "success": success,
        "partial": partial,
        "failed": failed,
        "success_rate": round(success / n, 4),
        "partial_rate": round(partial / n, 4),
        "failure_rate": round(failed / n, 4),
        "rollback_rate": round(rolled / n, 4),
        "duration_s": {
            "avg": round(sum(duration) / len(duration), 2) if duration else None,
            "p50": percentile(duration, 50) if len(duration) >= 5 else None,
            "p95": percentile(duration, 95) if len(duration) >= 5 else None,
            "n": len(duration),
        },
        "tokens": {
            "avg": round(sum(tokens) / len(tokens), 1) if tokens else None,
            "p50": percentile(tokens, 50) if len(tokens) >= 5 else None,
            "p95": percentile(tokens, 95) if len(tokens) >= 5 else None,
            "total": int(sum(tokens)) if tokens else None,
            "n": len(tokens),
        },
        "cost_usd": {
            "total": round(sum(costs), 6) if costs else None,
            "avg": round(sum(costs) / len(costs), 6) if costs else None,
            "per_success": round(sum(costs) / success, 6) if costs and success else None,
            "n": len(costs),
        },
        "quality": {
            "avg": round(sum(quality) / len(quality), 3) if quality else None,
            "n": len(quality),
        },
        "intervention_rate": round(sum(1 for x in interventions if x > 0) / len(interventions), 4) if interventions else None,
        "retry_rate": round(sum(1 for x in retries if x > 0) / len(retries), 4) if retries else None,
        "error_classes": dict(error_classes.most_common(10)),
        "top_tags": dict(tags.most_common(10)),
        "agents": dict(agents.most_common(10)),
        "window": f"{records[0].get('ts', '?')} .. {records[-1].get('ts', '?')}",
    }


def format_markdown(stats_data: dict[str, Any]) -> str:
    if stats_data.get("n", 0) == 0:
        return f"- 指标：{stats_data.get('message', '无数据')}\n"
    lines = [
        f"- 样本：{stats_data['n']} 个任务（{'充足' if stats_data.get('sufficient') else '不足，分位数仅供参考'}）",
        f"- 成功/部分/失败/回滚：{stats_data['success']} / {stats_data['partial']} / {stats_data['failed']} / {stats_data.get('rollback_rate', 0):.1%}",
        f"- 成功率：{stats_data['success_rate']:.1%}；失败率：{stats_data['failure_rate']:.1%}",
    ]
    dur = stats_data.get("duration_s", {})
    tok = stats_data.get("tokens", {})
    cost = stats_data.get("cost_usd", {})
    if dur.get("avg") is not None:
        lines.append(f"- 耗时：avg {dur['avg']}s，p50 {dur.get('p50') or '—'}，p95 {dur.get('p95') or '—'}")
    if tok.get("total") is not None:
        lines.append(f"- Token：total {tok['total']}，avg {tok.get('avg')}，p95 {tok.get('p95') or '—'}")
    if cost.get("total") is not None:
        lines.append(f"- 费用：total ${cost['total']:.4f}，每成功任务 ${cost.get('per_success') or 0:.4f}")
    if stats_data.get("intervention_rate") is not None:
        lines.append(f"- 人工干预率：{stats_data['intervention_rate']:.1%}；重试率：{(stats_data.get('retry_rate') or 0):.1%}")
    if stats_data.get("quality", {}).get("avg") is not None:
        lines.append(f"- 质量均分：{stats_data['quality']['avg']}/5")
    if stats_data.get("error_classes"):
        top = ", ".join(f"{k}={v}" for k, v in stats_data["error_classes"].items())
        lines.append(f"- 失败根因：{top}")
    return "\n".join(lines) + "\n"
