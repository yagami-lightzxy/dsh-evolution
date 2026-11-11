# 按需扩展：什么时候新建技能/工具，怎么保证它真的能用

## 判据

先问三个问题，顺序不能反：

1. 已有技能能做吗？ -> evolution recommend --task "..." + capabilities
2. 外部有成熟实现吗？ -> fetch --source github:owner/repo，审查许可证与脚本风险
3. 只能自己写吗？ -> new + verify

只有第 3 条成立时才新建。新建的成本不在写，而在长期维护与上下文占用。

## 三条路径

~~~bash
# 安装外部技能（默认 dry-run，审查通过再 --apply）
evolution fetch --source github:owner/repo --name skill --apply

# 自建技能（默认 dry-run，--apply 落盘）
evolution new --name my-skill --description "一句话说清什么时候该用" \
  --with-script --with-tests --apply

# 验收（任何来源的技能都要过这道门）
evolution verify --path ~/.dsh/skills/my-skill
evolution verify --path ~/.dsh/skills/my-skill --command '["python","-m","unittest","discover","-s","tests"]'
~~~

## 验收闸门

verify 会检查：SKILL.md 存在且 frontmatter 合法（name kebab-case、description 够长）、
SKILL.md 体量、scripts 下每个 .py 能编译、json 能解析、正文引用的本地路径真实存在、
以及（可选）冒烟命令退出码为 0。

不过闸门就不算"已完成"。这不是建议，是这条链路唯一的证据。

## 生效顺序

- 本地技能根 ~/.dsh/skills 秩 400，插件内置技能秩 600；同名时本地优先。
- 新建后不需要重启 DSH：技能目录会被重新扫描；但正在跑的会话要等下一次工具发现才看到它。
- 跨 Agent 想共用：evolution install --apply 会把 ~/.agents、~/.codex、~/.claude 建成目录链接。

## 工具而不是技能

- 系统级工具（node/python/git/winget 软件）由 envcheck 的 toolchain 检查 + scripts/windows 的 winget 层负责。
- 一次性脚本不要包装成技能：放进项目 scripts/，用 README 说明，避免污染技能目录。
- 需要新依赖时记录版本与来源，并在 verify 的冒烟命令里证明它能跑。

## 反模式

- 只写 SKILL.md、没有任何可执行证据，就直接说"能力已具备"。
- 把"按需执行"写进步骤：模型无法执行不可判定的话。
- 技能里引用不存在的 references/ 文件：静态检查能抓，别等运行时才暴露。
- 新建完不 verify：等于把一个未验证的指令源放进上下文。
