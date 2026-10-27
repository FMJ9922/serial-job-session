# Changelog

## 4.0.0 — 2026-10-27

首个开源版本（MIT）。

- 协议：v4 卡状态机、七章卡合同、CURRENT/菜单/LOG/FACTS 投影、事务与回滚。
- 执行：串行 / 并行 / 托管 / 夜跑、lease 与单实例资源约束、方向纠正卡。
- 估分：`estimate` / `ai_net` / `elapsed` / `business_wait` / `variance`，跨实例估分库。
- 工具：注册表、上下文菜单、卡校验、机械门禁、迁移（通用）、回放夹具。
- 删除内部业务域专用的迁移默认值与示例，改为实例配置（`direction-review.json`、`norms-v4.md`、`dynamic-constraints.md`）。
- 测试夹具内置到 `Test/serial-job-session/`，`quality_gate.py` 支持仓内路径。
