# Benchmark B1 执行包

[冻结规范](BENCHMARK_B1.md)包含实验规则、终止条件和100项公开验收。`spec.py`是标准库实现的规范来源，不包含参赛业务代码。

- `CHECKS`：按001–100排列的字典列表，字段为`id`、`title`、`category`，每项固定1分。
- `GROUPS`：六组字典列表，字段为`id`、`project`、`model`、`use_skill`；`ORDER`是固定运行顺序。
- `common_prompt()`：全部参赛者完全相同的公开任务与完整验收清单。
- `treatment_prompt(use_skill, skill_path)`：仅技能使用条件的差异提示；技能路径由冻结包提供，对照组不接收路径。
- `audit_prompt(snapshot_path, evidence_path)`：独立盲审提示，传入匿名副本与证据输出位置，不传入组别信息。
- `SPEC_VERSION`、`UNITY_VERSION`、`SKILL_VERSION`、`REASONING_EFFORT`、`MAX_ACTIVE_SECONDS`、`MAX_TEAM_AGENTS`：调度器公共常量。

在仓库根目录运行规范验证：

```powershell
python -m unittest discover -s Test/benchmark-b1 -p test_spec.py -v
```

正式调度入口及操作命令以本目录调度器的`--help`为准；本模块本身不会启动模型、Unity或修改参赛工程。调度器先验证模型可调用性、工具权限与审计通道，再冻结规范/提示/技能/基线哈希。任一强制接入检查失败即停止，禁止替换型号或带病启动。

评分必须接收完整100项0/1判定及证据索引；不能从实现静态存在、自报完成或技能任务卡状态自动赋分。单组总分为判定之和，分母始终100，违规或用户中止需单独标记。原始日志可能含模型组别，不能直接提供给盲审员。

Windows 正式调度与审计统一在进程环境中设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，使 Python 标准输入输出及其后代 MCP 进程使用 UTF-8。仅验证 Python 进程内的原生工具调用不足以证明 CLI 接入可用，还须验证外层 stdio 的 UTF-8 JSON 和真实 CLI 的工具发现。此设置不修改系统区域设置或冻结任务。

从桌面应用启动独立 CLI 时，还应移除继承的 `CODEX_APP_TOOLS_PIPE_PATH`、`CODEX_THREAD_ID` 和 `CODEX_SESSION_ID`，避免连接回桌面会话的工具宿主。`with_utf8.ps1` 为当前命令设置上述环境并在结束后恢复；例如 `./Test/benchmark-b1/with_utf8.ps1 Test/benchmark-b1/runner.py status`。组别专用 `CODEX_HOME` 仍由调度器设置。

工具宿主启动 MCP 进程时可能不继承 CLI 的环境，故 Unity 服务还须显式设置 `mcp_servers.unity.env.PYTHONUTF8="1"` 和 `mcp_servers.unity.env.PYTHONIOENCODING="utf-8"`。正式入口为 `python -X utf8 Test/benchmark-b1/runtime.py development <runner参数>`，审计入口使用 `audit <audit_runner参数>`。该入口只补充运行环境，不改变模型、技能、任务和评分。已冻结实验的修复应另存版本、哈希和故障证据，不覆盖原冻结文件。

Unity 保持打开；一般改动通过资源刷新、重新编译或域重载生效。仅在不可恢复的编辑器故障，或关闭后重开的持久化验收中重启，并保留原因记录。

`pipeline.py` 仅监视既有开发调度器，六组全部交卷且封存哈希一致后启动独立盲审。遇到暂停、实验无效、用户中止或封存问题时停止推进并记录原因，不自行续开参赛会话。结果查询按用户要求每10分钟进行，内部本地状态监视不调用参赛模型。

会话续接后的结果检查统一使用 `python -X utf8 Test/benchmark-b1/monitor.py --admin E:/Test/.benchmark-admin/b1 --wait`。它把上次查询时间持久化到评委目录，间隔不足600秒时只读取轮询策略，不读取参赛状态；`--wait` 在同一进程中等到下一次查询，避免每次会话续接都提前检查。访问不到进程时保留未知状态，不把权限或观察问题误报成进程退出。

