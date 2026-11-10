"""Filesystem layout for the evolution skill, runtime and agent homes."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .util import expand_path, ensure_dir


@dataclass
class Paths:
    """All paths used by the CLI.

    ``os_home`` simulates the user's home for tests; ``runtime_home`` is the
    persistent data directory (default ``~/.agents/evolution``).
    """

    skill_dir: Path
    os_home: Path
    runtime_home: Path
    cwd: Path = field(default_factory=Path.cwd)

    @property
    def config(self) -> Path:
        return self.runtime_home / "config.json"

    @property
    def state_dir(self) -> Path:
        return self.runtime_home / "state"

    @property
    def snapshots_dir(self) -> Path:
        return self.state_dir / "snapshots"

    @property
    def releases_dir(self) -> Path:
        return self.state_dir / "releases"

    @property
    def reports_dir(self) -> Path:
        return self.runtime_home / "reports"

    @property
    def weekly_dir(self) -> Path:
        return self.reports_dir / "weekly"

    @property
    def audit_dir(self) -> Path:
        return self.reports_dir / "audit"

    @property
    def knowledge_dir(self) -> Path:
        return self.runtime_home / "knowledge"

    @property
    def backups_dir(self) -> Path:
        return self.runtime_home / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.runtime_home / "logs"

    @property
    def bin_dir(self) -> Path:
        return self.runtime_home / "bin"

    @property
    def exports_dir(self) -> Path:
        return self.runtime_home / "exports"

    @property
    def metrics_file(self) -> Path:
        return self.state_dir / "metrics.jsonl"

    @property
    def usage_file(self) -> Path:
        return self.state_dir / "usage.json"

    @property
    def install_manifest(self) -> Path:
        return self.state_dir / "install-manifest.json"

    @property
    def schedule_state(self) -> Path:
        return self.state_dir / "schedule.json"

    @property
    def safety_lock(self) -> Path:
        return self.state_dir / "safety.lock"

    @property
    def lock_file(self) -> Path:
        return self.state_dir / "evolution.lock"

    @property
    def agents_block_snapshot(self) -> Path:
        return self.state_dir / "agents-block.md"

    @property
    def skill_md(self) -> Path:
        return self.skill_dir / "SKILL.md"

    @property
    def version_file(self) -> Path:
        return self.skill_dir / "VERSION"

    def ensure_runtime(self) -> None:
        for path in (
            self.runtime_home,
            self.state_dir,
            self.snapshots_dir,
            self.releases_dir,
            self.weekly_dir,
            self.audit_dir,
            self.knowledge_dir,
            self.backups_dir,
            self.logs_dir,
            self.bin_dir,
            self.exports_dir,
        ):
            ensure_dir(path)

    def relative(self, path: Path) -> str:
        """Best-effort human path under runtime or skill root."""
        p = Path(path).resolve()
        for root, prefix in ((self.runtime_home, ""), (self.skill_dir, "skill:")):
            try:
                return prefix + p.relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
        return str(p)


def build_paths(
    runtime_home: str | os.PathLike[str] | None = None,
    os_home: str | os.PathLike[str] | None = None,
    skill_dir: Path | None = None,
    cwd: Path | None = None,
) -> Paths:
    from . import skill_dir as default_skill_dir

    home = expand_path(os_home, home=Path.home()) if os_home else Path.home()
    if runtime_home:
        runtime = expand_path(runtime_home, base=Path.cwd(), home=home)
    else:
        env = os.environ.get("EVOLUTION_HOME")
        runtime = expand_path(env, home=home) if env else home / ".agents" / "evolution"
    return Paths(
        skill_dir=(skill_dir or default_skill_dir()).resolve(),
        os_home=home,
        runtime_home=runtime,
        cwd=(cwd or Path.cwd()).resolve(),
    )
