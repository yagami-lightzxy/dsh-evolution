"""Environment currency check ("环境年代感" 体检).

Why this exists: an agent is only as good as the environment it runs in. A
stale runtime, a plugin the host refuses to load, a model route pinned to
yesterday's settings, or a broken bundle edge all quietly cap what the model can
do -- and none of that shows up in a task-level review. This module looks for
exactly that class of friction: environment or tooling that is older, emptier or
more inconsistent than the model needs.

Everything here is read-only and offline by default. online=True only adds
version/EOL lookups against public registries; no local content is uploaded.

The parser for cordis.patch.yml is deliberately bounded (indentation-based entry
splitting plus scoped key regexes). It is not a YAML implementation; it
understands the shapes the DSH profile actually uses and reports skip rather
than guessing when a shape is not recognized.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .config import get
from .paths import Paths
from .skill_health import run_skill_health
from .util import (
    md_cell,
    now_iso,
    now_stamp,
    read_json,
    read_text,
    run_cmd,
    write_json,
)

# ---------------------------------------------------------------------------
# Baselines (offline). Refreshed by --online from endoflife.date.
# ---------------------------------------------------------------------------

SEVERITY_WEIGHT = {"critical": 30, "high": 15, "medium": 6, "low": 2, "info": 0}

#: Upstream end-of-life dates. A minor release past EOL gets no security fixes.
PYTHON_EOL = {
    "3.8": "2024-10-07",
    "3.9": "2025-10-31",
    "3.10": "2026-10-31",
    "3.11": "2027-10-31",
    "3.12": "2028-10-31",
    "3.13": "2029-10-31",
    "3.14": "2030-10-31",
}
NODE_EOL = {
    "16": "2023-09-11",
    "18": "2025-04-30",
    "20": "2026-04-30",
    "22": "2027-04-30",
    "24": "2028-04-30",
    "26": "2029-04-30",
}

#: Route settings that leave model capability unused. Advisory, never enforced.
WEAK_EFFORT = {"", "low", "minimal", "none", "off"}

#: Registry endpoints.
NPM_REGISTRY = "https://registry.npmjs.org"
EOL_API = "https://endoflife.date/api"
USER_AGENT = f"evolution/{__version__} (+agent-envcheck)"

#: Project marker -> tool that must exist. Carried over from the Codex-era skill,
#: which inferred missing tooling from what the workspace actually contains.
DEFAULT_TOOLCHAIN = [
    {"marker": "package.json", "command": "node", "install_hint": "winget install OpenJS.NodeJS.LTS"},
    {"marker": "pyproject.toml", "command": "python", "install_hint": "winget install Python.Python.3.12"},
    {"marker": "requirements.txt", "command": "python", "install_hint": "winget install Python.Python.3.12"},
    {"marker": ".git", "command": "git", "install_hint": "winget install Git.Git"},
]

CHECK_ORDER = [
    "dsh_cli",
    "dsh_runtime",
    "plugin_tree",
    "plugin_compat",
    "plugin_channel",
    "lock_drift",
    "model_route",
    "web_tools",
    "runtime_eol",
    "toolchain",
    "skill_health",
    "skill_shadow",
    "heartbeat",
    "hygiene",
]


def _add(
    findings: list[dict[str, Any]],
    check: str,
    *,
    severity: str,
    title: str,
    evidence: str,
    recommendation: str,
    path: str = "",
    auto_fixable: bool = False,
) -> None:
    """Append one finding. severity=info documents context without scoring."""
    findings.append(
        {
            "id": f"{check}-{len([f for f in findings if f['check'] == check]) + 1}",
            "check": check,
            "severity": severity,
            "title": title,
            "evidence": evidence[:600],
            "recommendation": recommendation,
            "path": path,
            "auto_fixable": bool(auto_fixable),
        }
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _semver(value: str) -> tuple[int, int, int] | None:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", str(value or "").strip())
    if not match:
        return None
    return tuple(int(x) for x in match.groups())  # type: ignore[return-value]


def _age_days(stamp: float) -> float | None:
    if not stamp:
        return None
    return max(0.0, (time.time() - stamp) / 86400.0)


def _dir_size(path: Path, *, max_files: int = 20000) -> int:
    """Best-effort recursive size; bounded so a huge tree cannot stall a review."""
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    seen = 0
    for child in path.rglob("*"):
        seen += 1
        if seen > max_files:
            break
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def _dsh_home(paths: Paths) -> Path:
    return paths.os_home / ".dsh"


def _profile_dir(paths: Paths, cfg: dict[str, Any]) -> Path:
    name = str(get(cfg, "paths.dsh_profile", "desktop") or "desktop")
    return _dsh_home(paths) / "profiles" / name


def _find_dsh_cli(paths: Paths, cfg: dict[str, Any]) -> tuple[Path | None, str]:
    """Locate the dsh launcher. Returns (path, how).

    The desktop app ships the CLI under resources/runtime/cli/bin; it is not on
    PATH. Users may pin it with paths.dsh_cli or the DSH_CLI env var.
    """
    explicit = os.environ.get("DSH_CLI") or str(get(cfg, "paths.dsh_cli", "") or "")
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.exists():
            return candidate, "config"
        # An explicit path that does not exist is authoritative: fall through to
        # some other binary would hide the misconfiguration and make the check
        # machine-dependent instead of reproducible.
        return None, "config-missing"
    which = shutil.which("dsh")
    if which:
        return Path(which), "path"
    roots: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Programs" / "DeepSeek Harness" / "resources" / "runtime" / "cli" / "bin")
    if sys.platform.startswith("win"):
        for base in (
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
        ):
            roots.append(base / "DeepSeek Harness" / "resources" / "runtime" / "cli" / "bin")
    else:
        roots.append(Path("/usr/local/bin"))
    for root in roots:
        for name in ("dsh.cmd", "dsh.exe", "dsh"):
            candidate = root / name
            if candidate.exists():
                return candidate, "app-runtime"
    return None, "missing"


def _run_dsh(cli: Path, args: list[str], *, timeout: float = 90.0, cwd: Path | None = None) -> tuple[int, str, str]:
    """Run the dsh launcher, handling Windows .cmd shims without a shell."""
    argv = [str(cli), *args]
    if sys.platform.startswith("win") and cli.suffix.lower() in {".cmd", ".bat"}:
        argv = ["cmd", "/c", *argv]
    return run_cmd(argv, timeout=timeout, cwd=cwd)


def _http_json(url: str, *, timeout: float = 15.0) -> Any:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed hosts
        return json.loads(response.read().decode("utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# Bounded patch-file parser
# ---------------------------------------------------------------------------


def parse_patch_entries(text: str) -> list[dict[str, str]]:
    """Split a cordis patch list into entries.

    Returns [{"id","name","raw"}]. Only top-level "- " items are entries; nested
    config stays inside raw for scoped key lookups.
    """
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.replace("\r\n", "\n").split("\n"):
        if re.match(r"^\s*#", line) or not line.strip():
            continue
        if re.match(r"^- ", line):
            current = {"id": "", "name": "", "raw": line + "\n"}
            entries.append(current)
            head = line[2:]
            match = re.match(r"id:\s*[\"']?([^\"'\s#]+)", head)
            if match:
                current["id"] = match.group(1)
            match = re.match(r"name:\s*[\"']?([^\"'\n#]+)", head)
            if match:
                current["name"] = match.group(1).strip().strip("\"'")
            continue
        if current is not None:
            current["raw"] += line + "\n"
            match = re.match(r"^\s{2}name:\s*[\"']?([^\"'\n#]+)", line)
            if match and not current["name"]:
                current["name"] = match.group(1).strip().strip("\"'")
    return entries


def entry_config(entry: dict[str, str] | None, key: str) -> str:
    """Read "key: value" inside one patch entry's config subtree."""
    if not entry:
        return ""
    match = re.search(rf"^\s+{re.escape(key)}:\s*[\"']?([^\s\"'#]+)", entry["raw"], re.M)
    return match.group(1).strip() if match else ""


