---
name: serial-job-session
description: >-
  串行任务会话 v4：按 route_key 选定领域实例，用一张可验收任务卡推进一个原子合同，支持批量迁移、doing 中提问、验收补丁、自动经验整理和方向纠正。仅在标准口令「route_key：意图」、明确点名 serial-job-session、或当前会话已绑定实例时使用。
disable-model-invocation: true
metadata:
  author: 沈劢
  contact: shenmai@taiyouxi.cn
  license: MIT
---

# 串行任务会话

主会话是控制面写者，任务卡是事实源，Worker 只执行被点名的卡。一次调用完成一个动作：初始化、出菜单、执行一张卡、验收收口、补丁、方向审阅或协议维护。完整 v4 规则按需读 [V4.md](V4.md)。实例 `protocol_version: 3.x` 时改读 [LEGACY-V3.md](LEGACY-V3.md) 和 `_template-v3.md`，不要把 v4 状态或章节混入 v3。

## 先读什么

冷启动只读注册表、实例 `INSTANCE.json`、`CURRENT.md` 和 core `build_context/load_snapshot` 返回的摘要。点名具体卡后，再读该卡 YAML 摘要、依赖接口、相关 `ENV`/`NORMS`/`FACTS` 条目和卡中列出的证据。不要开局通读长文档、全部历史卡或 `output/`。迁移归档目录由实例 `.ignore` 精确隔离；需要历史时按 `archive_ref` 读，或对精确路径显式使用 `rg --no-ignore`。

按需读取：

- 估分、规划或复盘：`ESTIMATE.md`；
- 机械门禁：`GATE-PATTERNS.md`；
- 托管、夜跑或批量执行：`DELEGATION.md`、`PARALLEL.md`；
- 经验整理：`EVOLUTION.md`；
- 初始化、安装、飞书：`FIRST-RUN.md`、`INIT.md`、`INSTALL.md`、`FEISHU.md`；
- v4 状态、计时、补丁、方向审阅、迁移：`V4.md`。

## 路由与实例边界

先解析 `.serial-job-session/instances.json`，再读取目标实例协议。没有 `route_key` 时，只有当前会话已绑定单实例才能续接；多个候选必须请人点名。普通对话里的“推进一下”不扫描全部实例。

运行前执行：

```bash
python scripts/runtime_guard.py --instance <实例根>
```

实例必须在母版目录外；母版完整性判红、实例路径落在母版内或未完成 Close 事务时停止。迁移后卡 Markdown 是任务事实源，菜单、CURRENT、LOG、FACTS 和上下文由 core 投影；`ENV` 只管环境边界，`NORTH-STAR` 只管稳定目标。跨实例估分库写仓库根 `.serial-job-session/`。v3 实例按 [LEGACY-V3.md](LEGACY-V3.md) 使用原目录与脚本，不因 v4 母版更新自动改写。未来实例默认 v4；创建 v3 必须显式选择 legacy/v3。

用户明确要求修改本 skill 时，本次才进入 Skill Maintainer 角色；先备份、跑测试，再更新完整性基线。通常的领域工作不能改母版。

## 角色与动作

| 用户意图 | 动作 |
|---|---|
| 初始化/接入长期工作 | Bootstrap：读 `INIT.md`，探查授权范围，生成并确认实例契约；不自动开第一张卡 |
| 推进/下一步 | 菜单：只展示当前实例 ready 卡，不代选、不自动施工 |
| 点名编号或卡 | Worker：只做这一张卡，控制面由主会话更新 |
| 验收通过/打回 | Closer：读取证据并收口；反馈轻微时追加补丁 |
| 询问路线、前置结果要大改 | 保持相关卡 `doing`，记录问题与候选，等人决断 |
| 重新估算 | Estimator：只改计划与菜单预计，不改历史工时 |
| 方向纠正 | 高级模型审阅 + 人讨论；只输出判断和建议，不施工 |
| 托管/夜跑/并行 | 读 `DELEGATION.md`/`PARALLEL.md`；控制面仍由主会话串行合并 |
| 协议演进 | Skill Maintainer；不混做领域任务 |

同一句既点名又说推进，以点名为准。做完一张卡停下，允许列推荐菜单，不自动开工。

## v4 卡合同

新卡 YAML 至少包含 `id`、`shape`、`estimate_group`、`depends_on`、`faces`、`estimate`、`estimate_source`、`actor`、`human_accept`、`status`、`attempt`。状态只写：

