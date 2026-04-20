# Tracing 模块

`tracing.py` 提供 Langfuse 追踪以及无副作用的 `NoOpTracer`。

## 当前追踪点

主链路会记录：

- `task-agent.window.submit`
- `task-agent.fast-brain.run`
- `task-agent.deep-brain.run`
- `task-agent.handoff.build`

## 追踪器选择

### NoOpTracer

在以下场景会自动使用：

- 未配置 Langfuse key
- 测试环境
- 本地只想跑逻辑，不想上报 tracing

### LangfuseTracer

在配置了 key 并且依赖可用时启用。

## 当前测试策略

为了避免测试环境频繁连到真实 Langfuse：

- 如果检测到 `PYTEST_CURRENT_TEST`
- 会自动退回 `NoOpTracer`

这样可以保证单测稳定，不被 tracing 网络波动拖慢。
