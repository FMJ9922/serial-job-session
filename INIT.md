# 初始化领域包（从模板创建实例）

日常标准口令：`{route_key}：推进` / `{route_key}：按菜单做 N` / `{route_key}：验收通过` / `{route_key}：打回` / `{route_key}：托管执行`。

**本文不是安装 skill。** 这里假定本仓库已经能读到本母版的 `SKILL.md` 与 `scripts/`（`SKILL_ROOT` = `SKILL.md` 所在目录）。把母版拷进另一个 git 仓、改加载入口、核对目录规范 → [INSTALL.md](INSTALL.md)。人类第一次怎么开口 → [FIRST-RUN.md](FIRST-RUN.md)。

本母版就是 `SKILL.md` 所在目录；同一仓库只保留一份，禁止双入口（例如同时放 `.cursor/skills/`）。

---

## 一、这是什么，解决什么

人负责拆模块、点名下一张卡、验收或打回。AI 只干被点名的那一张，不扩边界。**记忆写在领域实例里，所以新开一个会话也能接着干**——它读文件，不读上周的对话。

和「一次把大任务自动扇出给很多 Agent」不是同一件事。适合：工作能拆成可二值验收的原子合同、要跨很多天、中途需要人拍方向。不适合：一次性小活；没法定义「过 / 不过」的活。

路线未定可以先 `plan_mode: discovery`（只出调研/决策/原型卡）；路线确认后切 `delivery`。控制模式默认 `manual`；只有用户明确说托管 / 代理 / 批量 / 夜跑才读 [DELEGATION.md](DELEGATION.md) 进入 `managed`。

普通的“推进一下”“下一步做什么”没有 `route_key`、会话未绑定、注册表多于一个实例时，不应触发本 skill。

---

## 二、第一次初始化

Skill 只是协议，**需要一个领域实例**才能工作。用户在主 Agent 里说：

```text
使用 serial-job-session 初始化。约定 route_key：{唯一关键字}。
我想长期推进：{一句话}。路线未定走 discovery / 已定走 delivery。
允许你探查：{目录，以及可以查询的工具或服务}。
控制模式默认 manual。
```

主 Agent 会：

1. 在授权范围内只读探查，不默认扫描整仓，不启动未知服务。
2. 用 `instantiate_domain.py --target … --domain-name … --route-key …` 在 skill 目录外创建独立实例；`--route-key` 与仓库 `.serial-job-session/instances.json` 登记同成同败。`INSTANCE.json` 由脚本生成（`serial-job-session-instance/v2`），不要手填模板。
3. 把 `ENV.md` 标为 `draft`，区分已验证、推断、策略和待确认项。
4. 拟出可解析待做表与 `PLAN.md` 阶段粗估，不创建任务卡、不施工。
5. 策略、菜单、总量估分合并成一次确认。用户可回“默认即可”。飞书：probe 不通则确认清单里不出现飞书项；已通才问要不要表。
6. 锁定 ENV、`plan_status: confirmed`，跑正式 `validate_domain_pack.py`。校验未过不得开卡。已写 `feishu.json` 则 `feishu_sync.py --event seed-menu`。
7. 显示 [FIRST-RUN.md](FIRST-RUN.md) 的接入完成提示，然后停。

实例目录必须在 `SKILL_ROOT` 之外，也**不得**落在文档树（`feature-docs` / `system-docs` / `drafts` / `human-readme`）。示例默认：

`.cursor/workflows/{slug}/`

`instantiate_domain.py` 对文档树目标直接 `DOCS_TREE_FORBIDDEN`。路径仍须用结构化提问确认 slug，禁止默认写进母版或未授权的目录。

### 启动确认（Bootstrap 必须问清）

| 项 | 取值 | 默认 |
|---|---|---|
| `route_key` | 仓库内唯一 | Agent 提议，人确认 |
| `plan_mode` | `discovery` / `delivery` | 路线未定 → discovery |
| `control_mode` | `manual` / `managed` | **manual**；managed 必须用户当面点名托管 |
| 实例目录 | `SKILL_ROOT` 外、尚不存在、非文档树 | 默认 `<repo>/.cursor/workflows/{slug}/`；**结构化提问**确认 slug |
| 飞书任务表 | 不需要 / 创建 / 绑定已有表 | **不需要**。仅当 `feishu_sync.py --event probe` 已通才问；不通则跳过。见 [FEISHU.md](FEISHU.md) |

### Agent 可以自动发现什么

路径、软件版本、探活、可能的单实例资源、faces 与估分类。

### 哪些需要用户确认

本域禁止项、人眼验收面、转走口令、单实例资源、非默认 `max_faces` / 估分上限。

