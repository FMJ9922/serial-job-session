# PROTOCOL · {{领域名}}（领域差异）

> **本文不得覆盖 generic 硬约束。** 一卡一事、时间账本、Closer 事务、并行/lease、NORMS 规则以 skill `SKILL.md` 为准。
> 执行入口：`{{route_key}}：意图`。

工作记忆根：本实例目录。`workflows/`、`managed/` 模板默认可存在，日常不加载。

## 加载边界（防超时）

- 查「任务 N / JOB N / 编号 N」：只读 `JOB-SHAPES.md`「待做」。已有任务卡时 Grep 仅限 `jobs/` 且 `glob: *.md`。
- **禁止**对实例根、`output/` 做 Grep 或 Glob `**/*`。`@` 本目录不等于扫证据。
- 证据只按本卡写明的路径 Read。课件 zip 只在 zip 所在目录解压并只读；禁止把解压树或课件文件拷进实例 `output/`。

## 本域差异

- 只写本领域工具、路径、禁止项和转走口令。
- 普通打回（合同仍成立）→ `rework_required`，不是一律 `rejected`。
- 若存在 `feishu.json` 且 `feishu_sync.py --event probe` 通过：读 skill `FEISHU.md`，只跑薄脚本维护本表。CLI 未通则跳过。禁止现编飞书 CLI。
