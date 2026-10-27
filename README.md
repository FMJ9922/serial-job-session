简体中文 | [English](README.en.md)

# serial-job-session

**人类把关的串行任务协议：一次一张卡，一张卡一个合同，先有证据再验收。**

A human-gated serial task protocol for AI coding agents. One card at a time, one acceptance contract per card, evidence before acceptance.

---

## 它解决什么问题

把一个大任务直接丢给 AI，常见死法是人不在回路：它连做多步、跳过依赖、自己宣布完成、拿假数据过门禁。`serial-job-session` 的做法相反：

- **人当编排器**：拆卡、点名下一张、验收或打回。
- **AI 只做被点名的那一张**：不扩边界、不自动开工下一张。
- **每张卡有合同**：目标、依赖、影响面、验收方式、耗时、证据。
- **控制面单写者**：主会话写 `CURRENT` / 菜单 / `LOG` / `FACTS`；Worker 只写自己的卡和证据。
- **状态机固定**：`ready → doing → awaiting_acceptance → accepted`，异常侧只有 `cancelled`。
- **门禁优先于人眼**：机械门禁判红时，人也不能口头覆盖。

它与「一次扇出很多 Agent 的 workflow」不是同一件事；后者适合边界清楚的一次性大任务，前者适合长期、需要人持续拍板的工程。

---

## 快速开始

```bash
# 1. 安装（DSH 示例；其它宿主见 INSTALL.md）
git clone https://github.com/FMJ9922/serial-job-session.git \
  <your-repo>/.agents/skills/serial-job-session

# 2. 跑测试
cd <your-repo>/.agents/skills/serial-job-session
python -m unittest discover -s scripts -p "test*.py"

# 3. 创建一个实例（按提示确认路径与 slug）
python scripts/instantiate_domain.py --target <repo>/.cursor/workflows/<slug> \
  --domain-name "<领域名>" --route-key "<口令>"
```

之后对 Agent 说标准口令：

```text
<route_key>：推进
<route_key>：按菜单做 12
<route_key>：验收通过
<route_key>：打回：<原因>
```

裸「推进一下」不会触发，除非当前会话已绑定唯一实例。

---

## 协议一览

| 角色 | 职责 |
|---|---|
| 人 | 定方向、拆模块、点名下一张、验收 / 打回 |
| 菜单会话 | 只读，只展示当前 `ready` 卡，不代选 |
| Worker | 只做被点名的一张卡，写卡与证据 |
| Closer | 收口：计时、状态、`LOG` / `FACTS` / 菜单 |
| Reviewer | 只给 `pass / blocking`，不结案 |

卡的最小合同字段：`id`、`shape`、`estimate_group`、`depends_on`、`faces`、`estimate`、`estimate_source`、`actor`、`human_accept`、`status`、`attempt`；正文固定七章（目标范围验收 / 依赖输入影响 / 进展问题决策 / 执行耗时 / 产物验证反馈 / 结案补丁 / 事实经验变化）。

硬规则摘要：一卡一事；依赖未结档不得连做；缺计时不得 `accepted`；证据路径不可变；共享控制文件只有控制面写者能改；`output/` 不得被迁移重写；探活失败硬停。

---

## 目录

```text
SKILL.md                 入口（按需加载下面这些）
V4.md                    v4 状态、计时、补丁、方向审阅、迁移
ESTIMATE.md              估分与复盘口径
PARALLEL.md              并行判据与禁止清单
DELEGATION.md            托管 / 夜跑授权合同
GATE-PATTERNS.md         机械门禁写法
IMMUTABILITY.md          母版不可变规则
EVOLUTION.md             经验沉淀
INIT.md / FIRST-RUN.md   创建实例 / 人类首次使用
INSTALL.md               各宿主安装
FEISHU.md                可选飞书表（本机 CLI 已通时）
scripts/                 30 个 Python 脚本（无第三方依赖）
schemas/                 JSON Schema
domain-pack-template/    领域包模板
Test/serial-job-session/ 回放夹具与 goldens（维护者用）
skill-integrity.json     母版完整性基线
```

初始化一个领域包只需要：`ENV.md`、`CURRENT.md`、`JOB-SHAPES.md`、`jobs/`、`NORMS.md`、`FACTS.md`、`LOG.md`，外加一个证据目录。

---

## 估算与偏差

估分记录 `estimate`、`ai_net`、`elapsed`、`business_wait`、`variance`；缺失可信计时时写 `untracked`，不按聊天时间倒推。估分库在 `<git-root>/.serial-job-session/estimate-library.jsonl`，可跨实例复用。

---

## 可选集成

- **飞书 / Lark**：本机 `lark-cli` 已通时，可把任务表做成轻量镜像；不通就跳过，不影响主流程。见 [FEISHU.md](FEISHU.md)。
- **Git**：提交由唯一获授权的写者串行执行；不整文件 `git add` 混合工作区。
- **编辑器 / DCC**：同一单实例资源（Unity、Houdini 等）同一时刻只允许一个写 Worker，其余并行卡必须是纯读或纯 Shell。

---

## 开发

- Python 3.11+，只用标准库。
- 单元测试：`python -m unittest discover -s scripts -p "test*.py"`
- 回放夹具：`python -m unittest discover -s Test/serial-job-session -p "test*.py"`
- 发布门槛：`python scripts/quality_gate.py --domain <instance-root>`
- 修改母版文本后重算完整性：`python scripts/check_skill_integrity.py --update --maintenance-ack MODIFY_SKILL`

更多实现细节见 [V4.md](V4.md)、[ESTIMATE.md](ESTIMATE.md)、[PARALLEL.md](PARALLEL.md)、[DELEGATION.md](DELEGATION.md)。

---

## 授权与署名

- License: **MIT** — 见 [LICENSE](LICENSE)
- 版权：© 2026 北京钛核互动信息技术有限公司
- 作者：**沈劢** — 联系邮箱 `shenmai@taiyouxi.cn`
- 开发方式：作者主导，AI 辅助

---

## 已知边界

- 本版只覆盖 v4 协议；v3 旧卡可以继续读，但迁移是通用能力，不包含任何具体业务域的预处理。
- `disable-model-invocation: true` 意味着模型不会自动加载；需要宿主命令或规则层把口令转给 skill。
- 飞书同步依赖本机 CLI；没有 CLI 时相关命令会跳过或拒绝，但主流程不受影响。
