# Task-Agent 文档

`task-agent` 当前是一个“窗口驱动 + 双脑协同 + chat 输出”的任务编排系统。

它的目标不是一次性给出最终答案，而是：

- 对新窗口快速反应
- 在复杂问题上持续思考
- 尽早产出可执行的阶段任务
- 让执行和规划并行发生

## 当前架构

```text
上游事件窗口
    ↓
TaskAgentSession.submit_window()
    ↓
Fast Brain
    ├─ 并行 acknowledge()
    ├─ think()
    ├─ 更新意图 / 生成快速任务
    └─ 判断是否委派给 Deep Brain
    ↓
Deep Brain.stream_think()
    ├─ reasoning
    ├─ milestone
    ├─ stage_task
    ├─ warning
    └─ final_summary
    ↓
TaskBlackboard + Action-Agent Handoff + ChatAdapter
```

## 设计原则

### 1. `chat` 是唯一的用户可见输出面

用户侧应该消费 `ChatMessage`，而不是内部 `AgentEvent`。

内部 `_emit()` 只服务于：

- 调试
- 测试
- tracing / telemetry

### 2. `acknowledge` 与 `think` 并行

前台不再先确认、再思考，而是同时启动两条路径：

- 尽快发出一句短确认
- 同时开始快速推理

### 3. deep brain 采用流式阶段产出

复杂问题不要求“一步思考到位”。

deep brain 可以先给出一个立即可执行的阶段任务，例如：

- 先把眼前的纸团丢进垃圾桶

随后继续深挖下一阶段的整理计划。

### 4. 任务支持阶段 lineage

一个前台意图下可以挂多个阶段任务。

下游通过以下字段理解任务位置：

- `parent_task_id`
- `root_intent_id`
- `stage_index`
- `stage_label`
- `is_final`

## 模块概览

| 模块 | 文件 | 当前职责 |
|------|------|------|
| [session](session.md) | `session.py` | 前台编排、chat 输出、双脑协调、中断处理 |
| [blackboard](blackboard.md) | `blackboard.py` | 共享工作记忆 |
| [brains](brains.md) | `brains.py` | 模型接口、流式 deep brain、tag 解析 |
| [types](types.md) | `types.py` | 核心数据类型 |
| [handoff](handoff.md) | `handoff.py` | 交给 action-agent 的正式协议 |
| [tracing](tracing.md) | `tracing.py` | Langfuse / NoOp tracing |
| [prompts](prompts.md) | `prompts.py` | fast/deep 的 prompt 生成 |
| [service](service.md) | `service.py` | 会话容器和依赖装配 |
| [logging](logging.md) | `logging_config.py` | 日志上下文和统一输出 |

## 一条完整链路

### 1. 上游提交事件窗口

```python
result = await session.submit_window(events)
```

返回值会告诉调用方：

- `session_id`
- `window_id`
- `generation`

### 2. Fast Brain 立刻开始前台处理

- ingest window 到黑板
- 并行启动 `acknowledge()` 和 `think()`
- 更新当前意图
- 如有需要，直接产出快速任务
- 如问题复杂，拉起 deep brain

### 3. Deep Brain 流式工作

deep brain 输出的 chunk 会被持续消费：

- `reasoning`：内部思考内容
- `milestone`：中间里程碑
- `stage_task`：可立即下发的阶段任务
- `warning`：风险或阻塞
- `final_summary`：当前轮次的总结

### 4. 外部系统消费 chat

```python
message = await session.next_chat_message(timeout=2.0)
```

常见类型：

- `acknowledgement`
- `progress`
- `stage_result`
- `blocker`
- `final`

## 中断语义

当新窗口到来时：

- 旧的前台 fast/deep 工作会被取消
- 黑板写入 interruption 记录
- 新窗口获得新的 `generation`
- 过期 deep chunk 会被丢弃，不再污染当前状态
