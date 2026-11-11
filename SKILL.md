---
name: evolution
description: "全局 Agent 自优化与能力管理：识别并移除拖累模型的旧技能和工具，从任务证据中复盘，按需安装或生成新能力，并用可量化验收决定是否晋级。"
whenToUse: "当需要优化 Agent 或工作流、审计和清理技能/工具、同步跨 Agent 规则、执行周审阅、处理能力缺口、安装或生成技能，或评估技能变更是否真的变强时使用。普通一次性问答不加载完整实现。"
metadata:
  version: "1.7.1"
  license: "MIT"
  author: "DSH evolution"
  homepage: "~/.dsh/skills/evolution"
---

# evolution

## 目标

让 Agent 的能力集合随真实任务结果持续变好。旧技能、失效工具和重复副本应被识别并退出路由。重复成功的工作流可以生成新技能。所有晋级都要有可复现的证据。

## 默认闭环

执行 `improve`，它按以下顺序完成一次完整循环：

1. `envcheck` 检查运行时、模型通道、插件和工具链是否已经落后。
2. `audit` 与 `health` 检查技能、MCP、插件、权限、引用和脚本。
3. `reflect` 从任务记录提取反复失败和重复成功的工作流。
4. `maintain` 去重、更新来源明确的技能、处理安装队列并隔离已经证明闲置的受管技能。
5. `evolve` 把有证据的失败根因和维护结果沉淀成规则。
6. 对技能候选运行 `evaluate`，通过训练集、留出集和函数保留门槛后才替换现有版本。

```bash
python scripts/evolution.py improve --apply --online
```

没有 `--apply` 时只生成计划和报告。需要长期运行时使用宿主原生自动化调用同一条命令。

## 能力选择

- 先运行 `capabilities` 或 `recommend --task`，只加载与当前任务有关的技能正文。
- 本地规范源、已安装能力和项目脚本优先。只有存在明确缺口时才用 `fetch`、`new` 或安装队列补全。
- 运行时只注入技能名称、描述、健康状态和来源。完整正文按需读取，避免旧技能占用上下文。
- 记录任务的 `outcome`、`quality`、`error_class`、`skill`、`agent`、`model` 和 `tags`，否则无法判断优化是否有效。

## 自主维护

- 内容相同的技能合并为一个规范源，其余位置使用链接。
- 有来源、达到闲置阈值且有明确使用数据的受管技能可以隔离。隔离记录来源和还原路径。
- `improve --apply` 可以从重复成功的工作流生成受管技能。生成后先 `verify`，再由 `evaluate` 决定是否用于替换已有技能。
- 工具或插件升级沿用宿主安装通道。新能力写入 `maintenance.install_queue` 后由维护循环处理。
- 手写技能和没有来源的技能不参加自动淘汰。需要清理隔离区时使用配置的保留期和 `--purge`。

## 技能晋级

准备一个 JSON 输入后运行：

```bash
python scripts/evolution.py evaluate --input evaluation.json --apply
```

候选必须满足以下条件：

- 基线重复运行的波动不超过阈值。
- 训练集平均增益达到有效效果量。
- 留出集不回退。
- 变更后保留原技能声明的核心函数。

未通过时保留候选和评估记录，不替换当前版本。

## 复盘与新技能

```bash
python scripts/evolution.py reflect --days 30
python scripts/evolution.py new --name example-skill --description "..." --apply
python scripts/evolution.py verify --path <skill>
```

`reflect` 只使用已经记录的任务证据。重复成功达到阈值时，`improve --apply` 会生成带来源记录的技能目录。改进建议和生成结果写入运行时状态，便于下一轮继续评估。

## 验收与回退

- 修改技能、规则或工具前创建快照。
- 修改后运行 `selfcheck --strict` 和完整测试。
- 技能内容的替换由 `evaluate` 决定，配置和环境变更由 `doctor`、`health`、`envcheck` 验证。
- 失败时使用 `rollback`、`skills --restore` 或晋级记录中的备份恢复。
- 不把静态扫描、配置成功或模型自评当成真实能力提升。

## 入口命令

`improve`、`reflect`、`evaluate`、`capabilities`、`recommend`、`audit`、`health`、`maintain`、`skills`、`fetch`、`new`、`verify`、`record`、`stats`、`evolve`、`snapshot`、`rollback`、`install`、`schedule`、`selfcheck`。

完整参数和数据结构见 `references/commands.md`、`references/task-loop.md`、`references/self-extension.md`、`rules/security.md` 和 `references/architecture.md`。
