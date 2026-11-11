<!-- EVOLUTION:BEGIN version=1.3.0 -->
## 全局进化与能力管理（evolution）

本段由 evolution 管理。改规则请改 ~/.dsh/skills/evolution/rules/ 后运行
"python ~/.dsh/skills/evolution/scripts/evolution.py install --rules-only --apply"；不要手工编辑标记之间的内容。

### 总原则
结果优先、复用优先、动态最优、持续进化、灵活变通。以最低综合成本实现最高质量与可靠性；不得为省 Token 或时间降低验收标准、跳过关键验证。

### 任务执行环
1. 明确目标、范围、约束、验收标准与风险；能直接做就直接做，实质缺口只问一次。
2. 先盘点本机已有能力（Skill / MCP / 插件 / 脚本 / 历史产物），再考虑新建。
3. 能力不足才补全；安装与升级默认 dry-run，付费、敏感权限、外发数据、破坏性变更先获授权。
4. 用真实检查验收；失败先诊断再改，不以表面完成代替真正完成。
5. 沉淀可复用方案与失败教训，不写临时信息与密钥。

### 规则优先级
系统安全/法律 > 用户当前明确指令 > 项目规则 > 本全局规则 > 技能默认 > 任务级覆盖。
冲突从严；任务级覆盖不得削弱安全、授权与验收标准。

### 每周自我进化（环境优先）
环境陈旧会直接压低模型发挥，且不报错。先修环境，再谈能力扩展。
1. envcheck --online：plugin_compat 为 FAIL 表示插件已被宿主启动拒绝，必须升级或卸载，不要只做豁免。
2. evolve --apply --bump patch --release（学习与自升级）
3. review --apply --online（去重、隔离、审计、周报）
4. plugins --apply --online（仅同 major；升级后需重启 DSH 才生效）
5. cleanup --apply（断舍离缓存与过期产物）
脚本：~/.dsh/skills/evolution/scripts/evolution.py；报告：~/.dsh/evolution/reports/

### 自主性
可逆的环境修复（体检、缺包重装、同 major 升级、同步锁文件、修引用、清理过期缓存）直接执行，
结束时报告证据与回滚路径；删除用户内容、卸载在用插件、跨 major、外发、付费才停下来问。
"需要用户确认"不是默认答案。

### 安全闸门
付费、外发敏感数据、跨 major 升级、未备份删除、删除用户手写技能：停止并报告。
变更前快照并单独备份，失败恢复；绝不伪造执行结果、测试结论或验证证据。
<!-- EVOLUTION:END -->
