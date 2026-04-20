# Session 模块

`session.py` 是当前系统最核心的前台编排层。

它负责把“事件窗口 -> 双脑处理 -> chat 输出 -> 任务下发”这条链路串起来。

## 当前职责

`TaskAgentSession` 主要负责：

1. 接收新的 `EventWindow`
2. 把窗口写入黑板并做预处理
3. 并行启动 `acknowledge` 与 fast-brain `think`
4. 在复杂场景下拉起 deep-brain 流式思考
5. 把真正对外可见的内容送到 `chat`
6. 保留 `_emit` 作为内部观测面
7. 处理中断、generation 过期和 stale deep chunk 丢弃

## 重要边界

### 对外边界：`next_chat_message()`

调用方如果想拿到“用户能看到的消息”，应该消费：

```python
message = await session.next_chat_message(timeout=1.0)
```

这是当前唯一的产品侧输出面。

### 内部边界：`next_event()`

`next_event()` 返回的是内部 `AgentEvent`，适合：

- 测试断言
- 调试
- tracing / telemetry
- 运行态观测

不要把它当成用户侧聊天输出。

## 当前主流程

```text
submit_window()
    ↓
record_window + ingest_window
    ↓
cancel_active_work()
    ↓
_run_fast_brain()
    ├─ _run_acknowledge()
    └─ fast_brain.think()
            ↓
       _apply_fast_result()
            ├─ 更新意图
            ├─ 快速任务下发
            ├─ 发送 progress/final chat
            └─ 如有需要拉起 _run_deep_brain()
                        ↓
                  持续消费 deep chunk
                        ├─ stage_task 立即下发
                        ├─ final_summary 写入黑板
                        └─ 按策略选择性发 chat
```

## 关键实现点

### `submit_window(events)`

入口做的事情：

- 把原始事件列表封装成 `EventWindow`
- 记录到黑板
- ingest 系统信息、任务反馈、动作轨迹
- 中断上一轮前台工作
- 递增 generation
- 启动新的 fast-brain 主流程

### `_run_fast_brain(generation, window)`

这一步是当前改动最大的地方。

它会并行启动：

- `_run_acknowledge(...)`
- `fast_brain.think(...)`

这样 `ack` 不会阻塞快速思考。

### `_apply_fast_result(...)`

这里会消化 fast brain 的结构化结果：

- `intent_summary` + `relation`：写入黑板意图
- `task`：立即发布给下游
- `response_text`：作为对外发言
- `delegate_to_deep`：拉起 deep brain

当前策略是：

- 如果已经有 `response_text`，就不再额外重复播报 `delegation_message`
- 这样能减少重复话术

### `_run_deep_brain(...)`

deep brain 不再一次性返回最终对象，而是流式输出 `DeepBrainChunk`。

### `_handle_deep_chunk(...)`

当前行为：

- `stage_task`
  - 立刻补齐 `intent_id/root_intent_id`
  - 状态从 `draft` 提升为 `active`
  - 立即发布 handoff 给 action-agent
- `final_summary`
  - 写入黑板摘要
- `milestone`
  - 默认只保留内部观测，不直接对外播报
- `warning`
  - 可转成对外 blocker 消息

## 中断与过期

### 新窗口到来时

`_cancel_active_work()` 会：

- 记录 interruption
- 取消当前 fast 任务
- 取消仍在运行的 deep 任务

### generation 机制

每次 `submit_window()` 都会提升 generation。

后续任何旧 generation 的结果都不能再写回当前状态。
