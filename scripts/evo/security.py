"""Static, non-executing security and supply-chain checks."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .frontmatter import Skill
from .util import detect_secrets, file_size, read_text, redact_secrets

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
SEVERITY_WEIGHT = {"critical": 40, "high": 15, "medium": 5, "low": 1, "info": 0}

DANGEROUS_PATTERNS: list[tuple[str, str, str, str]] = [
    (
        "remote-pipe-shell",
        r"(?i)\b(?:curl|wget)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|k|da)?sh\b",
        "high",
        "远程脚本直接管道到 shell：下载内容不可审计，易被投毒。",
    ),
    (
        "powershell-encoded",
        r"(?i)powershell(?:\.exe)?\b[^\n]{0,120}\s-(?:enc|encodedcommand)\b",
        "high",
        "PowerShell 编码命令常被用于隐藏执行内容。",
    ),
    (
        "destructive-root",
        r"(?i)(?:rm\s+-rf\s+/(?:\s|$)|Remove-Item\s+[^\n]{0,80}-Recurse[^\n]{0,80}(?:C:\\|/)\s*$)",
        "critical",
        "可能删除根目录/系统盘的破坏性命令。",
    ),
    (
        "dynamic-eval",
        r"(?i)\b(?:eval|exec)\s*\(\s*(?:base64|atob|Buffer\.from|__import__|compile)\b",
        "high",
        "动态解码后执行，常见于混淆/后门。",
    ),
    (
        "shell-true",
        r"(?i)\bsubprocess\.(?:run|call|Popen|check_output)\([^\n]{0,200}shell\s*=\s*True",
        "medium",
        "shell=True 会把参数拼接进命令行，存在注入风险。",
    ),
    (
        "os-system",
        r"(?i)\bos\.system\s*\(",
        "medium",
        "os.system 缺少参数边界，建议改用 subprocess 列表参数。",
    ),
    (
        "chmod-777",
        r"(?i)\bchmod\s+(?:-R\s+)?777\b",
        "medium",
        "全局可写权限过大。",
    ),
    (
        "git-force-main",
        r"(?i)\bgit\s+push\b[^\n]{0,120}(?:--force|-f)\b[^\n]{0,80}\b(?:main|master)\b",
        "high",
        "强制推送主分支可能覆盖他人提交。",
    ),
    (
        "skip-verify",
        r"(?i)--no-verify\b",
        "low",
        "跳过 Git hook/校验，需确认理由。",
    ),
    (
        "plain-http",
        r"(?i)http://(?!127\.0\.0\.1|localhost|\[::1\])[A-Za-z0-9.-]+",
        "medium",
        "明文 HTTP 端点可能被篡改。",
    ),
    (
        "unpinned-npx",
        r"(?i)\bnpx\s+(?:-y|--yes)\s+[A-Za-z0-9@/._-]+(?:\s|$)",
        "medium",
        "npx -y 未固定版本，可能执行未来被投毒的版本。",
    ),
    (
        "unpinned-pip",
        r"(?i)\bpip(?:3)?\s+install\s+(?!--require-hashes|-r\b)[A-Za-z0-9_.-]+(?:\s|$)",
        "low",
        "pip 安装未固定版本；生产/长期环境建议锁定版本与哈希。",
    ),
]

SCAN_SUFFIXES = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".ps1", ".sh", ".cmd", ".bat",
    ".json", ".toml", ".yaml", ".yml", ".md", ".env",
}


def severity_rank(value: str) -> int:
    return SEVERITY_ORDER.get(str(value).lower(), 9)


def risk_score(findings: list[dict[str, Any]]) -> int:
    total = sum(SEVERITY_WEIGHT.get(str(f.get("severity", "info")).lower(), 0) for f in findings)
    return max(0, min(100, total))


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _finding(
    *,
    area: str,
    severity: str,
    title: str,
    evidence: str,
    recommendation: str,
    path: Path | None = None,
    line: int | None = None,
    kind: str = "",
) -> dict[str, Any]:
    return {
        "area": area,
        "severity": severity,
        "title": title,
        "evidence": redact_secrets(evidence)[:500],
        "recommendation": recommendation,
        "path": str(path) if path else "",
        "line": line,
        "kind": kind,
    }


def scan_text(text: str, *, area: str, path: Path | None = None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for kind, pattern, severity, recommendation in DANGEROUS_PATTERNS:
        # Documentation links/install examples are not executable; avoid
        # drowning real script risks in Markdown noise.
        if path is not None and path.suffix.lower() in {".md", ".mdx"} and kind in {
            "plain-http",
            "unpinned-npx",
            "unpinned-pip",
        }:
            continue
        for match in re.finditer(pattern, text):
            snippet = match.group(0).strip().replace("\n", " ")
            if kind == "plain-http":
                # XML namespaces/documentation links are not network calls.
                line_no = _line_of(text, match.start())
                line_text = text.splitlines()[line_no - 1] if 0 <= line_no - 1 < len(text.splitlines()) else ""
                if not re.search(
                    r"(requests\.|urllib|http\.client|httpx|aiohttp|fetch\(|axios|curl\s|wget\s|"
                    r"endpoint|base[_-]?url|api[_-]?url|api\.)",
                    line_text,
                    re.I,
                ):
                    continue
            findings.append(
                _finding(
                    area=area,
                    severity=severity,
                    title=pattern_title(kind),
                    evidence=f"{snippet[:160]}",
                    recommendation=recommendation,
                    path=path,
                    line=_line_of(text, match.start()),
                    kind=kind,
                )
            )
            if len(findings) >= 60:
                return findings
    for secret in detect_secrets(text):
        findings.append(
            _finding(
                area="secret",
                severity="high",
                title=f"疑似凭据：{secret['kind']}",
                evidence=secret["preview"],
                recommendation="确认并轮换该凭据；从文件/历史中移除；改用环境变量或密钥管理器。",
                path=path,
                line=_line_of(text, int(secret["start"])),
                kind="secret",
            )
        )
        if len(findings) >= 60:
            return findings
    return findings


def pattern_severity(kind: str) -> str:
    for name, _pattern, severity, _rec in DANGEROUS_PATTERNS:
        if name == kind:
            return severity
    return "low"


def pattern_title(kind: str) -> str:
    titles = {
        "remote-pipe-shell": "远程脚本管道执行",
        "powershell-encoded": "PowerShell 编码命令",
        "destructive-root": "破坏性删除命令",
        "dynamic-eval": "动态解码执行",
        "shell-true": "shell=True 参数注入风险",
        "os-system": "os.system 参数边界风险",
        "chmod-777": "过宽文件权限",
        "git-force-main": "强制推送主分支",
        "skip-verify": "跳过校验",
        "plain-http": "明文 HTTP 端点",
        "unpinned-npx": "npx 未固定版本",
        "unpinned-pip": "pip 未固定版本",
    }
    return titles.get(kind, kind)


def pattern_recommendation(kind: str) -> str:
    recs = {
        "remote-pipe-shell": "下载到临时文件并校验哈希/签名后再审查执行；优先使用包管理器。",
        "powershell-encoded": "要求提供明文脚本并审查；拒绝无法解释的编码命令。",
        "destructive-root": "改为精确路径 + 备份；删除前确认依赖与作用域。",
        "dynamic-eval": "移除动态执行；如必须，限制输入并隔离运行。",
        "shell-true": "改用参数列表并校验输入。",
        "os-system": "改用 subprocess 列表参数，避免 shell 解析。",
        "chmod-777": "改为最小必要权限（如 755/644）。",
        "git-force-main": "改用受保护分支流程；禁止对 main/master 强推。",
        "skip-verify": "确认跳过原因；在 CI/发布流程中恢复校验。",
        "plain-http": "改用 HTTPS；校验证书与来源。",
        "unpinned-npx": "固定版本（package@x.y.z）并校验来源。",
        "unpinned-pip": "锁定版本/哈希（requirements.txt + --require-hashes）。",
    }
    return recs.get(kind, "人工复核该模式。")


def scan_file(path: Path, *, area: str, max_bytes: int = 1024 * 1024) -> list[dict[str, Any]]:
    try:
        if file_size(path) > max_bytes:
            return []
        text = read_text(path, max_bytes=max_bytes)
    except OSError:
        return []
    if "\x00" in text[:4096]:
        return []
    return scan_text(text, area=area, path=path)


def scan_skill(skill: Skill, *, max_files: int = 400, max_bytes: int = 1024 * 1024) -> list[dict[str, Any]]:
    # Flat skill: the file itself is the whole bundle.
    if skill.path.name != "SKILL.md":
        return scan_file(skill.path, area="skill", max_bytes=max_bytes)
    base = skill.path.parent
    findings: list[dict[str, Any]] = []
    count = 0
    for path in sorted(base.rglob("*")):
        if count >= max_files:
            break
        if not path.is_file():
            continue
        rel_parts = set(path.relative_to(base).parts)
        if rel_parts & {"tests", "references", "rules", "node_modules", "__pycache__", ".git"}:
            continue
        name = path.name.lower()
        env_like = name.startswith(".env") or name in {"config", "credentials", "secrets", "config.json", "config.yaml", "config.yml"}
        if path.suffix.lower() not in SCAN_SUFFIXES and not env_like:
            continue
        count += 1
        # Avoid scanning the same file twice: SKILL.md is already in rglob.
        findings.extend(scan_file(path, area="skill", max_bytes=max_bytes))
        if len(findings) >= 80:
            break
    return findings


def scan_mcp(server: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    command = str(server.get("command") or "")
    args = " ".join(server.get("args") or [])
    url = str(server.get("url") or "")
    text = " ".join(x for x in (command, args, url) if x)
    findings.extend(scan_text(text, area="mcp"))
    if url.lower().startswith("http://") and not any(h in url for h in ("127.0.0.1", "localhost", "[::1]")):
        findings.append(
            _finding(
                area="mcp",
                severity="medium",
                title="MCP 远程端点使用明文 HTTP",
                evidence=url,
                recommendation="改用 HTTPS；确认服务端证书与来源。",
            )
        )
    if server.get("env_keys"):
        broad = [k for k in server["env_keys"] if k.upper() in {"PATH", "HOME", "USERPROFILE", "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"}]
        if broad:
            findings.append(
                _finding(
                    area="mcp",
                    severity="medium",
                    title="MCP 暴露敏感/宽泛环境变量",
                    evidence=", ".join(sorted(broad)),
                    recommendation="仅传入该服务必要的最小变量；优先使用密钥管理器/短期凭据。",
                )
            )
    return findings


def scan_permissions(permissions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in permissions:
        if str(item.get("severity")) != "high":
            continue
        findings.append(
            _finding(
                area="permission",
                severity="high",
                title=f"高风险权限配置：{item.get('key')}",
                evidence=f"{item.get('source')} -> {item.get('value')}",
                recommendation="日常任务改用 workspace-write/read-only 或按需审批；仅在高风险任务临时放宽并记录。",
                kind="permission",
            )
        )
    return findings


def summarize(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for item in findings:
        sev = str(item.get("severity", "info")).lower()
        counts[sev] = counts.get(sev, 0) + 1
    return counts
