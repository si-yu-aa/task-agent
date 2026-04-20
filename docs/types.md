# Types 模块

`types.py` 定义了 task-agent 的核心对象。

## 核心枚举

### EventType

- `NLP_MESSAGE`
- `TASK_FEEDBACK`
- `ACTION_INFO`
- `SYSTEM_INFO`
- `OTHERS`

### IntentRelation

- `NEW`
- `AMEND`
- `REPLACE`
- `NOOP`

### ChatMessageKind

- `ACKNOWLEDGEMENT`
- `PROGRESS`
- `STAGE_RESULT`
- `BLOCKER`
- `FINAL`

### DeepChunkKind

- `REASONING`
- `MILESTONE`
- `STAGE_TASK`
- `FINAL_SUMMARY`
- `WARNING`

## 重要数据结构

### EventWindow

窗口是当前系统的最小处理单元。

```python
@dataclass
class EventWindow:
    window_id: str
    events: list[EventEnvelope]
    created_at: float
```

### TaskGoalCard

任务卡片现在不仅表示“最终任务”，也可以表示阶段任务。

```python
@dataclass
class TaskGoalCard:
    task_id: str
    intent_id: str
    goal: str
    context_summary: str
    constraints: list[str]
    priority: TaskPriority
    completion_criteria: list[str]
    status: TaskStatus
    superseded_by: str | None
    parent_task_id: str | None
    root_intent_id: str | None
    stage_index: int
    stage_label: str | None
    is_final: bool
```

### ChatMessage

真正面向产品侧的输出消息。

### AgentEvent

内部观测事件，不应该直接作为用户消息展示。

### DeepBrainChunk

deep brain 流式解析后的标准化片段。