def _app_runtime_dir(cli: Path | None) -> Path | None:
    """Derive <app>/resources/runtime from the dsh launcher path."""
    if cli is None:
        return None
    for parent in [cli, *cli.parents]:
        candidate = parent / "primary-runtime" / "dependencies"
        if candidate.is_dir():
            return candidate
    return None


def expected_store_dir(pnpm_version: str) -> str:
    """pnpm N stores content under a vN directory (pnpm 10 -> v10, 11 -> v11)."""
    match = re.match(r"^(\d+)", str(pnpm_version or "").strip())
    return f"v{match.group(1)}" if match else ""


def store_mismatch(modules_yaml: str, pnpm_version: str) -> tuple[str, str] | None:
    """Return (actual, expected) store dirs when the profile predates this pnpm."""
    expected = expected_store_dir(pnpm_version)
    if not expected:
        return None
    match = re.search(r'"storeDir"\s*:\s*"([^"]+)"', modules_yaml or "")
    if not match:
        return None
    actual = Path(match.group(1).replace("\\", os.sep)).name
    if not actual or actual == expected:
        return None
    return actual, expected


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_dsh_cli(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any]) -> None:
    cli, how = _find_dsh_cli(paths, cfg)
    ctx["dsh"]["cli"] = str(cli) if cli else ""
    ctx["dsh"]["cli_source"] = how
    if cli is None:
        ctx["checks"]["dsh_cli"] = {"status": "skip", "detail": "未找到 dsh 启动器"}
        _add(
            ctx["findings"],
            "dsh_cli",
            severity="medium",
            title="找不到 dsh 启动器：无法核对插件与运行时的兼容性",
            evidence="dsh 不在 PATH，且未发现应用内 CLI；插件树/兼容性检查降级为静态解析。",
            recommendation="在 config.json 的 paths.dsh_cli 指向 DSH 应用的 resources/runtime/cli/bin/dsh.cmd。",
        )
        return
    code, out, err = _run_dsh(cli, ["--version"], timeout=60)
    blob = (out or err or "").strip()
    version = blob.splitlines()[0].strip() if blob else ""
    ctx["dsh"]["version"] = version
    if code != 0 or not re.search(r"\d+\.\d+", version):
        ctx["checks"]["dsh_cli"] = {"status": "warn", "detail": f"dsh --version 返回 {code}: {version[:80]}"}
        _add(
            ctx["findings"],
            "dsh_cli",
            severity="low",
            title="dsh --version 未能返回可用版本",
            evidence=f"exit={code} out={version[:120]!r} err={err.strip()[:120]!r}",
            recommendation="手动执行 dsh --version 确认启动器可用，再重跑 envcheck。",
            path=str(cli),
        )
        return
    ctx["checks"]["dsh_cli"] = {"status": "ok", "detail": f"{version} ({how})"}


def _check_dsh_runtime(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any]) -> None:
    version = str(ctx["dsh"].get("version") or "")
    core = _semver(version)
    if not core:
        ctx["checks"]["dsh_runtime"] = {"status": "skip", "detail": "无运行时版本"}
        return
    rc = str(get(cfg, "paths.dsh_runtime_baseline", "") or "")
    baseline = _semver(rc) if rc else None
    lowered = version.lower()
    if "rc" in lowered or "beta" in lowered or "alpha" in lowered:
        ctx["checks"]["dsh_runtime"] = {"status": "warn", "detail": f"DSH {version}（预发布通道）"}
        _add(
            ctx["findings"],
            "dsh_runtime",
            severity="info",
            title="DSH 运行在预发布版本上",
            evidence=f"dsh --version 返回 {version}",
            recommendation="预发布版功能新但稳定性未承诺；出现工具异常时先怀疑版本，而不是模型能力。",
        )
    else:
        ctx["checks"]["dsh_runtime"] = {"status": "ok", "detail": f"DSH {version}"}
    if baseline and core < baseline:
        _add(
            ctx["findings"],
            "dsh_runtime",
            severity="medium",
            title=f"DSH 版本低于期望基线 {rc}",
            evidence=f"当前 {version} < 基线 {rc}",
            recommendation="升级 DSH 应用；旧宿主常常缺少新工具与修复。",
        )


