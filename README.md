简体中文 | [English](README.en.md)

# serial-job-session v5

面向 AI agent 的轻量长期任务工作流：**共享方法模板，实例只保存参数和追加式数据。**

每个任务有合同、版本和证据。人工主导、条件自治和全 AI 由人在创建时选择；独立验收、有限返工、角色会话复用和嵌套任务使用同一份记录。

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
- [创建与验收策略](references/governance.md)
- [角色复用](references/agents.md) · [嵌套](references/nesting.md)
- [估分计时](references/estimates.md) · [飞书镜像](references/feishu.md)
- [开发与测试](CONTRIBUTING.md) · [版本变化](CHANGELOG.md)

## 版本边界

v5 是直接替换版本：不保留 v3/v4 执行器或迁移器。已有实例保持原处；需要旧版执行时使用原安装，新工作流创建 v5 实例。更新本仓不会扫描或改写其他项目的数据。

MIT；作者沈劢，版权归北京钛核互动信息技术有限公司。见 [LICENSE](LICENSE)、[署名](CREDITS.md)。
