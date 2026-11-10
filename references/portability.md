# 安装、跨 Agent 共享与迁移

## 1. 规范源

```
~/.agents/skills/evolution/     # 技能本体（唯一规范源）
~/.agents/evolution/            # 运行时数据（不属于任何单个 Agent）
```

不要把规范源放在某个项目的 `.dsh/skills` 或某次会话目录：项目删除/迁移会导致技能失效。项目级只放项目规则（`AGENTS.md`）与项目专属能力。

## 2. 安装矩阵

| Agent | 技能根 | evolution 目标 | 规则入口 |
|---|---|---|---|
| DSH | `~/.dsh/skills` | `~/.dsh/skills/evolution` | `SKILL.md`；兼容写 `~/.dsh/AGENTS.md` |
| 共享 | `~/.agents/skills` | `~/.agents/skills/evolution`（规范源） | `~/.agents/AGENTS.md` |
| Claude Code | `~/.claude/skills` | `~/.claude/skills/evolution` | `CLAUDE.md` → 全局 AGENTS |
| Codex | `~/.codex/skills` | `~/.codex/skills/evolution` | `~/.codex/AGENTS.md` |
| 其他 Agent | 其 `skills` 根 | 链接/副本 | 其全局规则文件 |

DSH 本地发现顺序（来自 `dsh-skill-filesystem`）：

1. `<project>/.dsh/skills`（rank 100）
2. `<project>/.agents/skills`（rank 200）
3. `config.customSkillDirs`（rank 300）
4. `~/.dsh/skills`（rank 400）
5. `~/.agents/skills`（rank 500）
6. 捆绑技能（rank 600）

DSH 只发现 `<root>/<name>/SKILL.md` 或 `<root>/<name>.md`，不递归；同名技能按 rank/来源解析。因此：

- 规范源放 `~/.agents/skills/evolution` 可直接被 DSH 发现（rank 500）。
- 再链接到 `~/.dsh/skills/evolution`（rank 400）可提高优先级并明确归属。
- 不要在项目里再放一份不同内容的副本；会导致同名冲突。

> 本机 DSH 已实测会读取 `~/.dsh/AGENTS.md` 并把受管块注入工作区指令；`SKILL.md` 仍是技能加载的可靠入口。其他 Agent 是否读取 `AGENTS.md` 取决于其实现；`~/.codex/AGENTS.md` 面向 Codex，`~/.agents/AGENTS.md` 作为通用共享入口。不要假设所有 Agent 都会加载同一文件。

## 3. 安装方式

### 推荐：链接（单实例，更新一处生效）

```bash
python ~/.agents/skills/evolution/scripts/evolution.py install
python ~/.agents/skills/evolution/scripts/evolution.py install --apply
```

- Windows：目录 junction（`mklink /J`，通常不需要管理员）；失败回退 symlink/copy。
- POSIX：symlink；失败回退 copy。
- 目标已存在且非 evolution 管理：冲突；审阅后 `--force`（先备份）。
- 只装部分 Agent：`--targets dsh,claude`。
- 只更新规则块：`--rules-only --apply`。

### 副本（不支持链接/需要离线拷贝）

```bash
python .../evolution.py install --mode copy --apply
```

副本带 `.evolution-managed.json` 标记；升级时重新安装。`doctor` 会显示 copy 状态。

### 手动安装（最小方案）

1. 复制整个技能目录到目标 `skills/evolution`。
2. 确保 `SKILL.md` 的 frontmatter `name: evolution`。
3. 如需全局规则，把 `templates/AGENTS.block.md` 的内容追加到目标 `AGENTS.md`（保留标记）。
4. 运行 `doctor` 检查。

## 4. 迁移

### 从旧位置迁移

```bash
# 1) 备份
cp -a ~/.dsh/skills/evolution ~/evolution-backup-$(date +%Y%m%d)

# 2) 迁移到规范源（若尚未存在）
python .../evolution.py import --from <old-bundle>.zip --verify-only
python .../evolution.py import --from <old-bundle>.zip --apply --dest ~/.agents/skills

# 3) 修正链接
python .../evolution.py install --apply --force

# 4) 验证
python .../evolution.py doctor
python .../evolution.py selfcheck --strict
```