def _check_plugin_tree(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any]) -> None:
    profile = _profile_dir(paths, cfg)
    ctx["profile"]["path"] = str(profile)
    package = profile / "package.json"
    data = read_json(package, default=None)
    if not isinstance(data, dict):
        ctx["checks"]["plugin_tree"] = {"status": "skip", "detail": "未找到 profile package.json"}
        _add(
            ctx["findings"],
            "plugin_tree",
            severity="medium",
            title="未找到 DSH profile 的 package.json",
            evidence=str(package),
            recommendation="确认 paths.dsh_profile 指向真实 profile；否则插件与技能盘点都会失真。",
            path=str(package),
        )
        return
    deps = data.get("dependencies") or {}
    bundles = ((data.get("dsh") or {}).get("profile") or {}).get("bundles") or []
    ctx["profile"]["name"] = str(data.get("name") or "")
    ctx["profile"]["dependencies"] = len(deps)
    ctx["profile"]["bundles"] = len(bundles)
    node_modules = profile / "node_modules"

    missing: list[str] = []
    broken_links: list[str] = []
    unpinned_github: list[str] = []
    drifting_specs: list[str] = []
    for name, spec in deps.items():
        spec = str(spec)
        target = node_modules / name
        if spec.startswith("link:"):
            link_target = Path(spec[5:]).expanduser()
            if not link_target.is_absolute():
                link_target = (profile / link_target).resolve()
            if not link_target.exists():
                broken_links.append(f"{name} -> {link_target}")
            elif not target.exists():
                missing.append(f"{name} (link target exists, node_modules entry missing)")
        elif not target.exists():
            missing.append(name)
        if spec.startswith("github:") or spec.startswith("git+"):
            if "#" not in spec:
                unpinned_github.append(name)
        elif re.match(r"^[\^~]|^\*$|^latest$", spec):
            drifting_specs.append(f"{name}@{spec}")

    orphan_bundles = [b for b in bundles if b not in deps and not str(b).startswith("@deepseek-ai/dsh-")]
    if orphan_bundles:
        _add(
            ctx["findings"],
            "plugin_tree",
            severity="high",
            title=f"{len(orphan_bundles)} 个 bundle 声明没有对应依赖",
            evidence=", ".join(map(str, orphan_bundles[:10])),
            recommendation="补装依赖或从 dsh.profile.bundles 移除；否则 profile 启动时解析失败。",
            path=str(package),
        )
    if missing:
        _add(
            ctx["findings"],
            "plugin_tree",
            severity="high",
            title=f"{len(missing)} 个依赖已声明但未安装",
            evidence=", ".join(missing[:10]),
            recommendation="运行 dsh plugin --profile <p> install 补齐；缺失依赖会在启动时被静默跳过。",
            path=str(node_modules),
            auto_fixable=True,
        )
    if broken_links:
        _add(
            ctx["findings"],
            "plugin_tree",
            severity="critical",
            title=f"{len(broken_links)} 个本地链接依赖指向不存在的路径",
            evidence="; ".join(broken_links[:5]),
            recommendation="修复 link 目标或改成已发布的版本；断链插件等于永久缺席。",
            path=str(package),
        )
    if unpinned_github:
        _add(
            ctx["findings"],
            "plugin_tree",
            severity="medium",
            title=f"{len(unpinned_github)} 个 GitHub 依赖未固定提交/标签",
            evidence=", ".join(unpinned_github[:8]),
            recommendation="固定到 tag 或 commit；未固定时同一 profile 重装会拿到不同代码。",
            path=str(package),
        )

    installed_plugins: list[str] = []
    if node_modules.is_dir():
        for entry in sorted(node_modules.iterdir()):
            candidates = [entry]
            if entry.name.startswith("@") and entry.is_dir():
                candidates = [c for c in sorted(entry.iterdir()) if c.is_dir()]
            for cand in candidates:
                manifest = read_json(cand / "package.json", default=None)
                if isinstance(manifest, dict) and (manifest.get("dsh") or {}).get("bundle"):
                    installed_plugins.append(str(manifest.get("name") or cand.name))
    not_bundled = sorted(set(installed_plugins) - {str(b) for b in bundles})
    if not_bundled:
        # Deliberately info, not medium: bundles is only one composition layer.
        # Client-only plugins and Electron-injected entries legitimately live
        # outside it, and --dump-config is refused on app-managed profiles. A
        # weekly task must not be nudged into uninstalling a working plugin.
        _add(
            ctx["findings"],
            "plugin_tree",
            severity="info",
            title=f"{len(not_bundled)} 个已安装插件未出现在 profile bundles（需人工确认是否由客户端层加载）",
            evidence=", ".join(not_bundled[:10]),
            recommendation="先在 GUI 确认这些插件确实生效；确认未生效时再装入 bundles 或卸载。不要仅凭本项自动卸载。",
            path=str(package),
        )
    ctx["profile"]["installed_plugins"] = len(installed_plugins)
    ctx["profile"]["drifting_specs"] = drifting_specs
    status = "ok"
    if orphan_bundles or missing or broken_links:
        status = "fail"
    elif unpinned_github or not_bundled:
        status = "warn"
    ctx["checks"]["plugin_tree"] = {
        "status": status,
        "detail": f"依赖 {len(deps)} · bundles {len(bundles)} · 已装插件 {len(installed_plugins)}",
    }


