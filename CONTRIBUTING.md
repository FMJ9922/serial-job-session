# Contributing

修改协议时同时修改实现、按需参考文档和对应行为测试。Python 3.11+，仅标准库。

```bash
python -m unittest discover -s scripts -p "test*.py"
python -m unittest discover -s Test/serial-job-session -p "test*.py"
python scripts/check_skill_integrity.py --update --maintenance-ack MODIFY_SKILL
python scripts/check_skill_integrity.py
```

完整性更新仅限明确维护本 skill 的工作，不能作为运行时绕过检查的方法。验证已安装技能还可使用宿主的技能 frontmatter 验证器。

测试关注可观察行为：提交前验证、幂等和版本冲突、写入中断、分片重放、三种决策策略、真实必验项、独立 reviewer、嵌套资源边界、可选模块隔离和上下文读取成本。远程服务使用模拟传输；不在 CI 创建真实飞书资源。

不要提交业务实例、私人路径、宿主历史或凭据。新方法先判断能否用实例参数表达；仅方法确有差异时使用局部 override。保持文档单一来源，避免为每个任务或角色复制说明。

保留真实失败证据，不用调整样本让门禁变绿。回放与完整性验证必须能发现故意加入的反例。许可证和作者归属保持不变。

## DSH 插件维护

本仓同时是 npm 包 `dsh-serial-job-session`。插件侧改动（Host/Client 半、locale、面板文案、冒烟脚本）落在 `dsh/`，不要因此修改完整性基线内的 skill 文件。发版前的必跑项：

```bash
node --check dsh/index.js && node --check dsh/client.js
node --test dsh/plugin.test.mjs
node dsh/check-pack.mjs
node dsh/smoke-stdin.mjs
python scripts/check_skill_integrity.py
```

插件版本看根 `package.json` 的 `version`，skill 协议版本看 `VERSION`；日常插件补丁不动 `VERSION`。npm 发布后需等待约 30 秒再确认 registry（传播延迟，不是需要人工批准）；GitHub Release 的 tag 要先推送。本机的凭据路径与逐条操作细节不写入仓库。
