# Handoff 模块

`handoff.py` 定义 Task-Agent 交给 Action-Agent 的正式协议。

## 设计目标

handoff 需要同时满足：

1. 结构化
2. 可追踪
3. 可版本化
4. 能表达阶段任务而不只是最终任务

## 当前结构

### ActionAgentHandoff

```python
@dataclass
class ActionAgentHandoff:
    handoff_id: str
    schema_version: str
    task: TaskGoalCard
    dispatch: DispatchRecord
```

### DispatchRecord

```python
@dataclass
class DispatchRecord:
    source: str
    generation: int
    session_id: str
    window_id: str
    created_at: float
```

## 传输示例

```json
{
  "handoff_id": "handoff-a1b2c3d4",
  "schema_version": "task-agent.action-handoff.v1",
  "task": {
    "task_id": "task-xxx",
    "intent_id": "intent-xxx",
    "goal": "先清理桌面上的纸团",
    "context_summary": "这是整理房间任务的第一阶段。",
    "constraints": ["先做最近且最容易完成的一步"],
    "priority": "high",
    "completion_criteria": ["纸团已经进入垃圾桶"],
    "status": "active",
    "superseded_by": null,
    "parent_task_id": null,
    "root_intent_id": "intent-xxx",
    "stage_index": 1,
    "stage_label": "第一阶段",
    "is_final": false
  },
  "dispatch": {
    "source": "deep_brain",
    "generation": 3,
    "session_id": "default",
    "window_id": "window-xxx",
    "created_at": 1713600000.123
  }
}
```

## 当前语义

这次 streaming 改造后，handoff 可能表示：

- 当前意图下的第一阶段任务
- 执行过程中的后续阶段任务
- 某个旧任务被替换后的新任务

因此 action-agent 在消费时，建议同时关注：

- `task.status`
- `task.stage_index`
- `task.parent_task_id`
- `task.root_intent_id`
- `task.is_final`
