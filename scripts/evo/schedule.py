"""Weekly review scheduling: agent-native first, OS scheduler as fallback."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import get
from .paths import Paths
from .util import now_iso, read_json, run_cmd, write_json

CRON_BEGIN = "# EVOLUTION:BEGIN"
CRON_END = "# EVOLUTION:END"


def native_prompt(paths: Paths, cfg: dict[str, Any]) -> str:
    script = paths.skill_dir / "scripts" / "evolution.py"
    report_dir = paths.weekly_dir
    py = f'"{sys.executable}" "{script}"'
    return (
        "执行 evolution 每周环境体检、自我进化与断舍离。核心判据：环境/工具的陈旧与不一致"
        "会直接压低模型发挥，先修环境，再谈能力扩展。\n"
        f"1) 环境体检：{py} envcheck --online\n"
        "   - plugin_compat 为 FAIL：该插件已被宿主判定启动拒绝，升级到兼容版本或卸载；不要仅豁免了事。\n"
        "   - lock_drift 为 WARN：先 install 同步依赖，再评估其余结论。\n"
        f"2) 学习与自升级：{py} evolve --apply --bump patch --release --quiet\n"
        f"3) 维护与审阅：{py} review --apply --online --quiet\n"
        f"4) 插件升级：仅当同 major 且来源可信时执行 {py} plugins --apply --online；跨 major 只报告。升级后必须提示重启 DSH 才生效。\n"
        f"5) 断舍离：{py} cleanup --apply（缓存/过期报告/日志；备份默认保留，需显式 --include-backups）\n"
        f"6) 读取 {paths.state_dir / 'envcheck-latest.json'}、{paths.state_dir / 'evolution' / 'versions.json'}、"
        f"{paths.state_dir / 'maintenance-latest.json'} 与 {report_dir} 最新报告。\n"
        "7) 输出 ≤12 行摘要：环境分数与最差 3 项、自升级版本/学习规则、去重/隔离、插件、健康分、失败、下周动作。\n"
        "边界：付费、外发敏感数据、跨 major 升级、未备份删除、删除用户手写技能一律停止并报告；"
        "所有变更先 snapshot/backup，失败恢复；测试失败不得保留新版本；不得把未验证写成已通过。"
    )


def native_schedule(paths: Paths, cfg: dict[str, Any], *, apply: bool = False) -> dict[str, Any]:
    paths.ensure_runtime()
    prompt = native_prompt(paths, cfg)
    day = str(get(cfg, "review.day", "SUN"))
    time_value = str(get(cfg, "review.time", "20:00"))
    state = {
        "schema": 1,
        "driver": "native",
        "platform": "dsh",
        "created_at": now_iso(),
        "day": day,
        "time": time_value,
        "time_zone": "Asia/Shanghai",
        "prompt": prompt,
        "active": False,
        "note": "由 Agent 原生调度器创建；Python 侧只记录提示，不直接激活。",
    }
    if apply:
        write_json(paths.schedule_state, state)
        md = paths.runtime_home / "state" / "native-schedule.md"
        md.write_text(
            "# Agent 原生每周审阅\n\n"
            f"- 建议时间：{day} {time_value}（Asia/Shanghai）\n"
            "- 在 DSH 中调用 `schedule_create`，title=`evolution 全局审阅`，weekly 使用上面的时间。\n"
            "- 其他 Agent 使用 `templates/NATIVE-AUTOMATION.md` 的提示文本。\n\n"
            "## Prompt\n\n```text\n" + prompt + "\n```\n",
            encoding="utf-8",
            newline="\n",
        )
    return state


def confirm_native_schedule(
    paths: Paths,
    cfg: dict[str, Any],
    *,
    schedule_id: str,
    scheduled_at: str = "",
    time_zone: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """Record that the agent-native reminder was actually created."""
    paths.ensure_runtime()
    state = read_json(paths.schedule_state, default=None)
    if not isinstance(state, dict) or state.get("driver") != "native":
        state = native_schedule(paths, cfg, apply=False)
    state.update(
        {
            "active": True,
            "agent_schedule_id": schedule_id,
            "scheduled_at": scheduled_at,
            "time_zone": time_zone,
            "confirmed_at": now_iso(),
            "note": "已由 Agent 原生调度器创建；Python 侧仅记录引用。",
        }
    )
    write_json(paths.schedule_state, state)
    return state


def _os_supported() -> bool:
    return os.name == "nt" or os.name == "posix"


def _task_name(cfg: dict[str, Any]) -> str:
    return str(get(cfg, "review.task_name", "DSH-Evolution-WeeklyReview"))


def _weekly_cmd(paths: Paths, cfg: dict[str, Any]) -> Path:
    paths.ensure_runtime()
    script = paths.skill_dir / "scripts" / "evolution.py"
    log = paths.logs_dir / "weekly.log"
    python = sys.executable
    pythonw = Path(python).with_name("pythonw.exe")
    if pythonw.exists():
        python = str(pythonw)
    cmd_path = paths.bin_dir / "weekly.cmd"
    content = (
        "@echo off\r\n"
        f'"{python}" "{script}" improve --apply --online --quiet >> "{log}" 2>&1\r\n'
    )
    cmd_path.write_text(content, encoding="utf-8", newline="")
    return cmd_path


def _schtasks_query(name: str) -> tuple[bool, str]:
    code, out, err = run_cmd(["schtasks", "/Query", "/TN", name], timeout=15)
    if code != 0:
        return False, "not installed"
    detail = (out or err).strip().splitlines()[0] if (out or err).strip() else "installed"
    return True, detail


def _os_install(paths: Paths, cfg: dict[str, Any], *, apply: bool) -> dict[str, Any]:
    day = str(get(cfg, "review.day", "SUN")).upper()
    time_value = str(get(cfg, "review.time", "20:00"))
    if os.name == "nt":
        name = _task_name(cfg)
        cmd = paths.bin_dir / "weekly.cmd"
        if apply:
            cmd = _weekly_cmd(paths, cfg)
        args = [
            "schtasks", "/Create", "/TN", name,
            "/TR", f'"{cmd}"',
            "/SC", "WEEKLY", "/D", day, "/ST", time_value, "/F",
        ]
        if not apply:
            return {"driver": "os", "supported": True, "installed": False, "dry_run": True, "command": args, "cmd": str(cmd)}
        code, out, err = run_cmd(args, timeout=30)
        installed, detail = _schtasks_query(name)
        state = {
            "schema": 1,
            "driver": "os",
            "platform": "windows",
            "task_name": name,
            "command": args,
            "cmd": str(cmd),
            "created_at": now_iso(),
            "installed": installed,
            "last_output": (out or err)[-2000:],
        }
        write_json(paths.schedule_state, state)
        return {**state, "detail": detail, "exit_code": code}
    # POSIX cron
    script = paths.skill_dir / "scripts" / "evolution.py"
    line = f'{time_value} * * {_cron_dow(day)} * "{sys.executable}" "{script}" improve --apply --online --quiet'
    block = f"{CRON_BEGIN}\n{line}\n{CRON_END}\n"
    current = _crontab_get()
    has = CRON_BEGIN in current
    if not apply:
        return {"driver": "os", "supported": True, "installed": has, "dry_run": True, "cron_line": line}
    if has:
        return {"driver": "os", "supported": True, "installed": True, "detail": "cron block already present"}
    _crontab_set(current.rstrip("\n") + "\n" + block)
    installed = CRON_BEGIN in _crontab_get()
    state = {"schema": 1, "driver": "os", "platform": "posix", "cron_line": line, "created_at": now_iso(), "installed": installed}
    write_json(paths.schedule_state, state)
    return {**state, "detail": "cron block installed" if installed else "cron install failed"}


def _cron_dow(day: str) -> str:
    mapping = {"MON": "1", "TUE": "2", "WED": "3", "THU": "4", "FRI": "5", "SAT": "6", "SUN": "0"}
    return mapping.get(day.upper(), "0")


def _crontab_get() -> str:
    code, out, _err = run_cmd(["crontab", "-l"], timeout=10)
    return out if code == 0 else ""


def _crontab_set(text: str) -> None:
    subprocess.run(["crontab", "-"], input=text, text=True, encoding="utf-8", errors="replace", timeout=15)


def _os_remove(paths: Paths, cfg: dict[str, Any], *, apply: bool) -> dict[str, Any]:
    if os.name == "nt":
        name = _task_name(cfg)
        installed, detail = _schtasks_query(name)
        if not installed:
            return {"driver": "os", "supported": True, "installed": False, "detail": "task not found"}
        if not apply:
            return {"driver": "os", "supported": True, "installed": True, "dry_run": True, "command": ["schtasks", "/Delete", "/TN", name, "/F"]}
        code, out, err = run_cmd(["schtasks", "/Delete", "/TN", name, "/F"], timeout=20)
        return {"driver": "os", "supported": True, "installed": False, "detail": (out or err)[-500:], "exit_code": code}
    current = _crontab_get()
    lines = current.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == CRON_BEGIN:
            skipping = True
            continue
        if line.strip() == CRON_END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    if not apply:
        return {"driver": "os", "supported": True, "installed": CRON_BEGIN in current, "dry_run": True}
    _crontab_set("\n".join(out).rstrip("\n") + "\n")
    return {"driver": "os", "supported": True, "installed": False, "detail": "cron block removed"}


def schedule_status(paths: Paths, cfg: dict[str, Any]) -> dict[str, Any]:
    driver = str(get(cfg, "review.driver", "native")).lower()
    native = read_json(paths.schedule_state, default=None)
    native_active = bool(isinstance(native, dict) and native.get("driver") == "native" and native.get("active") is True)
    native_recorded = bool(isinstance(native, dict) and native.get("driver") == "native")
    if os.name == "nt":
        installed, detail = _schtasks_query(_task_name(cfg))
        os_info = {"supported": True, "installed": installed, "detail": detail}
    elif os.name == "posix":
        cron = _crontab_get()
        os_info = {"supported": True, "installed": CRON_BEGIN in cron, "detail": "cron block present" if CRON_BEGIN in cron else "not installed"}
    else:
        os_info = {"supported": False, "installed": False, "detail": "unsupported platform"}
    return {
        "supported": True,
        "driver": driver,
        "native_recorded": native_recorded,
        "native_active": native_active,
        "installed": native_active or bool(os_info.get("installed")),
        "native": native if native_recorded else None,
        "os": os_info,
        "detail": (
            "Agent 原生调度已确认。"
            if driver == "native" and native_active
            else (
                "Agent 原生调度已记录；请在 Agent 内创建周任务（schedule_create）。"
                if driver == "native" and native_recorded
                else ("Agent 原生调度未记录；运行 schedule native --apply。" if driver == "native" else os_info.get("detail", ""))
            )
        ),
    }


def install_schedule(
    paths: Paths,
    cfg: dict[str, Any],
    *,
    driver: str = "native",
    apply: bool = False,
    time_value: str | None = None,
    day: str | None = None,
) -> dict[str, Any]:
    driver = (driver or "native").lower()
    if time_value:
        cfg.setdefault("review", {})["time"] = time_value
    if day:
        cfg.setdefault("review", {})["day"] = day
    if driver == "native":
        return native_schedule(paths, cfg, apply=apply)
    if driver == "os":
        return _os_install(paths, cfg, apply=apply)
    raise ValueError("driver must be native or os")


def remove_schedule(paths: Paths, cfg: dict[str, Any], *, driver: str = "native", apply: bool = False) -> dict[str, Any]:
    driver = (driver or "native").lower()
    if driver == "native":
        if apply and paths.schedule_state.exists():
            data = read_json(paths.schedule_state, default={})
            if isinstance(data, dict) and data.get("driver") == "native":
                paths.schedule_state.unlink()
        return {"driver": "native", "removed": bool(apply), "detail": "Python 侧记录已移除；Agent 内的 schedule_create 需在平台侧删除。"}
    if driver == "os":
        return _os_remove(paths, cfg, apply=apply)
    raise ValueError("driver must be native or os")
