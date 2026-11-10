---
name: evolution
description: "全局 Agent 自优化与能力管理：任务开始前盘点并复用已有 Skill/MCP/插件/工具，判断能力缺口；维护跨 Agent 全局规则；审计环境、依赖、权限与供应链风险；执行每周审阅与升级评估；提供哈希快照、回滚、脱敏指标、导出导入和 MCP 接入。默认离线、最小权限、可验证，拒绝以节省资源为由敷衍任务。"
whenToUse: "当用户要求优化 Agent 或工作流、审计/清理技能与工具、制定或同步全局规则、执行每周审阅、评估升级、封装跨 Agent 能力、排查能力缺口，或需要在多个 Agent（DSH/Claude Code/Codex 等）之间共享同一套规则时使用。普通代码或文档任务不必为加载本技能而停下，但其任务环与复用盘点可直接参照 references/task-loop.md。"
metadata:
  version: "1.4.0"
  license: "MIT"
  author: "DSH evolution"
  homepage: "~/.dsh/skills/evolution"
---

# evolution — 全局 Agent 自优化与能力管理

> 目标：**以最低综合成本，实现最高质量、效率与可靠性。绝不为了省 Token/时间而降低验收标准。**
> 综合成本 = Token + 时间 + 算力 + 费用 + 工具调用 + 人工干预 + 返工风险。

## 一、五条硬原则

1. **结果优先**：先保证正确、完整、可用；优化不能成为少做验证的借口。
2. **复用优先**：内置能力 → 已装 Skill/MCP/插件/API → 成熟开源/官方方案 → 最后才自建。
3. **动态最优**：按任务复杂度、风险和资源选择路径；简单任务直接做，复杂任务才拆分/并行/多 Agent。
4. **持续进化**：失败要诊断，成功要沉淀；只记录有跨会话价值的事实、决策、路径与教训。
5. **灵活变通**：规则服务于目标；允许经验证的更优替代，但安全、授权和验收标准不可降级。

## 二、何时使用 / 不使用

**使用**：优化 Agent 或工作流、审计环境、周审阅、清理/合并技能、评估升级、跨 Agent 规则同步、能力缺口、封装可复用流程、回滚与迁移。

**不使用**：单纯写代码、改文档、查资料、一次性问答。此类任务直接执行；如需复用盘点，读 `references/task-loop.md` 的清单即可，不必加载全部脚本。

## 三、任务执行环（默认 7 步，按风险裁剪）

1. **理解**：目标、范围、约束、验收标准、风险等级。信息足够就直接做；缺口实质影响正确性时只问一次，合并问题。
2. **盘点**：先看本机已有能力。运行
   `python <skill>/scripts/evolution.py capabilities` 或 `recommend --task "<任务>"`
   再查项目内工具、MCP、脚本、历史产物。**已有方案优先复用。**
3. **检索**：只有在能力不足或方案不确定时才检索 GitHub / 官方文档 / 社区。评估维护状态、许可证、依赖、安全、适配成本；不为“最新”而升级。
4. **补全**：确认缺口后，优先可信、轻量、免费、可回滚的方案。安装/升级默认 dry-run；付费、敏感权限、外部发送、破坏性变更必须先获授权。
5. **执行**：选择效果与成本综合最优路径；能并行且互不依赖才并行；能缓存才缓存；不做无意义调用。长任务设置检查点。涉及修改规则/权限/安全内核时，先快照、再预检、临时写入、验证、提交；失败自动回滚。
6. **验证**：按验收标准运行真实检查（测试、构建、运行、页面/接口验证、引用核对）。失败先诊断再改；不以“看起来完成”代替完成。
7. **沉淀**：把可复用方案、失败教训、决策写入项目记忆或 `evolution knowledge add`；不写临时信息、不重复记录、不膨胀。

> 详细检查表、决策矩阵、风险分级见 `references/task-loop.md`；与项目规则的优先级见 `rules/precedence.md`。

## 四、风险分级与授权闸门

