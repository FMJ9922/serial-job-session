# INTENTS · {{领域名}}

标准口令：`{{route_key}}：意图`

| 用户说 | 意图 | 动作 |
|---|---|---|
| {{route_key}}：推进 | 菜单 | 只抄 JOB-SHAPES「待做」，禁止代选 |
| {{route_key}}：按菜单做 N | 开工 | 只开编号 N |
| {{route_key}}：验收通过 | Close | `close_transaction.py` |
| {{route_key}}：打回 | 返工或撤销 | 合同仍成立 → rework_required |
| {{route_key}}：出估分单 | Estimator | 只改 PLAN 预计与菜单预计列 |
| {{route_key}}：托管执行 | 编排器 | 读 DELEGATION.md；高风险须加粗确认 |
