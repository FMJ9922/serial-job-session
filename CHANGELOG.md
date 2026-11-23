# Changelog

## 5.3.1 — 2026-11-23

- 新增 `context-budget`，读取 Codex 线程历史并估算派发 subagent 前的上下文余量。
- 增加独立的上下文分配核心接口，按 75% 目标、50%–100% 可接受区间评估任务利用率。
- 最近 3 张同类已验收任务的利用率中位数低于 50% 时提示重新审视后续任务卡。

## 5.3.0 — 2026-11-23

- `context` 和 `handoff` 增加 `--view recovery`。交接先显示活动叶任务、暂停依据、授权、最近进展和完整必验项。多个活动叶任务时列出选择。
- `--view full` 与既有完整接口兼容，可用 `--pointer` 定向读取合同和继承参数的具体条款，用 `--keys-only` 浏览键名。DSH 工具支持对应选项。
- 新增阻断性检查时，说明所承接的合同义务，以及通过或失败分别会改变哪项决定。辅助检查无法验证的行为交由实际运行验收。
- 方法身份已更新。既有 v5 实例首次使用新方法写入前须显式重绑定，旧快照可直接读取。

## 5.2.2 — 2026-11-16

明确任务命名与工作层级，让长期工作的实际进展能被人读懂。

- 以“对象＋预期结果”的可读名称作为任务主称谓，稳定 ID、字母与 Stage 仅作历史追溯；开工、换批次、派工、提交、验收和返工披露所属任务与当前工作。
- 区分正式任务卡、卡内实施批次、候选版本和返工次数；批次检查或证据冻结不代表整卡验收，不为每次编译或快照建立微卡。
- 历史命名通过现有 parameters、progress / knowledge 追加显示名与层级映射，不改写旧 ID、title、日志、预算和验收义务；需要独立合同与依赖管理才建子卡。
- 本次为 agent 协议与文档修改，未新增 CLI 字段或改名 API，也不宣称名称受到程序强制校验。方法身份变化：此前实例需审阅新方法并显式 rebind 一次，无需重建实例。

## 5.2.1 — 2026-11-16

介绍重写：明确面向跨天、跨周商业交付的 AI 辅助编排，持续对齐目标与验收标准。

- 中英文 README 首屏改为目标用户、长期编排的设计重点、路线调整与验收义务承接，并用系统迁移示例解释实际用途；安装与实现细节放在介绍之后。
- 区分 CLI 检查、agent 协议与宿主能力；说明证据真实性、重规划语义覆盖和领域判断的边界，移除不会丢进度、授权不会越界及固定成本效果的绝对表述。
- 更新 npm 包简介。运行逻辑与共享方法未变；发布版本统一升至 5.2.1，已绑定 5.2.0 的实例无需再次重绑定。更早实例仍按 5.2.0 升级说明接受新方法。

## 5.2.0 — 2026-11-16

关键节点主动汇报（human disclosure），并统一版本号。

- 新增 `references/reporting.md`：检查点、四段格式、创建/建任务/开工/派工/验收/失败/阻塞/恢复/收口模板，以及错误路径、终态、duplicate、心跳和反例。
- `SKILL.md` 增加「向人类汇报」硬规则；`governance.md` 增加创建简报与确认门；`runtime.md` 明确“工具输出≠汇报”、先读 `outcomes`、非终态 `progress`/终态对话、无当前任务的恢复分支；`agents.md` 增加派工/恢复披露；`nesting.md`、`estimates.md`、`feishu.md` 补充对应汇报。
- DSH：`sjs` 工具描述、工具卡片标题、结果提醒和失败提示改为人类可读；对 `DECISIONS_REQUIRED`、`TEMPLATE_CHANGED`、`REVISION_CONFLICT`、`TASK_NOT_ACTIVE`、超时/提交未知、`allowed:false` 给定向提示；设置页补充汇报说明。
- 版本统一：`VERSION` 与 `package.json.version` 同为 `5.2.0`，`skill-integrity.json.protocol_version` 随之为 `5.2.0`。`template_identity()` 新增 `method`（排除 `VERSION` 的方法身份），`integrity` 仍覆盖完整安装文件；`same_template` 优先比较 `method`，单纯升版本号不再要求 rebind。
- 旧实例：5.2.0 本身改动了方法文件，首次写入会报 `TEMPLATE_CHANGED`；审阅新方法后用带真实人类来源的 `rebind_template` 接受。完成后再升版本号不再触发 rebind。
- 测试：新增方法/安装身份真实文件测试、版本号变化不 rebind/方法变化仍 rebind、`progress` 终态与无当前任务恢复、DSH 标题/提醒/失败提示；`check-pack` 要求打包 `references/reporting.md`。

