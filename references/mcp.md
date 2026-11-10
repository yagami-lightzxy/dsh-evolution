# MCP 接入

`evolution mcp` 启动一个最小 MCP stdio server（JSON-RPC 2.0，换行分隔）。它不执行第三方代码；审计/审阅只写本地报告，`record` 只追加本地指标。

## 启动

```bash
python ~/.agents/skills/evolution/scripts/evolution.py mcp
```

- stdout 只输出协议 JSON；日志/异常写 stderr。
- 支持 `initialize`、`notifications/initialized`、`ping`、`tools/list`、`tools/call`。
- 不需要网络；默认离线。

## 客户端配置

### Claude Desktop / Claude Code（`.mcp.json` 或客户端配置）

```json
{
  "mcpServers": {
    "evolution": {
      "command": "C:\\Program Files\\Python313\\python.exe",
      "args": [
        "C:\\Users\\<user>\\.agents\\skills\\evolution\\scripts\\evolution.py",
        "mcp"
      ]
    }
  }
}
```

### Codex（`~/.codex/config.toml`）

```toml
[mcp_servers.evolution]
command = "C:\\Program Files\\Python313\\python.exe"
args = ["C:\\Users\\<user>\\.agents\\skills\\evolution\\scripts\\evolution.py", "mcp"]
```

### DSH / 其他 MCP 客户端

使用 stdio transport，`command` + `args` 同上；若客户端支持环境变量，可设置 `EVOLUTION_HOME` 指向运行时目录。路径必须是绝对路径（MCP 客户端通常不展开 `~`）。

## 工具

| 工具 | 参数 | 作用 | 写入 |
|---|---|---|---|
| `evolution_status` | — | 版本、路径、计数、调度 | 否 |
| `evolution_capabilities` | `agent?` | 技能/MCP/插件/权限/运行时 | 否 |
| `evolution_recommend` | `task`, `limit?` | 本地能力建议与缺口 | 否 |
| `evolution_audit` | `scopes?`, `online?` | 审计并写报告 | 报告 |
| `evolution_review` | `week?`, `online?`, `days?` | 周审阅并写报告 | 报告 |
| `evolution_record` | 指标字段 | 追加任务结果 | 指标 |
| `evolution_stats` | `days?` | 指标聚合 | 否 |
| `evolution_doctor` | — | 安装/规则/安全锁/调度检查 | 否 |

## 协议示例

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"evolution_recommend","arguments":{"task":"审计本机技能"}}}
```

成功响应：

```json
{"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"..."}],"isError":false}}
```

## 安全

- 工具不接收任意 shell 命令；不执行被审代码。
- `evolution_audit/review` 只写 `~/.agents/evolution/reports/` 与 `state/`。
- `evolution_record` 拒绝未知 outcome/error_class；任务标签与备注脱敏。
- 远程客户端应限制为本地 stdio；不要把 server 暴露到网络。
- MCP 客户端可能缓存工具列表；升级后重启客户端。

## 故障排查

| 现象 | 排查 |
|---|---|
| 客户端无响应 | 直接用 `echo '{"jsonrpc":"2.0","id":1,"method":"initialize"}' | python ... mcp` 测试 |
| 输出解析失败 | 检查是否有 print 到 stdout；日志必须 stderr |
| Python 找不到 | 配置绝对解释器路径；Windows 用 `python.exe` |
| 工具报错 | 先跑 `evolution doctor`；检查 `--home`/`--os-home` |
| 权限错误 | 运行时目录不可写；设置 `EVOLUTION_HOME` 到可写目录 |
