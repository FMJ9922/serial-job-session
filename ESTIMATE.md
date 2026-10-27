# 估分闭环

细则，由 [SKILL.md](SKILL.md) §五 引入。**按需读，不要开局全读。** Worker 开工前读 §二、§三；Closer 结档读 §四–§六；Estimator 读 §七、§八。

估分不是为了报工时。它是**拆卡是否合理的探测器**：估 0.5h 实跑 6h，说明这张卡不是一件事，或者依赖没核清。所以偏差率必须回填，且必须被下一次同类卡看见。

---

## 一、时间口径（不要混）

| 字段 | 定义 | 用来干什么 |
|---|---|---|
| `ai_first_pass` | 首次 attempt 的 AI 主动关键路径 | 拆首次成本 |
| `ai_rework` | 后续 attempt 的 AI 主动关键路径 | 拆返工成本 |
| `ai_net` | `ai_first_pass + ai_rework` | 估分准不准（排期里的 AI 段） |
| `ai_effort` | 并行子 Agent 主动耗时之和；无并行时等于 `ai_net` | 资源/成本 |
| `business_wait` | AI 停手到人验收/打回/再授权，与北京 09:30–12:30、14:00–19:00 的交集；夜间空等不计 | 人侧等待 |
| `delivery_workhours` | `ai_net + business_wait` | **单卡排期主口径** |
| `wall_elapsed` / 旧 `elapsed` | 真实墙钟 | 观察，不直接训练排期 |
| `orchestration_wait` | 上一卡结案到下一卡启动的工作窗口等待 | 只进阶段 rollup |
| `estimate_total` | 创建后不可回改的初估 | 对照基线 |
| `forecast_total` | 可追加 revision 的预测 | 滚动预测 |
| `ai_variance` | `ai_net / estimate_total` | 旧 `variance` 双读兼容 |

夜间真实 AI 执行仍计入 AI 时间。区间权威，合计由脚本复算；重叠/倒序/漏端点必须判红。

`agent_runs` / `total_tokens` / `context_compactions` / `peak_context_pct` 为可选实验字段，缺省为 null，**不得**当结档硬闸。

在线校准：按 estimate_group / faces / gate / 风险分层；冷启动用任务类型速度先验；有历史后用实际/预测的乘法校准，log 空间向全局收缩；目标 P70/P80，不用悲观最大值。`rejected` 记沉没成本，不抵扣剩余进度。估分库 v2 以 `instance_id + job_id + closure_revision` 幂等，事件为 `closed | acceptance_revoked | correction`；旧 v1 行不删除。新记录用 `closed_at`，不要把 rejected 写成 `accepted_at`。

| 字段 | 定义 | 用来干什么 |
|---|---|---|
| `elapsed` | **墙钟**：`started_at` → `ended_at`，含等人验收、等人开软件、等人回消息 | 算日历排期 |
| `ai_net` | **净耗时**：任务卡「执行区间」中各 attempt 主动执行时长之和 | 算估分准不准 |
| `estimate` | 开工前的净耗时预计，单位 h | 拆卡判据 + 排期输入 |
| `variance` | `ai_net / estimate` | 1.0 估准；>2 估低了或范围漂了；<0.5 估高了 |

**为什么不能直接拿 `elapsed` 算偏差**：墙钟含等人。人去吃饭两小时，会被记成「估分不准」，然后下一次同类卡的预计被这个噪音抬高。

**禁止用“墙钟减一个凭印象的等待数”计算 `ai_net`。** Worker 每次开始 attempt 写 `active_started_at`，停手并进入人验/返工等待前写 `active_ended_at`，同时把区间追加到任务卡。无法给出起止时间的主动工作标为 `untracked`，该卡不进入偏差率主分布。

## 二、Worker：开工前填 `estimate`

任务卡落盘时必填 `estimate` 与 `estimate_source`：