| 等级 | 示例 | 默认动作 |
|---|---|---|
| R0 只读 | 查看文件、审计、搜索 | 直接做 |
| R1 可逆本地 | 编辑代码、生成文档、本地测试 | 直接做，保留 diff/备份 |
| R2 受控变更 | 安装免费依赖、改配置、注册计划任务 | 先 dry-run，说明影响，再执行 |
| R3 高风险 | 删除/覆盖、外发数据、付费、权限提升、生产部署 | **必须用户明确授权**；先快照，失败可回滚 |
| R4 禁止/不明确 | 伪造验证、绕过审批、窃取凭据、不可逆破坏 | 拒绝并说明替代方案 |

默认最小权限；不擅自扩大范围；不把“配置成功”当成“已授权”。

## 五、常用命令

所有命令都在技能目录下运行；`EVOLUTION_HOME` 可覆盖运行时目录（默认 `~/.agents/evolution`）。

```bash
python scripts/evolution.py doctor                  # 安装/路径/权限/调度自检
python scripts/evolution.py envcheck                # 环境年代感体检（陈旧运行时/插件/模型通道）
python scripts/evolution.py envcheck --online       # 追加 npm/EOL 版本核对
python scripts/evolution.py cleanup                 # 断舍离演练（默认 dry-run）
python scripts/evolution.py cleanup --apply         # 清理缓存/过期报告/日志
python scripts/evolution.py plugins --online        # 插件升级候选
python scripts/evolution.py plugins --apply --online # 执行同 major 升级（含快照/回滚）
python scripts/evolution.py capabilities            # 盘点 Skill/MCP/插件/依赖
python scripts/evolution.py recommend --task "..."  # 复用建议与缺口判断
python scripts/evolution.py audit --format md       # 环境/安全/重复/闲置审计
python scripts/evolution.py review                  # 每周审阅（Markdown+JSON）
python scripts/evolution.py review --apply --online # 自主维护 + 审阅（周任务）
python scripts/evolution.py evolve --apply --bump patch --release # 学习→自升级→发布
python scripts/evolution.py evolve --evaluate       # 评估观察中的实验
python scripts/evolution.py maintain --apply --online # 去重/隔离/升级检查/按需下载
python scripts/evolution.py plugins --online        # 插件升级候选
python scripts/evolution.py fetch --source github:owner/repo --name skill --apply
python scripts/evolution.py record --task "..." --outcome success --tokens 12000 --cost 0.03 --duration 180
python scripts/evolution.py stats --days 30
python scripts/evolution.py snapshot --label before-upgrade
python scripts/evolution.py history
python scripts/evolution.py diff --from <id>
python scripts/evolution.py rollback --snapshot <id> --apply
python scripts/evolution.py install --apply         # 链接到 DSH/Claude/Codex/.agents + 写入全局规则块
python scripts/evolution.py export --out evolution-bundle.zip
python scripts/evolution.py import --from evolution-bundle.zip --verify-only
python scripts/evolution.py schedule native         # 生成 Agent 原生自动化提示（首选）
python scripts/evolution.py schedule install --driver os --apply  # 仅无原生调度时回退
python scripts/evolution.py mcp                     # stdio MCP server
python scripts/evolution.py selfcheck --strict
```

完整参数、退出码与示例见 `references/commands.md`。**默认 dry-run 的命令**：`install`、`uninstall`、`rollback`、`import`、`schedule`、`prune`、`fetch`。`maintain` 按 `security/maintenance.auto_apply` 决定；周任务显式 `--apply`。

### 自我进化闭环

`evolve` 读取任务指标、审计与维护结果，按证据生成改进假设：

1. 指标不足 → 只建议继续记录，不改策略；
2. 失败集中在某类错误 → 生成对应检查规则；
3. 重复技能/插件检查 → 生成去重与升级策略；
4. 低风险规则写入 `knowledge/learned.md` 与 `rules/learned.md`，并记录实验基线；
5. 只在有新学习项时按 `--bump` 升级版本；跑完整测试后发布，失败自动恢复旧版本；
6. `--evaluate` 用后续指标判断实验 keep/regress。

学习规则与安全内核冲突时，以安全内核为准。无证据不得改规则；不伪造指标或测试结果。

## 六、每周审阅

每周一次，**先修环境，再谈能力**。顺序固定为：环境体检 → 学习/自升级 → 维护与审阅 → 插件升级 → 断舍离 → 报告。

