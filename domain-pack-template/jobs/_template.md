# 任务卡 · {id}

```yaml
schema: serial-job-session-card/v4
id: {date}_{slug}
shape: # JOB-SHAPES 编号或新种类编号
estimate_group: # 稳定估分类，不写本卡唯一编号
depends_on: []
faces: []
estimate: null # h；包含计划、执行与复核的主动工作
estimate_total: null
estimate_first_pass: null
estimate_rework: null
estimate_business_wait: null
forecast_total: null
forecast_remaining: null
estimate_source: # menu | similar:{job-id} | fresh | backfill
actor: agent # agent | human
human_accept: none
gate: false
negative_fixtures: []
expected_live_failures: []
ruler_ok: null
status: ready # ready | doing | awaiting_acceptance | accepted | cancelled
legacy_status: null # v3 迁移时保留 running/done_pending/rework_required/rejected 等旧值
attempt: 1
closure_revision: 0
planning_started_at: null # 可由可信宿主/用户事件定位时填写；不能可靠拆分可留空
planning_ended_at: null
planning_elapsed: null # h；不可可靠拆分时不补造，可用完整主动区间
started_at: null
active_started_at: null
active_ended_at: null
ended_at: null
closed_at: null
elapsed: null
wall_elapsed: null
ai_first_pass: null
ai_rework: null
ai_net: null # 可定位主动区间之和；缺失时 untracked
ai_effort: null
business_wait: null
delivery_workhours: null
variance: null
ai_variance: null
agent_runs: null
total_tokens: null
context_compactions: null
peak_context_pct: null
time_status: open # open | complete | untracked
artifact_valid: null # accepted 迁移可沿用旧结论；真实 patch/verify 会更新
pending_verification: null
artifact_valid_basis: null
artifact_valid_evidence: null
archive_ref: null
patch_count: 0
direction_review: false
direction_review_after: null
decision: false
branch_pass: []
branch_fail: []
precondition_shape: null
precondition_result: null
```

YAML 字段顶格，列表用单行 `[]`。旧 v3 卡可继续读取 `running`、`done_pending`、`rework_required`、`rejected`；新卡只生成 v4 状态。迁移不得删除旧字段、时间、attempt、验收结论或证据引用。

## 目标范围验收

**目标（一件可二值验收的原子合同）**

- 目标：
- 范围内：
- 明确不做：
- 验收通过条件：

## 依赖输入影响

- 已结档依赖：
- 外部资产/工具门禁：
- 输入版本与数据流：
- 下游接口与影响面：
- 未满足时的处理：保持 `ready` 或 `doing`，写明问题，不伪造完成。

## 进展问题决策

- 当前进展：
- 待人决策的问题：无 / 
- 候选与影响：
- 决策结果与时间：
- 重大上游影响：无 / 

问题未决时保持 `doing`。小反馈不重复切状态；改变合同、稳定目标或已开工下游时先讨论。

## 执行耗时

- 估分依据：
- 面与拆分理由：
- `planning_started_at` / `planning_elapsed`：
- `ai_net` 来源：
- 计时缺失说明：

| attempt | 主动开始 | 主动结束 | 主动时长 | 结果 |
|---|---|---|---|---|
| 1 | null | null | null | doing / awaiting_acceptance / terminal |

规划、阅读、思考和复核属于主动工作；能可靠拆分时记录 planning，否则使用完整主动区间。人类业务等待单列，不用墙钟差额倒推。无法定位写 `untracked`，不补造数字。

## 产物验证反馈

- 机械门禁与命令：
- 判红样本及结果：
- 当前现场预期：
- 证据目录：`output/{job-id}/`
- Worker RESULT：`output/{job-id}/RESULT.json`
- 主产物与不可变路径：
- 人眼验收步骤（如需）：打开哪、看哪、期望什么、如何还原、通过/打回口令。
- 反馈记录：

## 结案补丁

小的验收反馈、证据补充、文字修正或可逆修订在此追加；不覆盖旧证据，不重复结案状态。

```text
patch_id: null
created_at: null
reason: null
scope: null
changed_paths: []
verification: null
human_feedback: null
```

若修改合同、稳定目标或已开工下游，保持 `doing` 并讨论；按人类决定在原卡记录合同修订、追加补丁，或另开新卡/取消旧合同。

## 事实经验变化

- 拟写入 FACTS：
- 自动经验：无新增 / 
- 证据引用：
- 过时经验清理：无 / 
- `archive_ref`：

每张卡、补丁、取消事件都触发整理；没有新经验也记录“无新增”。当前有效经验不是等待审批队列，目标、验收、人类限制和冲突仍需人讨论。