| `estimate_source` | 什么时候用 | 怎么估 |
|---|---|---|
| `menu` | `JOB-SHAPES`「待做」这条已写了预计 | 原样抄。**不要**因为「我觉得能更快」擅自下调 |
| `similar:{job-id}` | 菜单没写，但有相同 `estimate_group`、相近面数的已结档卡 | 抄那张卡的 `ai_net`，写清抄的是哪张 |
| `fresh` | 前两条都没有 | 现估。同时在任务卡「估分依据」里写清按什么拆的时间 |
| `backfill` | 工具回溯补的历史卡 | 只由脚本写，统计里单列，不混进主分布 |

有 `AUTO:ESTIMATE-STATS` 块时优先看它的 p50/p90，而不是凭印象。

**p90 不是 buffer**：如果估分总落在 p50 而实跑总落在 p90，说明这类卡有一个没算进去的固定成本（通常是探活、编译、或某个必然要返工一次的步骤）。把它写进菜单条目，不要每次都当意外。

## 三、`faces` 与拆卡闸（硬闸）

`faces` 枚举本卡要动的「面」。一个面 = 一类独立的实现层或产物类别，取值由领域 `ENV.md` 定义。

**开工前硬停条件**（满足任一即停）：

- `len(faces) > ENV.max_faces`（默认 2；领域可设 1–3 并写依据）
- `estimate` 超出领域 `ENV.md` 的上限

硬停时 Worker **不许硬做**，要做的是：报出建议拆法（拆成几张、每张的面与目标、依赖顺序），请人点名其中一张。拆法是建议，点名权在人。

默认 2 是保守起点，不是跨领域真理。领域可根据历史数据设 1–3；变更上限属于环境契约变更，必须由人明确批准。面越多，任一面失败阻塞其它面的概率越高，打回也越难局部重做。

**不要靠改 `faces` 绕闸**。把「改代码 + 改 shader」写成一个面叫「渲染」是在骗自己，下一次估分统计会替你揭穿：同一个面标签下偏差率两极分化，就说明这个标签太粗。

## 四、Worker 与 Closer：执行区间和结档回填

`accepted` 与终态 `rejected` **都要**回填。`rework_required` 不是终态，只结束当前 attempt，不写最终 `ended_at`。

1. Worker 每次停手时写 `active_ended_at`，在「执行区间」表追加 attempt、起止时间、主动时长和结果。
2. 返工时 `attempt + 1`，写新的 `active_started_at`；保留旧区间，不修改历史 attempt。
3. 终态时写 `ended_at`、`elapsed`（首次 `started_at` 到终态的墙钟）。
4. 算 `ai_net`：所有可追溯主动执行区间之和；任一区间无法追溯时写 `ai_net: untracked` 并在卡内说明原因。
5. 写 `variance = ai_net / estimate`，保留两位小数；`ai_net: untracked` 时写 `variance: null`，统计脚本会单列跳过。
6. `LOG` 该行写成：`accepted；耗时 墙钟 {elapsed} / 净 {ai_net}（估 {estimate}h，偏差 {variance}，attempt {N}）`
7. `variance > 2` 或 `< 0.5` 时，结档摘要里加一句**是哪一种**：
   - **估低了**：范围没变，就是低估了工作量 → 下次同类卡按实测抬 `estimate`
   - **范围漂了**：做着做着变成了另一件事 → 这是拆卡问题，不是估分问题；考虑往 `NORMS` 自动沉淀写一条，或把菜单条目拆细
   - **执行区间漏记** → 写 `untracked`，修记录流程，不要用估算数字补齐

区分这两者是整条闭环的价值所在。混在一起，统计只会告诉你「估得不准」，不会告诉你该改估分还是该改拆卡。

## 五、自动统计

```bash
python scripts/estimate_stats.py --jobs <领域包>/jobs --job-shapes <领域包>/JOB-SHAPES.md
```

脚本扫 `jobs/*.md` 的受限 YAML，按 `estimate_group`、`faces` 数量与面标签分组，输出 p50/p90 与偏差率分布。`shape` 是唯一菜单编号，不用于聚合同类卡。样本少于 5 张时不展示 p90，避免把极小样本伪装成稳定分布：

