# v5 运行接口

所有路径参数由调用者明确提供；workspace 默认是实例所在 Git 根，无 Git 时是实例父目录。授权路径和共享估分库路径相对 workspace。CLI 返回 UTF-8 JSON，失败非零退出；策略转人工是成功记录的一次决定，需检查 outcomes 中 allowed/reason。

## 初始化与读取

`init --instance PATH --input 配置.json [--dry-run]` 验证选择。配置必须有 goal、route_key、authorization.paths、authorization.tools、policy.mode、modules.estimates、modules.feishu。条件自治还必须有 risk_ceiling、三个 thresholds 及真实 confirmation 来源。on_failure 默认 skip，但创建时仍向用户展示选项。其他默认值见共享 defaults.json。

初始化生成四文件与实例外 `.serial-job-session/instances.json` 注册表；唯一 route_key 通过注册表定位。实例必须在共享 skill 目录外，包括其 output 子目录也不能作为运行实例。相对路径不依赖创建 skill 的本机位置。已存在目录和重复口令拒绝覆盖。

`context --instance PATH [--task-id ID] [--role ROLE] [--domain DOMAIN] [--host-session SESSION] [--since REV] [--max-chars N]` 返回有限菜单、当前任务、直接依赖和相关经验。无活动任务时只显示菜单，不擅选历史卡。handoff 使用同样参数，但必须点名 task-id。

运行时不为找卡扫描目录。默认上下文预算12000字符，优先删减可选菜单和经验；合同、授权及约束不截断，超限明确告警。输出中的 evidence 是引用，不代表脚本读取或核实了证据正文。

## apply 与最小生命周期

`apply --instance PATH --input 命令.json`，也可 `--input -` 从 stdin 读取。每条命令必须有唯一 command_id、当前 expected_revision、真实 actor 和非空 operations。首次成功后原样重试返回 duplicate；同 ID 不同内容报冲突。示例命令中的 revision 必须从当前 context 取得。

```json
{
  "command_id": "create-task-1",
  "expected_revision": 1,
  "actor": {"id": "main-agent", "kind": "agent", "role": "orchestrator"},
  "operations": [{
    "op": "create", "task_id": "1", "title": "修复目标界面",
    "paths": ["src"], "tools": ["shell"], "resources": [],
    "contract": {"goal": "界面符合已确认需求", "checks": [{"id": "visual", "description": "对照参考与实机截图确认"}]}
  }]
}
```

随后用 assess 记录 phase=feasibility 的判断。判断结构见治理参考。`start --task-id 1 --command-id start-1` 是 start 操作的快捷形式；人工明确指令用 --human --actor 人类标识，agent 禁止自行冒充。

实现者完成后 report：

```json
{"op":"report","task_id":"1","candidate_version":"candidate-1","by":{"id":"worker-1","kind":"agent"},"evidence":["artifacts/1-runtime.png"],"summary":"实现及自检结果"}
```

独立评估放进下一条 apply 的 operations：

```json
{
  "op":"assess","task_id":"1","phase":"acceptance",
  "assessment":{
    "id":"review-1","by":{"id":"reviewer-1","kind":"agent"},
    "candidate_version":"candidate-1","contract_version":1,
    "score":97,"risk":"low","conclusion":"pass",
    "evidence":["artifacts/1-runtime.png"],"unknowns":[],
    "rationale":"实际观察到必验项通过","recovery_cost":"撤销本任务隔离改动",
    "checks":{"visual":{"status":"passed","evidence":["artifacts/1-runtime.png"]}}
  }
}
```

`accept` 按当前模式收口。反馈使用 reject，记录 reason，最多重试配置次数；fail 直接标记失败，cancel 取消。preserved_results、recovery 可记录保留成果与续接方法，不自动修改业务文件。

## 支持的操作

- create：合同、可选 title / parent_id / depends_on / paths / tools / resources / domain / required / policy / overrides。
- assess：task_id、phase、assessment。start、accept：task_id、可选 approval。
- report：task_id、candidate_version、by、evidence；贡献者包含 report.by 和 contributors。
- reject / fail / cancel：task_id、reason，可选 preserved_results / recovery / learning。
- block：task_id、reason。revise_contract：task_id、contract、reason；patch：已验收 task_id、summary、evidence。
- progress：task_id、summary、可选 questions / decisions / next_steps 数组；最新进展进入 handoff，旧记录仍在日志，不另建历史文档。
- set_policy：policy、可选 task_id 与 approval；缺少 task_id 表示工作流策略修订。
- nest：父 task_id、reason、可选 approval，随后 create 子任务。
- assess_direction：assessment，含 route、candidates；apply_direction：assessment_id、可选 approval。两者不能混在同一条命令中。
- assign_role：slot、role、domain、handle、host_session；invalidate_role：slot、reason。
- knowledge：id、text、evidence、可选 tags / task_id / replaces。替代旧经验保留在历史，不维持等待沉淀目录。
- set_overrides：overrides、reason、可选 task_id。每项包含 step（research/implement/verify/report）、path（overrides/文件名）、reason；保存哈希，开工时检查内容未漂移。只能补充方法，不能覆盖授权或策略。
- set_parameters：parameters 对象、reason、可选 task_id；create 也可带 parameters。业务方法参数依次继承共享默认、工作流、父任务、当前任务，context 返回有效值。空白/未填省略，false、0、空数组保留；该命名空间不是权限或策略配置。
- estimate、timing、sync_receipt：见可选模块参考。sync_receipt 的 receipt 仅记录真实传输结果，不能用伪造回执掩盖错误。
- usage：tokens、source；budget 可限制 max_tasks、max_active_agents、max_tokens 或带时区 deadline。只使用可信宿主数据。
- rebind_template：template（新 version/integrity）、reason 和真实人类来源。resume：真实人类来源，解除 stop 失败策略造成的派发暂停。

可选 approval 结构为 `{by, source}`，必须能对应真实人类指令。它是来源声明，不是系统鉴权。纯 worker/reviewer 等角色不能直接写控制日志，须由编排器接收结果并合并。

## 记录与恢复

创建配置 run.json 不再原地改写。第一次 tasks 事件保存一次有效参数和 run 哈希；后续策略和模板修订追加到日志。state 是包含校验和、日志游标的可重建投影，不是第二套事实源。

每条命令验证完毕后写一行 JSONL 并 fsync，再更新快照。完整事件即为提交，快照写失败不撤销它；原样重试不会重复生效。命令不能横跨两条日志，方向结论由独立评估引用落实。封存后不改旧分片，accepted 补充证据写 patch 到当前分片。

`check` 显式重放日志检查结构、快照和模板完整性；它会读取历史，但不读取证据正文。`rebuild` 重建并保存快照，`--dry-run` 只验证重建结果。快照缺失或损坏时，context 可只读重建；建议之后显式保存。

不完整末行不是已提交事件。先 `recover --dry-run` 查看，再 `recover`：把残片保存到实例外 private 目录，仅截去未提交尾部并重建快照。完整但损坏的记录不能自动跳过。残留锁需明确 `--break-stale-lock`，只允许确认进程已结束的锁；活跃写者绝不强行覆盖。

实例外 `.serial-job-session/private/` 保存锁和故障恢复数据，建议 gitignore。运行数据不进入共享 skill。模板变化报告 TEMPLATE_CHANGED，不自动重算完整性或改写实例来掩盖变化。
