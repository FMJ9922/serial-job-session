# 上下文余量估算

`context-budget` 是一个只读估算命令，用于在派发子 agent 前检查当前 Codex 线程还能承载多少内容。

```bash
python scripts/sjs.py context-budget \
  --thread-id <codex-thread-id> \
  --instance <workflow-instance> \
  --input - <<'JSON'
{"task_group":"ui","task_text":"实现并测试一个独立模块","task_output_tokens":12000,"task_tool_tokens":50000,"recent_tasks":[{"task_id":"ui-1","group":"ui","status":"accepted","utilization_percent":62},{"task_id":"ui-2","group":"ui","status":"accepted","utilization_percent":48},{"task_id":"ui-3","group":"ui","status":"accepted","utilization_percent":44}]}
JSON
```

命令从 Codex 的 `state_5.sqlite` 读取线程模型，从 `thread_history_1.sqlite` 读取该线程已保存的条目，再从 `models_cache.json` 读取上下文窗口配置。数据库只读打开，不修改宿主会话数据。没有提供 `--state-db`、`--history-db` 或 `--codex-home` 时使用 `CODEX_HOME` 或 `~/.codex`。

返回值包含以下信息。

- `remaining_before_task_tokens` 表示加入新任务前的估算余量。
- `task.fit` 分为 `light`、`moderate`、`heavy`、`near_limit` 和 `over_limit`。
- `task.recommendation` 给出 `dispatch`、`dispatch_with_checkpoint`、`review_scope` 或 `split_required`，用于决定子 agent 任务是否需要复核范围、设置检查点或拆分。旧的 `fit_recommendation` 保留原有分档建议。
- `allocation` 使用 75% 作为目标利用率，50%–100% 作为可接受区间。`allocation_band` 为 `underfilled` 时建议复核后续任务卡，超过上限时要求拆分。
- `recent_utilization` 按 `task_group` 只统计最近 3 张同类、已验收且没有标记 `intentional_small` 的任务。中位数低于 50% 时返回 `adjustment_triggered: true` 和 `review_next_cards`。
- `task.projected_tokens` 包含任务文本、预期输出、预期工具结果和安全余量。工具结果难以预估时可以保守填写 `task_tool_tokens`。
- `automatic_overhead_tokens` 会在模型缓存提供 `instructions_template` 时计入宿主基础指令，`overhead_tokens` 还可以通过 `--overhead-tokens` 增加已知的工具或业务提示开销。
- `confidence` 固定为 `estimated`，因为系统提示、工具 schema、压缩策略和实际 tokenizer 可能无法从历史库完整观察。

`threads.tokens_used` 是累计使用量，命令不会把它当作当前上下文占用。若 DSH 工具运行时能提供当前线程 ID，插件会自动传给 CLI。也可以在工具参数中显式传入 `threadId`。

传入 `--instance` 且未在输入中提供 `recent_tasks` 时，CLI 会从已验收任务的 `parameters.context_budget` 自动组装样本。手动传入 `recent_tasks` 可以覆盖自动读取结果。每项至少包含 `status`、`group` 和 `utilization_percent`，窄批次可加 `intentional_small: true`。

纯计算接口位于 `scripts/sjs_context_budget_core.py`，包括 `estimate_task_allocation(...)` 和 `evaluate_recent_utilization(...)`。前者接收任务提示、预期输出和预期工具结果的 token 估算，后者接收任务分组与已验收样本。它们只接收普通数据，不读取文件、数据库或插件对象，未来可以直接移入独立插件。

