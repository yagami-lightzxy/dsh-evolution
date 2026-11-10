# 知识沉淀与跨 Agent 同步

## 1. 什么值得记录

**记录**：
- 可复用的流程、命令、路径、配置片段
- 重要技术选型与理由（含被否决的方案）
- 失败根因与防复发措施
- 用户长期偏好/约束（写入用户级记忆或全局规则）
- 跨项目适用的规则（用户级）；仅本项目适用的（项目级）

**不记录**：
- 临时搜索结果、一次性路径、调试输出
- 可从代码/文档直接得到的事实
- 重复内容、未经验证的猜测
- 密钥、凭据、隐私数据

## 2. 分层

| 层 | 位置 | 用途 | 生命周期 |
|---|---|---|---|
| 全局规则 | `rules/` + `AGENTS.md` 受管块 | 跨 Agent 必须遵守 | 长期，版本化 |
| 技能 | `SKILL.md` + `references/` | 按需加载的方法论 | 长期，版本化 |
| 项目记忆 | 项目 `.dsh-memory/` / 项目笔记 | 本项目决策与进度 | 项目周期 |
| 知识库 | `~/.agents/evolution/knowledge/` | 跨项目经验、决策、教训 | 长期，定期蒸馏 |
| 指标 | `state/metrics.jsonl` | 性能与成本趋势 | 365 天 |
| 报告 | `reports/` | 审阅证据 | 180 天 |
| 快照 | `state/snapshots/` | 回滚 | 90 天 |

## 3. 条目格式

```markdown
## 2026-11-04 · 标题
- 场景：...
- 结论：...
- 证据：命令/文件/测试结果
- 适用范围：全局 / 项目 X / Agent Y
- 失效条件：版本/环境变化时复审
- 关联：文件路径、issue、快照 id
```

写入 `knowledge/lessons.md` 或 `knowledge/decisions.md`；用 `evolution knowledge add` 自动加日期与哈希去重。

## 4. 去重与蒸馏

- 写入前检索标题/关键词；相同结论更新原条目，不新增。
- 每 30 天或条目 >100 时蒸馏：合并重复、删除过时、保留可执行结论。
- 蒸馏不删除原始证据：归档到 `knowledge/archive/`，索引保留。
- 指标与报告只保留聚合，不保留完整对话。

## 5. 跨 Agent 同步

- **规范源唯一**：`~/.agents/skills/evolution` + `~/.agents/evolution`。
- 其他 Agent 通过链接/受管副本使用；`evolution install` 生成/校验。
- 规则变更：改 `rules/` → `install --rules-only` 更新 `AGENTS.md` 受管块 → 各 Agent 下次会话生效。
- 不支持链接的平台：`export` 生成包，`import` 安装；导入前校验哈希与版本。
- 冲突：不覆盖用户手写内容；受管块之外的内容原样保留；副本冲突生成 `.evolution-conflict-<timestamp>`。
- 版本记录：`CHANGELOG.md` + `VERSION` + 快照；回滚用 `rollback`。
