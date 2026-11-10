"""Minimal MCP stdio server (JSON-RPC 2.0, newline-delimited).

Tools are read-mostly; audit/review write local reports, record appends local
metrics. No tool executes third-party code.
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from . import __version__
from .audit import run_audit, write_audit
from .config import load_config
from .discover import discover_all
from .install import doctor
from .metrics import load as load_metrics
from .metrics import record as record_metric
from .metrics import stats as metrics_stats
from .paths import Paths, build_paths
from .recommend import recommend
from .review import run_review
from .schedule import schedule_status
from .util import now_iso, redact_secrets


TOOLS: list[dict[str, Any]] = [
    {
        "name": "evolution_status",
        "description": "Return evolution version, runtime paths, schedule state and counts.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "evolution_capabilities",
        "description": "Inventory local skills, MCP servers, plugins and dependency files.",
        "inputSchema": {"type": "object", "properties": {"agent": {"type": "string"}}, "additionalProperties": False},
    },
    {
        "name": "evolution_recommend",
        "description": "Recommend existing local capabilities for a task and flag capability gaps.",
        "inputSchema": {
            "type": "object",
            "properties": {"task": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
            "required": ["task"],
            "additionalProperties": False,
        },
    },
    {
        "name": "evolution_audit",
        "description": "Run a local environment/security/hygiene audit and write Markdown+JSON reports.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scopes": {"type": "array", "items": {"type": "string"}},
                "online": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "evolution_review",
        "description": "Run the weekly review (audit + metrics + upgrade candidates) and write reports. Read-only except reports/metrics.",
        "inputSchema": {
            "type": "object",
            "properties": {"week": {"type": "string"}, "online": {"type": "boolean"}, "days": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "evolution_record",
        "description": "Append one task result to the local metrics ledger.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "outcome": {"type": "string", "enum": ["success", "partial", "failed", "blocked", "rolled_back"]},
                "duration_s": {"type": "number"},
                "tokens": {"type": "integer"},
                "cost_usd": {"type": "number"},
                "tools": {"type": "integer"},
                "interventions": {"type": "integer"},
                "retries": {"type": "integer"},
                "quality": {"type": "number"},
                "error_class": {"type": "string"},
                "notes": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "agent": {"type": "string"},
                "model": {"type": "string"},
                "skill": {"type": "string"},
            },
            "required": ["task", "outcome"],
            "additionalProperties": False,
        },
    },
    {
        "name": "evolution_stats",
        "description": "Aggregate local task metrics for a recent window.",
        "inputSchema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 3650}},
            "additionalProperties": False,
        },
    },
    {
        "name": "evolution_doctor",
        "description": "Check installation, paths, rules, safety lock and schedule.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class Server:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths
        self.cfg = load_config(paths)
        self.initialized = False

    def tool_text(self, value: Any) -> dict[str, Any]:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        return {"content": [{"type": "text", "text": redact_secrets(text)[:120_000]}], "isError": False}

    def tool_error(self, message: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": message}], "isError": True}

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "evolution_status":
            data = discover_all(self.cfg, self.paths)
            return self.tool_text(
                {
                    "version": __version__,
                    "generated_at": now_iso(),
                    "paths": {
                        "skill": str(self.paths.skill_dir),
                        "runtime": str(self.paths.runtime_home),
                        "reports": str(self.paths.reports_dir),
                    },
                    "counts": {
                        "skills": data["skills"]["counts"]["skills"],
                        "valid_skills": data["skills"]["counts"]["valid"],
                        "mcp": len(data["mcp"]["servers"]),
                        "plugins": data["plugins"]["count"],
                    },
                    "schedule": schedule_status(self.paths, self.cfg),
                }
            )
        if name == "evolution_capabilities":
            data = discover_all(self.cfg, self.paths)
            return self.tool_text(
                {
                    "skills": [s.as_dict() for s in data["skills"]["skills"]],
                    "skill_roots": data["skills"]["roots"],
                    "mcp": data["mcp"]["servers"],
                    "plugins": data["plugins"]["plugins"],
                    "permissions": data["permissions"]["permissions"],
                    "runtime": data["runtime"],
                }
            )
        if name == "evolution_recommend":
            task = str(args.get("task", "")).strip()
            if not task:
                return self.tool_error("task is required")
            return self.tool_text(recommend(self.paths, self.cfg, task, limit=int(args.get("limit", 5))))
        if name == "evolution_audit":
            result = run_audit(self.paths, self.cfg, scopes=args.get("scopes"), online=bool(args.get("online", False)))
            md, js = write_audit(self.paths, result)
            return self.tool_text(
                {
                    "score": result["score"],
                    "severity_counts": result["severity_counts"],
                    "counts": result["counts"],
                    "top_actions": result["next_actions"],
                    "reports": {"markdown": str(md), "json": str(js)},
                }
            )
        if name == "evolution_review":
            result = run_review(
                self.paths,
                self.cfg,
                week=str(args.get("week", "")),
                online=bool(args.get("online", False)),
                days=int(args.get("days", 30)),
            )
            return self.tool_text(
                {
                    "week": result["week"],
                    "health": result["audit"]["score"],
                    "metrics": {k: result["metrics"].get(k) for k in ("n", "success_rate", "failure_rate", "cost_usd")},
                    "top_actions": result["audit"]["next_actions"][:3],
                    "reports": result["paths"],
                }
            )
        if name == "evolution_record":
            record = record_metric(
                self.paths,
                task=str(args.get("task", "")),
                outcome=str(args.get("outcome", "")),
                duration_s=args.get("duration_s"),
                tokens=args.get("tokens"),
                cost_usd=args.get("cost_usd"),
                tools=args.get("tools"),
                interventions=args.get("interventions"),
                retries=args.get("retries"),
                quality=args.get("quality"),
                error_class=str(args.get("error_class", "")),
                notes=str(args.get("notes", "")),
                tags=list(args.get("tags", []) or []),
                agent=str(args.get("agent", "")),
                model=str(args.get("model", "")),
                skill=str(args.get("skill", "")),
            )
            return self.tool_text({"recorded": record, "path": str(self.paths.metrics_file)})
        if name == "evolution_stats":
            data = metrics_stats(load_metrics(self.paths, days=int(args.get("days", 30))))
            return self.tool_text(data)
        if name == "evolution_doctor":
            return self.tool_text(doctor(self.paths, self.cfg))
        return self.tool_error(f"unknown tool: {name}")

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        msg_id = message.get("id")
        is_notification = "id" not in message
        params = message.get("params") or {}
        if method == "initialize":
            self.initialized = True
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "evolution", "version": __version__},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        if method == "tools/call":
            name = str(params.get("name", ""))
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": "arguments must be an object"}}
            try:
                result = self.call_tool(name, args)
            except Exception as exc:  # keep the server alive
                traceback.print_exc(file=sys.stderr)
                result = self.tool_error(f"{type(exc).__name__}: {exc}")
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        if is_notification:
            return None
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }


def serve(paths: Paths, stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = Server(paths)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        if not isinstance(message, dict):
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}) + "\n")
            stdout.flush()
            continue
        response = server.handle(message)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


def run_mcp(paths: Paths) -> int:
    return serve(paths)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_mcp(build_paths()))
