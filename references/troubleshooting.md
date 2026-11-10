# 故障排查

## DSH 看不到 evolution 技能

1. 确认路径：`~/.dsh/skills/evolution/SKILL.md` 或 `~/.agents/skills/evolution/SKILL.md`。
2. 检查 frontmatter：`name: evolution`（kebab-case）、`description` 必填；`python ... validate --path <skill>`。
3. DSH 只扫描直接子目录/扁平 `.md`，不递归；不要把 `SKILL.md` 放更深。
4. 同名技能可能被更高 rank 的根覆盖；`capabilities` 查看 `agent/rank/path`。
5. 新会话最可靠；文件监视器通常会自动刷新，但某些版本需重启。
6. 运行 `skill(name="evolution")` 验证加载。

## `doctor` 报 target:xxx FAIL

- `absent`：该 Agent 根未安装；`install --apply --targets <name>`。
- `foreign`：目标已存在且非 evolution 管理；审阅后 `--force`（会备份）。
- `link` 但 `points_to_source=false`：链接指向旧路径；删除旧链接后重装。
- `copy` 版本落后：`install --mode copy --apply`。

## 链接失败（Windows）

- junction 通常不需要管理员；若被策略禁用，自动回退 copy。
- 查看 `install` JSON 的 `link_error`。
- 不要在 OneDrive/网络盘上创建 junction 到本地目录；改为 copy。
- 符号链接需要开发者模式/权限；优先 junction。

## 安全锁不匹配

```
安全内核规则自上次接受后已变更
```

1. 运行 `git diff`/`diff` 或 `evolution diff --from <snapshot>` 查看改动。
2. 确认改动是预期的、未放宽安全边界。
3. `install --apply --accept-safety-change`，并在 `knowledge/decisions.md` 记录原因。
4. 若改动是误操作：`rollback --snapshot <id> --apply`。

## 周任务没有触发

- **原生调度是会话级的**：DSH `schedule_create` 只在当前会话有效；会话结束/切换后需重新创建，或使用平台持久自动化。
- `schedule status` 查看 `native_recorded/active` 与 OS 状态。
- OS 回退：`schedule install --driver os --apply`；Windows 查 `schtasks /Query /TN DSH-Evolution-WeeklyReview`。
- 查看 `~/.agents/evolution/logs/weekly.log`。
- 任务运行但无报告：Python 路径/权限/`EVOLUTION_HOME` 不对；用任务命令手动跑一次。

## MCP 无输出/客户端报错

- stdout 不能有非 JSON 输出；所有日志写 stderr。
- 用绝对 Python 路径；检查 `python ... mcp` 是否能启动。
- 手工测试：
  ```bash
  printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python .../evolution.py mcp
  ```
- 客户端缓存旧工具列表：重启客户端。
- `--home`/`--os-home` 只在直接 CLI 时用；MCP 客户端通常无法传，使用默认路径或环境变量 `EVOLUTION_HOME`。

## 审计误报

- 危险模式是**信号**，不是判决；打开报告中的 `path:line` 人工确认。
- 密钥形态命中不要复制传播；确认后轮换并移除。
- `npx -y`/未固定 pip 版本在一次性脚本中可能可接受；在长期环境中应固定。
- 描述近似重复只是提示；确认职责是否真的重叠。
- 目前不支持 suppression 文件；如误报频繁，先调整 `security.py` 规则并补测试，再发布新版本（需安全锁接受）。

## 回滚失败

- `missing_sources`：快照目录不完整；不要继续，先找回原文件/重新导出。
- `all_ok=false`：恢复后哈希不匹配；检查磁盘/权限/杀软占用，重试或从备份恢复。
- 回滚不会删除快照后新增的文件；如新增文件造成冲突，手动移到 `backups/`。

## 导出/导入失败

- `unsafe zip path`：包内含绝对路径/`..`；拒绝是预期行为。
- `hash mismatch`：包损坏或被修改；重新导出。
- `target exists; use --force`：目标已存在；确认后 `--force`（会备份）。
- 文件数/体积超限：检查是否误打包 `node_modules`/大资产；默认已跳过常见目录。

## Windows 编码/中文乱码

- 所有文件 UTF-8；CLI 已 `reconfigure(encoding="utf-8")`。
- 若终端仍乱码，设置 `chcp 65001` 或用 Windows Terminal。
- 报告用 UTF-8 写入；不要用 GBK 编辑器另存。

## Python 版本

- 需要 Python 3.11+（`tomllib`、类型语法）。
- DSH 自带运行时路径可在会话中通过 `load_workspace_dependencies` 获取；也可用系统 Python 3.13。
- 不要在技能目录创建 venv；用项目隔离环境或系统 Python。

## 性能

- 审计扫描技能脚本有文件数/大小上限；技能很多时仍可能慢。
- `--scope skills` 可缩小范围。
- 清理旧快照/报告：`prune --days 90 --apply`。
- 不要每小时跑审计；周审阅足够。
