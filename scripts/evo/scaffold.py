"""Create and verify skills on demand.

Two halves of one loop:

  * create_skill -- turn an identified capability gap into a valid skill folder
    (frontmatter, optional runnable script, optional test) in one step;
  * verify_skill -- the gate that decides whether a skill is usable at all:
    parses, compiles, resolves its references, and optionally runs a smoke
    command with a timeout.

The point is that "the agent wrote a skill" is not evidence. Verification is.
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .skill_health import local_references
from .util import now_iso, read_text, run_cmd

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MIN_DESCRIPTION = 20
MAX_SKILL_BYTES = 24 * 1024

SKILL_TEMPLATE = """---
name: {name}
description: "{description}"
metadata:
  version: "{version}"
  license: "{license}"
  author: "{author}"
---

# {title}

## 何时使用

{when_to_use}

## 用法

1. 确认输入与边界：需要什么、产出什么、失败了怎么回退。
2. 执行主体步骤（把命令写死，不要留"按需执行"这类空话）。
3. 用真实检查验收，不以"看起来完成"结束。

## 边界

- 不伪造执行结果；无法执行时说明限制与替代方案。
- 需要权限、付费或外发数据的步骤必须先获得授权。

## 验证

~~~bash
cd "$(dirname "$0")" 2>/dev/null || true
# 把这里的命令替换成能自证的检查
~~~
"""

SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
"""{name} -- {description}

Replace this stub with the real implementation. Keep it stdlib-only unless the
dependency is recorded and installed explicitly.
"""
from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="{name}", description="{description}")
    parser.add_argument("--input", default="", help="input path or value")
    parser.add_argument("--dry-run", action="store_true", help="report the plan without writing")
    args = parser.parse_args(argv)
    if not args.input:
        parser.error("--input is required")
    print(f"{name}: input={{args.input}} dry_run={{args.dry_run}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

TEST_TEMPLATE = '''"""Structural tests for {name}. Run: python -m unittest discover -s tests"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Test{cls}(unittest.TestCase):
    def test_skill_md_has_frontmatter(self) -> None:
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"))
        match = re.search(r"^name:\\s*(\\S+)", text, re.M)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "{name}")

    def test_scripts_compile(self) -> None:
        for path in (ROOT / "scripts").glob("*.py"):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")


if __name__ == "__main__":
    unittest.main()