```text
ready → doing → awaiting_acceptance → accepted
ready/doing/awaiting_acceptance → cancelled
```

正文固定七章，骨架见 [domain-pack-template/jobs/_template.md](domain-pack-template/jobs/_template.md)：

```text
目标范围验收 / 依赖输入影响 / 进展问题决策 / 执行耗时
产物验证反馈 / 结案补丁 / 事实经验变化
```

`doing` 可以持续记录探查、计划、执行和等待人决定。只有合同成立且验收通过才 `accepted`；方向错误、被替代或用户取消才 `cancelled`，旧状态写 `legacy_status`，历史正文归档一次并以 `archive_ref` 回链。

小的验收反馈、证据补充、文字修正可以直接写入 `结案补丁`，保留原验收结论和状态。改变合同、已开工下游或稳定目标时，先保持 `doing` 并讨论；根据讨论结果在原卡记录合同修订、追加补丁，或由人决定另开编号/取消旧合同，不强制某一种形式。

## 估分、计时与量级

估分包含本轮主动读取、思考、规划、执行和复核。若可信宿主、用户事件或同一会话计时能完整定位规划区间，可填写 `planning_started_at`/`planning_elapsed`；无法可靠拆分时记录一个完整主动区间即可，不强行制造 planning 数字。`ai_net` 是可定位主动区间总和；人类业务等待单独记录，不从主动区间反推或补造。无法定位时写 `untracked` 和原因，缺失计时是非阻断提示。

`faces` 超出实例上限或范围明显变大时，先给出拆卡方案。累计三轮可提示人另开任务评估量级；这是建议，不是自动停工或格式门禁。人的反馈优先判断合理性、目标和范围，不为小改动反复切状态。

## 验收、经验与方向

机器产物先有证据，再请求人眼；机械门禁必须有已知判红样本，人眼不能覆盖红灯。人验步骤必须写在设 `awaiting_acceptance` 的同一条回复中。

每张卡、补丁、取消事件结束后自动整理有证据的经验：清理过时项、合并重复项、记录“无新增”也可以；不维护等待沉淀区。推荐下一张卡时列出经验变更，人只需审阅目标、验收、用户限制和冲突项，有问题再改。

方向节点由 `INSTANCE.json.v4.direction_review_nodes` 声明；母版不硬编码领域编号或分支。方向卡必须由高级模型完成并与人讨论后通过，条件分支必须在卡元数据中写明 OR/AND 语义。

## 工具入口

```bash
python scripts/validate_domain_pack.py --domain <实例>
python scripts/runtime_guard.py --instance <实例>
python scripts/sjs.py context --instance <实例> [--card-id <id>]
python scripts/sjs.py start --instance <实例> --card-id <id>
python scripts/sjs.py apply --instance <实例> --input <command.json>
python scripts/sjs.py handoff --instance <实例> --card-id <id>
python scripts/sjs.py check --instance <实例> --compare-projections
python scripts/sjs.py migrate --instance <实例> --mode dry-run|apply|recover|rollback
python scripts/sjs_v4_migrate.py dry-run --instance <实例> --plan <计划.json>
python scripts/post_close.py --instance <实例>
```

迁移先 dry-run，再在副本 apply；只使用 core 公开的 `load_snapshot`、`build_context`、`apply_migration`、`recover_transaction` 等函数。迁移不删除、移动或改写 `output`；输出路径和哈希必须在 apply 前后相同。脚本批量填卡和摘要，一次一个事务节点。

## v3 兼容与硬停

先按 `INSTANCE.json.protocol_version` 路由：`4.x` 使用本文件和 `_template.md`；`3.x` 使用 [LEGACY-V3.md](LEGACY-V3.md)、`_template-v3.md` 及实例原 v3 脚本。旧 v3 卡可读写 `running`、`done_pending`、`rework_required`、`accepted`、`rejected`；运行时可在迁移计划中映射为 v4 语义，但只有明确迁移的实例才改用 v4 状态和七章格式。

必须停止的情况：未点名且无法确定实例；依赖未结档；探活失败；机器门禁判红；`gate: true` 没有有效判红样本；路径越出实例；事务未完成；试图覆盖 accepted 证据；试图修改稳定目标却未讨论；试图把多面大改塞进一张卡。

禁止扫描整个实例或 `output/` 来找编号；按索引和卡内路径读取。禁止 Worker 修改 `CURRENT`、菜单、正式 `FACTS/LOG/NORMS` 或 Close；禁止用聊天记录代替卡内事实；禁止自动选择或启动下一张卡。
