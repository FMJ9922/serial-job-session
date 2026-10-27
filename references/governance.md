# 创建、判断与验收

创建前先读取用户已明确的范围和选择，只对缺失项集中提问：

1. `manual` 人工主导、`conditional` 条件自治、`full_ai` 全 AI。必须由人选择，没有默认模式。
2. 条件自治的风险上限与可行性、方向、验收门槛。按任务提出建议，说明证据和未知项，由人确认；示例参数不是通用标准。
3. 返工上限与失败处置。建议默认首次实现后最多返工3次；达到上限保留成果，跳过失败任务并继续其他独立任务。另一选项为停止派发新任务。
4. 是否启用估分计时、飞书同步。启用估分计时须明确共享库路径；启用飞书须明确新建或绑定及目标。

`init --dry-run` 不创建文件、不发现宿主、不探活服务。缺失选择返回 `DECISIONS_REQUIRED`，不能把建议视作人已同意。用户已经回答的项目不重复询问。

判断记录包含 `id`、`by: {id, kind}`、`score`（0–100或null）、`risk`（low/medium/high）、`conclusion`（pass/blocking/uncertain）、`evidence`、`unknowns`、`rationale`、`recovery_cost`。null分数必须解释未知原因。评分是带依据的判断，不是统计成功概率。

条件自治逐项比较门槛（大于等于确认值）、风险和证据，不能平均抵消短板。人工模式由真实人类指令或明确的 `approval: {by, source}` 决策。全 AI 保留自评，即使低分、高风险也可在授权和预算内尝试；不能跳过必验项或将未知写成通过。

`actor.kind: human`、`--human` 和 `approval` 是宿主转述真实授权的来源记录，不是鉴权凭据。Agent 禁止自填人类授权来绕过策略。系统权限和用户原始限制始终优先。

任务状态为 ready / doing / awaiting_acceptance / accepted / failed / cancelled。等待决策、依赖或工具记入 `blocked_reason`，不伪造完成。失败和取消均保存成果引用，不自动 stash、reset 或删除文件。

开工前建立合同 `contract: {goal, checks: [{id, description, required?}]}`。默认每项必验。可选项须在实现前明确；环境缺失时记录 unknown/not_run。`assess` 的 feasibility 与 acceptance 分开，后者必须带当前 `candidate_version`、`contract_version` 和 `checks: {检查ID: {status, evidence}}`。必验项仅在 status=passed 且有证据时通过。

`report` 提交新的候选版本并记录实际实现者 `by` 和 `contributors`。`accept` 只接受当前合同及候选；新候选会使旧验收失效。独立 reviewer 不能是本任务实现贡献者。连续两轮返工后换一位 reviewer，避免反复自证。

合同修订使用 `revise_contract`，记录原因，增加合同版本并清空旧候选和评估。改动必验项须人类明确确认；不通过删改失败检查放行同一候选。已开工下游依赖的合同不直接改写。小的 accepted 证据补充使用 `patch`，不改变原合同和验收版本。

工作流策略由普通任务继承。任务 `policy` 覆盖和 `set_policy` 写入来源及版本，不污染其他任务。提高自治程度、降低门槛、提高风险上限、增加重试或取消独立验收须真实人类批准。

方向判断用单独的 `assess_direction` 命令写入 direction 日志，记录候选路线和理由。随后 `apply_direction` 在 tasks 日志引用评估 ID；按当前模式决策。方向记录不能修改用户目标或扩大授权。
