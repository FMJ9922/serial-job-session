简体中文 | [English](README.en.md)

# serial-job-session v5

**面向跨天、跨周商业交付的 AI 辅助编排协议。**

让长期重度 AI 工作持续对齐目标与验收标准。**关键事实落盘，交接有据，减少对聊天记忆和上下文压缩的依赖。**

当工作超出一个 agent 的上下文，人往往需要反复拆任务、交代背景、协调执行者，并确认多轮返工之后的“完成”是否仍符合最初要求。serial-job-session 让 AI 辅助承担这些编排工作：把目标、任务合同、候选版本、评审、失败与下一步保存在项目中；续接时读取当前状态，按依赖推进，按约定标准验收。

**允许调整路线，但调整要有依据，未完成的验收义务要明确承接。** 人可以选择人工主导、条件自治或全 AI，把参与方式和授权边界提前说清楚。

## 为谁而做

适合已经用 AI 做商业级工作，需要跨天、以周为单位持续交付的人：迭代开发、持续重构、系统迁移，以及需要多人或多个 agent 交接的长期工程。目标、领域知识与成功标准由人和团队提供；AI 按协议辅助拆解、编排、执行和评审。

在敏捷开发中，可以围绕一个迭代目标拆分有依赖、可独立验收的任务，并在新证据出现后调整路线。任务记录承接每次交接中的事实；已有的 Backlog、Sprint 和团队协作工具继续承担各自职责。

## 它着重守住什么

1. **验收标准在多轮尝试后仍然有效。** 每次评审绑定合同版本与候选版本；新候选不能沿用旧验收。必验项失败、未知或没有证据引用时，分数再高也不能通过。删改原有必验项需要记录真实人类批准，并使旧候选与评估失效。
2. **换会话、换执行者时有据可接。** 提议、执行中、候选、已验收和失败有不同状态。追加式记录保留历史，`context` / `handoff` 提供当前合同、依赖、反馈和下一步；接手不只依赖上一轮压缩出来的聊天摘要。
3. **路线可以调整，欠下的验收不能静默消失。** 方向评估先记录备选、依据和未知，再按已选模式采纳。任务重排时，编排者需要明确原有未完成义务由哪些合同承接；原失败保留，失败依赖不能当作已通过。
4. **人决定参与多少，关键变化保持可见。** 开工、方向和验收按人工主导、条件自治或全 AI 模式决策；关键节点要求用简报说明结果、依据、下一步和待决项。返工有上限，耗尽后按策略停止或继续独立任务，保留成果与恢复线索。
5. **经过验证的方法可以复用。** 共享 skill 保存方法，项目实例保存参数和事实；经验带证据记录，方法升级显式接受。具体项目不需要复制整套流程，也不必在每次交接时重新讲完所有规则。

### 一个具体例子

两周的系统迁移要求“保持原行为、通过回归测试、完成真实环境验证”。途中原方案行不通，AI 可以评估替代路线、调整任务顺序；“真实环境验证”仍须在后续合同中明确承接。若新候选只有单元测试通过，真实环境验证仍为 unknown，就不能把整项任务记为验收通过；换一个会话后，这个缺口仍在记录中。

### 与任务规划、多 agent 调度如何配合

规划工具帮助决定下一步做什么，调度工具负责调用模型和执行者；本协议重点维护长期执行中的目标、合同、版本、依赖、决策与交接记录。它可以和宿主已有的规划、协作工具一起使用。实际模型选择、agent 创建和工具执行由宿主提供；不要求固定的强弱模型组合。

这些是本项目的设计重点，并不声称是市场独有能力。对同样具备这些机制的工作流，使用成本与交付效果需要在真实任务中比较。

### 承诺的边界

- CLI 会检查版本、任务状态、依赖、记录中的 reviewer 身份及必验项结果；它不会自动证明证据真实、测试充分、验收标准正确，也不会自动验证重规划前后的语义覆盖。实际产物仍需测试、独立评审和领域判断。
- 授权与角色身份是来源记录，不是安全鉴权或操作系统沙箱；日志也不是防篡改审计系统。可靠性依赖宿主如实记录，并通过公开操作追加事件。
- 汇报、交接和验收义务承接包含 agent 应遵守的协议，不能保证每个宿主或模型都执行到位。经验沉淀也需要判断与维护，不承诺自动学习、零漂移或固定节省多少 token / 人工时间。

短小、一次性任务通常不需要这套流程；当反复交接、纠偏和核验已经成为负担时，它更值得使用。

## 开始

需要 Python 3.11+，只使用标准库。将仓库安装到宿主可发现的 skill 目录，运行测试：

```bash
python -m unittest discover -s scripts -p "test*.py"
python -m unittest discover -s Test/serial-job-session -p "test*.py"
python scripts/check_skill_integrity.py
```

从共享示例选择并修改参数，先预检，再创建。实例必须在 skill 外；下面的路径均是占位示例：

