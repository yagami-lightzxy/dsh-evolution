<!-- EVOLUTION:BEGIN version=1.7.1 -->
## 全局进化与能力管理（evolution）

本段由 evolution 管理。修改规则请改 `~/.dsh/skills/evolution/rules/`，然后运行
`python ~/.dsh/skills/evolution/scripts/evolution.py install --rules-only --apply`。

### 默认闭环

需要优化 Agent、技能、工具或工作流时运行：

`python ~/.dsh/skills/evolution/scripts/evolution.py improve --apply --online`

它会检查环境是否过旧，审计技能和工具，复盘任务证据，维护来源明确的能力，并生成可验证的新技能提案。

### 能力选择

- 先盘点 `capabilities` 或 `recommend --task`，按需加载技能正文。
- 复用已有能力。只有确认缺口时才 `fetch`、`new` 或加入安装队列。
- 重复成功的工作流可以生成受管技能。候选技能通过训练集、留出集和函数保留门槛后才替换已有版本。
- 记录任务结果、失败根因、技能、Agent、模型和标签，复盘只使用已有证据。

### 自主维护

- 内容相同的技能合并为规范源，其余位置使用链接。
- 有来源、达到闲置阈值并有使用数据的受管技能可以隔离并恢复。
- 隔离超过保留期的受管技能可按 `improve --purge` 清理。手写或无来源技能不自动淘汰。
- 工具安装通过维护队列执行，结果必须记录退出码。

### 验收

- 规则、技能或工具变更后运行 `selfcheck --strict` 和完整测试。
- 技能替换必须经过 `evaluate`。静态扫描、配置成功和模型自评不等于能力提升。
- 失败时使用 `rollback`、`skills --restore` 或晋级备份恢复。
<!-- EVOLUTION:END -->