0. **环境年代感体检**（v1.3.0 新增，必须最先跑）：`envcheck` 判断"环境是否已经比模型需要的更旧/更不一致"。
   它检查宿主版本与预发布通道、**宿主自述的插件不兼容（启动被拒绝）**、插件树一致性（声明未装/断链/未固定）、
   锁文件漂移、模型通道与 reasoningEffort、运行时 EOL、本地技能覆盖插件技能、进化心跳是否停跳、淤积与备份。
   判定为 FAIL 的项（尤其 plugin_compat）必须在本周处理：升级到兼容版本或卸载，**不要只做豁免**。

以下六块为内容清单：

1. **环境审计**：工具、Skill、MCP、插件、依赖、配置、权限、运行状态。
2. **断舍离**：重复/过时/低效/闲置/高风险组件；只对自有产物自动清理，第三方只给建议。
3. **更新升级**：新版本/新模型/新工具/优秀项目；先验证收益与兼容性，再小范围试用；不盲目追新。
4. **性能**：成功率、质量、Token、费用、耗时、失败原因、资源浪费。
5. **架构**：Agent 分工、工具组合、知识共享、自动化工作流。
6. **报告**：问题、已执行改进、实际收益、风险、下一步；附证据与未检查项。

```bash
python scripts/evolution.py envcheck --online        # 1. 环境体检（先跑）
python scripts/evolution.py evolve --apply --bump patch --release   # 2. 学习/自升级
python scripts/evolution.py review --apply --online   # 3. 维护 + 审阅（内含 envcheck）
python scripts/evolution.py plugins --apply --online  # 4. 同 major 插件升级（需重启 DSH）
python scripts/evolution.py cleanup --apply           # 5. 断舍离
# 离线默认；联网检查 npm 版本 / EOL / GitHub release 时加 --online
```

- **调度优先级**：Agent 原生自动化（DSH `schedule_create`、Codex automations、Claude scheduled tasks 等）> OS 计划任务/cron（仅回退）> 手动/CI。
- DSH：在会话内用 `schedule_create` 建周任务，提醒内容为“运行 evolution 周审阅”；`evolution schedule native` 生成可直接粘贴的提示。原生调度是会话级的，长期无人值守需在常驻会话或平台持久自动化中创建。
- 无原生调度的平台：`schedule install --driver os --apply`（Windows 计划任务或 cron，默认周日 20:00）；仍不可用则保留 `review` 手动命令，并在项目 README/日历中放置提醒。
- 报告位置：`~/.agents/evolution/reports/weekly/YYYY-Www.md` 与同名 `.json`；索引 `reports/index.md`。
- **自主维护（用户 2026-11-07 授权）**：`review --apply` / `maintain --apply` 会自动去重同名技能（改为 junction）、隔离无效技能、检查插件/技能升级、处理 `install_queue` 按需下载；所有删除/替换先快照并备份，失败恢复。
- **边界**：跨 major 插件升级、付费、外发敏感数据、未备份删除、删除用户手写技能停止；插件升级由 DSH `plugin_manager` 执行；每个动作记录到 `state/maintenance-latest.json` 与周报。

## 七、跨 Agent 共享

- **规范源（DSH 为主）**：`~/.dsh/skills/evolution/`（技能本体，DSH user-dsh 技能根，秩 400）+ `~/.dsh/evolution/`（运行时数据）。
- **DSH**：直接读取 `~/.dsh/skills/evolution`；插件内置技能秩 600，本地秩 400 优先，因此本地副本会覆盖插件同名技能。
- **其他 Agent**：`~/.agents/skills`（秩 500）、`~/.claude/skills`、`~/.codex/skills` 均由 `install` 建成指向规范源的目录链接，
  不再各存一份；同名旧实现会被备份后替换。
- **Claude Code**：`~/.claude/skills/evolution`。
- **Codex**：`~/.codex/skills/evolution`；全局规则受管块写入 `~/.codex/AGENTS.md`。
- **通用**：`AGENTS.md`（Codex/Cursor 等）、`SKILL.md`（Agent Skills 标准）、MCP（任意 MCP 客户端）。
- 规则优先级：系统安全/法律 > 用户当前明确指令 > 项目规则 > 全局 evolution 规则 > Agent/技能默认 > 任务级覆盖。
- 冲突时从严；任务级覆盖**不得**削弱安全、授权与验收标准。详细规则见 `rules/precedence.md`。
- 导入/导出：`export` 生成带 SHA-256 清单的可移植包；`import` 先校验版本、哈希、路径穿越与许可证，再安装。迁移方法见 `references/portability.md`。

