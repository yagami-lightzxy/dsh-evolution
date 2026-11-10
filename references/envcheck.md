# 环境年代感体检（envcheck）

## 1. 要回答的问题

任务级审阅回答"这次做得怎么样"。它回答不了另一个问题：**是不是环境本身已经落后，导致模型只能在被削弱的条件下工作。**

典型表现，都不是模型能力问题：

- 插件装着、还在依赖清单里，但宿主启动时拒绝加载它；
- 依赖声明了却没装，或 link 目标已被删除；
- 模型通道没显式配置推理强度，复杂任务被悄悄降级；
- 锁文件比依赖清单旧，实际跑的不是你以为的那套包；
- 运行时已经 EOL，新库逐步不可用；
- 进化循环自己停跳了几周，没人发现。

envcheck 把这些变成可排序的发现项（finding），而不是"最近有点不顺手"。

## 2. 十一项检查

| 检查 id | 数据来源 | 判定为 FAIL/WARN 的条件 |
|---|---|---|
| dsh_cli | 配置 / PATH / 应用内 CLI | 找不到启动器（skip + medium） |
| dsh_runtime | dsh --version | 预发布通道（info）；低于配置基线（medium） |
| plugin_tree | profile package.json + node_modules | 声明未装（high）、断链 link（critical）、未固定 GitHub 依赖（medium）、未挂 bundles（info） |
| plugin_compat | dsh plugin list | 宿主自述 denies 的插件（high，FAIL） |
| plugin_channel | node_modules/.modules.yaml 的 storeDir + 内置 pnpm 版本 | store 版本与 pnpm 不匹配，add/remove 必然失败（high，FAIL） |
| lock_drift | package.json / pnpm-lock.yaml / .modules.yaml 的 mtime | 锁文件早于依赖清单或缺失（medium） |
| model_route | profile cordis.patch.yml | 缺 provider/model（high）；reasoningEffort 偏低（medium） |
| web_tools | 同上，web 条目 | 未配置（skip）；否则仅展示 |
| runtime_eol | sys.version / node --version + EOL 表 | 已 EOL（high）；180 天内到期（low） |
| skill_shadow | 本地技能根 vs 插件内置 SKILL.md | 同名覆盖（info，秩 400 覆盖秩 600） |
| heartbeat | 周报/审计/体检文件时间 | 无周报（medium）；>14 天（medium）；>28 天（high） |
| hygiene | ~/.dsh 备份、字节码缓存、sessions/logs 体量 | 备份残留/缓存（low~medium）；状态目录过大（low） |

## 3. 评分

满分 100，按严重度扣分：critical 30 / high 15 / medium 6 / low 2 / info 0。
状态：green（≥85 且无 critical）、red（<60 或有 critical）、其余 yellow。

分数是趋势指标，不是验收门槛。周任务只按发现项行动，不追分数。

## 4. 为什么 plugin_compat 是最高信号

宿主自己对不兼容插件的处理是"**保留安装、启动拒绝**"。于是：

- 依赖清单里有它，node_modules 里有它，看起来一切正常；
- 实际上本次会话根本拿不到它的工具；
- 表现是"模型不会用某个能力"，归因会落到模型或提示词上。

envcheck 直接把宿主的判断读出来（dsh plugin list 的 stderr 警告），让这条隐形降级变成一条 high。

处理顺序固定：**升级到兼容版本 → 卸载 → 才考虑显式豁免**。豁免会保留风险并把问题推给下一次。

## 4.5 为什么还要看 plugin_channel

插件树完整、兼容性也通过，仍然可能"装不进去"：profile 的 node_modules 由旧版 pnpm（如 store v10）安装，
而应用内置的 pnpm 是 11.x，执行 dsh plugin add/remove 会直接报 ERR_PNPM_UNEXPECTED_STORE。

这项失败的影响不是"少了几个工具"，而是**自动升级这件事本身不可用**。所以它必须是显式 FAIL，
而不是等到周任务执行升级时才以一条晦涩的 pnpm 报错出现。

修复需要 dsh plugin --profile <p> install 迁移 store（会重建 node_modules），属于会改变依赖树的动作，
必须由人确认后执行；envcheck 只报告，不自动做。

## 5. 集成位置

- review 自动运行并把结果写进周报的"环境年代感"一节，同时落盘 reports/envcheck/；
- evolve 读取 state/envcheck-latest.json，把 critical/high 变成学习建议（environment 领域，auto_apply=false）；
- doctor 保持安装自检，不重复这些检查。

## 6. 边界

- 插件兼容结论来自宿主自述；宿主没报告不等于运行时一定安全。
- 模型通道只检查配置，不验证该模型在本机是否真的可用（需要一次真实调用）。
- 离线模式不查 npm 新版本与上游 EOL 更新，用内置基线表，可能滞后。
- 不修改任何文件；需要修复时由调用方显式执行（plugins --apply、dsh plugin ...）。

## 7. 退出码

--fail-on 指定阈值时，存在同级别或更严重的发现即返回 1，用于 CI/周任务的强制门。