def _check_plugin_compat(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any]) -> None:
    """Ask the host which plugins it refuses to load.

    This is the highest-signal check in the module: an incompatible plugin stays
    on disk and looks installed, but the host denies it at startup -- the exact
    "old tooling silently capping the model" failure mode.
    """
    cli = Path(ctx["dsh"]["cli"]) if ctx["dsh"].get("cli") else None
    profile_name = _profile_dir(paths, cfg).name
    if cli is None:
        ctx["checks"]["plugin_compat"] = {"status": "skip", "detail": "无 dsh 启动器"}
        return
    code, out, err = _run_dsh(cli, ["plugin", "--profile", profile_name, "list"], timeout=120)
    blob = (out or "") + "\n" + (err or "")
    if code != 0 and not blob.strip():
        ctx["checks"]["plugin_compat"] = {"status": "warn", "detail": f"dsh plugin list 失败 exit={code}"}
        return
    seen: set[tuple[str, str]] = set()
    pattern = (
        r"Plugin\s+(?P<name>@?[^\s@]+(?:/[^\s@]+)?)@(?P<ver>[0-9][^\s]*)\s+"
        r"is incompatible with dsh\s+(?P<dsh>[^\s:]+):\s*requirements\s+(?P<req>\{[^}]*\})"
    )
    for match in re.finditer(pattern, blob):
        name, ver, dsh_ver, req = match.group("name"), match.group("ver"), match.group("dsh"), match.group("req")
        if (name, ver) in seen:
            continue
        seen.add((name, ver))
        _add(
            ctx["findings"],
            "plugin_compat",
            severity="high",
            title=f"插件 {name}@{ver} 与 DSH {dsh_ver} 不兼容，启动时被拒绝加载",
            evidence=f"宿主自述 requirements {req}；该插件保持安装，但 profile 启动时 denies。",
            recommendation=(
                f"升级到兼容 DSH {dsh_ver} 的版本，或 dsh plugin --profile {profile_name} remove {name} 卸下；"
                "确需保留时用 dsh plugin allow-version 显式豁免并记录理由。"
            ),
        )
    if seen:
        ctx["checks"]["plugin_compat"] = {"status": "fail", "detail": f"{len(seen)} 个插件被宿主判定不兼容"}
    elif "startup denies it" in blob:
        ctx["checks"]["plugin_compat"] = {"status": "fail", "detail": "宿主报告 startup denies（未解析出插件名）"}
    else:
        ctx["checks"]["plugin_compat"] = {"status": "ok", "detail": "未发现不兼容插件"}

    packages: dict[str, str] = {}
    for line in (out or "").splitlines():
        match = re.search(r"[├└│─\s]+(@?[\w./@-]+)@([0-9][\w.\-+]*)", line)
        if match:
            packages[match.group(1)] = match.group(2)
    ctx["profile"]["installed_versions"] = packages


def _check_plugin_channel(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any]) -> None:
    """Can the host actually write new plugins?

    A profile installed by an older pnpm has a content-addressed store the
    bundled pnpm refuses to reuse (ERR_PNPM_UNEXPECTED_STORE). Nothing else in
    the audit notices: the tree looks complete, upgrades simply fail at the last
    moment. Detecting it here means "auto-upgrade" stops lying.
    """
    profile = _profile_dir(paths, cfg)
    modules = profile / "node_modules" / ".modules.yaml"
    cli = Path(ctx["dsh"]["cli"]) if ctx["dsh"].get("cli") else None
    if not modules.is_file() or cli is None:
        ctx["checks"]["plugin_channel"] = {"status": "skip", "detail": "缺少 .modules.yaml 或无启动器"}
        return
    runtime = _app_runtime_dir(cli)
    if runtime is None:
        ctx["checks"]["plugin_channel"] = {"status": "skip", "detail": "未找到应用运行时目录"}
        return
    node = runtime / "node" / "bin" / "node.exe"
    if not node.is_file():
        node = runtime / "node" / "bin" / "node"
    pnpm = runtime / "pnpm" / "bin" / "pnpm.mjs"
    if not node.is_file() or not pnpm.is_file():
        ctx["checks"]["plugin_channel"] = {"status": "skip", "detail": "未找到内置 node/pnpm"}
        return
    code, out, _err = run_cmd([str(node), str(pnpm), "--version"], timeout=30)
    version = (out or "").strip()
    if code != 0 or not version:
        ctx["checks"]["plugin_channel"] = {"status": "skip", "detail": "内置 pnpm 版本不可得"}
        return
    ctx["pnpm"] = {"version": version, "node": str(node)}
    mismatch = store_mismatch(read_text(modules, max_bytes=256 * 1024), version)
    if mismatch:
        actual, expected = mismatch
        ctx["checks"]["plugin_channel"] = {
            "status": "fail",
            "detail": f"store {actual} != 内置 pnpm 需要的 {expected}",
        }
        _add(
            ctx["findings"],
            "plugin_channel",
            severity="high",
            title="插件安装/升级通道不可用：pnpm store 版本不匹配",
            evidence=(
                f"profile 的 node_modules 来自 store {actual}，内置 pnpm {version} 要求 {expected}；"
                "直接执行 dsh plugin add/remove 会以 ERR_PNPM_UNEXPECTED_STORE 失败。"
            ),
            recommendation=(
                "确认可接受重装后执行 dsh plugin --profile <p> install 迁移 store（会重建 node_modules），"
                "完成后再做插件升级；在此之前「自动升级插件」实际不可用，不要报告为已完成。"
            ),
            path=str(profile),
            auto_fixable=False,
        )
    else:
        ctx["checks"]["plugin_channel"] = {"status": "ok", "detail": f"store 与 pnpm {version} 匹配"}


