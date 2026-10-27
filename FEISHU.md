# 可选飞书表（轻量维护）

开局不读。仅当 `feishu_sync.py --event probe` 已通，或实例已有 `feishu.json` 时读本文。

本 skill **不提供飞书 CLI**。不教安装、不写子命令、不转其它 SOP。本机 CLI 未通 = 此可选项不存在。

## 何时问

Bootstrap：先 `python scripts/feishu_sync.py --event probe`。

- 不通 → 不追问，不建表。只说「本机飞书 CLI 未通，跳过飞书表」。
- 已通 → 结构化提问（默认不需要）：不需要 / 创建新表 / 绑定已有表。
- 人未同意 → 不建 Wiki 节点、不写 `feishu.json`。

## 只跑这一条

禁止现编 `lark-cli` / `base +*` / `wiki +*`。

```text
python scripts/feishu_sync.py --event probe
python scripts/feishu_sync.py --route-key <key> --event provision --title "{领域名} 任务"
python scripts/feishu_sync.py --route-key <key> --event bind --wiki-url <URL>
python scripts/feishu_sync.py --route-key <key> --event seed-menu
python scripts/feishu_sync.py --route-key <key> --event upsert-job --job-id <id>
```

无 `feishu.json` 时 seed/upsert 直接跳过（退出 0）。同步失败只报告，不回滚结案。

菜单锁定后 seed-menu。Worker 写任务卡或改状态、Closer 结案后 upsert。

## 短名

待做表「短名」给飞书「任务」和聊天菜单用。「目标」仍是二值合同。

- 要：8–20 字，一句话概括阶段意图。例：`主 TOP 接入地形布局`
- 不要：把节点/HDA/开关用 `+` 串起来。例：`导出布局 + 主 TOP IP_LevelCreation 骨架`
- 细则写编号细则，同步进「详细描述」