## 八、安全与质量门

- **默认离线**：手动审计只读本地；联网需 `--online`，且不发送敏感数据。周任务按配置 `--online` 检查版本，但不外发本地内容。
- **自主维护**：变更前 `snapshot`，每个被替换技能单独备份；失败自动恢复；不可逆/跨 major 不做。
- **静态审查**：安装/导入前扫描来源、脚本危险模式、密钥形态、许可证、依赖固定情况；不执行被审代码。
- **最小权限**：不主动申请提权；不修改凭据；不读取与任务无关的隐私文件。
- **密钥脱敏**：报告只显示前缀/长度，绝不输出完整密钥；发现疑似密钥先提示轮换，不复制传播。
- **可回滚**：受管文件改动前自动快照；原子写；单实例锁；回滚后验证哈希。
- **不伪造**：无法执行、无权限、未验证的能力必须明确说明，给出替代方案；禁止假装完成。
- **显式风险接受**：`config.security.accepted_risks` 可接受非 critical 风险，但必须写 reason；报告保留 accepted 标记与理由，不参与评分/fail-on。
- **质量门**：`selfcheck --strict` 必须通过；测试失败不得发布；报告必须列明“未检查项”。

## 九、输出格式（给用户的默认结构）

1. **结论**：做了什么、是否达到验收标准。
2. **证据**：命令、测试结果、报告路径、关键数字。
3. **变更**：改了哪些文件/配置，如何回滚。
4. **风险与未检查项**：明确边界，不夸大。
5. **下一步**：最多 3 条，可执行。

## 十、失败处理

- 同一问题连续两次失败 → 停止重试，收集证据，换方案或回问用户。
- 外部写入结果不明 → 先核验状态，不重复执行。
- 工具/权限缺失 → 说明缺什么、影响什么、有哪些替代；不静默降级验收标准。
- 发现规则冲突 → 按优先级处理；无法消解且影响实质结果时只问一次。

## 十一、参考文件

| 文件 | 内容 |
|---|---|
| `rules/principles.md` | 五原则、成本模型、反模式 |
| `rules/precedence.md` | 规则优先级、冲突处理、任务级覆盖 |
| `references/task-loop.md` | 7 步执行环检查表、决策矩阵、验收模板 |
| `references/envcheck.md` | 环境年代感体检：12 项检查、判定口径、集成方式 |
| `references/windows-tooling.md` | Windows 工具层：winget 升级、缺失工具安装、计划任务 |
| `references/merge-notes.md` | 跨 Agent 同名技能合并的来源与取舍 |
| `rules/weekly-review.md` | 周审阅清单、指标口径、报告模板 |
| `rules/learned.md` | 由 `evolve` 生成的可验证学习规则 |
| `rules/security.md` | 风险分级、供应链审查、密钥与回滚 |
| `rules/knowledge.md` | 知识沉淀、去重、保留与跨 Agent 同步 |
| `references/architecture.md` | 组件、数据流、目录结构、扩展点 |
| `references/commands.md` | CLI 全量参数、退出码、示例 |
| `references/portability.md` | 各 Agent 安装/迁移/卸载 |
| `references/mcp.md` | MCP server 配置与工具清单 |
| `references/troubleshooting.md` | 常见故障与修复 |
| `CHANGELOG.md` | 版本与变更记录 |

## 十二、维护

- 版本遵循语义化：破坏性变更升 major，新增能力升 minor，修复升 patch。
- 修改规则/脚本后运行 `selfcheck --strict` 与 `tests`；发布前 `release` 生成带校验和的包。
- 升级前 `snapshot`；升级后 `doctor` + 真实任务回归；失败按 `rollback` 恢复。
- 本文件是跨 Agent 入口，保持精简；细节放 `references/`，避免每次加载都消耗大量上下文。