def _check_lock_drift(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any]) -> None:
    profile = _profile_dir(paths, cfg)
    package = profile / "package.json"
    lock = profile / "pnpm-lock.yaml"
    modules = profile / "node_modules" / ".modules.yaml"
    if not package.is_file():
        ctx["checks"]["lock_drift"] = {"status": "skip", "detail": "无 package.json"}
        return
    try:
        pkg_mtime = package.stat().st_mtime
    except OSError:
        ctx["checks"]["lock_drift"] = {"status": "skip", "detail": "package.json 不可读"}
        return
    drift: list[str] = []
    for label, path in (("pnpm-lock.yaml", lock), ("node_modules/.modules.yaml", modules)):
        if not path.is_file():
            drift.append(f"{label} 缺失")
            continue
        try:
            if path.stat().st_mtime < pkg_mtime:
                drift.append(f"{label} 早于 package.json")
        except OSError:
            continue
    if drift:
        ctx["checks"]["lock_drift"] = {"status": "warn", "detail": "; ".join(drift)}
        _add(
            ctx["findings"],
            "lock_drift",
            severity="medium",
            title="依赖清单与安装结果可能不同步",
            evidence="; ".join(drift),
            recommendation="运行 dsh plugin --profile <p> install 让锁文件与 node_modules 追上 package.json。",
            path=str(profile),
            auto_fixable=True,
        )
    else:
        ctx["checks"]["lock_drift"] = {"status": "ok", "detail": "锁文件与依赖清单同步"}


def _check_model_route(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any]) -> None:
    profile = _profile_dir(paths, cfg)
    patch = profile / "cordis.patch.yml"
    if not patch.is_file():
        ctx["checks"]["model_route"] = {"status": "skip", "detail": "无 profile patch 层"}
        return
    text = read_text(patch, max_bytes=512 * 1024)
    entries = parse_patch_entries(text)
    by_id = {e["id"]: e for e in entries}

    route_entry = by_id.get("agent-default-model")
    provider = entry_config(route_entry, "provider")
    model = entry_config(route_entry, "model")
    effort = entry_config(route_entry, "reasoningEffort")
    ctx["route"] = {"provider": provider, "model": model, "reasoning_effort": effort}

    preset_entry = by_id.get("agent-preset-registry")
    ctx["presets"] = {
        "selected": entry_config(preset_entry, "selectedDefault"),
        "default": entry_config(preset_entry, "default"),
    }

    if not provider or not model:
        ctx["checks"]["model_route"] = {"status": "warn", "detail": "未找到 agent-default-model 配置"}
        _add(
            ctx["findings"],
            "model_route",
            severity="high",
            title="模型通道未在 profile 中显式配置",
            evidence=f"{patch} 中缺少 agent-default-model 的 provider/model",
            recommendation="显式写入 provider/model/reasoningEffort，避免新会话回落到未知默认值。",
            path=str(patch),
        )
    else:
        detail = f"{provider}/{model}"
        if effort:
            detail += f" effort={effort}"
        ctx["checks"]["model_route"] = {"status": "ok", "detail": detail}
        if str(effort).lower() in WEAK_EFFORT:
            _add(
                ctx["findings"],
                "model_route",
                severity="medium",
                title="推理强度偏低：模型能力被主动收窄",
                evidence=f"reasoningEffort={effort or '(未设置)'}（provider={provider}, model={model}）",
                recommendation="对复杂工程任务把 reasoningEffort 提到模型支持的较高级别；低强度会让多步推理变浅。",
                path=str(patch),
            )

    selected = ctx["presets"].get("selected") or ""
    if selected and selected != "deep-ptc":
        _add(
            ctx["findings"],
            "model_route",
            severity="info",
            title=f"默认 Agent 预设为 {selected}",
            evidence=f"agent-preset-registry.selectedDefault={selected}",
            recommendation="若需要长任务工具编排，确认预设与任务形态匹配；换预设前先备份 profile patch 层。",
            path=str(patch),
        )


def _check_web_tools(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any]) -> None:
    profile = _profile_dir(paths, cfg)
    patch = profile / "cordis.patch.yml"
    if not patch.is_file():
        ctx["checks"]["web_tools"] = {"status": "skip", "detail": "无 profile patch 层"}
        return
    text = read_text(patch, max_bytes=512 * 1024)
    entries = parse_patch_entries(text)
    by_id = {e["id"]: e for e in entries}
    web = by_id.get("web")
    provider = entry_config(web, "searchProvider")
    fetch = entry_config(web, "fetchProvider")
    ctx["web"] = {"search_provider": provider, "fetch_provider": fetch}
    if provider:
        ctx["checks"]["web_tools"] = {"status": "ok", "detail": f"search={provider} fetch={fetch or '-'}"}
    else:
        ctx["checks"]["web_tools"] = {"status": "skip", "detail": "未配置 web provider"}


def _check_runtime_eol(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any], *, online: bool) -> None:
    py = sys.version.split()[0]
    node = ""
    node_path = shutil.which("node")
    if node_path:
        code, out, _ = run_cmd([node_path, "--version"], timeout=10)
        if code == 0:
            node = out.strip().lstrip("v")
    ctx["runtime"] = {"python": py, "node": node or "not found", "python_exe": sys.executable}

    tables = {"python": dict(PYTHON_EOL), "node": dict(NODE_EOL)}
    if online:
        for key, product in (("python", "python"), ("node", "nodejs")):
            try:
                rows = _http_json(f"{EOL_API}/{product}.json")
                fresh = {}
                for row in rows if isinstance(rows, list) else []:
                    cycle = str(row.get("cycle") or "")
                    eol = row.get("eol")
                    if cycle and isinstance(eol, str):
                        fresh[cycle] = eol
                if fresh:
                    tables[key].update(fresh)
            except Exception:  # noqa: BLE001 - network shape is not contractual
                pass

    before = len(ctx["findings"])
    today = _dt.date.today()
    for label, value, table in (("Python", py, tables["python"]), ("Node", node, tables["node"])):
        if not value or not re.match(r"^\d+", value):
            continue
        minor = ".".join(value.split(".")[:2]) if label == "Python" else value.split(".")[0]
        eol = table.get(minor)
        if not eol:
            continue
        try:
            eol_date = _dt.date.fromisoformat(eol)
        except ValueError:
            continue
        remaining = (eol_date - today).days
        if remaining < 0:
            _add(
                ctx["findings"],
                "runtime_eol",
                severity="high",
                title=f"{label} {value} 已停止维护（EOL {eol}）",
                evidence=f"{label} {minor} 的上游支持已于 {eol} 结束，不再有安全修复。",
                recommendation=f"升级到仍受支持的 {label} 版本；旧运行时会让新库与工具链逐步不可用。",
            )
        elif remaining < 180:
            _add(
                ctx["findings"],
                "runtime_eol",
                severity="low",
                title=f"{label} {value} 即将 EOL（{eol}，剩 {remaining} 天）",
                evidence=f"{label} {minor} 将在 {eol} 停止维护。",
                recommendation="在窗口期内规划升级，避免到期后被动迁移。",
            )
    ctx["checks"]["runtime_eol"] = {
        "status": "warn" if len(ctx["findings"]) > before else "ok",
        "detail": f"python {py} · node {node or 'not found'}",
    }


