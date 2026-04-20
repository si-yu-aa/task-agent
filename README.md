# task-agent

`task-agent` 是一个独立于 `claude-core` 的窗口驱动任务编排服务，用来把上游事件流解释成：

- 当前前台意图
- 对外可见的 `chat` 消息
- 可立即交给 `action-agent` 的阶段性任务
- 持续演进的深度规划结果

当前版本采用“双脑 + 黑板 + chat 输出边界”的结构：

- `fast brain`：优先响应最新窗口，负责快速判断和关键节点发言
- `deep brain`：流式思考，持续产出里程碑、阶段任务和最终总结
- `blackboard`：保存意图、任务、执行反馈、动作轨迹和处理中状态
- `chat adapter`：唯一的对外可见输出面
- `_emit`：只保留给内部观测、测试和追踪

## 当前架构要点

### 1. chat 是唯一对外输出

现在不再把 `_emit("acknowledged")`、`_emit("response")` 当成产品侧输出。

对外消息统一走 `ChatMessage`：

- `acknowledgement`
- `progress`
- `stage_result`
- `blocker`
- `final`

如果要接真实聊天通道，只需要替换 `ChatAdapter` 的实现。

### 2. acknowledge 与 fast think 并行

每个窗口进入后，前台会话会并行启动两条分支：

- `acknowledge()`：尽快给出一句自然语言确认
- `think()`：快速解释窗口、更新意图、决定是否委派 deep brain

这样可以避免“先确认、再思考”的串行阻塞。

### 3. deep brain 是流式的

deep brain 不再一次性返回一个最终结果，而是流式输出带标签的 chunk，例如：

- `<reasoning>`
- `<milestone>`
- `<stage_task>`
- `<warning>`
- `<final_summary>`

其中 `stage_task` 会立刻转成真实 `TaskGoalCard` 并下发给下游，这样执行和规划可以并行推进。

### 4. 阶段任务支持 lineage

`TaskGoalCard` 现在支持阶段性规划字段：

- `parent_task_id`
- `root_intent_id`
- `stage_index`
- `stage_label`
- `is_final`

这些字段也会进入 handoff payload，便于下游理解“这是完整计划中的哪一段”。

## 配置

`task-agent` 会自动读取项目根目录的 `.env` 文件。

推荐保留两个文件：

- `.env`：真实密钥和当前模型配置
- `.env.example`：安全模板

### 模型相关变量

- `TASK_AGENT_MODEL_API_KEY`
- `TASK_AGENT_MODEL_BASE_URL`
- `TASK_AGENT_FAST_MODEL`
- `TASK_AGENT_DEEP_MODEL`
- `TASK_AGENT_ROLE_PROMPT`

默认模型拆分：

- fast brain：`gpt-5.4-nano`
- deep brain：`gpt-5.4-mini`

对于 ChatGPT 家族模型，请求会统一带：

- `reasoning_effort=none`

推理内容由 prompt 中的 reasoning tag 约束，不依赖供应商侧隐藏推理模式。

## Action-Agent Handoff

当 `task-agent` 创建任务时，内部会产出 `task.created` 事件，其中 `payload.handoff` 是正式交接协议。

当前 schema 版本：

- `task-agent.action-handoff.v1`

handoff 主要包含两部分：

- `task`
  - 目标、上下文、约束、完成标准、状态
  - 阶段性 lineage 字段
- `dispatch`
  - 来源脑区、generation、session_id、window_id、created_at

## Langfuse 追踪

整个主链路已经接入 Langfuse。

当前会记录的 observation 包括：

- 窗口提交
- fast-brain 运行
- deep-brain 运行
- handoff 构建

配置同样从项目 `.env` 读取：

- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `LANGFUSE_HOST`

说明：

- 如果 Langfuse 配置缺失，会自动退回 `NoOpTracer`
- 模型调用不会因为 tracing 缺失而失效
- 测试环境默认不启用真实 Langfuse 上报
- CLI 退出前会主动 `flush()`

## 最小示例

```python
from task_agent.service import TaskAgentService
from task_agent.types import EventEnvelope, EventType, NlpMessagePayload, make_id

service = TaskAgentService()
session = service.get_session("demo")

event = EventEnvelope(
    event_id=make_id("event"),
    event_type=EventType.NLP_MESSAGE,
    payload=NlpMessagePayload(speaker="user", text="帮我先想想怎么整理房间"),
)

await session.submit_window([event])

# 对外消息请消费 chat，而不是内部事件
message = await session.next_chat_message(timeout=2.0)
print(message.kind, message.text)
```
