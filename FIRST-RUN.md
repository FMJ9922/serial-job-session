# 第一次使用

你不需要填写配置表，也不需要准备长 prompt。

本文是**人类开口**和接入完成提示。Agent：把母版拷进另一个仓库 → [INSTALL.md](INSTALL.md)（用结构化提问确认路径）。从模板创建实例 → [INIT.md](INIT.md)。不要把「安装 skill」和「初始化领域包」混成一步。

## 1. 告诉 Agent 要长期推进什么

在主 Agent 会话里发送：

```text
使用 serial-job-session 初始化。约定 route_key：{关键字}。
我想长期推进：{一句话}。
路线：discovery（未定）或 delivery（已定）。
控制：默认 manual。不要托管，除非我另说。
允许你探查：{范围}。
```

例如：

```text
使用 serial-job-session 初始化。约定 route_key：UI预制体。
我想长期推进一批 UI prefab 的制作与验收。
路线：delivery。控制：manual。
允许你探查当前项目的 UI 目录和正在运行的 Unity。
```

## 2. Agent 自动准备环境

Agent 会在 skill 目录之外创建独立实例（默认 `<repo>/.cursor/workflows/{slug}/`），登记 `route_key`，再在你允许的范围内查看路径、软件版本和探活方式，生成 `ENV.md` 草案、首批菜单和 `PLAN.md` 阶段估分。它不会把工作包写进 `feature-docs`，不会修改 skill 母版、默认扫描整个仓库、启动未知服务、创建任务卡或开始施工。

无法从现场确定的策略会合并成一次确认。大多数情况下回复：

```text
默认即可
```

可选飞书任务表：Agent 会先探活本机飞书 CLI。不通则不问、不建表。已通才会问你要不要一张同步表；默认不需要。本协议不负责安装飞书 CLI。

## 3. 接入完成后开始使用

环境确认并校验通过后，Agent 应显示：

```text
已接入：{领域名}（route_key：{key}，plan_mode：{discovery|delivery}，control_mode：manual）

第一次开始：
1. 说「{key}：推进」——只查看待做菜单。
2. 说「{key}：按菜单做：{编号}」——只执行这一张任务卡。
3. 需要你验收时，回复「{key}：验收通过」或「{key}：打回：原因」。
4. 打回后继续时，说「{key}：继续返工」。
5. 看总量或重估，说「{key}：出估分单」——只改估分，不开工。
6. 托管 / 夜跑必须另说「{key}：托管执行 …」，高风险要加粗确认。

现在可以说：{key}：推进
```

Agent 不会替你选择菜单或自动开工。裸「推进一下」在多实例仓库里无效。

## 以后怎么回来

新开会话用标准口令 `{route_key}：推进`。现场状态在领域包里，不依赖旧聊天。

环境变化时说：

```text
使用 serial-job-session 更新环境
```

Agent 先展示变化，确认后才重新锁定 `ENV.md`。
