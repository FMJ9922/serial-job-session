# {{领域名}} · JOB-SHAPES（菜单）

> **聊天里只抄「待做」，已结档不进聊天。** 待做是推荐依赖顺序，不是自动施工计划。

## 工时口径

- 工时口径：`delivery_workhours = ai_net + business_wait`。待做表必须可解析：`shape_id`/`编号`、`phase_id`/`阶段`、`短名`、`目标`、`estimate_group`、`depends_on`/`依赖`、`faces`/`面`、`actor`、`human_accept`、`风险`、`预计`。校验器拒重复编号、环依赖、表与细则依赖漂移、同一 shape 挂两个 PLAN 阶段。`短名` 给菜单抄送与飞书「任务」用（概括，禁止 `+` 堆砌）；`目标` 是二值合同。
- `estimate` 上限、`max_faces` 与面枚举见 `ENV.md`。完整口径见 skill 的 `ESTIMATE.md`。
- 本文是**卡级细估**（每条一件），阶段级总量在 [`PLAN.md`](PLAN.md)。两者冲突以本文为准——本文是唯一施工菜单。

<!-- AUTO:ESTIMATE-STATS -->
_尚未生成。运行 skill 的 `scripts/estimate_stats.py` 回写本块，不要手改块内内容。_
<!-- /AUTO:ESTIMATE-STATS -->

## 待做（推进时只发这一节）

| 编号 | phase_id | 短名 | 目标（一件，可二值验收） | estimate_group | 依赖 | 面 | actor | human_accept | 风险 | 预计 |
|---|---|---|---|---|---|---|---|---|---|---|
| {{1}} | {{P1}} | {{概括}} | {{…}} | {{例：schema-check}} | — | {{…}} | agent | none | med | {{0.5}} |

**「预计」必填**（`delivery_workhours` 单值 h，不写区间——区间是阶段级的事）。填不出来说明这条目标还不是一件可二值验收的事，先改目标，不要留空等开工时再猜。Worker 抄它进任务卡时记 `estimate_source: menu`，并拆 `estimate_first_pass` / `estimate_rework` / `estimate_business_wait`。

长说明放在同文件「编号细则」节，必须与计划章节全文一致。日常聊天只抄「待做」表。

`rework_required` 卡仍留在本节，目标后标注“返工 {job-id}”；只有收到“继续返工”才可开启下一 attempt。

## 已结档

| 编号 | 目标 | job-id | 原估 | attempt | 墙钟 | 净耗时 | 偏差率 |
|---|---|---|---|---|---|---|---|

## 已作废

| 编号 | 目标 | job-id | 作废原因（`rejected` 终态，永不重开） |
|---|---|---|---|
