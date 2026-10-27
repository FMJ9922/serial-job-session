# {{领域名}} · PLAN（启动阶段整体估分）

> 两层估分的**上层**：阶段级粗估，回答「这摊活大概多少」。**下层**是 `JOB-SHAPES.md` 的卡级细估，回答「这一件多少」。
> 两者冲突时以 `JOB-SHAPES.md` 为准——它是唯一施工菜单，**本文不是施工计划**，不能按本文的阶段点名开工。

```yaml
plan_status: draft   # draft | confirmed
plan_mode: delivery  # discovery | delivery
plan_version: 1
estimated_at: null
confirmed_at: null
baseline_total: null
forecast_total: null
library_ref: []
```

写者只有 Bootstrap 与 Estimator。Closer 只跑 `estimate_stats.py --plan` 回写下方 `AUTO:PLAN-ROLLUP` 块，不手改阶段行。

## 口径声明（给非执行方读）

- 阶段推荐范围是 **`delivery_workhours` 4–16h**（约 0.5–2 个工作日），不是绝对硬闸。小项目允许不足 4h；超过 16h 必须写不能继续拆的理由；超过 24h 强提醒重分阶段。
- 保留 immutable `baseline_total` 与当前 `forecast_total`，以及 schedule / scope variance。禁止滚动重估覆盖原排期基线。
- 单卡排期主口径是 `delivery_workhours = ai_net + business_wait`；`ai_net` 仍用于估分准不准。
- **AI 净耗时 ≠ 人力工时**。夜间空等不计入 wait；夜间真实 AI 执行计入 AI 时间。
- `discovery`：ENV 已锁定但路线未定，只允许调研/决策/原型卡，阶段写宽区间。`delivery`：路线与收口标准确认后正式执行。
- 人类数字必须区分「经验估计」和「业务期限」。默认混合模式：AI 先独立预测，再用人的预算约束范围，禁止为了凑总数压低任务估分。

## 阶段（未拆成菜单条目的阶段必须写区间）

| 阶段 | 收口判据（二值） | 预计区间 h | unc | 依据 | 已拆卡编号 | 状态 |
|---|---|---|---|---|---|---|
| {{P1 阶段名}} | {{一句话，能独立说过/不过}} | {{2–5}} | {{high}} | {{fresh}} | {{—}} | {{pending}} |

- **预计区间**：`低–高`（如 `2–5`）。只有 `unc: low` 的阶段才允许收窄到 1.5 倍以内；`high` / `med` 必须留出真实的不确定度，别用假精度换好看。
- **unc**：`high` = 只有目标、还没拆法；`med` = 拆法已定但没落菜单；`low` = 已全部拆成菜单条目。
- **依据**：`library:{estimate_group}`（跨实例库的 p50/p90）/ `similar:{job-id}`（比照本域已结档卡）/ `fresh`（现估，须在下方写清按什么拆的时间）。
- **已拆卡编号**：本阶段已经落进 `JOB-SHAPES.md`「待做/已结档」的编号，逗号分隔；一个都没拆写 `—`。滚动统计靠这一列把实测归到阶段。
- **状态**：`pending` | `in_progress` | `done` | `dropped`。

## 粗估依据（fresh 的阶段必填）

- {{阶段名}}：{{按什么拆的时间、参照了什么、最大的不确定性是什么}}

## 滚动收敛记录

每次改动阶段区间时追加一行，不改历史行。**只在有实测或范围确实变了时改**，不为了让偏差好看去回改。

| 日期 | 阶段 | 旧区间 | 新区间 | 为什么改（实测抬高 / 范围漂了 / 拆细后收窄 / 阶段作废） |
|---|---|---|---|---|

<!-- AUTO:PLAN-ROLLUP -->
_尚未生成。运行 skill 的 `scripts/estimate_stats.py --plan` 回写本块，不要手改块内内容。_
<!-- /AUTO:PLAN-ROLLUP -->
