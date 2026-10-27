# 角色与最小上下文

角色职责在共享 `roles.json`，不复制到实例。按需启用 orchestrator / assessor / worker / reviewer / committer / recorder。编排器唯一写入控制日志；其他角色通过返回结果交给编排器合并。

派工前运行 `context --task-id ID --role worker --domain ui --host-session 当前宿主会话ID`。返回职责、授权、合同、当前进展、依赖产物、相关经验和 reuse/spawn 建议。宿主必须确认 handle 仍有效，再使用已有协作工具续接，不重复创建并灌入所有历史。

`assign_role` 保存 slot、role、domain、handle、host_session。slot 是逻辑岗位，handle 是实际宿主 agent 标识。生命周期按宿主事实记录，不把逻辑岗位当成永不失效的进程。

无匹配、跨领域、职责版本变化、句柄失效或重复误判时，新建角色会话并重新加载最小交接包。使用 `invalidate_role` 记录原因。压缩后也应重新取 context；跨宿主重启只保证落盘记录可恢复，不保证会话句柄可恢复。

独立 reviewer 不承担该候选的实现；即使改名为 reviewer，也不能复用贡献者身份。重大方向争议与连续返工时换新 reviewer，依据原始合同和真实产物判断。宿主没有独立 agent 能力时明确说明；无法满足独立验收条件就按策略升级或保留阻塞，不能虚构独立审查。

`handoff --task-id ID --role ROLE` 返回同一任务的恢复包，不建新卡。完整实现细节和证据正文仅在当前任务需要时读取。Agent 的回报优先包含结论、候选版本、证据位置、实际贡献者、限制和下一步。

默认串行写。显式增加 max_parallel 后，只有依赖已验收、写路径无交集、资源无冲突的叶任务可以并行。将 Unity、Houdini、Git 暂存区等共享资源放进 resources；同一资源同时只允许一个任务持有，直到验收、失败或取消。不要把触发编辑器求值的操作误认为纯读。
