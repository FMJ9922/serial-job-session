# 安装与接入

本仓库的根目录就是 skill 根目录（`SKILL.md` 与 `scripts/` 同级）。安装方式取决于宿主，但**同一个位置只保留一份**，禁止双入口。

---

## 0. 环境要求

- Python 3.11+（在 3.12 上验证过），只用标准库，无第三方依赖。
- 可选：本机已通的飞书 / Lark CLI（`lark-cli`）。没有也不影响使用，飞书同步会跳过。
- Windows / macOS / Linux 均可；脚本按 `pathlib` 处理路径。

---

## 1. DSH（DeepSeek Harness）

DSH 的 skill 发现只扫描一级目录：`<skills-root>/<name>/SKILL.md`。把本仓库 clone 成名为 `serial-job-session` 的目录即可。

```bash
# 项目级（随仓库走，推荐放在业务仓的 .agents/skills/ 下）
git clone https://github.com/FMJ9922/serial-job-session.git \
  <your-repo>/.agents/skills/serial-job-session

# 用户级（本机所有项目可用）
git clone https://github.com/FMJ9922/serial-job-session.git \
  ~/.agents/skills/serial-job-session
```

注意：

- clone 的目标目录名必须是 `serial-job-session`，否则目录名与 skill 名不一致。
- `SKILL.md` 带 `disable-model-invocation: true`：模型不会自动加载它，需要由人显式调用（或由宿主的命令 / 规则层把 `{route_key}：意图` 转给它）。这是刻意设计，避免裸「推进一下」误触发。
- DSH 的项目级 skills 根是 `<repo>/.agents/skills` 或 `<repo>/.dsh/skills`，用户级是 `~/.agents/skills` / `~/.dsh/skills`。

---

## 2. Cursor / 其它 `.cursor` 形态

推荐把本仓库挂到加载入口旁边，只放一份：

```text
<repo>/.cursor/references/sop-assets/serial-job-session/   # 母版（本仓库内容）
<repo>/.cursor/rules/serial-job-session.mdc               # 一条入口规则，指向 SKILL.md
```

入口规则只需要写明触发条件：出现 `{route_key}：推进 / 验收通过 / 打回 / 托管执行`，或用户明确点名 `serial-job-session` 时，Read `SKILL.md`，再按它加载 `scripts/`。不要复制第二份到 `.cursor/skills/`。

---

## 3. 其它 Agent / CLI

把 `SKILL.md` 作为系统提示或按需加载的指令文件，提供 Bash / Python 执行能力即可。宿主如果有「结构化提问」工具（如 `ask_user_question`），接上它；没有就用普通对话确认。

最小可跑命令：

```bash
python scripts/runtime_guard.py --instance <instance-root>
python scripts/validate_domain_pack.py --domain <instance-root>
python scripts/sjs.py context --instance <instance-root>
```

---

## 4. 运行时目录

| 目录 | 作用 | 是否进 git |
|---|---|---|
| `<git-root>/.serial-job-session/instances.json` | 注册表 | 可进 |
| `<git-root>/.serial-job-session/estimate-library.jsonl` | 跨实例估分库 | 建议进 |
| `<git-root>/.serial-job-session/private/` | 事务快照、迁移备份 | **必须 gitignore** |
| `<repo>/.cursor/workflows/{slug}/` | 领域实例（CURRENT/jobs/ENV…） | 按团队约定 |
| `<instance>/output/{job-id}/` | 单卡证据 | 建议 gitignore |

实例目录必须在 `SKILL_ROOT` 之外，且不得落在 `feature-docs` / `system-docs` / `drafts` 这类文档树里。`instantiate_domain.py` 会拒绝文档树目标。

---

## 5. 测试与完整性

```bash
# 单元测试（纯本地）
python -m unittest discover -s scripts -p "test*.py"

# 回放夹具
python -m unittest discover -s Test/serial-job-session -p "test*.py"

# 发布门槛（未放 Test 目录时会因缺少夹具而失败）
python scripts/quality_gate.py --domain <instance-root>

# 母版完整性
python scripts/check_skill_integrity.py
```

本仓库把测试夹具放在仓内 `Test/serial-job-session/`；`quality_gate.py` 优先读这里，读不到再退回旧布局的兄弟目录 `../Test/serial-job-session/`。

维护者修改了任何被完整性清单覆盖的文本后，必须重算基线：

```bash
python scripts/check_skill_integrity.py --update --maintenance-ack MODIFY_SKILL
```

---

## 6. 安装完成后

从 [FIRST-RUN.md](FIRST-RUN.md) 读人类第一次怎么开口，从 [INIT.md](INIT.md) 读怎么创建一个领域实例。不要拷贝他人的活实例、注册表或任务卡；每个仓库自己 Bootstrap。
