# 命令参考

入口：`python ~/.agents/skills/evolution/scripts/evolution.py [全局选项] <命令> [参数]`

## 全局选项

| 选项 | 说明 |
|---|---|
| `--home PATH` | 运行时目录；默认 `~/.agents/evolution` 或 `$EVOLUTION_HOME` |
| `--os-home PATH` | 模拟用户主目录（测试/迁移）；影响 `~` 解析 |
| `--json` | 仅输出 JSON（适合 Agent/CI） |
| `--quiet` | 抑制人类可读输出 |
| `--version` | 版本 |

退出码：`0` 成功；`1` 失败/发现超过阈值；`2` 参数/配置/IO 错误；`3` 安全拒绝（需授权/需确认）。

## 初始化与诊断

```bash
evolution.py init [--force]
evolution.py status
evolution.py doctor
evolution.py selfcheck [--strict]
evolution.py test [--verbose]
evolution.py validate [--path PATH] [--bundle ZIP]
```

- `init`：创建运行时目录与默认配置。
- `status`：版本、路径、能力计数、调度、安全锁。
- `doctor`：安装/目标/规则/安全锁/调度检查；失败返回 1。
- `selfcheck`：SKILL frontmatter、版本一致、必需文件、Python 语法、安全扫描；`--strict` 跑测试。
- `validate`：校验单个技能或导出包。

## 环境年代感体检

```bash
evolution.py envcheck [--online] [--days 30] [--print-report] [--fail-on high]
```

回答"环境本身是否已经比模型需要的更旧/更不一致"。11 项检查（详见 `references/envcheck.md`）：

| 检查 | 判定 |
|---|---|
| dsh_cli / dsh_runtime | 启动器可用、宿主版本、是否预发布通道 |
| plugin_tree | 声明未装、断链 link:、未固定 GitHub 依赖、未挂 bundles（info） |
| plugin_compat | **宿主自述启动拒绝的插件**（最高信号） |
| lock_drift | pnpm-lock / .modules.yaml 早于 package.json |
| model_route | provider/model 缺失或 reasoningEffort 偏低 |
| web_tools | searchProvider/fetchProvider |
| runtime_eol | Python/Node 是否已 EOL 或 180 天内到期 |
| skill_shadow | 本地技能（秩 400）覆盖插件技能（秩 600） |
| heartbeat | 最近周报距今 >14/28 天 |
| hygiene | 备份残留、__pycache__、sessions/logs 体量 |

输出 `reports/envcheck/<时间>.md|json` 与 `state/envcheck-latest.json`；退出码由 `--fail-on` 决定。
默认离线；`--online` 追加 npm/EOL 查询。

## 断舍离

```bash
evolution.py cleanup [--apply] [--days 30] [--grace-days 7] [--include-backups] [--no-logs]
```

白名单删除：字节码/工具缓存、超保留期的周报/审计/体检报告、过期日志与导出、（可选）还原备份。
宽限期内的文件一律保留；默认 dry-run；每项记录大小、年龄与理由。不删除技能正文、配置、用户手写文件。

## 能力盘点与建议

```bash
evolution.py capabilities [--agent NAME]
evolution.py recommend --task "..." [--limit 5]
```

- `capabilities`：技能/MCP/插件/权限/运行时/依赖文件。
- `recommend`：本地词元匹配；输出候选、分数、理由、缺口判断。**不是语义检索**。

## 审计

```bash
evolution.py audit [--scope skills] [--scope mcp] [--online] [--format md|json] [--out PATH] [--fail-on high]
```

范围：`skills, mcp, plugins, permissions, runtime, dependencies, hygiene`（可重复）。

输出：`reports/audit/audit-<stamp>.md` + `.json`，并写 `state/audit-latest.json`。

发现等级：`critical/high/medium/low/info`。默认 `--fail-on` 取 `config.security.fail_on`（`high`）。

**显式接受风险**：在 `config.security.accepted_risks` 增加
`{"kind":"permission","title_contains":"defaultPreset","reason":"...","accepted_at":"2026-11-04","expires_at":"2027-01-01"}`。
报告仍显示 `(accepted)` 与理由，但该条不参与健康分、`next_actions`、`--fail-on`；`critical` 不能被接受。

