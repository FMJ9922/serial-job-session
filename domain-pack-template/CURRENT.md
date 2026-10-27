# {{领域名}} · CURRENT

现场快照。串行时由当前主会话 Worker / Closer 写；并行时只由编排器写，子 Worker 禁止修改。`idle` 只表示没有进行中的任务卡，不表示现场是空的。

```yaml
status: idle              # idle | running | awaiting_acceptance
control_mode: manual      # manual | managed
job_id: null
active_job: null
awaiting: null
managed_run_id: null
active_transaction: null
lease_holder: null
lease_resources: []
lease_acquired_at: null
parallel_pending_jobs: []
last_accepted: null
last_rejected: null
updated_at: null
```

`status` 是**主路**单路语义。并行卡不进 `status`，进 `parallel_pending_jobs`，每项：

```yaml
- job_id: {{…}}
  shape: {{编号}}
  faces: [{{…}}]
  status: running
  started_at: {{ISO +08:00}}
  evidence_dir: output/{{job-id}}/
```

四条并行判据与禁止清单见 skill 的 `PARALLEL.md`；本域单实例资源清单在 `ENV.md`。编排器必须先把卡预留到此列表，再启动 Worker，并在 Worker 返回后逐张串行收口。

## 现场

{{一段话：产物到哪一步了、下一个人接手要知道什么}}

## 待验收稿（仅 awaiting_acceptance 时有）

{{Worker 写给人看的验收步骤副本}}
