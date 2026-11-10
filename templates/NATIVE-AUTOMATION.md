# Agent 原生每周自动化（首选）

> 原则：优先用 Agent 平台自带的调度/自动化；OS 计划任务或 cron 只作为回退。
> 周任务默认**自我进化 + 自主维护 + 审阅**：先生成学习规则并在测试通过后升级版本，再去重、隔离、同 major 升级、按需下载；所有变更先快照/备份，失败恢复。

## DSH

在常驻会话中调用 `schedule_create`：

```json
{
  "title": "evolution 全局审阅",
  "prompt": "执行 evolution 每周自我进化与自主维护：\n1) 运行 `python C:\\Users\\28060\\.agents\\skills\\evolution\\scripts\\evolution.py evolve --apply --bump patch --release --quiet`\n2) 运行 `python C:\\Users\\28060\\.agents\\skills\\evolution\\scripts\\evolution.py review --apply --online --quiet`\n3) 读取 `state/evolution/versions.json`、`state/maintenance-latest.json` 与 `reports/weekly` 最新报告\n4) 若 plugin candidates 非空：用 DSH plugin_manager 只升级同 major、来源可信、可回滚的插件；跨 major 跳过并记录；升级前 snapshot，失败回滚\n5) 输出 ≤12 行摘要：自升级版本/学习规则、去重/隔离/下载、插件、健康分、失败、下周 1-3 个动作\n边界：付费、外发敏感数据、跨 major、未备份删除、删除用户手写技能停止；测试失败不得保留新版本；不伪造结果。",
  "weekly": {"weekdays": [7], "time": "20:00:00", "time_zone": "Asia/Shanghai"}
}
```

说明：`schedule_create` 是**当前会话**的提醒；长期无人值守需在平台持久自动化或常驻会话中创建。会话切换后，在新会话重新创建一次，或使用 `evolution schedule native` 生成的提示。

## Codex

在 `~/.codex/automations/` 或平台 Automations 中创建周任务，prompt 使用上面同一段文本；工作目录设为 `D:\\28060\\Documents\\dsh` 或项目根。

## Claude Code

在平台 Scheduled tasks / Routines 中创建周任务；prompt 同上。若平台只支持项目级命令，把 `review --apply --online` 封装为项目脚本，并让任务调用脚本。

## 通用文本（可粘贴）

```
执行 evolution 每周自我进化与自主维护。步骤：
1) 运行 python ~/.agents/skills/evolution/scripts/evolution.py evolve --apply --bump patch --release --quiet
2) 运行 python ~/.agents/skills/evolution/scripts/evolution.py review --apply --online --quiet
3) 读取 state/evolution/versions.json、state/maintenance-latest.json 与 reports/weekly/*.md
4) 有插件候选时用 plugin_manager 只升级同 major；升级前 snapshot，失败回滚
5) 检查维护/自升级错误；测试失败或发布失败时恢复旧版本，记录原因
6) 输出 ≤12 行摘要：自升级版本/学习规则、去重/隔离/下载、插件、健康分、失败、下周 1-3 个动作
边界：付费、外发敏感数据、跨 major、未备份删除、删除用户手写技能停止；测试失败不得保留新版本；不伪造结果。
```

## 回退：OS 调度

仅当平台没有原生自动化时：

```bash
python ~/.agents/skills/evolution/scripts/evolution.py schedule install --driver os --apply
python ~/.agents/skills/evolution/scripts/evolution.py schedule status
python ~/.agents/skills/evolution/scripts/evolution.py schedule remove --driver os --apply
```

Windows 使用当前用户计划任务（不请求管理员）；POSIX 使用带标记的 crontab 行。卸载只移除 evolution 自己的任务/标记行。