'''


def _title(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split("-"))


def _cls(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("-"))


def create_skill(
    dest_root: Path,
    name: str,
    description: str,
    *,
    version: str = "0.1.0",
    license: str = "MIT",
    author: str = "",
    when_to_use: str = "",
    with_script: bool = False,
    with_tests: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    """Scaffold a skill folder. Returns the plan, and writes only when apply=True."""
    name = str(name or "").strip()
    description = str(description or "").strip()
    problems: list[str] = []
    if not NAME_RE.match(name):
        problems.append(f"name 必须是 kebab-case（当前 {name!r}）")
    if len(description) < MIN_DESCRIPTION:
        problems.append(f"description 至少 {MIN_DESCRIPTION} 个字符（当前 {len(description)}）")
    target = dest_root / name
    if target.exists():
        problems.append(f"目标已存在：{target}")
    if problems:
        return {"ok": False, "name": name, "target": str(target), "errors": problems, "written": []}

    files: dict[Path, str] = {}
    files[target / "SKILL.md"] = SKILL_TEMPLATE.format(
        name=name,
        description=description.replace('"', "'"),
        version=version,
        license=license,
        author=author,
        title=_title(name),
        when_to_use=when_to_use.strip() or "在这里写清楚：什么任务该触发这个技能，什么任务不该。",
    )
    if with_script:
        files[target / "scripts" / f"{name}.py"] = SCRIPT_TEMPLATE.format(name=name, description=description.replace('"', "'"))
    if with_tests:
        files[target / "tests" / f"test_{name.replace('-', '_')}.py"] = TEST_TEMPLATE.format(name=name, cls=_cls(name))

    if not apply:
        return {
            "ok": True,
            "applied": False,
            "name": name,
            "target": str(target),
            "files": [str(p) for p in files],
            "errors": [],
        }

    written: list[str] = []
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(str(path))
    return {
        "ok": True,
        "applied": True,
        "name": name,
        "target": str(target),
        "files": written,
        "errors": [],
        "next": f"evolution verify --path {target}",
    }


def _compile_scripts(root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    scripts = sorted(root.glob("scripts/**/*.py"))
    for path in scripts:
        rel = path.relative_to(root).as_posix()
        try:
            compile(read_text(path), str(path), "exec")
            checks.append({"name": f"compile {rel}", "ok": True, "detail": ""})
        except (SyntaxError, ValueError, OSError) as exc:
            checks.append({"name": f"compile {rel}", "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
    return checks


def verify_skill(path: str | Path, *, command: str | list[str] = "", timeout: float = 120.0) -> dict[str, Any]:
    """Decide whether a skill is structurally usable. Never guesses."""
    target = Path(path).expanduser()
    skill_md = target / "SKILL.md" if target.is_dir() else target
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    if not skill_md.is_file():
        return {
            "ok": False,
            "path": str(target),
            "checks": [{"name": "SKILL.md exists", "ok": False, "detail": "未找到 SKILL.md"}],
            "errors": ["未找到 SKILL.md"],
            "warnings": [],
        }
    root = skill_md.parent
    text = read_text(skill_md)
    checks.append({"name": "SKILL.md exists", "ok": True, "detail": str(skill_md)})

    frontmatter, _body, parse_errors = parse_frontmatter(text)
    name = str(frontmatter.get("name") or "").strip()
    description = str(frontmatter.get("description") or "").strip()
    ok_name = bool(NAME_RE.match(name))
    checks.append({"name": "frontmatter name kebab-case", "ok": ok_name, "detail": name or "(缺失)"})
    if not ok_name:
        errors.append(f"name 不是 kebab-case：{name!r}")
    ok_desc = len(description) >= MIN_DESCRIPTION
    checks.append({"name": "description length", "ok": ok_desc, "detail": f"{len(description)} 字符"})
    if not ok_desc:
        errors.append(f"description 太短（{len(description)} < {MIN_DESCRIPTION}）")
    if parse_errors:
        errors.extend(parse_errors)
    size = len(text.encode("utf-8"))
    checks.append({"name": "SKILL.md size", "ok": size <= MAX_SKILL_BYTES, "detail": f"{size} 字节"})
    if size > MAX_SKILL_BYTES:
        warnings.append(f"SKILL.md 超过 {MAX_SKILL_BYTES} 字节，每轮上下文成本偏高")

    for check in _compile_scripts(root):
        checks.append(check)
        if not check["ok"]:
            errors.append(f"{check['name']} 失败：{check['detail']}")
    if not any(c["name"].startswith("compile ") for c in checks):
        checks.append({"name": "scripts", "ok": True, "detail": "无脚本"})

    body = _body or ""
    missing = [ref for ref in local_references(body) if not (root / ref).exists() and not (root / ref.split("/")[0]).exists()]
    checks.append({"name": "references resolve", "ok": not missing, "detail": ", ".join(missing[:6]) or "全部存在"})
    for ref in missing:
        errors.append(f"正文引用了不存在的路径：{ref}")

    for manifest in sorted(root.glob("*.json")):
        try:
            json.loads(read_text(manifest))
            checks.append({"name": f"json {manifest.name}", "ok": True, "detail": ""})
        except (ValueError, OSError) as exc:
            checks.append({"name": f"json {manifest.name}", "ok": False, "detail": str(exc)})
            errors.append(f"{manifest.name} 解析失败：{exc}")

    if command:
        argv = shlex.split(command) if isinstance(command, str) else [str(part) for part in command]
        code, out, err = run_cmd(argv, timeout=timeout, cwd=root)
        ok = code == 0
        checks.append({"name": f"smoke: {' '.join(argv)}", "ok": ok, "detail": f"exit={code}"})
        if not ok:
            errors.append(f"冒烟命令失败（exit {code}）：{(err or out).strip()[:300]}")

    return {
        "schema": 1,
        "kind": "skill-verify",
        "generated_at": now_iso(),
        "path": str(root),
        "name": name,
        "ok": not errors,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }


def verify_markdown(result: dict[str, Any]) -> str:
    lines = [f"# 技能验收 · {result.get('name') or result.get('path')}", ""]
    lines.append(f"- 结论：{'PASS' if result.get('ok') else 'FAIL'}")
    lines.append("")
    lines.append("| 检查 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    for check in result.get("checks", []):
        lines.append(f"| {check['name']} | {'ok' if check['ok'] else 'FAIL'} | {str(check.get('detail') or '')[:120]} |")
    lines.append("")
    if result.get("errors"):
        lines.append("## 必须修复")
        for item in result["errors"]:
            lines.append(f"- {item}")
        lines.append("")
    if result.get("warnings"):
        lines.append("## 提醒")
        for item in result["warnings"]:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines)
