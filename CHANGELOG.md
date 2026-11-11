# Changelog

All notable changes to `evolution` are documented here.
Format: Keep a Changelog; versioning: SemVer.

## [1.3.1] — 2026-11-10

### Changed
- 每周先运行 envcheck 再评估能力：插件被宿主拒绝加载时，先升级或卸载该插件，再讨论是否新增工具；模型通道必须显式配置 reasoningEffort。


## [1.6.0] — 2026-11-11

### Added
- **Skill provenance, usage and retirement** (skills command + evo/skill_sources.py):
  reads each skill's .evolution-source.json, joins it with the metrics ledger, and reports source, age and use count.
  Unused managed skills can be quarantined (moved to state/quarantine with a manifest) and restored byte-for-byte.
  Hand-written skills and protected skills are never nominated. Nothing is ever deleted.
- **Skill update from recorded source** (skills --update NAME --apply): re-fetches the recorded source/ref/subpath
  through the existing fetch pipeline, which backs up the previous copy first.
- 5 new tests (60 total).

### Changed
- The weekly flow can now retire and update skills, not just plugins: health finds broken ones, skills finds
  unused ones, and both actions are reversible or reported with an explicit reason.

### Notes
- "Unused" is age plus the metrics ledger, because the host exposes no skill-invocation telemetry. No record
  is not proof of no use, which is why the action is quarantine rather than deletion and requires --apply.


## [1.5.1] — 2026-11-11

### Added
- **Autonomy policy** (rules/security.md 3.6, mirrored into the global AGENTS block): reversible environment work is
  done and reported with evidence and a rollback path; only destructive, irreversible, cross-major, outbound,
  paid or privileged actions stop for confirmation. "Needs your confirmation" is explicitly not a default answer.
- Tests for the new classification paths (55 total).

### Changed
- Lock drift is now judged by content: declared dependencies versus the lockfile's root importer. Rewriting a
  manifest without changing versions no longer looks like drift, and a version mismatch is caught even when
  timestamps happen to agree. Unrecognized lock shapes fall back to the old mtime heuristic.
- Plugin upgrades pass --config.strictDepBuilds=false, and a rejection caused by the 24h minimumReleaseAge
  policy (or ignored build scripts) is recorded as deferred instead of rolling back the whole batch.

### Fixed
- An environment where pnpm blocks dependency build scripts no longer makes every install/upgrade report failure
  after the packages were in fact installed correctly.


## [1.5.0] — 2026-11-11

### Added
- **Skill health** (health command + evo/skill_health.py, wired into envcheck as skill_health):
  per-skill integrity instead of per-skill presence. Detects docs that reference files which no longer exist,
  scripts that no longer compile, unparsable config, missing version metadata, oversized SKILL.md and long
  periods without change (180 days warn / 360 days high). Read-only, bounded, never executes skill code.
- **Create on demand** (new command + evo/scaffold.py): scaffold a valid skill from an identified capability gap
  in one step -- frontmatter, optional runnable script stub, optional tests -- with dry-run by default.
- **Verification gate** (verify command): frontmatter validity, SKILL.md size, every .py compiles, every .json
  parses, every referenced local path resolves, and an optional smoke command that must exit 0.
  A JSON-array form (--command '["python","--version"]') avoids all shell quoting traps.
- references/self-extension.md: when to reuse / install / build, and the rule that an unverified skill is not a capability.
- 7 new tests (50 total).

### Changed
- envcheck now reports skill_health alongside plugin_compat: an environment can be healthy while a skill's
  instructions are already broken.
- Weekly checklist gained an explicit step to act on bad/warn skills instead of only listing them.


## [1.4.0] — 2026-11-10

### Added
- **toolchain check** (envcheck): workspace markers (package.json / pyproject.toml / requirements.txt / .git) now
  declare which commands must exist. A missing node/python/git becomes a finding with an install hint instead of an
  invisible workaround. Rules are configurable via envcheck.toolchain.
- **Windows tooling layer**: scripts/windows/evolve-environment.ps1 and register-evolution-task.ps1, plus
  references/windows-tooling.json. Handles winget upgrades, Codex plugin marketplace refresh, marker-driven tool
  installation and quarantine of stale reports. Audit-only by default; every mutation needs an explicit switch.
- **Cross-agent availability**: install targets for DSH, ~/.agents, ~/.codex and ~/.claude. One canonical tree,
  the others are directory junctions maintained by install (foreign copies are backed up before replacement).