- 先 `export` 旧版本，再迁移；保留旧目录至少 7 天。
- 同名不同内容会触发审计 high；迁移后只保留一个权威源。
- 运行时数据（`~/.agents/evolution`）单独迁移：复制 `config.json`、`knowledge/`、`reports/`；指标可按需复制。
- 新机器：安装 Python 3.11+ → 解压/复制技能 → `init` → `import --verify-only` → `install --apply` → `doctor`。

### 跨机器导出/导入

```bash
evolution.py export --out evolution-1.0.0.zip
evolution.py import --from evolution-1.0.0.zip --verify-only
evolution.py import --from evolution-1.0.0.zip --apply
```

导出的包包含技能、规则、文档、测试和 SHA-256 清单；不含指标/报告/凭据。`--include-knowledge` 才会带 `knowledge/`（先确认无敏感内容）。

## 5. 各 Agent 补充

### DSH

- 技能写入 `~/.dsh/skills/evolution/SKILL.md` 或 `~/.agents/skills/evolution/SKILL.md` 后，DSH 的文件监视器通常会刷新目录；新会话最可靠。
- 会话中可用 `skill(name="evolution")` 验证加载。
- `~/.dsh/AGENTS.md` 是兼容写入点；若 DSH 版本不读取，不影响 `SKILL.md`。
- 权限预设若长期 `danger-full-access`，审计会报 high；日常建议 workspace-write/read-only。

### Claude Code

- 技能：`~/.claude/skills/evolution/SKILL.md`。
- 全局规则：`~/.claude/CLAUDE.md` 既可指向 `~/.codex/AGENTS.md`，也可直接包含受管块；不要两处写不同规则。
- 权限：避免长期 `bypassPermissions` / `Bash(*)`。

### Codex

- 技能：`~/.codex/skills/evolution`（若该版本支持 skills）；规则：`~/.codex/AGENTS.md`。
- `config.toml` 的 `approval_policy="never"` / `sandbox_mode="danger-full-access"` 会被审计标记。
- Codex automations 可用于周任务；prompt 使用 `templates/NATIVE-AUTOMATION.md`。

### 通用 AGENTS.md

把 `templates/PROJECT-AGENTS.md` 放到项目根，补充项目命令/验收/回滚。全局块只写最小规则，细节放 `rules/`，避免每个会话都加载大量文本。

## 6. 版本、升级、回滚

```bash
evolution.py snapshot --label before-upgrade
# 替换技能/规则后
evolution.py doctor && evolution.py selfcheck --strict
evolution.py diff --from <snapshot-id>
evolution.py rollback --snapshot <snapshot-id> --apply
```

- 版本在 `VERSION` 与 `SKILL.md` metadata 中必须一致；`selfcheck` 会校验。
- 破坏性变更升 major；升级前读 `CHANGELOG.md`。
- 安全内核变更需 `--accept-safety-change`，并记录原因。
- 回滚只恢复受管文件；用户手写规则块外内容不被覆盖。

## 7. 卸载

```bash
evolution.py uninstall                 # dry-run
evolution.py uninstall --apply         # 移除链接/副本 + 受管块
evolution.py uninstall --purge --yes --apply  # 额外删 config/调度状态
```

保留 `reports/`、`knowledge/`、`backups/` 以便审计；确认不再需要后再手动归档/删除。

## 8. 冲突处理

| 情况 | 处理 |
|---|---|
| 目标已有非 evolution 目录 | 默认拒绝；`--force` 备份后替换 |
| 同名技能不同内容 | 审计 high；确定权威版本，其余归档/链接 |
| 受管块标记缺失/重复 | 拒绝写入；手工修复标记后再运行 |
| 副本版本落后 | `install --mode copy --apply` 刷新，或改链接 |
| 安全锁不匹配 | 审阅 diff 后 `--accept-safety-change` |
| 链接不可用 | 自动回退 copy；`doctor` 显示 link_error/状态 |
