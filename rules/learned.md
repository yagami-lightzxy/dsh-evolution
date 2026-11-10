# 自动学习规则

由 `evolution evolve` 生成；仅记录有证据的长期改进，不写临时信息。

## 使用方式

- 任务开始或复盘时，读取与当前任务相关的条目。
- 与 `rules/security.md`、`rules/precedence.md` 冲突时，以安全内核与更高优先级规则为准。
- 无证据、一次性现象、未验证猜测不得写入。

## 2026-11-08 · plugin-upgrade-policy
<!-- evolve:plugin-upgrade-policy -->
- 观察：插件版本检查已接入；跨 major 自动升级风险高于收益。
- 调整：周任务只自动升级同 major 且来源可信的插件；跨 major 只报告，升级前 snapshot。
- 证据：plugin checked=9, candidates=0

## 2026-11-08 · dedupe-policy
<!-- evolve:dedupe-policy -->
- 观察：当前 159 个技能、duplicates=0，说明规范源优先策略有效。
- 调整：同内容技能保留 `~/.agents/skills` 规范源，其他位置改为 junction；替换前备份原目录。
- 证据：audit skills=159, duplicates=0

## 2026-11-08 · maintenance-safety-policy
<!-- evolve:maintenance-safety-policy -->
- 观察：自主删除/替换必须可恢复，否则一次误判会破坏工作环境。
- 调整：每次维护先 snapshot，并为每个被替换技能单独备份；链接失败时恢复原目录；不删除用户手写技能。
- 证据：maintenance.auto_apply=true

## 2026-11-10 · env-currency-policy
<!-- evolve:env-currency-policy -->
- 观察：环境（运行时/插件/模型通道）落后时，模型会在能力被削弱的条件下工作，且表现归因会指向模型。
- 调整：每周先运行 envcheck 再评估能力：插件被宿主拒绝加载时，先升级或卸载该插件，再讨论是否新增工具；模型通道必须显式配置 reasoningEffort。
- 证据：envcheck score=69, status=yellow

## 2026-11-10 · canonical-source-dsh
<!-- evolve:canonical-source-dsh -->
- 观察：DSH 用户技能根是 ~/.dsh/skills（秩 400），插件内置技能秩 600；旧规则写的 ~/.agents/skills 在本机并不存在。
- 调整：规范源统一为 ~/.dsh/skills/evolution，其他 Agent 目录（~/.agents、~/.codex、~/.claude）只做目录链接，不再各存副本。
- 证据：install 目标 dsh=canonical，agents/claude/codex=ReparsePoint；doctor ok=True。