- references/merge-notes.md: provenance and decisions for folding the same-named Codex-era skill into this one.

### Changed
- Canonical source is ~/.dsh/skills/evolution only; no agent keeps its own divergent copy.
- 43 stdlib tests (added toolchain coverage).


## [1.3.2] — 2026-11-10

### Added
- **Plugin write-channel check** (plugin_channel): compares the profile's recorded pnpm store
  (node_modules/.modules.yaml storeDir) with the store the bundled pnpm requires. A profile
  installed by an older pnpm cannot accept dsh plugin add/remove at all
  (ERR_PNPM_UNEXPECTED_STORE), which makes "automatic plugin upgrade" silently impossible.
  Reported as a FAIL with the exact repair command; never auto-fixed because rebuilding
  node_modules is a dependency-tree change.
- 1 new test (42 total).

### Fixed
- The storeDir probe silently passed: its regex lost the backslash escapes when the module was
  generated, so the pattern could never match and the check always reported "ok".


## [1.3.0] — 2026-11-10

### Added
- **Environment currency check**（环境年代感体检）: new envcheck command and evo/envcheck.py. It answers one question the task-level review cannot: is the environment itself older or more broken than the model needs? Checks cover plugin-tree consistency (declared but missing / broken link / unpinned GitHub dependency), host-reported plugin incompatibility, lockfile drift, explicit model route and reasoning effort, runtime EOL windows, skill shadowing, evolution heartbeat staleness, and leftover clutter.
- **Host compatibility probe**: envcheck asks "dsh plugin list" which plugins the host refuses to load. An incompatible plugin stays installed and looks present while being denied at startup -- previously invisible.
- **Declutter**: cleanup command and evo/cleanup.py. Allowlist-only, grace-windowed deletion of byte-code caches, expired reports/logs/exports and (opt-in) restore backups. Dry-run by default; every item records size, age and reason.
- **Executable plugin upgrades**: plugins --apply and maintenance.apply_plugin_updates. Same-major only, snapshot plus package.json/lockfile backup, per-upgrade version verification, automatic rollback of the whole batch on the first failure. Cross-major, link: and github: dependencies are never touched.
- **Environment as a learning signal**: evolve consumes envcheck findings, so an outdated or inconsistent environment becomes a proposal in the self-evolution loop instead of an unexplained capability ceiling.
- **Weekly report section**: review now runs envcheck and includes an "环境年代感" section (score, route, top actions) plus an envcheck report index.
- references/envcheck.md; 12 new tests (41 total).

### Changed
- Weekly order is now: evidence -> envcheck -> evolve -> review --apply --online -> plugin upgrades -> cleanup -> report. Environment first, because everything else is measured through it.
- Install canonical source documented as ~/.dsh/skills/evolution (DSH user-dsh skill root, rank 400), replacing the stale ~/.agents/skills assumption.
- Unpinned/broken dependency findings and lock drift are reported with auto-fix hints; the bundle-membership signal is downgraded to info because dsh.profile.bundles is only one composition layer.

### Fixed
- cleanup no longer confuses the configured retention window with the hygiene threshold.
- An explicitly configured but missing paths.dsh_cli no longer falls back to another binary, which made envcheck results machine-dependent.


## [1.2.0] — 2026-11-08

### Added
- **Self-evolution loop**: `evolve` reads metrics/audit/maintenance evidence, generates improvement hypotheses, writes verified low-risk rules to `knowledge/learned.md` + `rules/learned.md`, records experiment baselines, and can bump/release its own version.
- **Versioned self-upgrade**: `evolve --apply --bump patch|minor --release` snapshots, updates VERSION/SKILL metadata/`__version__`/CHANGELOG, runs the full suite, and only keeps the release if tests pass; failure restores the previous files.
- **Experiment ledger**: `state/evolution/experiments.jsonl` and `versions.json`; `evolve --evaluate` checks later metrics and marks keep/regress.
- 29 stdlib tests.

### Changed
- 周任务只自动升级同 major 且来源可信的插件；跨 major 只报告，升级前 snapshot。
- 同内容技能保留 `~/.agents/skills` 规范源，其他位置改为 junction；替换前备份原目录。
- 每次维护先 snapshot，并为每个被替换技能单独备份；链接失败时恢复原目录；不删除用户手写技能。


## [1.1.0] — 2026-11-07