- 落 `<job-shapes 同级>/estimate-stats.json`（可用 `--out` 改）
- 回写 `JOB-SHAPES.md` 里 `<!-- AUTO:ESTIMATE-STATS -->` … `<!-- /AUTO:ESTIMATE-STATS -->` 之间的内容

历史回填必须提供人工核过的 JSON 映射，禁止从菜单自然语言中猜编号：

```json
{"12": 0.5, "13": 1.0}
```

```bash
python scripts/estimate_stats.py ... --backfill --backfill-map mapping.json
```

工具用映射中的 shape 预计和历史 `elapsed` 补写，一律标 `estimate_source: backfill`。**回溯数据只看趋势，不作为新卡 `similar:` 依据**——它的 `ai_net` 是墙钟冒充的，偏高。

在线校准（log 收缩，默认 P70；先验见母版 `estimate-config.json`，禁止「AI 永远快 5–10 倍」全局常数）：

```bash
python scripts/estimate_stats.py --library-stats --calibrate --prior-hours 8 --group schema-check
```

区间判红码：`INTERVAL_REVERSED`、`INTERVAL_OVERLAP`、`INTERVAL_UNPARSED`、`LEDGER_SUM_MISMATCH`。阶段 >16h 警告 `PHASE_OVER_16H`，>24h 警告 `PHASE_OVER_24H`。菜单表：`MENU_DUP_SHAPE`、`MENU_CYCLE`、`MENU_DEP_DRIFT`、`SHAPE_MULTI_PHASE`。

`--dry-run` 只打印不写文件。Windows 上默认 `python` 可能是 2.x，需要显式用 `py -3` 或 `python3`。

**新卡强制填 `estimate`，旧卡不追改。** 不要为了让统计好看去改已 `accepted` 卡上的历史数字。

## 六、Closer 硬闸

新协议卡缺 `estimate` / 执行区间 → 不得标 `accepted` 或终态 `rejected`。原则上必须填写 `ai_net` / `variance`；确实无法复算时显式写 `ai_net: untracked`、`variance: null` 并说明原因。历史卡可用 `estimate_source: backfill`。两类不完整数据都不进入主偏差率分布。

## 七、启动阶段整体估分（两层）

上面六节讲的是**单卡**估分。启动一摊长期工作时还需要一个总量，否则第一次就只能靠感觉答「这大概要多久」。总量放在领域包的 `PLAN.md`，与卡级估分构成两层：

| 层 | 落在哪 | 单位 | 回答什么 | 谁写 |
|---|---|---|---|---|
| 阶段级粗估 | `PLAN.md` 阶段表 | 净耗时区间 `低–高` h | 「这摊活大概多少」 | Bootstrap / Estimator |
| 卡级细估 | 任务卡 `estimate` | 净耗时单值 h | 「这一件多少」 | Worker（开工前） |

**冲突时以 `JOB-SHAPES.md` 为准**：它是唯一施工菜单，`PLAN.md` 只是估分单。不得按 `PLAN.md` 的阶段点名开工，也不得把阶段当任务卡。

### 区间与不确定度（防假精度）

未拆成菜单条目的阶段**必须写区间**，不许写单点值。单点值在信息最少的时候伪装成承诺，之后不管超没超都会被当成「估分不准」。

| `unc` | 什么状态 | 区间宽度要求 |
|---|---|---|
| `high` | 只有目标，还没拆法 | 留足；上界通常 ≥ 2× 下界 |
| `med` | 拆法已定，但没落菜单 | 上界一般 ≥ 1.5× 下界 |
| `low` | 已全部拆成菜单条目 | 只有这一档允许收窄到 1.5× 以内 |

`unc` 不是态度，是**已拆到什么程度**的事实描述。已拆卡编号为空却写 `low`，下一次滚动统计会揭穿：这个阶段的实测会稳定落在区间外。

### 依据优先级

