# Changelog

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
