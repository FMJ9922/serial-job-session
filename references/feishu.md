# 可选飞书多维表格镜像

创建时询问是否需要；不需要时不导入模块、不探活、不访问网络。选择启用后明确 create 或 bind 及目标。使用现有本机 lark-cli；本 skill 不安装 CLI、不处理登录或秘密。可在 feishu.cli 配置字符串数组形式的既有命令前缀，包括项目认可的 PowerShell shim；不执行拼接的 shell 字符串。

绑定已有 Base：

```json
{"modules":{"estimates":false,"feishu":true},"feishu":{"mode":"bind","base_token":"目标Base标识","table_id":"目标表标识"}}
```

也可配置 wiki_url 和可选 table_id；有多个表时必须点名，不猜目标。新建使用 mode=create、title 和可选 parent_node。创建时的选择明确包含相应目标下的建表/字段及任务镜像写入授权；只问缺失信息，不逐条重复确认已授权的动作。

先 `sync --event probe` 检查 CLI。选择新建后运行 `sync --event provision`；绑定可用 `sync --event bind`。日常 `sync` 或 `sync --event flush` 同步任务。任何命令加 `--dry-run` 都不访问远端、不写本地。

镜像用固定 SJS key（实例ID/任务ID）匹配，不依赖标题。字段只包含任务、状态、版本及描述，描述保留父任务、候选证据和可选估分。可通过 field_map 映射现有字段名。缺失字段按文本字段创建；已有其他字段保持不变。任务日志是唯一状态权威，不从远端反向覆盖任务。

准备好数据再写远端，写后回读。支持分页查找与幂等更新。绑定、每项待同步状态和回执写入 tasks 日志，快照提供待补发索引；没有 per-card 配置、outbox 文件或结果文档。

同步失败保留本地验收，记录错误并在后续 sync 补发。远端新建超时可能已成功，不能盲目再建：先检查目标，定位已创建 Base/记录，再显式补充真实 binding 或回执。创建 Base 的未知结果会返回 PROVISION_OUTCOME_UNKNOWN；使用 apply 的 sync_receipt 写入经过实际核实的 binding 后再 flush。禁止伪造成功回执。

CLI 不支持分页、目标不明确或回读不一致时报告错误，不为“同步成功”而新建重复数据。自动测试只使用模拟传输，真实 CLI 版本与权限需要在实际启用的目标环境探活。