## 5.1.4 — 2026-10-28

修复仅影响测试脚本的问题：`npm run smoke:stdin` 写死 `const python = 'python'`，在 `python` 指向 2.7 的机器上自己踩中了它要防的坑（`future feature annotations is not defined`）。

- `dsh/index.js` 导出 `resolvePython()`：与 `sjs` 工具共用同一套候选顺序（设置 → `SJS_PYTHON` → `python3` / `python` / `py`）和同一个 3.11+ 版本门。
- `dsh/smoke-stdin.mjs` 改用它；失败时会列出每个候选被拒的原因，成功时打印实际解释器与版本。
- 实测：把 `C:\Python27` 放到 PATH 最前（`python` = 2.7.16）后 `smoke:stdin` 仍通过（回退到 `py`，3.11.9）；`SJS_PYTHON=C:\Python27\python.exe` 会被明确拒绝并说明原因。
- 运行时路径本来就有版本门，本次不改行为，只让冒烟脚本复用同一逻辑。

## 5.1.3 — 2026-10-28

修复 cp936（GBK 控制台）下 `sjs` 工具传中文 payload 直接崩溃。

- 根因：Windows 管道 stdin 按控制台代码页解码，Node 写入的 UTF-8 中文在 `sjs.py` 里被按 GBK 解读成孤立代理项，随后 re-encode 抛 `'utf-8' codec can't encode character … surrogates not allowed`。
- 宿主侧（`dsh/index.js`）为每个 Python 子进程强制 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`（新增 `pythonEnv()`），解释器探测、`sjs` 调用与面板「检测环境」共用。不再依赖宿主机控制台代码页。
- 新增 `npm run smoke:stdin`（`dsh/smoke-stdin.mjs`）：用真实 CLI + 中文 payload + stdin 跑 `init --dry-run`，断言中文往返无损；CI 执行。
- `dsh/check-pack.mjs` 与 `dsh/smoke-stdin.mjs` 补进 npm `files`（此前 `smoke:pack` 指向的文件没打包）。
- 离线校验增至 22 项。
- 说明：本次只改宿主侧，未触碰完整性基线内的 `scripts/sjs.py`，已有实例不会报 `TEMPLATE_INTEGRITY_FAILED` / `TEMPLATE_CHANGED`；手动在 cp936 下用 `--input -` 时仍需自行设 `PYTHONUTF8=1`，或改用 `--input <utf8.json>`。

## 5.1.2 — 2026-10-28

修复 npm 安装包缺文件导致的启动失败（5.1.0/5.1.1 的 npm 包受影响，GitHub/本地目录安装不受影响）。

- `package.json` 的 `files` 补上 `skill-integrity.json`：`sjs.py` 启动时无条件读取该基线，缺文件会让 `init / context / apply / check` 全部在入口失败。
- 新增 `npm run smoke:pack`（`dsh/check-pack.mjs`）：发布前用 `npm pack --dry-run --json` 断言 tarball 含全部运行时文件，CI 同步执行。
- 插件在启动/调用时检查安装完整性：缺文件时 `sjs` 工具直接报「installation is incomplete: missing …」并提示重装，设置/插件页显示同样的警告，不再等 Python 抛 traceback。
- Python 探测改为解析 `--version` 并要求 **3.11+**：Windows 上 PATH 里排第一的 `python` 常是 2.7（`--version` 退出码为 0），以前会被误采纳，随后 `from __future__ import annotations` 直接 SyntaxError。候选顺序同时改为 `python3 / python / py`，失败信息列出每个候选被拒的原因。
- 离线校验增至 22 项；Python 版本解析、安装完整性、`files` 覆盖均有断言。

## 5.1.1 — 2026-10-27

- 插件页补全介绍：bundle 详情页与设置页的面板新增「这个插件是什么 / 解决的问题 / 适合与不适合 / 怎么用 / 注意事项」，并附完整介绍链接。
- 新增 `dsh/INTRO.md`（中文）与 `dsh/INTRO.en.md`（英文）完整介绍；中英 README 的 DSH 章节补精简版。
- 插件卡片短描述改为定位型一句话（`dsh/locale/{en,zh}.json`），`package.json` 的 `description` 同步。

## 5.1.0 — 2026-10-27

首个 DSH 插件版本（`package.json` 的 `dsh-serial-job-session@5.1.0`；skill 协议版本仍是 `VERSION` 的 5.0.0）。

- 插件新增 Web UI 参数面板：设置页「串行任务」可调整 Python 解释器、单次超时与额外 CLI 参数，保存后下一次 `sjs` 调用生效；含环境检测与只读信息。参数存 `$DSH_HOME`（默认 `~/.dsh`）下的 `serial-job-session.json`，经 `GET /serial-job-session/api/state`、`POST /serial-job-session/api/settings`、`POST /serial-job-session/api/probe` 读写。
- 插件注册改为允许模型按需加载（`invocation.modelInvocable: true`），移除 `SJS_MODEL_INVOCABLE` 开关。
- 新增 DSH bundle：根 `package.json` 声明 `dsh.bundle.patch` 与 `dsh.client`，`dsh/index.js` 把 `SKILL.md` 注册为 Harness skill `serial-job-session` 并新增 `sjs` 工具（在会话工作目录调用 `scripts/sjs.py`，JSON 走 stdin）。
- 修复启动顺序：skill/tool/HTTP 路由改为经 `ctx.inject` 等服务就绪后注册；否则全新启动时 Host 半会静默不注册（面板接口 404、`sjs` 工具缺失）。
- 插件显示信息：`dsh/locale/{en,zh}.json` 与 `dsh/icon.svg`；离线校验 `dsh/plugin.test.mjs`（`node --test`，18 项覆盖 skill 构建、argv 映射、参数清洗/落盘、三个接口与启动顺序）。
- `check_skill_integrity.py` 排除 `dsh/`、`node_modules/` 与根 `package.json`，插件打包文件不计入 skill 文本基线；基线随本次维护更新。
- 文档补充 DSH 安装方式、参数面板与差异说明（勿与 `~/.dsh/skills` 混装、允许模型按需加载、`SJS_*` 环境变量优先级）；CI 增加 Node 语法与插件测试。

## 5.0.0 — 2026-10-27

- 共享模板加轻量实例：四个初始数据文件、两条追加式 JSONL 日志、可重建快照及可配置分片；不再复制领域包、协议或 Markdown 卡片。
- 创建时选择人工主导、条件自治或全 AI；确认任务相关门槛、风险、失败策略和可选模块。判断保留证据与未知项，必验项不由评分覆盖。
- 支持角色 agent 会话复用、最小交接、独立 reviewer、父子任务与统一资源边界。
- 估分计时、宿主只读采集与飞书 Base 镜像按需加载；共享估分库与同步回执改为事件数据。
- 统一 sjs.py CLI，保留幂等、版本检查、唯一写者、崩溃恢复和模板身份检查；增加1000任务的上下文读取验证。
- 直接替换 v3/v4 运行链路及旧夹具，不提供执行兼容或实例迁移，不触碰其他项目的旧数据。
- 中英文使用说明、共享参考、示例、schema、测试和 CI 同步到 v5。

## 4.0.0 — 2026-10-27

首个开源版本（MIT）。

- 协议：v4 卡状态机、七章卡合同、CURRENT/菜单/LOG/FACTS 投影、事务与回滚。
- 执行：串行 / 并行 / 托管 / 夜跑、lease 与单实例资源约束、方向纠正卡。
- 估分：`estimate` / `ai_net` / `elapsed` / `business_wait` / `variance`，跨实例估分库。
- 工具：注册表、上下文菜单、卡校验、机械门禁、迁移（通用）、回放夹具。
- 删除内部业务域专用的迁移默认值与示例，改为实例配置（`direction-review.json`、`norms-v4.md`、`dynamic-constraints.md`）。
- 测试夹具内置到 `Test/serial-job-session/`，`quality_gate.py` 支持仓内路径。
