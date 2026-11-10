# 合并记录：跨 Agent 的同名 evolution

## 起因

机器上曾同时存在两套同名技能：

| 位置 | 形态 | 状态 |
|---|---|---|
| ~/.dsh/skills/evolution | Python CLI，本仓库 | 保留为唯一规范源 |
| ~/.codex/skills/evolution | PowerShell 脚本 + 内嵌一份自我复制 | 已备份并改为指向规范源 |

两者都基于同一个判断：环境陈旧会削弱模型表现且不报错。差别在覆盖面——
Codex 版偏"这台 Windows 机器上的软件与工具"，本仓库偏"Agent 的技能/插件/模型通道/配置"。
两边各缺一半，所以这次做的是取长补短，不是二选一。

## 吸收了什么

1. **Windows 工具层脚本**：evolve-environment.ps1 与 register-evolution-task.ps1 收进 scripts/windows/，
   配置改名为 references/windows-tooling.json，报告目录从 work/evolution-reports 改到 .evolution/reports，
   计划任务用当前用户交互登录运行。它负责 winget 升级、Codex 插件市场刷新、按标记安装缺失工具。
2. **按标记推断缺失工具**：这条思路在 Python 侧变成了 envcheck 的 toolchain 检查——
   工作区有 package.json / pyproject.toml / requirements.txt / .git，就要求 node / python / git 存在，缺了报警并给安装提示。
3. **隔离优先于删除**：Codex 版的 quarantine 思路与 cleanup 的"宽限期 + 只删白名单"一致，保留在各自实现里。

## 没有搬的东西

- 内嵌的自我复制目录（evolution/evolution/...）：那是打包失误，直接丢弃。
- Codex CLI 专属调用（codex plugin marketplace upgrade / codex update）：只在 Windows 脚本内部保留，
  不进入 Python 主流程，避免在没有 Codex 的机器上制造假依赖。
- 周一时段：本仓库周任务是周日 20:00（DSH 会话级）；Windows 计划任务保持周一 09:00，两者互不替代。

## 合并后的规则

- 规范源只有一份：~/.dsh/skills/evolution。
- 其他 Agent 的目录（~/.codex/skills、~/.agents/skills、~/.claude/skills）一律用目录链接指向它，
  不再各存一份。链接由 install 维护，替换前自动备份。
- 旧实现整棵树保留在 ~/.dsh/evolution/backups/ 下，可随时取回。
