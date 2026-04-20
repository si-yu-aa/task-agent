# Blackboard 模块

`blackboard.py` - 共享黑板，任务代理的中央状态存储

## 设计理念

Blackboard 模式（黑板模式）是一种常用的多 Agent 协作模式：

- **单一数据源**：所有组件读写同一个黑板
- **共享状态**：通过快照传递状态，保证一致性
- **事件溯源**：记录所有状态变化

## 数据结构

```
TaskBlackboard
├── _recent_windows      # 最近5个事件窗口
├── _intents             # 所有意图记录 {intent_id: IntentRecord}
├── _current_intent_id   # 当前活跃意图ID
├── _context_summaries   # 上下文摘要列表
├── _tasks               # 所有任务卡片 {task_id: TaskGoalCard}
├── _execution_records   # 执行记录列表
├── _action_traces       # 动作轨迹 {task_id: [ActionTraceRecord]}
├── _processing          # 当前处理状态
└── _interruptions       # 中断记录列表
```

## 核心方法

### record_window(window)

记录新的事件窗口到黑板。

- 深拷贝防止外部修改
- 保留最近 5 个窗口（可配置）

### apply_intent(relation, summary, source_event_ids)

应用意图变化。

| relation | 行为 |
|----------|------|
| `AMEND` | 修改当前意图摘要 |
| `NEW` | 创建新意图，旧意图标记 SUPERSEDED |
| `REPLACE` | 创建新意图，建立替代关系 |

### publish_task(task, intent_id)

发布任务到黑板，等待 Action Agent 执行。

- 深拷贝任务
- 绑定 intent_id
- 设置时间戳

### update_task_from_feedback(payload, source_event_id)

根据 Action Agent 反馈更新任务状态。

`TaskFeedbackOutcome` → `TaskStatus` 映射：
- `SUCCESS` → `COMPLETED`
- `FAILED` → `FAILED`
- `BLOCKED` → `BLOCKED`
- `PARTIAL` → `PARTIAL`

### supersede_active_tasks(replacement_task_id)

将所有活跃任务标记为 SUPERSEDED。

- 只处理 `ACTIVE`、`PARTIAL`、`BLOCKED` 状态的任务
- 建立替代关系

### snapshot()

获取黑板的只读快照。

**重要**：返回深拷贝的所有状态，用于：
- Fast Brain 和 Deep Brain 获取处理时的一致状态
- 防止并发修改

## 典型使用流程

```
1. Session.submit_window()
   └── blackboard.record_window(window)

2. Session._ingest_window