检查内容：
- 同名技能镜像/冲突、无效 frontmatter、超大 SKILL、描述近似重复
- 危险命令、编码 PowerShell、密钥形态、明文 HTTP、未固定 npx/pip
- MCP 同名不同实现、敏感环境变量名、明文端点
- 权限预设（danger-full-access / approval never / bypassPermissions）
- Python 版本、磁盘、运行时可写
- 快照/报告/知识数量、周调度缺失
- 许可证缺口

## 每周审阅

```bash
evolution.py review [--week 2026-W45] [--online] [--no-audit] [--days 30] [--print-report] [--apply] [--fail-on none]
```

产物：`reports/weekly/<week>.md` + `.json`，`state/review-latest.json`，`reports/index.md`。

- 默认离线；`--online` 检查 `config.upgrade_watch` 的 GitHub release/元数据（缓存 24h，未认证限流 60/h）。
- `--apply`：先执行 `maintain --apply`，再做审计与报告；周任务使用此模式。
- `--no-audit` 复用 `state/audit-latest.json`。
- `--fail-on` 可用于 CI 门禁；默认 `none`，CI 可设 `high`。

## 自我进化

```bash
evolution.py evolve [--apply] [--bump none|patch|minor|major] [--release] [--force-bump] [--evaluate] [--days 30]
```

- `evolve` 读取 `state/metrics.jsonl`、`state/audit-latest.json`、`state/maintenance-latest.json`，生成改进假设。
- 样本 <5：只记录，不调策略；样本足够：按错误类型生成检查规则。
- `--apply` 把低风险规则写入 `knowledge/learned.md` 与 `rules/learned.md`，并记录实验基线。
- 有新学习项且 `--bump` 非 none 时：snapshot → 更新 VERSION/SKILL/`__version__`/CHANGELOG → 跑完整测试 → 导出 release；测试失败恢复旧文件，不保留新版本。
- `--evaluate` 用实验创建后的指标判断 keep/regress。
- `--force-bump` 仅用于显式的功能发布，不用于日常周任务。

## 自主维护

```bash
evolution.py maintain [--apply] [--dry-run] [--online|--offline] [--scope skills,plugins] [--max-actions 30]
evolution.py plugins [--online] [--apply] [--include-major] [--max-actions 5]
evolution.py fetch --source github:owner/repo [--name skill] [--ref main] [--path subdir] [--apply] [--no-link]
```

`maintain` 自动执行：
- 同内容技能去重：保留 `~/.agents/skills` 规范源，其他位置改为 junction；原目录备份。
- 无效技能隔离：移到 `state/quarantine/<时间>/<agent>/`，不永久删除。
- 插件升级检查：对比 npm registry，输出同 major/跨 major 候选；跨 major 不自动升级。
- `install_queue` 按需下载：来源、许可证、静态扫描通过后安装。

配置：`config.maintenance.auto_apply`、`scope`、`max_actions`、`install_queue`、`require_license`、`protected_skills`。默认 `auto_apply=true`；手动可用 `--dry-run`。
所有删除/替换先 snapshot + 单独备份；失败恢复；记录 `state/maintenance-latest.json` 与 `reports/maintenance/`。

`plugins` 默认只读检查；`--apply` 通过宿主 CLI（`dsh plugin --profile <p> add <name>@<ver>`）执行同 major 升级：
先 snapshot + 备份 package.json/pnpm-lock.yaml，逐个校验安装后版本，任一失败则整体回滚并重装。
跨 major、`link:`、`github:` 依赖永不被改写。升级写入后**必须重启 DSH 才生效**，结果含 `requires_restart`。
`fetch` 支持 GitHub 仓库/zip/本地目录；拒绝路径穿越、超大包、高风险脚本；默认 dry-run。

## 指标

```bash
evolution.py record --task "..." --outcome success \
  [--duration 120] [--tokens 12000] [--cost 0.03] [--tools 8] \
  [--interventions 0] [--retries 0] [--quality 4.5] \
  [--error-class tool-failure] [--notes "..."] [--tags a,b] \
  [--agent dsh] [--model deepseek-flash] [--skill evolution]

evolution.py stats [--days 30]
```