附加统计在全部封存后运行 `python -X utf8 Test/benchmark-b1/metrics.py --admin E:/Test/.benchmark-admin/b1`，写入 `results/ancillary-metrics.json`。按会话 ID 合并续接日志，使用各会话最后一份单调递增的累计 token 计数，再跨会话求和；不把累计快照、缓存输入或推理输出重复相加。缺失、计数回退或损坏日志会保留未知值和原因，不据此估造费用或返工次数。压缩统计仅指日志中实际记录的事件。此文件与主分数独立。

修改规范即产生新的实验版本，不能与已经开始的B1运行混用。机器规范与冻结文档的清单一致性由`test_spec.py`校验；首次冻结时还需保存文件哈希作为外部不可变记录。

全部六组交卷并封存后，才启动验收。六组使用相同冻结清单和测试种子，审计员不接收模型和技能组别；全部逐项报告齐备后，再按001–100逐项横向复核证据与判定标准。争议交独立审计员复测，不因其他组表现改变要求。横向复核完成前不发布最终排名。

已交卷时若编辑器已经关闭，`closed_seal.py` 提供评委专用封存路径，不启动 Unity、不继续开发模型。它保留原始失败记录，核验完整进程清单、已知后台工具终态及日志调用配对，对工程和存档做封存前后与副本哈希比对；全部通过才发布 `seal.json`。这证明没有已知写入任务，不代表操作系统级隔离。失败时保留待复核状态，禁止进入下一组；正式运行需归档实现版本和哈希，不能覆盖原冻结调度器。

## 当前测量方法：derived_operations_v1

本节说明当前源码中的审计基础设施，不替换原冻结公开任务、100项要求或初始 manifest。较早的 runtime 归档是历史测量版本；正式恢复须另行归档实现、哈希及故障证据，不能用本 README 默许覆盖旧版本或接受不完整测量。

每份匿名审计使用两个目录：`blind/candidate` 是从封存成果准备的原件参照 U，保持原业务内容与命名空间，永不由 Unity 打开，也不允许评委写入；`blind/runtime` 是最初逐字节复制 U 的执行影子 R，只有 R 可以被 Editor、Unity MCP 和标准资源构建流程打开。候选身份和初始哈希始终锚定封存成果，R 的合法派生写入不成为新的业务基线。评委不得修复业务代码或资产。

R 中由原程序、原包、原配置的标准操作引起的写入，要使用 `operation_provenance` 记录连续的操作 journal：操作前 begin，保留实际调用及结果，再 finish，保存完整前后字节与哈希。启动、正常游戏运行、原 Addressables builder 构建、正常停止 Play、正常退出 Editor 均属于可记录的生命周期；禁止把这些正常行为一概当作业务修复。原代码、依赖清单和原 GUID 锁定；不得手改原 `.meta`。非 GUID 的原 importer 自然写回仍须完整记录并逐路径独立复核，不能因 GUID 相同或声称材质语义等价就通过。

调度器初始化 provenance manifest，并复制经过哈希冻结的 `blind/provenance_tool.py`。以下是匿名 `blind` 目录内的 CLI 用法示例；`op.json` 必须先写入本次操作的真实身份与来源，不是可直接代用的占位证据。不要重复初始化已存在的 journal，也不要结束别人的活动操作。

```powershell
python provenance_tool.py begin --reference candidate --runtime runtime --evidence-root evidence --manifest evidence/provenance/manifest.json --kind normal_run --operation-id player-flow-001 --provenance-json op.json
# 执行并保存实际操作轨迹；补齐 op.json 的冻结证据引用后结束同一操作
python provenance_tool.py finish --reference candidate --runtime runtime --evidence-root evidence --manifest evidence/provenance/manifest.json --operation-id player-flow-001 --provenance-json op.json
python provenance_tool.py validate --reference candidate --runtime runtime --evidence-root evidence --manifest evidence/provenance/manifest.json
python provenance_tool.py validate --reference candidate --runtime runtime --evidence-root evidence --manifest evidence/provenance/manifest.json --require-review
```

