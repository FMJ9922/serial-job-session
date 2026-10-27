# serial-job-session v3 兼容说明

当实例 `INSTANCE.json` 的 `protocol_version` 为 `3.x`，运行时按本说明和原 v3 领域模板工作。v3 实例不会因为母版升级而自动改写；只有用户明确启动迁移事务的实例才切换到 v4。

## 路由

1. 先读注册表和实例 `INSTANCE.json`。
2. `protocol_version: 3.x` 使用实例原有 `protocol/`、`CURRENT.md`、`JOB-SHAPES.md`、`FACTS.md`、`LOG.md` 与旧脚本语义。
3. 新建或返工 v3 卡使用 [domain-pack-template/jobs/_template-v3.md](domain-pack-template/jobs/_template-v3.md)，不套用 v4 的七章骨架。
4. 未来新实例默认 v4；若必须创建 v3，必须由调用方显式选择 `instance_version: v3`/legacy，不能从当前上下文猜测。

## v3 状态与写入

v3 卡继续读写 `ready → running → done_pending → accepted`，返工使用 `rework_required → running`，合同方向错误使用终态 `rejected`。v3 的 `CURRENT`、菜单、FACTS、LOG 和 Close 事务仍按原规则维护；不要把 `doing`、`awaiting_acceptance` 或 v4 七章强行写进未迁移实例。

v3 的旧估分、`ai_net`、业务等待、门禁和人验约束继续有效。旧卡的历史时间、attempt、验收结论和证据引用必须原样保留。若要使用 v4 的暂停提问、结案补丁或自动经验整理，先迁移整个实例，不能在 v3 卡上混用局部字段。

## 与 v4 的边界

v4 使用 `cards → core projections`，状态和章节见 [SKILL.md](SKILL.md) / [V4.md](V4.md)。v3 仍以它自己的控制文档和脚本为事实源；母版中的 v4 迁移脚本只在显式迁移计划和事务中运行。任何 v3 实例的迁移必须先 dry-run、在副本 apply，并保留原卡与控制文档的归档引用。