- outcome：`success|partial|failed|blocked|rolled_back`。
- error_class：`info-gap|tool-failure|permission|wrong-approach|insufficient-verification|external-dependency|timeout|unknown`。
- 指标写入 `state/metrics.jsonl`；任务标签与备注脱敏；不采集完整对话。
- `stats`：成功率、部分/失败/回滚率、p50/p95 耗时与 Token、费用、干预率、重试率、失败根因、标签。

## 快照 / 回滚

```bash
evolution.py snapshot [--label before-upgrade]
evolution.py history [--limit 20]
evolution.py diff --from <snapshot-id> [--to current|<id>]
evolution.py rollback --snapshot <id>              # dry-run
evolution.py rollback --snapshot <id> --apply
```

- 快照覆盖：`config.json`、`rules/*.md`、`SKILL.md`、`VERSION`、`templates/AGENTS.block.md`、安装清单、AGENTS 块、安全锁、调度状态。
- `rollback` 前自动创建 `pre-rollback` 快照并备份当前文件；恢复后校验哈希。
- 只恢复清单内文件；不删除快照后新增文件。

## 安装 / 卸载

```bash
evolution.py install [--targets dsh,agents,claude,codex] [--mode link|copy] \
  [--rules-only] [--no-rules] [--force] [--accept-safety-change] [--apply]
evolution.py uninstall [--targets ...] [--purge --yes] [--apply]
```

- 默认 dry-run；`--apply` 才写。
- 目标已存在且非 evolution 管理 → 冲突，需 `--force`（会备份后替换）。
- `--mode link`：Windows junction / POSIX symlink；失败回退 copy。
- 规则受管块写入 `~/.codex/AGENTS.md`、`~/.agents/AGENTS.md`、`~/.dsh/AGENTS.md`；块外内容保留。
- 安全内核变更需 `--accept-safety-change`。
- `uninstall` 只移除指向规范源的链接或带 marker 的副本；不改用户手写规则块外内容。
- `--purge` 额外删除 config/调度状态，必须 `--yes`。

## 导出 / 导入 / 发布

```bash
evolution.py export [--out bundle.zip] [--include-knowledge]
evolution.py release [--version 1.2.0] [--out PATH]
evolution.py import --from bundle.zip --verify-only
evolution.py import --from bundle.zip [--dest DIR] [--force] [--apply]
```

- `release`：生成版本化发布包到 `state/releases/evolution-<version>.zip`，带 SHA-256；版本必须与 `VERSION` 一致。

- `export`：技能包 + SHA-256 清单 + manifest；默认不含运行时指标/报告。
- `verify`：拒绝绝对路径、`..`、超限文件数/体积、缺失/错误哈希、非 evolution 包。
- `import`：默认 dry-run；`--apply` 解压到 `<dest>/evolution`（默认 `~/.agents/skills`）；已存在需 `--force`（备份）。

## 调度

```bash
evolution.py schedule native [--apply]
evolution.py schedule install [--driver native|os] [--time 20:00] [--day SUN] [--apply]
evolution.py schedule status
evolution.py schedule remove [--driver native|os] [--apply]
```

- 首选 Agent 原生自动化；`native` 只记录 prompt/时间，需在 Agent 内用 `schedule_create` 等工具创建。
- `--driver os` 仅回退：Windows 当前用户计划任务；POSIX 带标记 crontab 行。
- 周任务 = `review --apply --online`：自主维护 + 审计 + 报告；插件升级由 `plugin_manager` 执行。

## 知识库

```bash
evolution.py knowledge add --title "..." --body "..." [--tags a,b] [--apply]
evolution.py knowledge list
evolution.py knowledge search --query "..."
```

写入 `knowledge/lessons.md`；默认 dry-run；标题+正文片段重复时不写入。

## 清理

```bash
evolution.py prune --kind snapshots|reports|all --days 90 [--apply]
```

仅清理 evolution 自己的快照/报告；默认 dry-run；不触碰第三方技能。

## MCP

```bash
evolution.py mcp
```

JSON-RPC 2.0 over stdio，换行分隔；日志只写 stderr。工具见 `references/mcp.md`。

## 常见组合

```bash
# CI 门禁
evolution.py audit --fail-on high

# 升级前
evolution.py snapshot --label before-upgrade
# 升级后
evolution.py doctor && evolution.py selfcheck --strict
# 失败
evolution.py rollback --snapshot <id> --apply

# 周审阅（Agent 原生调度 prompt）
python .../evolution.py review --quiet
```