`op.json` 包含 `actor_id`、精确 `invocation`（调用类型、原始命令、调用 ID）、原代码或原包入口的 `source_refs`，以及实际执行日志的 `evidence_refs`；引用均带 SHA256。finish 时的日志必须已经冻结，不能随后追加改写。controller 另导出本匿名审计的实际原生工具、会话及后代轨迹，并在独立来源复核前和发布前，核对导出字节与私有固定快照、身份和逐轮 manifest，不能只信评委自述或可写副本。

没有 `--require-review` 的 validate 仅说明结构和保护检查通过，不代表允许发布分数。独立的 `6.1 sol / high` provenance reviewer 必须检查每次操作、每条实际变化及原始入口/调用证据，在 `evidence/provenance-review.json` 作出逐路径与最终状态裁定；其真实会话身份不得是主审或被审批操作的执行者。正常的业务争议复核另行进行。最终停止 Play 的 drain 若产生新写集，也须纳入连续 journal 并再次独立批准。无法归因、未知写入或证据不足属于测量阻塞，不能当作被测功能的0分。

journal 的文件范围仅为 `Assets`、`Packages`、`ProjectSettings`，并不覆盖整个磁盘或操作系统。`Library` 中的目录/资源包、`ServerData`、构建输出及实际 JSON 存档等，必须按操作另外归档实际文件和哈希，提供原始来源与加载/持久化证据，再由 reviewer 审核归因。不能因 journal 通过就宣称这些文件也已被保护；091/095 仍须实际原 builder 的错误/输出路径/结果、catalog 与 bundle 以及原程序加载行为证据。

当前 `audit_runner.py` 的统一启动预检，限定经过源码哈希核验的 Addressables **1.21.21**。它先核对已加载 `Unity.Addressables.Editor` 程序集实际所属包的名称、版本和解析路径，再调用原 `ProjectConfigData` 的“已提示报告设置”标记入口，效果等同额外报告偏好提示中的“No”。已提示时不再调用 setter；`GenerateBuildLayout` 必须保持原值，不改变 builder、业务配置，也不执行或替代资源构建。原包源码、实际请求/响应、`Library/AddressablesConfig.dat` 前后完整字节均单独归档交独立复核。无 Addressables 明确记录跳过；来源、版本或响应不符则暂停测量。复用已有 Editor 也须预检，但有活动 journal 操作时先阻止，不代为结束操作或重启。

型号证据以 rollout 第一条 `session_meta` 作为自身身份锚；full-history 子会话内复制的父元数据不能覆盖子身份。所有继承和自身 `turn_context` 都核对实际型号及 `high`，并通过明确历史边界，或完整祖先自身 turn ID 集合，排除继承上下文；只有继承记录的子会话不能算作实际型号已验证。已完成轮次 checkpoint 要求原最终答复与末条原生 `agent_message` UTF-8 字节完全一致，之后存在对应 `turn.completed`，并绑定 controller 保存的同角色、同会话、同轮次原始日志 SHA。元数据解析恢复只继续既有完成轮次的后处理；真正剩余验收的明确纠正才在原会话续接，不因解析错误重新开审。

Editor 以隐藏窗口启动；一般刷新、编译和域重载不重启。持久化验收应合并为一次有记录的正常保存、停止 Play、idle、正常退出和重开流程，不追加崩溃恢复要求。正常原生命周期可能写回的文件须完整 journal，而不是通过禁止正常退出规避测量。禁止真实鼠标/键盘桌面注入、窗口激活或 OS 前台/焦点操作。定位 GameView 优先使用 `Resources.FindObjectsOfTypeAll`；确需获取窗口时显式 `focus=false`，不调用窗口 `Show`/`Focus`。这些约束不改变游戏内部正常输入框焦点逻辑。

六份有效报告齐备后，`consistency002` 横向一致性复核包必须同时携带同目录匹配版本的 `consistency.py`、`audit.py`、`operation_provenance.py` 和 `spec.py`，重新验证 U/R 与 provenance 前置条件；不得退回旧 runtime 的审计验证器，也不得只复制一致性脚本而沿用不支持当前方法的依赖。它按001–100检查六份匿名证据的判定标准，不自动改分或生成模型优劣结论。
