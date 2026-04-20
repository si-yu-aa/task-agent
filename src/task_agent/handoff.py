"""任务交接（handoff）模块 - 定义 Task-Agent 到 Action-Agent 的标准化交接协议。

Handoff 是 Task-Agent 生成任务后，交给 Action-Agent 执行的关键环节。
本模块定义了：
1. ActionAgentHandoff: 交接数据结构，包含任务详情和调度元信息
2. build_action_agent_handoff(): 构建交接对象的工厂函数
3. handoff_to_payload(): 将交接对象转换为字典格式（用于序列化传输）

交接内容：
- task: TaskGoalCard，包含任务目标、约束条件、完成标准等
- dispatch: DispatchRecord，包含来源、代际、会话ID、窗口ID等元信息

Schema 版本管理：
- 当前版本: "task-agent.action-handoff.v1"
- 版本号用于 Action Agent 判断如何解析 payload
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time

from task_agent.types import TaskGoalCard, make_id
from task_agent.logging_config import get_handoff_logger, debug

_logger = get_handoff_logger()


# 当前使用的 Handoff 协议版本
# Action Agent 需要根据此版本号决定解析方式
SCHEMA_VERSION = "task-agent.action-handoff.v1"


@dataclass(slots=True)
class DispatchRecord:
    """调度记录 - 描述任务如何被调度和来源。

    属性:
        source: 来源标识（"fast_brain" 或 "deep_brain"）
        generation: Task-Agent 处理的代际号
        session_id: 会话ID
        window_id: 触发此任务的事件窗口ID
        created_at: 创建时间戳
    """
    source: str
    generation: int
    session_id: str
    window_id: str
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class ActionAgentHandoff:
    """Action Agent 交接对象。

    这是 Task-Agent 通知下游 Action Agent 有新任务待执行的完整数据结构。

    属性:
        handoff_id: 交接唯一标识
        schema_version: 协议版本号
        task: 任务目标卡片（TaskGoalCard）
        dispatch: 调度元信息
    """
    handoff_id: str
    schema_version: str
    task: TaskGoalCard
    dispatch: DispatchRecord


def build_action_agent_handoff(
    *,
    session_id: str,
    window_id: str,
    generation: int,
    task: TaskGoalCard,
    source: str,
) -> ActionAgentHandoff:
    """构建 Action Agent 交接对象。

    这是创建 handoff 的工厂函数，确保所有必要字段都被正确填充。

    Args:
        session_id: 会话ID
        window_id: 触发任务的事件窗口ID
        generation: 当前代际号
        task: 任务卡片
        source: 来源（"fast_brain" 或 "deep_brain"）

    Returns:
        ActionAgentHandoff 实例
    """
    handoff = ActionAgentHandoff(
        handoff_id=make_id("handoff"),
        schema_version=SCHEMA_VERSION,
        task=task,
        dispatch=DispatchRecord(
            source=source,
            generation=generation,
            session_id=session_id,
            window_id=window_id,
        ),
    )
    debug(_logger, "ActionAgentHandoff built",
          handoff_id=handoff.handoff_id, source=source,
          task_id=task.task_id, generation=generation)
    return handoff


def handoff_to_payload(handoff: ActionAgentHandoff) -> dict:
    """将交接对象转换为字典格式。

    用于序列化传输到 Action Agent。
    转换后的格式是扁平的，便于 JSON 序列化和跨进程通信。

    返回格式:
    {
        "handoff_id": "...",
        "schema_version": "...",
        "task": {
            "task_id": "...",
            "intent_id": "...",
            "goal": "...",
            "context_summary": "...",
            "constraints": [...],
            "priority": "...",
            "completion_criteria": [...],
            "status": "...",
            "superseded_by": "..."
        },
        "dispatch": {
            "source": "...",
            "generation": ...,
            "session_id": "...",
            "window_id": "...",
            "created_at": ...
        }
    }

    Args:
        handoff: 交接对象

    Returns:
        扁平化的字典格式
    """
    task = handoff.task
    return {
        "handoff_id": handoff.handoff_id,
        "schema_version": handoff.schema_version,
        "task": {
            "task_id": task.task_id,
            "intent_id": task.intent_id,
            "goal": task.goal,
            "context_summary": task.context_summary,
            "constraints": list(task.constraints),
            "priority": task.priority.value,
            "completion_criteria": list(task.completion_criteria),
            "status": task.status.value,
            "superseded_by": task.superseded_by,
            "parent_task_id": task.parent_task_id,
            "root_intent_id": task.root_intent_id,
            "stage_index": task.stage_index,
            "stage_label": task.stage_label,
            "is_final": task.is_final,
        },
        "dispatch": {
            "source": handoff.dispatch.source,
            "generation": handoff.dispatch.generation,
            "session_id": handoff.dispatch.session_id,
            "window_id": handoff.dispatch.window_id,
            "created_at": handoff.dispatch.created_at,
        },
    }
