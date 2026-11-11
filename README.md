# evolution — 全局 Agent 自优化与能力管理

一套可移植、可审计、可回滚的 Agent 全局优化系统，封装为 Agent Skill，兼容 DSH、Claude Code、Codex、通用 `AGENTS.md`、MCP 与 CI。

**目标：以最低综合成本，实现最高质量、效率与可靠性。** 不以节省资源为由降低验收标准。

## 它解决什么

| 问题 | 机制 |
|---|---|
| 重复造轮子 | 任务开始前 `capabilities` / `recommend` 盘点本机能力 |
| 环境膨胀 | `audit` 识别重复、无效、高风险、闲置技能与 MCP |
| 无量化改进 | `record` + `stats` 记录成功率/耗时/Token/费用/干预/失败根因 |
| 升级靠感觉 | 每周 `review` + 可选 GitHub release 检查 + 收益/风险表 |
| 改坏无法恢复 | 哈希快照、diff、rollback、备份、安全内核校验 |
| 跨 Agent 规则不一致 | 单一规范源 + `AGENTS.md` 受管块 + 链接/副本同步 |
| 周审阅靠人记 | **Agent 原生自动化**优先（DSH `schedule_create` 等），OS 计划任务仅回退 |
| 第三方组件风险 | 静态扫描（危险命令/密钥/明文 HTTP/未固定版本/许可证）+ 默认 dry-run |

## 快速开始

```bash
# 1) 自检（无副作用）
python scripts/evolution.py selfcheck --strict

# 2) 安装：默认 dry-run；确认后加 --apply
python scripts/evolution.py install
python scripts/evolution.py install --apply
# Windows 一键（默认 dry-run）：.\install.ps1  确认后：.\install.ps1 -Apply
# POSIX：./install.sh           确认后：./install.sh --apply
python scripts/evolution.py doctor

# 3) 盘点与建议
python scripts/evolution.py capabilities
python scripts/evolution.py recommend --task "审计本机技能并给出清理建议"

# 4) 审计 / 周审阅
python scripts/evolution.py audit
python scripts/evolution.py review

# 5) 一次完整的自主改进循环
python scripts/evolution.py improve
python scripts/evolution.py improve --apply --online

# 6) 原生每周自动化（首选）
python scripts/evolution.py schedule native --apply
# 然后在 DSH 中调用 schedule_create，使用 state/native-schedule.md 的 prompt。
# 仅无原生调度时：schedule install --driver os --apply
```

> Windows 下把 `python` 换成实际解释器路径，或使用 `~/.agents/evolution/bin/evolution.cmd`（安装后生成，需自行加入 PATH）。

## 目录

```
~/.agents/skills/evolution/        # 规范源（技能本体）
  SKILL.md                         # 跨 Agent 入口，按需加载
  rules/                           # 全局规则（原则/优先级/周审阅/安全/知识）
  references/                      # 架构、命令、迁移、MCP、排障
  templates/                       # AGENTS 块、原生自动化、项目规则模板
  scripts/evolution.py             # CLI 入口（stdlib only）
  scripts/evo/                     # CLI 模块
  tests/                           # 19+ 单元/集成测试
~/.agents/evolution/               # 运行时数据
  config.json
  state/                           # 指标、快照、安装清单、安全锁、调度状态
  reports/                         # 审计与周报（Markdown + JSON）
  knowledge/                       # 跨项目经验/决策
  backups/  logs/  exports/  bin/
```

## 跨 Agent 安装矩阵

| Agent | 技能位置 | 规则入口 | 说明 |
|---|---|---|---|
| DSH | `~/.dsh/skills/evolution`（链接/副本） | `SKILL.md` + `~/.dsh/AGENTS.md` 受管块（本机实测会注入） | 也会扫描 `~/.agents/skills`（rank 500） |
| 通用 | `~/.agents/skills/evolution` | `~/.agents/AGENTS.md` | 单一规范源 |
| Claude Code | `~/.claude/skills/evolution` | `~/.claude/CLAUDE.md` 指向 Codex 全局规则 | 链接或副本 |
| Codex | `~/.codex/skills/evolution` | `~/.codex/AGENTS.md` 受管块 | 现有全局规则保留 |
| 任意 MCP 客户端 | — | `evolution mcp`（stdio） | 8 个只读/低风险工具 |
| CI | — | `review --fail-on high` | 报告作为 artifact |

`AGENTS.md` 受管块只替换 `<!-- EVOLUTION:BEGIN ... -->` 与 `<!-- EVOLUTION:END -->` 之间的内容，块外原文保留；写入前自动备份。

## 命令总览

`init/status/doctor/capabilities/recommend/audit/review/improve/reflect/evaluate/evolve/maintain/plugins/fetch/record/stats/snapshot/history/diff/rollback/install/uninstall/export/import/release/schedule/knowledge/validate/selfcheck/test/prune/mcp`

CI 模板：`templates/github-actions.yml`（复制到 `.github/workflows/`）；安装包装器：`install.ps1` / `install.sh` / `install.cmd`。

完整参数、退出码、示例见 [`references/commands.md`](references/commands.md)。

### 默认安全行为

