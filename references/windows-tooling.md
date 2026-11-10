# Windows 工具层（可选）

主流程是 Python（envcheck / evolve / review / cleanup）。这一层补的是另一类对象：
操作系统层面的工具链——winget 安装的软件、Codex CLI 与它的插件市场。

两者不冲突：Python 管 Agent 周边的技能、插件、模型通道；这一层管"这台机器上装了哪些软件、
哪些该升级、工作区需要但还没装的是什么"。

## 什么时候用

- 想集中看一遍 winget 里过期的软件；
- 工作区里有 package.json / pyproject.toml / requirements.txt / .git，但对应工具没装；
- 想用 Windows 计划任务跑"每周软件升级"，而不占用 DSH 会话。

日常周任务不需要它；它默认只读，只有显式加开关才会改系统。

## 用法

~~~powershell
# 只采集证据，不改任何东西
pwsh -File scripts/windows/evolve-environment.ps1 -Mode audit -Workspace D:\work\proj

# 升级 winget 软件 + 安装工作区缺失的工具（明确开关才生效）
pwsh -File scripts/windows/evolve-environment.ps1 -Mode apply -Workspace D:\work\proj -UpdateWingetApps -InstallMissingTools

# 注册每周计划任务（周一 09:00，当前用户，交互登录时运行）
pwsh -File scripts/windows/register-evolution-task.ps1 -Workspace D:\work\proj -At 09:00
~~~

报告写到 Workspace 下的 .evolution/reports/evolution-<时间>.json，摘要写 latest.txt。
过期报告在 -QuarantineStale 时移入 .evolution/quarantine/（移动而非删除）。

## 配置

references/windows-tooling.json：保留期、隔离期、报告保留、winget 缺失工具规则（marker -> command -> wingetId）、
允许隔离的目录、插件策略。改动前先读它。

## 边界

- 默认 -Mode audit；-UpdateWingetApps / -InstallMissingTools / -UpdateCodex 都是显式开关。
- winget 升级可能是大版本跳跃，且需要用户会话；失败会记进 failures 并以退出码 2 结束，不会伪装成功。
- 不卸载任何东西；插件只能被"建议"，不能被自动安装（config 里 autoInstall=false）。
- 需要 PowerShell 7（pwsh）。没有 winget 时脚本会跳过这一段，其余检查照常。
- 与 Python 侧不共享状态：这边写 .evolution/，那边写 ~/.dsh/evolution/。
