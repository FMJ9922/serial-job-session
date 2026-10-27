# Changelog

## Unreleased

- 新增 DSH bundle：根 `package.json` 声明 `dsh.bundle.patch`，`dsh/index.js` 把 `SKILL.md` 注册为 Harness skill `serial-job-session` 并新增 `sjs` 工具（在会话工作目录调用 `scripts/sjs.py`，JSON 走 stdin）。
- 插件显示信息：`dsh/locale/{en,zh}.json` 与 `dsh/icon.svg`；离线校验 `dsh/plugin.test.mjs`（`node --test`）。
- `check_skill_integrity.py` 排除 `dsh/`、`node_modules/` 与根 `package.json`，插件打包文件不计入 skill 文本基线；基线随本次维护更新。
- 文档补充 DSH 安装方式与三项差异（勿与 `~/.dsh/skills` 混装、默认允许模型按需加载、`SJS_*` 环境变量）；CI 增加 Node 语法与插件测试。

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
