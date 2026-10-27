# 安装与版本固定

将仓库放进宿主支持的 skill 目录，或由项目规则显式引用本仓的 SKILL.md。Python 3.11+，不需要 pip 依赖。不要把实例建在 skill 目录内部，也不要复制整个框架到每个实例。

本 skill 保留 `disable-model-invocation: true`，需要宿主显式调用 `$serial-job-session` 或将 `route_key：意图` 转发到它。不为所有普通任务自动加载工作流。不同宿主应使用其支持的技能入口，不新增未经请求的全局规则。

共享安装只需一份，可供多个项目使用。实例保存模板版本和完整性标识；共享方法升级后，旧实例报告 TEMPLATE_CHANGED。review 新方法后，用带真实人类来源的 rebind_template 操作接受当前身份；不自动下载或复制新旧模板，不静默切换方法。

`run.json` 保存创建时显式参数；不要手改运行中实例的创建配置。策略变化使用 set_policy 事件。模板重绑定也只追加事件，历史配置和判断保留。共享模板的身份可用 Python API `sjs_config.template_identity()` 获取，或从新安装的 init --dry-run 返回值获取。

本版本仅支持新建 v5 实例，不保留旧版执行器或导入器。需要继续使用旧实例时保留原版本安装；v5 不扫描、不转换、不删除它们。

安装后运行单元测试、回放和完整性检查，命令见 README。领域运行不得执行完整性基线更新；仅在用户明确要求维护 skill、检查完成后更新基线。

## DSH（DeepSeek Harness）插件安装

本仓根目录的 `package.json` 声明了 `dsh.bundle.patch`，因此整个仓库就是一个 DSH bundle。安装后：

- `SKILL.md` 被注册为 Harness skill `serial-job-session`，资源根是本仓目录（`references/`、`schemas/` 仍在原位读取），且允许模型按需加载。
- 新增 `sjs` 工具，在调用会话的工作目录里执行 `python scripts/sjs.py`，参数与 CLI 同名，JSON 负载从 stdin 传入。
- 设置页新增**「串行任务」**面板（`settings.section`，`dsh/client.js`）：解释器、超时、额外 CLI 参数，保存后下一次调用生效；另有环境检测与只读信息。参数存 `$DSH_HOME`（默认 `~/.dsh`）下的 `serial-job-session.json`，由 `GET /serial-job-session/api/state`、`POST /serial-job-session/api/settings`、`POST /serial-job-session/api/probe` 读写（仅同源、要求 JSON 请求体）。
- 插件页显示标题/说明/图标，来自 `dsh/locale/{en,zh}.json` 与 `dsh/icon.svg`。

安装（二选一，勿混用）：

```bash
# 本地目录：跟随本仓改动，适合开发
dsh plugin add <本仓绝对路径>

# 从 GitHub
dsh plugin add github:FMJ9922/serial-job-session
```

也可以在 DSH 会话里让 agent 调 `plugin_manager` 的 `install_bundle`，`target` 填本仓绝对路径；`application: applied` 才表示已生效，`restart-required` 需要重启 Harness。

环境变量（可选，需在启动 DSH 前设置；优先于面板设置）：

| 变量 | 默认 | 含义 |
|---|---|---|
| `SJS_PYTHON` | 依次尝试 `python`、`python3`、`py` | Python 3.11+ 解释器路径 |
| `SJS_TIMEOUT_MS` | `120000` | 单次 `sjs` 调用超时 |

注意：**不要把本仓同时复制/克隆到 `~/.dsh/skills` 或项目 `.dsh/skills`**。同名时文件系统来源会遮蔽插件注册（实测会出现模型侧看不到该 skill 的现象）；只保留一种安装方式。

插件的离线校验（不装 Harness 也能跑）：

```bash
node --check dsh/index.js
node --check dsh/client.js
node --test dsh/plugin.test.mjs
```

