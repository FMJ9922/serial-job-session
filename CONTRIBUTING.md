# Contributing

欢迎 issue 和 PR。提交前请：

1. 跑通本地测试：
   ```bash
   python -m unittest discover -s scripts -p "test*.py"
   python -m unittest discover -s Test/serial-job-session -p "test*.py"
   ```
2. 如果改了 `SKILL.md` / 文档 / `scripts/*.py` 等被完整性清单覆盖的文件，重算基线：
   ```bash
   python scripts/check_skill_integrity.py --update --maintenance-ack MODIFY_SKILL
   ```
3. 协议行为变更请同时更新 `V4.md` / 相关文档和测试；不要只改文档不改实现。发布门槛用 `python scripts/quality_gate.py --domain <instance-root>`。
4. 不要提交任何真实业务实例、注册表、证据目录或个人路径。
5. 一个 PR 只做一件事；大改先开 issue 讨论。

提交信息用一句话说明改了什么以及为什么。
