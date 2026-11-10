# 架构

## 1. 目标与非目标

**目标**：跨 Agent 的全局规则、能力盘点、审计、量化、周审阅、版本/回滚、迁移与 MCP 接入；默认离线、最小权限、可验证。

**非目标**：替代 Agent 的推理；自动执行第三方代码；自动升级/删除；收集完整会话或隐私数据；成为常驻服务。

## 2. 分层

```
┌─────────────────────────────────────────────┐
│ 入口层                                       │
│ SKILL.md（按需加载）· AGENTS.md 受管块 · MCP │
├─────────────────────────────────────────────┤
│ 规则层                                       │
│ principles / precedence / weekly / security  │
│ knowledge（安全内核哈希锁）                   │
├─────────────────────────────────────────────┤
│ 能力层（stdlib Python CLI）                   │
│ discover · audit · envcheck · cleanup         │
│ recommend · metrics · snapshot · install      │
│ review · schedule · mcp                       │
├─────────────────────────────────────────────┤
│ 状态层                                       │
│ config · metrics.jsonl · snapshots · reports  │
│ install-manifest · safety.lock · backups      │
└─────────────────────────────────────────────┘
```

## 3. 规范源与运行时分离

- **规范源**：`~/.agents/skills/evolution/`，可版本化、可导出、可链接到多个 Agent。
- **运行时**：`~/.agents/evolution/`，保存配置、指标、报告、快照、备份、日志；不进入导出包（除显式 `--include-knowledge`）。
- 好处：升级技能不碰历史数据；迁移运行时不影响代码；测试可用 `--home`/`--os-home` 隔离。

## 4. 数据流

### 任务开始
`recommend(task)` → 本地技能/MCP/插件词元匹配 → 候选表 + 能力缺口判断 → 复用或补全。

### 审计
`discover_all` → 技能根扫描（直接子目录/`SKILL.md`/扁平 `.md`）→ frontmatter 解析 → MCP/插件/权限/运行时 → 静态安全扫描 → findings 排序 → Markdown+JSON。

### 环境年代感
`envcheck` → 宿主 CLI 询问（`dsh --version` / `dsh plugin list`）→ profile package.json + patch 层解析 →
插件树一致性、模型通道、锁漂移、运行时 EOL、心跳与淤积 → 11 项状态 + 加权分数 + 发现列表。
只读；`--online` 才查 npm/EOL。

### 周审阅
`review` → `run_audit`（或读取最近审计）→ `run_envcheck` → 指标聚合 → 可选 GitHub release 检查 → 调度状态 → 报告 + 索引。

### 变更
`snapshot` → 预检/安全锁 → 受管写入（原子替换）→ `doctor` → 成功提交 / 失败回滚。

## 5. 安全内核

`rules/security.md`、`rules/precedence.md`、`templates/AGENTS.block.md` 与安全配置的哈希写入 `state/safety.lock`。

- Agent 可以提出规则变更，但不能静默放宽边界。
- `install` 检测到安全内核变更时拒绝执行，除非显式 `--accept-safety-change`；变更前自动快照。
- `doctor` 与 `selfcheck` 校验锁；`review` 报告变更。
- 这是**检测与流程约束**，不是 OS 级不可变；拥有文件权限的 Agent 仍可绕过，因此配合最小权限、审计与人工复核。

## 6. 事务与回滚

- 受管文件：config、rules、SKILL.md、AGENTS 块模板、安装清单、安全锁、调度状态。
- 每次 `install`/`rollback` 前创建快照；文件写入用临时文件 + `os.replace`。
- `rollback` 先创建 `pre-rollback` 快照，再备份当前文件，最后恢复并校验 SHA-256。
- 只恢复清单内文件；不删除快照后新增文件，避免误删。新增文件的清理需人工/`prune`。
- 锁：`msvcrt`/`fcntl` 单实例；超时与陈旧锁处理。

## 7. 周调度

优先级：Agent 原生自动化（DSH `schedule_create`、Codex automations、Claude scheduled tasks）> OS 计划任务/cron > 手动/CI。

- `schedule native` 写 `state/schedule.json` + `state/native-schedule.md`，包含可直接粘贴的 prompt；Python 不直接激活 Agent 提醒。
- `schedule install --driver os` 仅在无原生调度时使用；Windows 当前用户任务，POSIX 带标记 crontab 行。
- 周任务只读审计 + 报告，不自动变更系统。

## 8. 扩展点

| 扩展 | 方法 |
|---|---|
| 新 Agent 根 | `config.skill_roots` 增加 path/rank/agent |
| 新安装目标 | `config.install_targets` + `TARGET_ORDER`（代码） |
| 新规则目标 | `config.rules` 增加路径；`install` 遍历 |
| 新审计项 | `discover.py` 增加采集；`audit.py` 增加 findings |
| 新环境检查 | `envcheck.py` 增加 `_check_*` 并在 `run_envcheck` 与 `CHECK_ORDER` 注册 |
| 新清理目标 | `cleanup.py` 的 `_items` 增加白名单分支（必须限定 safe_roots） |
| 新 MCP 工具 | `mcp_server.TOOLS` + `call_tool` |
| 新危险模式 | `security.DANGEROUS_PATTERNS` |
| 新指标 | `metrics.record` 字段 + `stats` 聚合 |
| 新导出内容 | `snapshot.export_bundle` |

## 9. 设计取舍

- **stdlib only**：避免供应链与安装成本；牺牲 YAML/TOML 的完整解析（只覆盖 Skill 常用子集）。
- **默认离线**：可预测、无隐私外发；升级检查需显式 `--online`。
- **静态扫描**：安全但有限；不执行不可信代码。
- **本地关键词推荐**：零成本、可解释；不是语义检索，复杂任务仍需 Agent 判断。
- **报告与状态 JSON**：便于 CI/MCP/其他 Agent 消费；Markdown 便于人读。
- **快照按需**：只覆盖受管文件，避免全盘拷贝；代码版本用 export bundle。

## 10. 失败模式

| 失败 | 处理 |
|---|---|
| 快照缺文件 | `rollback` 拒绝执行，列出缺失 |
| 锁竞争 | 等待/超时；陈旧锁清理 |
| 安全锁变更 | 拒绝安装，提示审阅 + `--accept-safety-change` |
| 目标目录被占用 | 链接失败回退 copy；记录 `link_error` |
| MCP 客户端断开 | 服务退出；不写 stdout 日志 |
| GitHub 限流 | 记录 error，不阻塞报告 |
| 导入包损坏 | 校验失败即拒绝，不写目标 |
