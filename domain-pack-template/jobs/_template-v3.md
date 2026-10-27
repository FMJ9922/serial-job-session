# 任务卡 · {id}

```yaml
id: {date}_{slug}
shape: # 必须是 JOB-SHAPES「待做」编号或新种类新编号。禁止复用 accepted 卡 / 已结档编号的「后半」
estimate_group: # 从菜单原样抄稳定类别，如 schema-check；禁止写本卡唯一编号
depends_on: [] # 原样抄 JOB-SHAPES 依赖编号；开工前必须逐项已结档
faces: []      # 本卡动的「面」，取值见 ENV；超过 ENV.max_faces → 硬停拆卡
estimate: null # 必填。开工前写，单位 h。创建后作为 estimate_total 冻结
estimate_total: null
estimate_first_pass: null
estimate_rework: null
estimate_business_wait: null
forecast_total: null
forecast_remaining: null
estimate_source: # menu | similar:{job-id} | fresh | backfill
actor: agent   # agent | human。human 卡只出人手步骤，禁止改产物
human_accept: none  # none = 机器/AI 自验后同一会话 Closer；其它值 = 领域验收面
gate: false    # 交付物是否就是门禁
negative_fixtures: []      # 有机械门禁时填已知必红样本；gate:true 时必须非空
expected_live_failures: [] # 当前现场已知应红项；健康现场允许为空
ruler_ok: null             # fixture 与现场判断是否都符合预期
status: ready  # ready | running | done_pending | rework_required | accepted | rejected
execution_mode: direct  # direct | embedded_workflow
workflow_ref: null      # embedded 时相对实例根，如 output/{id}/runtime/light-workflow.json
workflow_revision: null
closure_revision: 0
attempt: 1
started_at: null
active_started_at: null
active_ended_at: null
ended_at: null
closed_at: null
elapsed: null
wall_elapsed: null
ai_first_pass: null
ai_rework: null
ai_net: null
ai_effort: null
business_wait: null
delivery_workhours: null
variance: null
ai_variance: null
delivery_variance: null
agent_runs: null
total_tokens: null
context_compactions: null
peak_context_pct: null
```

以上是统计脚本支持的受限 YAML：字段必须顶格，列表用单行 `[]`，不要写嵌套块。决策卡追加顶层 `decision: true`、`branch_pass: [...]`、`branch_fail: [...]`；分支卡追加 `precondition_shape: N`、`precondition_result: pass`。

状态机：`ready → running → done_pending → accepted`；需修时 `done_pending → rework_required → running`（attempt + 1）；只有合同方向错误才进入终态 `rejected`。

## 目标

（一件可二值验收的原子合同）

## 依赖门禁

- 已结档依赖：
- 外部资产门禁：
- 未满足则硬停原因：

## 下游接口

开工改产物前必填。只读 JOB-SHAPES 待做：依赖含本编号的条目；编号连续时可附看下一编号。

- 扫过的编号：
- 本卡必须留下的接口（命名、字段、路径、公开参数、版本）：
- 本卡明确不做（抄后续「目标」一行，禁止实现）：

## 估分依据

- `estimate` 从哪来（菜单条目 / 比照哪张已结档卡 / 现估）：
- 面枚举与为什么不超 `ENV.max_faces`：
- （结档时补）`ai_net` 为下方哪些主动执行区间之和：

## 必读路径

- （最多 5 个）

## 禁止

- 不得顺手实现后续编号。
- 不得改 accepted 卡或旧证据。
- （领域补充）

## 验收方式

- 机械门禁（二值）：写清判据、命令 / 工具、证据落盘路径。非几何产物按 skill 的 `GATE-PATTERNS.md` 五类范式对号入座。
- **判红样本（有机械门禁时必填）**：逐项对应 `negative_fixtures`，在副本/fixture/历史回放上实测判红，红的输出落证据目录。禁止破坏正式产物。构造不出 → 写明原因，该门禁降级为旁证；`gate:true` 卡不得结档。
- 当前现场预期：逐项对应 `expected_live_failures`；健康现场明确写“无已知应红项”。
- 人眼（仅领域验收面）：只写观感，不覆盖数字。

## 给人看的验收步骤

仅需人验时必填，并在设 `awaiting_acceptance` 的同一条聊天里原文列出。必须让没看过本轮聊天的人能做：打开哪、看哪、拧什么、期望看见什么、怎么还原、过了回「验收通过」、不过回「打回：…」。`human_accept: none` 写「无需人眼；AI 门禁全绿后 Closer」即可。

## 产物路径

- 证据目录：`output/{job-id}/`（相对实例根）
- Worker RESULT：`output/{job-id}/RESULT.json`（`serial-job-session-worker-result/v1`）。`close_requested` 必须 false；`control_writes` 必须空。编排器读取后串行 Close。
- 主产物：

## live（本会话探活）

```yaml
{{探活对象}}:
  connected:
  version:
```

## 执行区间

每次 Worker 开始/停手各记一次。等待人验不算主动执行；无法定位起止时写 `untracked`，本卡不进入偏差率主分布。区间权威：除表外再追加一行 `- attempt N: {ISO} ~ {ISO}`，倒序/重叠/与 `ai_net` 合计差 > 0.02h 会 `INTERVAL_REVERSED` / `INTERVAL_OVERLAP` / `LEDGER_SUM_MISMATCH`。`agent_runs` 等实验字段缺省 null，不得当结档硬闸。

| attempt | active_started_at | active_ended_at | 主动时长 | 结果 |
|---|---|---|---|---|
| 1 | {{ISO +08:00}} | {{ISO +08:00}} | {{…}} | done_pending / rework_required / terminal |

## 重开依据

仅 `rework_required` 重开时追加，不改旧记录：

- attempt：
- 上次打回因：
- 打回因如何已消除：

## 拟写入 FACTS

- （Closer 通过后才复制）

## 拟写入 NORMS 自动沉淀

仅当本卡命中负反馈（被打回 / 门禁判红过 / 工具同参校验失败过 / `variance > 2`）时填，格式与边界见 skill 的 `EVOLUTION.md`。无负反馈写「无」。

- `- [auto:{job-id}] 错法 → 正法（证据：{path}）`
