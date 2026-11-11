"""Evidence gates for promoting a better skill variant.

The optimizer may propose any text change. Promotion is decided by measured
rollouts, a held-out split, and preservation of the skill's declared
functions. The module stays independent of a model provider so Codex, Claude,
or a local harness can supply scores in the same JSON shape.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any

from .paths import Paths
from .snapshot import create_snapshot
from .util import append_jsonl, now_iso, now_stamp, read_json, write_json


def _scores(value: Any) -> list[float]:
    if not isinstance(value, list) or not value:
        raise ValueError("scores must be a non-empty list")
    return [float(item) for item in value]


def _mean(values: list[float]) -> float:
    return round(fmean(values), 4)


def _overlap(before: Any, after: Any) -> float:
    old = {str(item).strip() for item in (before or []) if str(item).strip()}
    new = {str(item).strip() for item in (after or []) if str(item).strip()}
    if not old:
        return 1.0
    return round(len(old & new) / len(old), 4)


def evaluate_candidate(payload: dict[str, Any], *, min_effect: float = 3.0, max_sd: float = 2.0, min_overlap: float = 0.8) -> dict[str, Any]:
    train_before = _scores(payload.get("train_before"))
    train_after = _scores(payload.get("train_after"))
    holdout_before = _scores(payload.get("holdout_before"))
    holdout_after = _scores(payload.get("holdout_after"))
    baseline_sd = round(pstdev(train_before), 4)
    threshold = round(max(float(min_effect), 2 * baseline_sd), 4)
    train_delta = round(_mean(train_after) - _mean(train_before), 4)
    holdout_delta = round(_mean(holdout_after) - _mean(holdout_before), 4)
    function_overlap = _overlap(payload.get("functions_before"), payload.get("functions_after"))
    gates = {
        "stable_baseline": baseline_sd <= float(max_sd),
        "train_effect": train_delta >= threshold,
        "holdout_effect": holdout_delta >= 0.0,
        "function_preservation": function_overlap >= float(min_overlap),
    }
    return {
        "schema": 1,
        "kind": "skill-evaluation",
        "generated_at": now_iso(),
        "skill": str(payload.get("skill") or ""),
        "candidate": str(payload.get("candidate") or ""),
        "target": str(payload.get("target") or ""),
        "baseline": {"train": train_before, "holdout": holdout_before, "train_mean": _mean(train_before), "holdout_mean": _mean(holdout_before), "sd": baseline_sd},
        "candidate_scores": {"train": train_after, "holdout": holdout_after, "train_mean": _mean(train_after), "holdout_mean": _mean(holdout_after)},
        "deltas": {"train": train_delta, "holdout": holdout_delta, "required": threshold},
        "function_overlap": function_overlap,
        "gates": gates,
        "accepted": all(gates.values()),
        "reason": "accepted" if all(gates.values()) else "; ".join(name for name, passed in gates.items() if not passed),
    }


def promote(paths: Paths, report: dict[str, Any], *, apply: bool = False) -> dict[str, Any]:
    candidate = Path(str(report.get("candidate") or ""))
    target = Path(str(report.get("target") or ""))
    result = {"applied": False, "candidate": str(candidate), "target": str(target), "snapshot": ""}
    if not report.get("accepted"):
        result["error"] = "evaluation gates rejected the candidate"
        return result
    if not candidate.is_file() or not target:
        result["error"] = "candidate file or target path is missing"
        return result
    if not apply:
        result["dry_run"] = True
        return result
    snapshot = create_snapshot(paths, label="pre-skill-promotion")
    backup = ""
    if target.exists():
        backup_path = paths.backups_dir / f"promotion-{now_stamp()}" / target.name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_path)
        backup = str(backup_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate, target)
    result.update({"applied": True, "snapshot": snapshot["id"], "backup": backup})
    return result


def record_evaluation(paths: Paths, report: dict[str, Any], promotion: dict[str, Any]) -> dict[str, Any]:
    row = {**report, "promotion": promotion}
    append_jsonl(paths.evaluations_file, row)
    write_json(paths.state_dir / "evolution" / "evaluation-latest.json", row)
    return row


def load_evaluation(path: str | Path) -> dict[str, Any]:
    data = read_json(Path(path), default=None)
    if not isinstance(data, dict):
        raise ValueError(f"evaluation input must be a JSON object: {path}")
    return data
