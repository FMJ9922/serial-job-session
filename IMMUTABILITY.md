# 母版不可变与实例隔离

本 skill 按“预制体母版 / 运行实例”工作：

```text
serial-job-session/          只读母版
└── domain-pack-template/    只读实例模板

<skill 目录之外>/.cursor/workflows/<slug>/   可写运行实例
├── INSTANCE.json            实例身份与来源
├── ENV.md
├── CURRENT.md
├── JOB-SHAPES.md
├── FACTS.md
├── LOG.md
├── NORMS.md
└── jobs/
```

## 运行时绝对禁止

- 修改、删除或向 skill 根目录新增任何运行数据。
- 直接编辑 `domain-pack-template/` 来代表某个具体领域。
- 把任务卡、证据、统计结果、缓存、截图、日志或 Close 回滚快照写入 skill 目录。
- 把 Close / migrate 备份写进领域包（有 git 时应落仓库 `.serial-job-session/private/`）。
- 把领域实例写进 `feature-docs` / `system-docs` / `drafts` / `human-readme`（那些目录只放沉淀文档）。
- 因完整性校验失败而自动执行 `--update`。
- 让 Worker、Closer、Bootstrap 子 Agent 或夜跑 Worker维护 skill。
- 通过相对路径、`..`、符号链接或 junction 绕过实例边界。

任何写操作前先解析规范化绝对路径。目标位于 `SKILL_ROOT` 内就停止。

## 实例化

始终使用：

```bash
python scripts/instantiate_domain.py --target <skill 外的实例目录> --domain-name <领域名> --route-key <关键字>
```

工具会：

1. 拒绝位于 skill 根目录内的目标。
2. 拒绝 `feature-docs` / `system-docs` / `drafts` / `human-readme` 文档树。
3. 拒绝覆盖已有非空目录。
4. 复制 `domain-pack-template/`，不修改母版。
5. 写入独立 `INSTANCE.json` v2，登记 `route_key`；注册表写入与创建同成同败。

实例创建后，Bootstrap 只修改实例中的文件。不同工作使用不同目标目录，不共享 `CURRENT`、任务卡或证据目录。

## 运行时完整性检查

已有实例时，每次调用第一次写操作前只需运行：

```bash
python scripts/runtime_guard.py --instance <实例目录>
```

它同时检查母版完整性、实例身份、路径隔离、ENV 状态和任务卡一致性。Bootstrap 草案可加 `--allow-draft`，但草案仍不得施工。

尚未创建实例时先运行：

```bash
python scripts/check_skill_integrity.py
```

它按 `skill-integrity.json` 核对母版文本文件的 SHA-256，并检查新增、缺失和被修改文件。

判红时：

1. 停止领域任务。
2. 报告具体差异。
3. 等人决定恢复母版还是进入显式维护。

禁止把“更新完整性基线”当修复；那只会承认污染。

## 显式维护 Skill

只有用户当前请求明确要求“修改 serial-job-session skill 本身”，或已点名授权改母版的发布卡（如协议进化编号 9）时，才进入 Skill Maintainer：

1. 修改前说明这是母版维护，不是领域运行。
2. 只改 skill 目录，不碰任何领域实例。
3. 运行全部测试和文本一致性检查。
4. 人类请求范围内的修改完成后，执行：

```bash
python scripts/check_skill_integrity.py --update --maintenance-ack MODIFY_SKILL
python scripts/check_skill_integrity.py
```

5. 报告修改文件和验证结果。

`--maintenance-ack` 只是防误操作门槛，不是安全授权替代品；是否允许维护仍由用户当前请求决定。

## 低能力 Agent 防污染原则

遇到含糊情况一律选保守路径：

- 不确定某路径是不是母版 → 当作母版，只读。
- 找不到 `INSTANCE.json` → 当作未实例化，禁止施工。
- `INSTANCE.json` 来源、版本或实例根不匹配 → 硬停。
- 想“顺手优化协议” → 不做，写升格提案。
- 不知道运行产物放哪 → 停止并读取实例 `ENV.md`，不得回退到 skill 目录。
