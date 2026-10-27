# 托管执行（按需）

细则，由 [SKILL.md](SKILL.md) 引入。**默认 `control_mode: manual`。** 只有用户明确说「托管 / 代理 / 批量执行 / 夜跑」时才读本文并加载 `scripts/managed_runner.py`。

授权文件 schema：`schemas/managed-run.schema.json`。模板：`domain-pack-template/managed/_template.json`。Worker 停手产物：`output/{job-id}/RESULT.json`（`schemas/worker-result.schema.json`）。

---

## 一、授权合同（缺一项不得 dispatch）

必须包含：

| 项 | 字段 |
|---|---|
| 路由与范围 | `route_key`；`allowlist.shapes` / `phases` / `filters` |
| 配额 | `max_jobs`、墙钟 `deadline`、`max_retries`、`max_parallel`、`agent_budget` |
| 写入面 | `allowlist.paths` / `faces` / `tools`；`isolate_writes` |
| 人与不可逆 | `human_only_policy`、`human_accept_policy`、`irreversible_policy` |
| 审查与停止 | `reviewer_policy`、`risk_ceiling`、`stop_on`（全部停止条件） |

空 allowlist 数组表示「不额外收窄」（仍受风险与资源锁约束）。非空则必须命中，否则 `over_grant`。

夜跑是托管执行的**领域 preset**（`overnight_preset`）：`preset=overnight`，工作时间之外不累计 `business_wait`，`max_parallel` 默认 1，仍走同一套 lease 与停止条件。领域特有门禁、晨检、禁止项不因夜跑失效。

---

## 二、风险

- **低**：硬门禁充分、可逆、隔离、历史首次通过率高，且样本 ≥3 → 可跳过独立 Reviewer。
- **中**：一般代码实现 → 至少一个独立 Reviewer。
- **高**：一次通过率低、生产/外部写、范围大、弱门禁或不可逆。对话必须**加粗**提示，获得明确确认（`high_risk_confirmed`）后才 dispatch。
- **冷启动**：`sample_n < 3` 时不得默认低风险。

高风险超过 `risk_ceiling` → `over_grant`。高风险未确认 → `high_risk_unconfirmed`。

---

## 三、编排器权限

主 Agent 只编排：选合法卡、预留状态、派新 Worker、读 RESULT、跑门禁/Reviewer、**串行 Close**、推进预授权队列。禁止亲自改业务产物。

Worker 只写本卡、隔离证据和授权路径；禁止写 CURRENT / JOB-SHAPES / FACTS / LOG / NORMS / PLAN；禁止 `close_requested: true`；禁止自行 Close。

Reviewer 只给 `pass/blocking`，永不 Close。

Houdini / Unity / HEU 等单实例资源同一时刻只允许一个写 Worker。lease 记在 **CURRENT**（`control_mode`、`managed_run_id`、`lease_holder` / `lease_resources` / `lease_acquired_at`、`active_transaction`），不另起第二套共享控制面。并行、夜跑、普通托管共用这把锁。

回滚：停止派发新任务（`drain=true`），已有 lease 进入 drain，不强杀正在操作 Unity/Houdini 的 Worker。

---

## 四、立即停止

`dependency_ambiguous`、`split_required`、`human_only`、`probe_failed`、`over_grant`、`lease_conflict`、`consecutive_failures`、`budget_exhausted`。另：`high_risk_unconfirmed`、`drain`。

RESULT 写入控制面或请求 Close → 视为 `over_grant`。