1. `library:{estimate_group}` — 跨实例估分库里同类卡的 p50/p90 × 预计卡数（见 §八）。冷启动时这是唯一能替代感觉的东西。
2. `similar:{job-id}` — 本域已结档卡。
3. `fresh` — 现估，必须在 `PLAN.md`「粗估依据」里写清按什么拆的时间和最大的不确定性在哪。

### 滚动收敛

每张卡结档后跑：

```bash
python scripts/estimate_stats.py --jobs <领域包>/jobs --job-shapes <领域包>/JOB-SHAPES.md --plan <领域包>/PLAN.md
```

脚本按阶段「已拆卡编号」把已结档卡的实测归到阶段，回写 `AUTO:PLAN-ROLLUP`：每阶段的实测墙钟与净耗时、未完成部分的剩余区间、总量区间、墙钟完成度，以及**净/墙钟膨胀系数**（`elapsed 合计 / ai_net 合计`）——用它把净耗时剩余量换算成日历时间，比凭印象加 buffer 靠得住。

改阶段区间时在「滚动收敛记录」追加一行并写清原因，不改历史行。只在有实测或范围确实变了时改；**不许为了让偏差好看去回改**。

### 粗估禁止回流成卡级 `estimate`（硬闸）

`estimate_source` 只有 §二 那四个取值，**没有 `plan`**。阶段区间是十几张卡的合计，把它写进单卡会让偏差率主分布失去意义——那之后统计既不能告诉你估分准不准，也不能告诉你拆卡对不对。校验脚本会对 `estimate_source: plan` 直接判红。

同理，`PLAN.md` 的阶段区间不进偏差率统计：偏差率只算「单卡估 vs 单卡实测」。阶段准不准由 `AUTO:PLAN-ROLLUP` 的实测 vs 区间自己说话。

## 八、跨实例估分库

一个领域包的历史只在这个领域包里。新起一个工作流就是新实例，`jobs/` 从零开始——如果没有库，每个新工作流的第一批估分都只能是 `fresh`，PM 那句「下次能估得更准」就永远不成立。

库解决的就是这一件事：**把已结档卡的估分字段汇总到一个跨实例、跨领域的追加式文件**，新工作流冷启动时可查。

### 位置与形态

- 默认落**实例所在 git 仓库根**的 `.serial-job-session/estimate-library.jsonl`，**进 git**，全组共用。
- 实际路径记在实例 `INSTANCE.json` 的 `estimate_library`（相对仓库根的 posix 路径，例如 `.serial-job-session/estimate-library.jsonl`），可用 `--library` 覆盖。
- 路径落在 skill 母版内 → 判红（母版不可变原则）。
- JSONL 追加式，一行一张已结档卡，按 `instance_id + job_id` 幂等去重；同一张卡重复跑不会写重，也不会被后来的运行改写。

### 写什么、不写什么

只写估分字段：`estimate_group` / `faces` / `estimate` / `estimate_source` / `ai_net` / `elapsed` / `variance` / `status` / `accepted_at`，加上 `instance_id` 与 `domain` 用于溯源。

**不写**产物路径、任务卡正文、目标描述、证据内容。库会跨项目流动，它只该携带「这类活多久」，不该携带业务内容。

### 怎么用

```bash
# 结档后追加（路径取 INSTANCE.json）
python scripts/estimate_stats.py --jobs <领域包>/jobs --job-shapes <领域包>/JOB-SHAPES.md --library-append

# 冷启动查同类活的历史分布（不需要 --jobs）
python scripts/estimate_stats.py --library-stats --library <库路径> --group ui-assembly
```

读库和读本域统计一样的纪律：样本 <5 不看 p90；`estimate_source: backfill` 与 `ai_net: untracked` 的行不进主分布。

**跨领域用库要看清 `estimate_group` 是不是同一件事**。两个项目都叫 `schema-check`，一个是校验 20 个字段、一个是校验 2000 行表格，合并统计只会得到一个谁都用不上的中位数。名字撞了但活不同时，起一个新的 `estimate_group`，不要图省事复用。
