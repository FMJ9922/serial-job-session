# {{领域名}} · ENV（冷启动契约）

```yaml
environment_status: draft # draft | locked
environment_version: 1
domain_name: {{领域名}}
route_key: {{route_key}}
plan_mode: delivery # discovery | delivery
authorized_roots: [{{用户允许探查的目录}}]
authorized_tools: [{{用户允许查询的工具或服务}}]
pending_confirmations: [{{锁定前待人确认的字段名}}]
discovered_at: null
confirmed_at: null
```

`INSTANCE.json` 由 `instantiate_domain.py` 生成，不要在本模板手填。`protocol/` 只写领域差异。`workflows/` 与 `managed/` 模板默认可存在但日常不加载，直到某张卡 `embedded_workflow` 或用户明确托管。

> Bootstrap 可在 `draft` 阶段根据只读探查填写本文；`locked` 后 Worker / Closer / 菜单会话全部只读。只有用户明确说“更新环境”才能重新进入 `draft`。

## 探查口径

- `verified`：主 Agent 已通过文件、命令或工具现场验证。
- `inferred`：根据目标或结构推断，锁定前需要确认。
- `user_policy`：属于人的治理选择，Agent 只能给保守默认。
- `unresolved`：尚无可靠答案；锁定前必须清空。

## 路径

| 项 | 值 | 状态 | 证据 / 依据 |
|---|---|---|---|
| 实例根目录 | `{{INSTANCE.json.instance_root}}` | verified | `INSTANCE.json`；示例默认 `.cursor/workflows/{slug}/`，禁止 `feature-docs` |
| 工作流目录 | `{{INSTANCE.json.instance_root}}` | verified | `INSTANCE.json` |
| 任务卡目录 | `{{工作流目录}}/jobs/` | verified | 由工作流目录派生 |
| 证据目录 | `{{工作流目录}}/output/{job-id}/` | verified | 实例内独立运行数据，建议 gitignore |
| Close/migrate 备份 | `{git根}/.serial-job-session/private/` | verified | 本机回滚快照，不进 git；无仓库才回退实例 `.sjs-tx/` |

## 版本与软件

| 软件 / 服务 | 版本 / 状态 | 起法或连接方式 | 状态 | 证据 |
|---|---|---|---|---|
| `{{…}}` | `{{…}}` | `{{…}}` | verified / inferred | `{{…}}` |

## 探活清单（开工前必须全过）

1. `{{命令或工具调用}}` → 期望 `{{…}}`（来源：verified）
2. …

任一项失败 → **硬停**，请人打开对应软件/服务。禁止用仓库快照假装现场。

## 单实例资源（并行约束）

| 资源 | 为什么单实例 | 并行时谁可以用 | 状态 |
|---|---|---|---|
| `{{…}}` | `{{例：全工程只有一个会话，两个 Worker 同时求值会互踩}}` | 同一时刻只允许**一个** Worker 改产物；其余并行卡必须是纯 Shell 或纯读 | inferred，锁定前人确认 |

没有单实例资源就写「无」，**不要留空**。留空会被当成「全部单实例」，即完全不许并行。

夜跑是托管执行的领域 preset：工作窗口外不累计 `business_wait`，但本域门禁、晨检与禁止项仍生效。Houdini / Unity 与普通托管共用 CURRENT 上的 lease。

## 控制面写者（并行必填）

- 默认：主会话编排器
- 共享控制文件：`CURRENT.md`、`JOB-SHAPES.md`、`FACTS.md`、`LOG.md`、`NORMS.md`
- 子 Worker 权限：只写本卡、独立证据目录和任务卡授权的业务产物

无法保证唯一写者时禁止并行。

## 面（faces）枚举

一卡不得超过本域 `max_faces`。本域的面：

- `{{例：type-interface}}` — `{{…}}`
- `{{例：client-code}}` — `{{…}}`
- `{{例：measure-only}}` — 纯度量，不改产物
- …

## 领域参数

| 项 | 值 |
|---|---|
| `max_faces` | `2`（user_policy；可按本域证据设 1–3） |
| `estimate` 上限（超出必须拆卡） | `1.5` h（user_policy 默认） |
| 未审自动沉淀条数上限 | `20`（user_policy 默认） |
| 人眼验收面 | `{{Agent 建议；user_policy，锁定前确认}}` |

## 本域禁止项

- `{{…}}`

## 转走其它流程的口令

| 用户这么说 | 转到 |
|---|---|
| `{{…}}` | `{{…}}` |

没有明确转走规则时写“无”，不要虚构其它流程。

## 待确认（locked 前必须清空）

Bootstrap 把所有 `inferred / user_policy / unresolved` 中需要人拍板的项目集中写在这里，并附推荐默认。用户可回复“默认即可”一次确认。

| 项 | Agent 建议 | 为什么需要确认 | 用户决定 |
|---|---|---|---|
| {{…}} | {{…}} | {{…}} | 待确认 |

`environment_status: locked` 时本节必须明确写：

```text
无待确认项。
```

## 初始化记录

- 探查范围：
- 未探查范围：
- 执行过的只读探活：
- 使用的保守默认：
- 用户确认摘要：
