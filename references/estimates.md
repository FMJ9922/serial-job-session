# 可选估分计时

创建时询问。关闭后不加载本模块、不读宿主历史、不要求时间字段。启用配置：

```json
{"modules":{"estimates":true,"feishu":false},"estimates":{"library":".serial-job-session/estimate-library.jsonl"}}
```

库路径相对 workspace，必须位于实例和共享 skill 外。它是跨实例的 JSONL 数据库，不是另一个任务事实源。

`estimate` 操作记录 task_id、hours、source 和可选 group。首次估分保留不变，后续 estimate 更新 forecast，偏差仍对照首次估分。`timing` 操作包含 task_id 与 sample。每个 sample 有稳定 id；同一样本后续完成时使用原 id 对账，不新增副本。

宿主样本使用 source=host、thread_id、turn_id，以及显式 records，或 collect_host=true 与已知 rollout_path/history_db/state_db/codex_home。只读取指定线程与轮次，SQLite 使用只读连接；外部导入、子任务和其他轮次不冒充本轮计时。默认路径发现仅在用户启用模块并明确请求 collect_host 时发生。

手动可信计时使用 source=manual、evidence、active_intervals 和可选 wait_intervals，每个区间是两个有时区的 ISO 时间。重叠区间取并集，等待与主动时间分开。没有可信区间就写 source=untracked 和 reason；不能用聊天墙钟差额倒推 AI 净耗时。

缺失开始或结束、低可信下界、未完成样本不进入主要校准。untracked 不阻断验收。相同线程轮次不可记到两个不同样本或任务；估分库按实例/任务投影最新记录，修订追加，重复写入幂等。

`stats` 输出逐任务 ai_net、elapsed、business_wait、variance、组别统计及历史校准因子。`stats --append-library` 把已验收且计时完整的样本追加到共享库，`--dry-run` 不写。父子任务的墙钟区间可能重叠，不把父任务与子任务的墙钟相加当作批次耗时。
