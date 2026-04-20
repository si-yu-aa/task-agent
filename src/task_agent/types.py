"""task-agent 的核心数据类型。

这里定义窗口、意图、任务、chat 消息以及 deep 流式 chunk 等基础对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class EventType(str, Enum):
    NLP_MESSAGE = "nlp_message"
    TASK_FEEDBACK = "task_feedback"
    ACTION_INFO = "action_info"
    SYSTEM_INFO = "system_info"
    OTHERS = "others"


class IntentRelation(str, Enum):
    NEW = "new"
    AMEND = "amend"
    REPLACE = "replace"
    NOOP = "noop"


class IntentStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    COMPLETED = "completed"


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class TaskStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class TaskFeedbackOutcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"
    PARTIAL = "partial"


class ProcessingPhase(str, Enum):
    IDLE = "idle"
    FAST_THINKING = "fast_thinking"
    DEEP_THINKING = "deep_thinking"
    INTERRUPTED = "interrupted"


class ContextSummaryKind(str, Enum):
    CONVERSATION = "conversation"
    SYSTEM = "system"
    DEEP_THOUGHT = "deep_thought"
    WINDOW = "window"


class ChatMessageKind(str, Enum):
    ACKNOWLEDGEMENT = "acknowledgement"
    PROGRESS = "progress"
    STAGE_RESULT = "stage_result"
    BLOCKER = "blocker"
    FINAL = "final"


class DeepChunkKind(str, Enum):
    REASONING = "reasoning"
    MILESTONE = "milestone"
    STAGE_TASK = "stage_task"
    FINAL_SUMMARY = "final_summary"
    WARNING = "warning"


@dataclass(slots=True)
class NlpMessagePayload:
    speaker: str
    text: str


@dataclass(slots=True)
class TaskFeedbackPayload:
    task_id: str
    outcome: TaskFeedbackOutcome
    feedback_text: str


@dataclass(slots=True)
class ActionInfoPayload:
    task_id: str
    action_name: str
    details: str = ""


@dataclass(slots=True)
class SystemInfoPayload:
    content: str


@dataclass(slots=True)
class OtherEventPayload:
    content: str
    metadata: dict[str, str] = field(default_factory=dict)


EventPayload = (
    NlpMessagePayload
    | TaskFeedbackPayload
    | ActionInfoPayload
    | SystemInfoPayload
    | OtherEventPayload
)


@dataclass(slots=True)
class EventEnvelope:
    event_id: str
    event_type: EventType
    payload: EventPayload
    timestamp: float = field(default_factory=time.time)


@dataclass(slots=True)
class EventWindow:
    window_id: str
    events: list[EventEnvelope]
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class IntentRecord:
    intent_id: str
    summary: str
    status: IntentStatus
    source_event_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    superseded_by: str | None = None


@dataclass(slots=True)
class ContextSummaryRecord:
    summary_id: str
    kind: ContextSummaryKind
    content: str
    related_event_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class TaskGoalCard:
    task_id: str
    intent_id: str
    goal: str
    context_summary: str
    constraints: list[str] = field(default_factory=list)
    priority: TaskPriority = TaskPriority.NORMAL
    completion_criteria: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.DRAFT
    superseded_by: str | None = None
    parent_task_id: str | None = None
    root_intent_id: str | None = None
    stage_index: int = 0
    stage_label: str | None = None
    is_final: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class TaskExecutionRecord:
    record_id: str
    task_id: str
    outcome: TaskFeedbackOutcome
    feedback_text: str
    source_event_id: str
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class ActionTraceRecord:
    trace_id: str
    task_id: str
    action_name: str
    details: str
    source_event_id: str
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class ProcessingState:
    phase: ProcessingPhase = ProcessingPhase.IDLE
    generation: int = 0
    active_window_id: str | None = None
    active_intent_id: str | None = None
    note: str = ""


@dataclass(slots=True)
class InterruptionRecord:
    interruption_id: str
    interrupted_generation: int
    interrupted_window_id: str | None
    new_window_id: str
    reason: str
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class BlackboardSnapshot:
    current_intent: IntentRecord | None
    intents: dict[str, IntentRecord]
    context_summaries: list[ContextSummaryRecord]
    tasks: dict[str, TaskGoalCard]
    processing: ProcessingState
    interruptions: list[InterruptionRecord]
    recent_windows: list[EventWindow]
    execution_records: list[TaskExecutionRecord]
    action_traces: dict[str, list[ActionTraceRecord]]


@dataclass(slots=True)
class AgentEvent:
    """内部观测事件。

    不应该被当成产品侧消息直接展示给用户。
    """
    event_type: str
    message: str
    generation: int
    window_id: str | None = None
    intent_id: str | None = None
    task_id: str | None = None
    payload: dict | None = None
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class ChatMessage:
    """真正对外可见的聊天消息。"""
    kind: ChatMessageKind
    text: str
    generation: int
    window_id: str | None = None
    intent_id: str | None = None
    task_id: str | None = None
    payload: dict | None = None
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class FastBrainTurnResult:
    intent_summary: str | None = None
    relation: IntentRelation = IntentRelation.NOOP
    response_text: str | None = None
    task: TaskGoalCard | None = None
    delegate_to_deep: bool = False
    delegation_message: str | None = None


@dataclass(slots=True)
class FastBrainChunk:
    kind: str
    message: str | None = None
    result: FastBrainTurnResult | None = None


@dataclass(slots=True)
class DeepBrainChunk:
    kind: DeepChunkKind
    message: str | None = None
    task: TaskGoalCard | None = None
    payload: dict | None = None


@dataclass(slots=True)
class FastBrainRequest:
    window: EventWindow
    snapshot: BlackboardSnapshot
    generation: int


@dataclass(slots=True)
class DeepBrainRequest:
    window: EventWindow
    snapshot: BlackboardSnapshot
    generation: int
    intent: IntentRecord


@dataclass(slots=True)
class SubmitResult:
    session_id: str
    window_id: str
    generation: int


def make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