### ENV 生命周期

```text
missing → draft → locked
```

- `draft`：Bootstrap 可修改，不能开任务卡。
- `locked`：日常会话只读。
- 更新环境时说“使用 serial-job-session 更新环境”，先展示差异再锁定。

```bash
python <skill>/scripts/runtime_guard.py --route-key <key> --allow-draft
python <skill>/scripts/runtime_guard.py --route-key <key>
```

实例目录（`INSTANCE.json` 由脚本生成，不在模板里手抄）：

```text
<工作流目录>/
├── INSTANCE.json
├── indexing ignore 文件  # 排除 output/；不要用会挡按路径 Read 的全局 ignore
├── ENV.md
├── CURRENT.md
├── PLAN.md
├── JOB-SHAPES.md
├── NORMS.md
├── FACTS.md
├── LOG.md
├── protocol/          # 领域差异；不得覆盖 generic 硬约束
├── jobs/
│   └── _template.md
├── workflows/         # 轻量 Workflow 模板；默认不加载
└── managed/           # 托管 run 模板；默认不加载
```

跨实例估分库默认在仓库根 `.serial-job-session/estimate-library.jsonl`。**建议进 git**。若宿主不允许点目录，先向人确认替代路径，再写 `INSTANCE.json` 的 `estimate_library`。

Close / migrate 回滚快照默认在同一仓库根的 `.serial-job-session/private/`（脚本写入该目录下 `.gitignore`）。**不要进 git**，也不要写进领域包的 `.sjs-tx/backup/`。无 git 的沙箱才回退到实例旁 `.sjs-tx/`。

### 旧实例升到 3.0

用 `migrate_protocol.py dry-run|apply|recover` 打副本，禁止对生产实例无记录原地改。v1/v2.2 终态卡只读兼容。禁止 `git reset --hard` 覆盖未提交修改。

维护者手动实例化：

```bash
python <skill>/scripts/instantiate_domain.py --target <工作流目录> --domain-name <领域名> --route-key <关键字>
```

目标必须在 skill 之外且尚不存在；禁止直接改 `domain-pack-template/`。

### 第一批菜单怎么写

`JOB-SHAPES.md`「待做」是推荐依赖顺序，不是自动施工计划。表必须可解析（编号 / phase_id / 短名 / 目标 / estimate_group / depends_on / faces / actor / human_accept / 风险 / 预计）。「短名」给聊天菜单与飞书「任务」用，禁止 `A + B + C` 堆砌。「目标」必须能独立说「过 / 不过」。长说明放编号细则。「预计」是 `delivery_workhours` 单值 h。

---

## 三、日常口令

必须带 `route_key`，除非当前会话已绑定唯一实例。

| 你说 | Agent 做什么 |
|---|---|
| `{key}：推进` | 只列待做，然后停 |
| `{key}：按菜单做 N` | 只开编号 N |
| `{key}：验收通过` | `close_transaction.py`，顶层可兼任 Closer |
| `{key}：打回` | 合同仍成立 → `rework_required` |
| `{key}：打回已结档 {job-id}` | `reopen_job.py`（无下游开工、无可逆副作用） |
| `{key}：继续返工` | 仅 `rework_required` 卡 attempt+1 |
| `{key}：出估分单` | 只写 PLAN 与菜单预计列 |
| `{key}：托管执行 …` | 读 DELEGATION.md；高风险必须加粗确认；未授权不得 dispatch |
| 审 NORMS 自动沉淀 | 只审自动沉淀节，不开工 |

结案后 `post_close.py` 可给导航，**禁止未经点名自动开工**。

---

## 四、头几天你会遇到的四件事

**「它又想帮我规划接下来三步」**：说「只做我点的这一件」。

**「估分离谱」**：跑 `estimate_stats.py`；重点是偏差大时分清估低了还是范围漂了。

**「PM 要总量」**：给 `PLAN.md`。AI 净耗时不是人力工时。

**「门禁全绿但产物不对」**：问「怎么让这个门禁红一次」。

**「想并行」**：先看 `ENV.md` 单实例清单与 [PARALLEL.md](PARALLEL.md)。

**「打回后」**：合同仍成立 → `rework_required` +「继续返工」；方向错 → `rejected`。

---

## 五、维护者参考字段

审查 Bootstrap：授权范围、探活、单实例资源、faces、`max_faces`、人眼验收面、禁止项、转走口令、`route_key`、`plan_mode`、实例根路径、飞书 CLI probe（不通则跳过表）。改母版走 Skill Maintainer：[IMMUTABILITY.md](IMMUTABILITY.md)。完整性 `--update` 仅测试全绿且用户授权维护 skill 之后。
