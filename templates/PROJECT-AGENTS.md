# 项目级 Agent 规则（evolution 兼容模板）

> 放在项目根 `AGENTS.md`，与全局 `~/.codex/AGENTS.md` / `~/.agents/AGENTS.md` 互补。
> 项目规则只能收紧或补充，不能放宽全局安全、授权与验收标准。

## 项目上下文

- 项目名：
- 技术栈：
- 关键目录：
- 运行/测试命令：
- 禁止事项：

## 复用入口

- 全局能力盘点：`python ~/.agents/skills/evolution/scripts/evolution.py capabilities`
- 复用建议：`... recommend --task "<任务>"`
- 项目内已有脚本/工具（优先复用）：
  - ...

## 验收

- [ ] 命令：
- [ ] 测试：
- [ ] 构建：
- [ ] 手动检查：

## 风险与回滚

- 高风险操作：
- 快照/备份：
- 回滚命令：

## 与全局规则冲突时

按 `~/.agents/skills/evolution/rules/precedence.md`：
系统安全/法律 > 用户当前明确指令 > 项目规则 > 全局 evolution 规则 > Agent/技能默认 > 任务级覆盖。
冲突无法消解且影响实质结果时，只问一次，附选项与影响。