def _check_toolchain(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any]) -> None:
    """Does the workspace ask for tools that are not installed?

    A missing interpreter or VCS is not a "task failure" the metrics can see: it
    shows up as the agent improvising around a tool it never had. Markers make
    the requirement explicit rather than inferred from guesswork.
    """
    rules = get(cfg, "envcheck.toolchain", None)
    if not isinstance(rules, list) or not rules:
        rules = DEFAULT_TOOLCHAIN
    cwd = paths.cwd
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        marker = str(rule.get("marker") or "")
        command = str(rule.get("command") or "")
        if not marker or not command:
            continue
        if not (cwd / marker).exists():
            continue
        found = shutil.which(command) or ""
        rows.append({"marker": marker, "command": command, "path": found, "required": True})
        if not found:
            hint = str(rule.get("install_hint") or "")
            missing.append(f"{command} (需要 {marker}{'; ' + hint if hint else ''})")
    ctx["toolchain"] = {"marked": rows, "missing": [r["command"] for r in rows if not r["path"]]}
    if missing:
        _add(
            ctx["findings"],
            "toolchain",
            severity="medium",
            title=f"工作区需要的 {len(missing)} 个工具未安装",
            evidence="; ".join(missing),
            recommendation="按提示安装对应工具；缺失时模型只能绕路或降级方案，而且不会报错。",
            path=str(cwd),
        )
    ctx["checks"]["toolchain"] = {
        "status": "warn" if missing else "ok",
        "detail": (f"{len(rows)} 项标记检查，缺 {len(missing)} 个" if rows else "工作区无标记"),
    }


def _check_skill_health(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any]) -> None:
    """Per-skill integrity: the failure mode where a skill stays installed but
    its instructions point at files (or scripts) that no longer work."""
    result = run_skill_health(paths, cfg)
    ctx["skill_health"] = result["counts"]
    ctx["skill_health"]["issues"] = len(result["findings"])
    order = {"high": 0, "medium": 1, "low": 2}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for finding in result["findings"]:
        grouped.setdefault((str(finding["severity"]), str(finding["kind"])), []).append(finding)
    added = 0
    for (severity, kind), items in sorted(grouped.items(), key=lambda kv: order.get(kv[0][0], 9)):
        if added >= 6:
            break
        names = ", ".join(str(i["skill"]) for i in items[:5])
        more = f" 等 {len(items)} 项" if len(items) > 5 else ""
        _add(
            ctx["findings"],
            "skill_health",
            severity=severity,
            title=f"技能问题（{kind}）：{len(items)} 处",
            evidence=f"{names}{more}；示例：{items[0]['detail'][:200]}",
            recommendation=(
                "引用失效的补文件或改文档；脚本编译失败先修代码再谈复用；"
                "过期技能用 evolution verify 复验，仍不适用则归档而非保留。"
            ),
            path=str(items[0].get("path") or ""),
        )
        added += 1
    counts = result["counts"]
    status = "fail" if counts.get("bad") else ("warn" if counts.get("warn") or result["findings"] else "ok")
    ctx["checks"]["skill_health"] = {
        "status": status,
        "detail": f"{counts['total']} 个技能：ok {counts['ok']} / warn {counts['warn']} / bad {counts['bad']}；问题 {len(result['findings'])}",
    }


def _check_skill_shadow(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any]) -> None:
    """Detect local skills shadowing plugin-bundled ones (rank 400 beats 600)."""
    local_roots = [_dsh_home(paths) / "skills", paths.os_home / ".agents" / "skills"]
    local: dict[str, str] = {}
    for root in local_roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "SKILL.md").is_file():
                local[child.name] = str(child)
    plugin: dict[str, str] = {}
    profile = _profile_dir(paths, cfg)
    node_modules = profile / "node_modules"
    if node_modules.is_dir():
        for child in sorted(node_modules.iterdir()):
            candidates = [child]
            if child.name.startswith("@") and child.is_dir():
                candidates = [c for c in sorted(child.iterdir()) if c.is_dir()]
            for cand in candidates:
                skill_md = cand / "SKILL.md"
                if skill_md.is_file():
                    plugin[cand.name] = str(skill_md)
                # Some plugins ship a skills/ subtree instead of one root SKILL.md.
                for nested in sorted(cand.glob("skills/*/SKILL.md")):
                    plugin[nested.parent.name] = str(nested)
    shadowed = sorted(set(local) & set(plugin))
    ctx["skills"] = {"local": len(local), "plugin_bundled": len(plugin), "shadowing": shadowed}
    if shadowed:
        _add(
            ctx["findings"],
            "skill_shadow",
            severity="info",
            title=f"{len(shadowed)} 个本地技能覆盖了插件自带版本",
            evidence=f"本地（秩 400）优先于插件（秩 600）：{', '.join(shadowed[:8])}",
            recommendation="确认是有意的本地特化；否则本地旧副本会长期压住插件的修复版本。",
            path=str(_dsh_home(paths) / "skills"),
        )
    ctx["checks"]["skill_shadow"] = {
        "status": "ok",
        "detail": f"本地 {len(local)} · 插件内置 {len(plugin)} · 覆盖 {len(shadowed)}",
    }