- `install/uninstall/rollback/import/schedule/prune` **默认 dry-run**，需 `--apply`。
- 审计/扫描**不执行**被审代码，不联网（除非 `--online`）。
- 报告对密钥形态脱敏；不读取无关隐私文件。
- 每次受管变更前自动快照；写入原子化；并发单实例锁。
- 安全内核（`rules/security.md`、`rules/precedence.md`、AGENTS 块模板、安全配置）有哈希锁；变更后必须 `--accept-safety-change` 才能安装，防止 Agent 静默放宽自己的边界。
- **周任务默认自我进化 + 自主维护**（用户 2026-11-07/08 授权）：先从指标、审计、维护证据生成学习规则；有新学习项时跑完整测试并升级自身版本；再去重、隔离、检查/升级插件与技能、处理按需下载和重复工作流生成。活动技能先记录来源并隔离，达到保留期后 `--purge` 才清理。跨 major、付费、敏感外发和用户手写技能不自动处理。

## 每周自我进化与审阅

```bash
python scripts/evolution.py improve --apply --online       # 环境→审计→复盘→维护→学习
python scripts/evolution.py reflect --days 30              # 从任务记录提取技能提案
python scripts/evolution.py evolve --apply --bump patch --release  # 学习→测试→自升级→发布
python scripts/evolution.py evolve --evaluate                      # 实验复盘
python scripts/evolution.py review --apply --online   # 维护 + 审阅
python scripts/evolution.py maintain --apply --online # 只做维护
python scripts/evolution.py plugins --online          # 插件升级候选
python scripts/evolution.py fetch --source github:owner/repo --name skill --apply
python scripts/evolution.py review --week 2026-W45
```

`evolve` 的证据门：样本 <5 只记录、不调策略；失败集中在某类错误才生成对应规则；低风险规则写入 `knowledge/learned.md` 与 `rules/learned.md`；版本升级必须通过完整测试，失败恢复旧版本；实验以 `state/evolution/experiments.jsonl` 跟踪，`--evaluate` 用后续指标判断 keep/regress。

技能内容的优化使用 `evaluate`。候选技能必须提供训练集和留出集分数，基线波动、实际增益和函数保留率同时通过才会晋级。评估输入是一个 JSON 文件，例如：

```json
{
  "skill": "search-skill",
  "candidate": "C:/tmp/search-candidate/SKILL.md",
  "target": "C:/Users/me/.agents/skills/search-skill/SKILL.md",
  "train_before": [70, 71, 70],
  "train_after": [75, 76, 75],
  "holdout_before": [68, 69],
  "holdout_after": [72, 73],
  "functions_before": ["search", "verify"],
  "functions_after": ["search", "verify", "report"]
}
```

```bash
python scripts/evolution.py evaluate --input evaluation.json --apply
```

报告：`~/.agents/evolution/reports/weekly/YYYY-Www.md` 与 `.json`；索引 `reports/index.md`。

**Agent 原生自动化优先**：DSH 用 `schedule_create`，Codex/Claude 用各自 automations。`schedule native --apply` 会生成可直接使用的 prompt 和记录；OS 计划任务/cron 只在平台无原生调度时使用。

## 安全模型

1. **规范源与运行时分离**：技能代码可版本化，数据/报告不混入技能目录。
2. **安全内核哈希锁**：规则与授权边界不被自优化流程静默修改。
3. **事务式变更**：快照 → 预检 → 临时写入 → 验证 → 提交；失败回滚。
4. **最小权限**：不主动提权，不读取凭据，不执行第三方代码。
5. **可审计**：每次安装/回滚/导出都有 manifest 与哈希；报告列明未检查项。
6. **不伪造**：无法执行的检查明确标注；不把静态扫描说成运行时验证。
7. **显式风险接受**：`config.security.accepted_risks` 必须写 reason/日期/可选到期；报告仍标 `(accepted)` 并保留理由，但不参与评分与 fail-on；critical 不可接受。

详细规则：[`rules/security.md`](rules/security.md)、[`rules/precedence.md`](rules/precedence.md)。

## 测试

```bash
python -m unittest discover -s tests -v
python scripts/evolution.py selfcheck --strict
```

测试覆盖（70 项）：frontmatter、发现/去重、危险命令与密钥脱敏、指标、快照/diff/回滚、安装/规则块/卸载、dry-run 无副作用、导出/导入与 zip-slip、周报、周号校验、原生调度、MCP 初始化/工具调用、显式接受风险、自动维护去重/隔离、按需下载、自我进化诊断/版本升级、留出集技能评估、复盘提案、自动生成技能、工具安装队列、隔离区清理、改进闭环与快照重试。

## 已知限制

- 静态扫描不能替代沙箱/运行时行为分析；不会执行被审代码。
- 关键词推荐不是语义检索；中文按二字切分，可能漏匹配。
- DSH 原生调度是会话级提醒；长期无人值守需常驻会话或平台持久自动化。
- `AGENTS.md` 是否被某 Agent 读取取决于该 Agent 的实现；`SKILL.md` 是 DSH 的可靠入口。
- 回滚只恢复清单内文件，不删除快照后新增的文件（避免误删）。
- GitHub 检查受未认证速率限制（60 次/小时），默认离线。

## 卸载 / 迁移

```bash
python scripts/evolution.py uninstall                 # dry-run
python scripts/evolution.py uninstall --apply         # 移除链接/副本与受管块，保留数据
python scripts/evolution.py uninstall --purge --yes --apply  # 额外删除 config/调度状态
python scripts/evolution.py export --out evolution.zip
python scripts/evolution.py import --from evolution.zip --verify-only
python scripts/evolution.py import --from evolution.zip --apply --force
```

迁移细节见 [`references/portability.md`](references/portability.md)。

## 版本

`VERSION` + `CHANGELOG.md` + 快照。语义化版本；破坏性变更升 major。发布用 `export` 生成带 SHA-256 清单的包。

## License

MIT。详见 `LICENSE`。