```bash
python scripts/sjs.py init --instance <project>/.cursor/workflows/demo --input examples/manual.json --dry-run
python scripts/sjs.py init --instance <project>/.cursor/workflows/demo --input examples/manual.json
python scripts/sjs.py context --instance <project>/.cursor/workflows/demo
```

创建时集中确认：决策模式；条件自治的风险和评分门槛；失败处置；是否启用估分计时和飞书同步。用户已明确的项目不重复询问。缺失模式会返回选项，不自动套用默认。

示例分别为 [人工主导](examples/manual.json)、[条件自治](examples/conditional.json)、[全 AI](examples/full-ai.json)。**示例中的路径、评分和预算需要按实际任务确认，不能当作通用授权。**

标准口令为 `route_key：意图`，如 `界面优化：推进`。宿主必须显式加载本 skill 或转发口令；`disable-model-invocation: true` 的含义见 [安装说明](INSTALL.md)。

## 在 DSH（DeepSeek Harness）中安装

本仓同时是一个 **DSH bundle**：安装后它把 `SKILL.md` 注册成名为 `serial-job-session` 的 skill（资源根仍是本仓目录），并新增 `sjs` 工具，在**当前会话工作目录**里调用 `scripts/sjs.py`。因此不需要再关心仓库绝对路径和 Python 解释器位置，`init / context / start / apply / check / recover` 等子命令都能直接调。

安装方式二选一（**不要同时用**）：

- 本地目录（开发、跟随本仓改动）：在 DSH 里执行 `dsh plugin add <本仓绝对路径>`，或让 agent 调 `plugin_manager` 的 `install_bundle`，target 填本仓绝对路径。
- 从 GitHub：`dsh plugin add github:FMJ9922/serial-job-session`。

安装后可以在插件页看到「串行任务会话」，新会话的 skill 目录出现 `serial-job-session`，`sjs` 工具可用；设置页会多一个**「串行任务」**页面用来调参数（见下）。依赖 Python 3.11+；`python` 不在 PATH 时用环境变量 `SJS_PYTHON` 指定解释器，`SJS_TIMEOUT_MS` 调整单次调用超时（默认 120000 ms）。

版本说明：从 5.2.0 起，`VERSION`（skill 协议）与 `package.json` 的 `version`（插件）统一为同一号。模板身份分两层：`integrity` 校验完整安装文件（含 `VERSION`），`method` 只跟踪方法文件；单纯升版本号不会让旧实例报 `TEMPLATE_CHANGED`，方法文件变化才需要按 INSTALL 的说明重绑定模板。

### 汇报节奏

创建实例前，协议要求 agent 先给一屏创建简报（北极星、成功标准、范围、授权、模式、失败处置、模块、预算），只问一个确认问题；确认后才写实例。之后每个状态变化（建任务、开工、派工、提交候选、验收/返工/失败/阻塞、方向/拆分、收口）要求给出一条 2–6 行简报，固定写清「结论、依据、下一步、需要你」。非终态汇报同时写入 `progress`；终态和收口以实际生命周期事件为准，不伪造进展。5.2.0 引入方法变更，早期实例需审阅新方法后用带真实人类来源的 `rebind_template` 接受；5.2.0 → 5.2.1 仅改介绍与发布元数据，方法身份不变。5.2.2 补充任务命名与层级披露：对话以可读任务名为主，区分卡内批次、候选版本和返工；此前实例需审阅新方法并显式重绑定，保留旧 ID、历史与验收义务。

DSH 安装与使用细节见 [dsh/INTRO.md](dsh/INTRO.md)（[English](dsh/INTRO.en.md)）。

### 参数面板（Web UI）

设置 → **串行任务**（`settings.section`）里可以改插件自己的运行参数，保存后下一次 `sjs` 调用立即生效，不需要重启 Harness：

| 参数 | 说明 |
|---|---|
| Python 解释器 | 留空为自动探测；优先顺序：`SJS_PYTHON` 环境变量 → 面板设置 → `python` / `python3` / `py`。 |
| 单次调用超时（毫秒） | 1000–600000；`SJS_TIMEOUT_MS` 环境变量优先。 |
| 额外 CLI 参数 | 每行一个，追加到每次 `sjs` 调用的命令行（例如 `--append-library`）。 |

面板还提供「检测环境」（探测解释器版本并验证 CLI 可启动）和只读信息（仓库目录、CLI 路径、skill 名、配置文件位置、版本、共享 `defaults.json` 摘要）。参数存放在 `$DSH_HOME`（默认 `~/.dsh`）下的 `serial-job-session.json`，由插件自己的 JSON 接口读写：`GET /serial-job-session/api/state`、`POST /serial-job-session/api/settings`、`POST /serial-job-session/api/probe`（仅同源、要求 JSON 请求体）。

创建实例的选择（决策模式、门槛、失败处置、估分/飞书开关）仍在 `init` 时按治理确认，面板不提供这些开关——它们属于实例合同，不该被全局默认悄悄改掉。