def _check_heartbeat(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any]) -> None:
    """Is the evolution loop still beating? Stale reports mean silent drift."""
    weekly = sorted(paths.weekly_dir.glob("*.md")) if paths.weekly_dir.is_dir() else []
    last_weekly = weekly[-1].stat().st_mtime if weekly else 0.0
    audit = paths.state_dir / "audit-latest.json"
    last_audit = audit.stat().st_mtime if audit.is_file() else 0.0
    envfile = paths.state_dir / "envcheck-latest.json"
    last_env = envfile.stat().st_mtime if envfile.is_file() else 0.0
    schedule_state = read_json(paths.schedule_state, default=None)
    ctx["heartbeat"] = {
        "weekly_days": _age_days(last_weekly),
        "audit_days": _age_days(last_audit),
        "envcheck_days": _age_days(last_env),
        "schedule_recorded": bool(isinstance(schedule_state, dict) and schedule_state),
    }
    worst = ctx["heartbeat"]["weekly_days"]
    if worst is None:
        ctx["checks"]["heartbeat"] = {"status": "warn", "detail": "尚无每周审阅报告"}
        _add(
            ctx["findings"],
            "heartbeat",
            severity="medium",
            title="进化的心跳尚未开始：没有任何周报",
            evidence=f"{paths.weekly_dir} 为空",
            recommendation="运行一次 review --apply 建立基线，再确认周调度已激活。",
            path=str(paths.weekly_dir),
        )
        return
    if worst > 28:
        severity = "high"
    elif worst > 14:
        severity = "medium"
    else:
        severity = ""
    if severity:
        _add(
            ctx["findings"],
            "heartbeat",
            severity=severity,
            title=f"上一份周报距今 {worst:.0f} 天：进化闭环可能已停跳",
            evidence=f"最近周报 {weekly[-1].name}",
            recommendation="检查周调度是否仍 active（schedule status），必要时重建提醒。",
            path=str(paths.weekly_dir),
        )
    ctx["checks"]["heartbeat"] = {"status": "warn" if severity else "ok", "detail": f"最近周报 {worst:.0f} 天前"}