### Added
- **Autonomous weekly maintenance**: `maintain` and `review --apply` dedupe exact skill copies into junctions, quarantine invalid skills, check plugin/skill upgrades, and process `maintenance.install_queue` downloads.
- **Plugin upgrade check**: npm registry comparison for DSH profile dependencies; same-major candidates only; actual upgrade delegated to DSH `plugin_manager`.
- **Skill fetch**: GitHub repo/zip/local-directory install with zip-slip/size limits, frontmatter validation, static scan, license check, provenance, backup and optional links.
- **Maintenance safety**: snapshot before changes, per-target backups, quarantine with manifests, rollback on link failure, protected skills list, max action cap.
- **Config**: `maintenance.auto_apply`, `scope`, `max_actions`, `online`, `install_queue`, `require_license`, `protected_skills`, `plugin_updates`.
- 26 stdlib tests (added maintenance dedupe/quarantine and local zip fetch).

### Changed
- Weekly schedule prompt now runs `review --apply --online` and uses `plugin_manager` for safe plugin upgrades instead of read-only review.
- Global AGENTS block and security policy updated for autonomous maintenance with explicit stop conditions.
- Version bumped to 1.1.0.

## [1.0.0] — 2026-11-04

### Added
- Agent Skill (`SKILL.md`) with 7-step task loop, five principles, risk gates and cross-agent portability.
- Global rules: `rules/principles.md`, `precedence.md`, `weekly-review.md`, `security.md`, `knowledge.md`.
- Stdlib-only Python CLI (`scripts/evolution.py`) with:
  - `init/status/doctor/capabilities/recommend`
  - `audit` (skills, MCP, plugins, permissions, runtime, dependencies, hygiene)
  - `review` (weekly Markdown+JSON, metrics, upgrade candidates)
  - `record/stats` (success rate, p50/p95, tokens, cost, interventions, error classes)
  - `snapshot/history/diff/rollback` (SHA-256, atomic writes, pre-rollback snapshot)
  - `install/uninstall` (link/copy, managed `AGENTS.md` block, backups, safety lock)
  - `export/import` (portable bundle, manifest, hash verification, zip-slip limits)
  - `schedule` (agent-native first, OS scheduler fallback)
  - `knowledge/validate/selfcheck/test/prune/mcp`
- Minimal MCP stdio server with 8 tools.
- Agent-native weekly automation templates for DSH/Codex/Claude; Windows Task Scheduler and crontab fallback.
- Managed global rule block that preserves user content outside markers.
- Static security scanner: dangerous shell patterns, encoded PowerShell, secrets, plain HTTP, unpinned npx/pip, license gaps.
- Hash-pinned safety kernel: security rules, precedence, block template and safety config.
- 23 stdlib tests covering parsing, audit (incl. `.env` secrets and accepted risks), redaction, metrics, snapshots, install/rules/dry-run/idempotence, export/import, review, week validation, schedule and MCP.
- One-line installers (`install.ps1`, `install.sh`, `install.cmd`), versioned `release` bundles, and a GitHub Actions weekly-review template.
- Documentation: architecture, commands, portability, MCP, troubleshooting, project `AGENTS.md` template.

### Refinements after first local audit
- Tolerant frontmatter parser for valid multi-line/list/nested YAML; avoids false "invalid skill" findings.
- Secret detection ignores env/config references and ALL_CAPS identifiers; full secrets never appear in reports.
- HTTP/version-pin noise is limited to executable contexts; XML namespaces and docs links are ignored.
- Duplicate mirrors are aggregated and scanned once per content hash; license gaps are summarized.
- Health score distinguishes critical from a few high findings; copy installs carry a content signature and stale copies are detected by `doctor`.
- Native schedule can be confirmed with the real agent reminder id; OS scheduler is fallback-only and dry-run writes nothing.

### Design decisions
- Canonical skill source at `~/.agents/skills/evolution`; runtime data at `~/.agents/evolution`.
- Agent-native scheduling is preferred over OS tasks; in 1.0 weekly runs were read-only, superseded by 1.1 autonomous maintenance.
- Destructive commands default to dry-run; third-party code is never executed by the audit.
- Safety kernel changes require explicit `--accept-safety-change` and are snapshot-backed.

### Known limitations
- Static scanning cannot prove runtime behavior.
- Keyword recommendation is not semantic retrieval.
- DSH native reminders are session-scoped; persistent unattended runs need a durable agent automation.
- `AGENTS.md` reading depends on each agent implementation; `SKILL.md` is the reliable DSH entry.