两点与纯 skill 安装的差别：

1. **不要再把本仓复制/克隆到 `~/.dsh/skills` 或项目 `.dsh/skills`。** 同名时文件系统来源会遮蔽插件的注册（本机实测：先注册的文件系统副本盖住了插件注册，且它带 `disable-model-invocation: true`，结果模型侧看不到该 skill）。只保留一种安装方式。

手动跑 CLI 时，`--input` 的 JSON 请用 **UTF-8 无 BOM** 保存：Windows 记事本与 PowerShell 的 `Set-Content -Encoding utf8` 会加 BOM，CLI 会报 `Unexpected UTF-8 BOM`；更省事的是用 `--input -` 从 stdin 传入（插件里的 `sjs` 工具默认就这样）。

控制台代码页为 cp936 时，管道 stdin 会被 Python 按 GBK 解码，中文 payload 会炸出 `surrogates not allowed`。插件内的 `sjs` 工具已给 Python 子进程强制 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`，所以工具入口不受影响；如果你在 cp936 终端里手动用 `--input -`，请自己设这两个环境变量，或改用 `--input <utf8.json>`。
2. 入口只有一处：`dsh/index.js`（Host 半：skill、`sjs` 工具、参数文件与 JSON 接口）+ `dsh/client.js`（参数面板）+ `dsh/cordis.patch.yml`（插入 row）+ `dsh/locale/{en,zh}.json`、`dsh/icon.svg`（插件页信息）+ `dsh/plugin.test.mjs`（离线校验，`node --test dsh/plugin.test.mjs`）。`scripts/`、`references/`、`schemas/` 仍是唯一事实源，插件只是把它们接进 Harness。

插件注册**允许模型按需加载**（`invocation.modelInvocable: true`）。这偏离 `SKILL.md` 里给文件系统来源写的 `disable-model-invocation: true`，因为 Harness 的 skill 目录就是它按需加载的入口；条目描述已写明“仅在明确点名、`route_key：意图` 或已绑定工作流时使用，不是普通任务的默认框架”。

维护注意：`check_skill_integrity.py` 已把 `dsh/`、`node_modules/` 与根 `package.json` 从 skill 文本基线中排除，插件打包文件不计入母版完整性。

## 最小实例

```text
demo/
  run.json
  state.json
  records/
    tasks-000001.jsonl
    direction-000001.jsonl
```

run 是创建参数；tasks 与 direction 是追加式事实记录；state 是可删除重建的当前投影。证据 artifacts/ 和局部 overrides/ 按需创建，实例文件深度最多两层。子任务 ID 可包含 `/`，不会生成子目录。

默认每20张完成的顶层任务或512 KiB 封存任务分片；方向每20条或512 KiB 封存。阈值可配置，旧卡补丁追加到当前分片。日常上下文只读快照和新增记录，不扫描封存历史或证据。

## 决策与复用

- **人工主导**：人决定开工、方向和验收；agent 准备合同、证据与建议。
- **条件自治**：agent 提议参数，人确认；逐项满足门槛、风险、授权和必验项后推进，否则留下判断数据转人工。
- **全 AI**：授权与预算内由 agent 决策；低分或高风险不自动转人工。失败、未知和未覆盖的必验项仍如实记录。

默认首次实现后最多返工3次；耗尽后保留成果、跳过失败任务并继续独立任务，也可选择停止。评分是判断，不是校准成功概率。

编排器、评估者、worker、reviewer、提交者和经验整理者按需启用。同角色同领域优先续接已有 agent；句柄失效时用最小交接包恢复。独立 reviewer 不验收自己实现的候选。宿主会话复用与模型缓存是不同机制，不承诺固定节省比例。

复杂任务可建议嵌套，按所选决策模式启用。默认深度2层、每父任务8个直接子任务；父任务必须等待必需子任务全部验收。估分计时、飞书 Base 镜像均在创建时询问并按需加载。

## 接口与文档

唯一日常 CLI：init / context / start / apply / handoff / check / rebuild / recover / stats / sync。支持 `--instance` 或通过注册表使用 `--route-key`，不全盘搜索实例。

- [事件与命令示例、恢复](references/runtime.md)
- [汇报协议与模板](references/reporting.md)
- [创建与验收策略](references/governance.md)
- [角色复用](references/agents.md) · [嵌套](references/nesting.md)
- [估分计时](references/estimates.md) · [飞书镜像](references/feishu.md)
- [开发与测试](CONTRIBUTING.md) · [版本变化](CHANGELOG.md)

## 版本边界

v5 是直接替换版本：不保留 v3/v4 执行器或迁移器。已有实例保持原处；需要旧版执行时使用原安装，新工作流创建 v5 实例。更新本仓不会扫描或改写其他项目的数据。

MIT；作者沈劢，版权归北京钛核互动信息技术有限公司。见 [LICENSE](LICENSE)、[署名](CREDITS.md)。