def _check_hygiene(ctx: dict[str, Any], paths: Paths, cfg: dict[str, Any], *, threshold_days: int = 30) -> None:
    """Accumulated leftovers that slow startup, blur ports, or eat disk."""
    dsh = _dsh_home(paths)
    backups: list[Path] = []
    if dsh.is_dir():
        for child in dsh.rglob("*"):
            if child.is_file() and re.search(r"\.bak-|\.bak$|\.old$", child.name):
                backups.append(child)
    skills = _dsh_home(paths) / "skills"
    pycache: list[Path] = []
    for root in (skills, paths.runtime_home):
        if root.is_dir():
            pycache.extend(root.rglob("__pycache__"))
    sessions = _dir_size(dsh / "sessions")
    logs = _dir_size(dsh / "logs")
    try:
        stale = [p for p in backups if (_age_days(p.stat().st_mtime) or 0) > threshold_days]
    except OSError:
        stale = []
    ctx["hygiene"] = {
        "backup_files": len(backups),
        "stale_backups": len(stale),
        "pycache_dirs": len(pycache),
        "sessions_bytes": sessions,
        "logs_bytes": logs,
    }
    if len(backups) >= 5 or stale:
        _add(
            ctx["findings"],
            "hygiene",
            severity="medium" if stale else "low",
            title=f"{len(backups)} 个备份残留（{len(stale)} 个超过 {threshold_days} 天）",
            evidence=", ".join(sorted(p.name for p in stale)[:6]) or "见 ~/.dsh",
            recommendation="确认无误后运行 evolution cleanup --apply；备份是安全网，过期的网也要收。",
            path=str(dsh),
            auto_fixable=True,
        )
    if pycache:
        _add(
            ctx["findings"],
            "hygiene",
            severity="low",
            title=f"{len(pycache)} 个 __pycache__ 目录残留",
            evidence=f"技能与运行时目录内共 {len(pycache)} 个字节码缓存目录",
            recommendation="运行 evolution cleanup --apply 清理字节码缓存；不影响功能，只影响整洁与扫描速度。",
            auto_fixable=True,
        )
    big = []
    if sessions > 2 * 1024**3:
        big.append(f"sessions {sessions / 1024**3:.1f} GiB")
    if logs > 512 * 1024**2:
        big.append(f"logs {logs / 1024**2:.0f} MiB")
    if big:
        _add(
            ctx["findings"],
            "hygiene",
            severity="low",
            title="本地状态目录偏大",
            evidence=", ".join(big),
            recommendation="按保留策略归档旧会话/日志；磁盘压力会以难以归因的方式拖慢一切。",
            path=str(dsh),
        )
    ctx["checks"]["hygiene"] = {
        "status": "warn" if (stale or pycache or big) else "ok",
        "detail": f"备份 {len(backups)} · 字节码 {len(pycache)} · sessions {sessions / 1024**2:.0f} MiB",
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _score(findings: list[dict[str, Any]]) -> int:
    penalty = sum(SEVERITY_WEIGHT.get(str(f.get("severity", "info")), 0) for f in findings)
    return max(0, min(100, 100 - penalty))


def run_envcheck(
    paths: Paths,
    cfg: dict[str, Any],
    *,
    online: bool = False,
    threshold_days: int = 30,
) -> dict[str, Any]:
    """Run every environment-currency check and return a structured report."""
    paths.ensure_runtime()
    ctx: dict[str, Any] = {
        "dsh": {},
        "profile": {},
        "route": {},
        "presets": {},
        "web": {},
        "runtime": {},
        "skills": {},
        "heartbeat": {},
        "hygiene": {},
        "checks": {},
        "findings": [],
        "limits": [],
    }

    _check_dsh_cli(ctx, paths, cfg)
    _check_dsh_runtime(ctx, paths, cfg)
    _check_plugin_tree(ctx, paths, cfg)
    _check_plugin_compat(ctx, paths, cfg)
    _check_plugin_channel(ctx, paths, cfg)
    _check_lock_drift(ctx, paths, cfg)
    _check_model_route(ctx, paths, cfg)
    _check_web_tools(ctx, paths, cfg)
    _check_runtime_eol(ctx, paths, cfg, online=online)
    _check_toolchain(ctx, paths, cfg)
    _check_skill_health(ctx, paths, cfg)
    _check_skill_shadow(ctx, paths, cfg)
    _check_heartbeat(ctx, paths, cfg)
    _check_hygiene(ctx, paths, cfg, threshold_days=threshold_days)

    findings: list[dict[str, Any]] = ctx.pop("findings")
    counts: dict[str, int] = {k: 0 for k in ("critical", "high", "medium", "low", "info")}
    for item in findings:
        counts[str(item["severity"])] = counts.get(str(item["severity"]), 0) + 1
    score = _score(findings)
    status = "green" if score >= 85 and not counts["critical"] else ("red" if score < 60 or counts["critical"] else "yellow")

    order = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    next_actions = sorted(
        [f for f in findings if f["severity"] != "info"],
        key=lambda f: (-order.get(str(f["severity"]), 0), f["check"]),
    )[:6]

    ctx["limits"].append("插件兼容性结论来自宿主 dsh plugin list 的自述；宿主未报告不等于运行时一定安全。")
    ctx["limits"].append("模型通道检查只看配置，不验证该模型在本机是否真的可用（需一次真实调用）。")
    if not online:
        ctx["limits"].append("离线模式：未检查 npm 上的新版本与上游 EOL 更新，运行时 EOL 用内置基线表。")

    result = {
        "schema": 1,
        "kind": "envcheck",
        "generated_at": now_iso(),
        "version": __version__,
        "platform": sys.platform,
        "online": bool(online),
        **ctx,
        "checks": {k: ctx["checks"].get(k, {"status": "skip", "detail": "未执行"}) for k in CHECK_ORDER},
        "findings": findings,
        "severity_counts": counts,
        "score": score,
        "status": status,
        "next_actions": next_actions,
        "auto_fixable": sum(1 for f in findings if f.get("auto_fixable")),
    }
    result["markdown"] = envcheck_markdown(result)
    return result


def envcheck_markdown(result: dict[str, Any]) -> str:
    counts = result.get("severity_counts", {})
    lines: list[str] = []
    lines.append(f"# 环境年代感体检 · {result.get('generated_at', '')}")
    lines.append("")
    lines.append(
        f"- 结论：**{str(result.get('status', '')).upper()}** · 分数 **{result.get('score')}/100** · "
        f"模式：{'联网' if result.get('online') else '离线'}"
    )
    lines.append(
        f"- 发现：critical {counts.get('critical', 0)} / high {counts.get('high', 0)} / "
        f"medium {counts.get('medium', 0)} / low {counts.get('low', 0)} / info {counts.get('info', 0)}"
    )
    dsh = result.get("dsh", {})
    profile = result.get("profile", {})
    route = result.get("route", {})
    lines.append(
        f"- 宿主：DSH {dsh.get('version') or '?'} · profile {profile.get('name') or '?'}"
        f"（依赖 {profile.get('dependencies', 0)} / bundles {profile.get('bundles', 0)}）"
    )
    if route:
        lines.append(
            f"- 模型通道：{route.get('provider') or '?'}/{route.get('model') or '?'}"
            f" effort={route.get('reasoning_effort') or '(未设置)'}"
        )
    lines.append("")

    lines.append("## 检查项")
    labels = {
        "dsh_cli": "宿主 CLI",
        "dsh_runtime": "宿主版本",
        "plugin_tree": "插件树一致性",
        "plugin_compat": "插件兼容性",
        "plugin_channel": "插件写入通道",
        "lock_drift": "锁文件同步",
        "model_route": "模型通道",
        "web_tools": "联网工具",
        "runtime_eol": "运行时生命周期",
        "toolchain": "所需工具",
        "skill_health": "技能健康",
        "skill_shadow": "技能覆盖关系",
        "heartbeat": "进化心跳",
        "hygiene": "淤积与备份",
    }
    marks = {"ok": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
    for key in CHECK_ORDER:
        check = result.get("checks", {}).get(key, {})
        lines.append(f"- [{marks.get(str(check.get('status')), '?')}] {labels.get(key, key)}：{check.get('detail', '')}")
    lines.append("")

    ranked = sorted(
        result.get("findings", []),
        key=lambda f: -{"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}.get(str(f.get("severity")), 0),
    )
    if ranked:
        lines.append("## 发现")
        for item in ranked:
            lines.append(f"### [{str(item['severity']).upper()}] {item['title']}")
            lines.append(f"- 证据：{md_cell(item.get('evidence', ''), 400)}")
            lines.append(f"- 建议：{item.get('recommendation', '')}")
            if item.get("path"):
                lines.append(f"- 位置：{item['path']}")
            lines.append("")

    if result.get("next_actions"):
        lines.append("## 下一步")
        for idx, item in enumerate(result["next_actions"], 1):
            lines.append(f"{idx}. **{str(item['severity']).upper()}** {item['title']} → {item['recommendation']}")
        lines.append("")

    lines.append("## 边界与未检查项")
    for limit in result.get("limits", []):
        lines.append(f"- {limit}")
    lines.append("")
    return "\n".join(lines)


def write_envcheck(paths: Paths, result: dict[str, Any]) -> tuple[Path, Path]:
    """Persist the report under reports/envcheck and refresh the latest pointer."""
    out_dir = paths.reports_dir / "envcheck"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_stamp()
    md_path = out_dir / f"{stamp}.md"
    json_path = out_dir / f"{stamp}.json"
    md_path.write_text(result["markdown"], encoding="utf-8", newline="\n")
    payload = {k: v for k, v in result.items() if k != "markdown"}
    write_json(json_path, payload)
    write_json(paths.state_dir / "envcheck-latest.json", payload)
    return md_path, json_path


def latest_envcheck(paths: Paths) -> dict[str, Any] | None:
    data = read_json(paths.state_dir / "envcheck-latest.json", default=None)
    return data if isinstance(data, dict) else None
