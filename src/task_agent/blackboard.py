"""共享黑板模块 - 会话状态的中央存储。

TaskBlackboard 是整个任务代理系统的状态存储中心，
类似 Agent Memory 的设计模式，所有组件都读、写同一个黑板。

设计原则：
1. 单一数据源：所有状态都存储在黑板中
2. 快照机制：提供只读快照供各 Brain 使用，防止竞态条件
3. 事件溯源：记录所有状态变化（中断、执行记录等）

数据结构：
- recent_windows: 最近5个事件窗口（用于上下文恢复）
- intents: 所有意图记录（按ID索引）
- current_intent_id: 当前活跃意图ID
- context_summaries: 上下文摘要列表
- tasks: 所有任务卡片（按ID索引）
- execution_records: 执行记录（用于反馈追踪）
- action_traces: 动作轨迹（Action Agent 执行过程）
- processing: 当前处理状态
- interruptions: 中断记录

典型使用流程：
1. Session.submit_window() -> blackboard.record_window()
2. Session._ingest_window() -> 更新意图、任务状态
3. Fast/Deep Brain 通过 snapshot() 获取只读状态
4. Brain 结果通过 apply_intent()、publish_task() 等写入
"""

from __future__ import annotations

from copy import deepcopy
import time

from task_agent.types import (
    ActionInfoPayload,
    ActionTraceRecord,
    BlackboardSnapshot,
    ContextSummaryKind,
    ContextSummaryRecord,
    EventWindow,
    IntentRecord,
    IntentRelation,
    IntentStatus,
    InterruptionRecord,
    ProcessingPhase,
    ProcessingState,
    TaskExecutionRecord,
    TaskFeedbackOutcome,
    TaskFeedbackPayload,
    TaskGoalCard,
    TaskStatus,
    make_id,
)
from task_agent.logging_config import get_blackboard_logger, info, debug

_logger = get_blackboard_logger()


# 任务反馈结果到任务状态的映射
# Action Agent 反馈 SUCCESS -> 任务标记为 COMPLETED
_OUTCOME_TO_STATUS = {
    TaskFeedbackOutcome.SUCCESS: TaskStatus.COMPLETED,
    TaskFeedbackOutcome.FAILED: TaskStatus.FAILED,
    TaskFeedbackOutcome.BLOCKED: TaskStatus.BLOCKED,
    TaskFeedbackOutcome.PARTIAL: TaskStatus.PARTIAL,
}


class TaskBlackboard:
    """共享黑板 - 任务代理的中央状态存储。

    所有的会话状态都存储在这里，包括：
    - 事件窗口历史
    - 意图记录
    - 任务卡片
    - 执行反馈
    - 处理状态

    外部组件通过 snapshot() 获取只读副本，保证数据一致性。

    属性:
        _recent_window_limit: 保留的最近窗口数量（默认5个）
        _recent_windows: 最近的事件窗口列表
        _intents: 所有意图记录（按 intent_id 索引）
        _current_intent_id: 当前活跃意图ID
        _context_summaries: 上下文摘要记录
        _tasks: 所有任务卡片（按 task_id 索引）
        _execution_records: 任务执行反馈记录列表
        _action_traces: Action Agent 动作轨迹（按 task_id 索引）
        _processing: 当前处理状态
        _interruptions: 中断记录列表
    """

    def __init__(self, recent_window_limit: int = 5) -> None:
        self._recent_window_limit = recent_window_limit
        self._recent_windows: list[EventWindow] = []
        self._intents: dict[str, IntentRecord] = {}
        self._current_intent_id: str | None = None
        self._context_summaries: list[ContextSummaryRecord] = []
        self._tasks: dict[str, TaskGoalCard] = {}
        self._execution_records: list[TaskExecutionRecord] = []
        self._action_traces: dict[str, list[ActionTraceRecord]] = {}
        self._processing = ProcessingState()
        self._interruptions: list[InterruptionRecord] = []
        debug(_logger, "TaskBlackboard initialized")

    def record_window(self, window: EventWindow) -> None:
        """记录一个新的事件窗口到黑板。

        Args:
            window: 待记录的事件窗口
        """
        self._recent_windows.append(deepcopy(window))
        if len(self._recent_windows) > self._recent_window_limit:
            self._recent_windows = self._recent_windows[-self._recent_window_limit:]
        debug(_logger, f"Window recorded: {window.window_id}, total_windows={len(self._recent_windows)}",
              window_id=window.window_id, event_count=len(window.events))

    def update_processing(
        self,
        *,
        phase: ProcessingPhase,
        generation: int,
        active_window_id: str | None,
        note: str = "",
    ) -> None:
        """更新当前处理状态。

        在处理阶段变化时调用（开始快速思考、深度思考、返回空闲等）。

        Args:
            phase: 新的处理阶段
            generation: 当前代际号
            active_window_id: 活跃窗口ID
            note: 额外备注（如中断原因）
        """
        self._processing = ProcessingState(
            phase=phase,
            generation=generation,
            active_window_id=active_window_id,
            active_intent_id=self._current_intent_id,
            note=note,
        )
        debug(_logger, f"Processing state updated: {phase.value}",
              phase=phase.value, generation=generation, note=note)

    def mark_interruption(
        self,
        *,
        interrupted_generation: int,
        interrupted_window_id: str | None,
        new_window_id: str,
        reason: str,
    ) -> InterruptionRecord:
        """记录一次中断事件。

        当新事件窗口到达时，如果当前有活跃任务在处理，
        会触发中断，此方法记录中断的详细信息。

        Args:
            interrupted_generation: 被中断的代际号
            interrupted_window_id: 被中断的窗口ID
            new_window_id: 导致中断的新窗口ID
            reason: 中断原因（如 "new_window_arrived"）

        Returns:
            创建的中断记录
        """
        record = InterruptionRecord(
            interruption_id=make_id("interrupt"),
            interrupted_generation=interrupted_generation,
            interrupted_window_id=interrupted_window_id,
            new_window_id=new_window_id,
            reason=reason,
        )
        self._interruptions.append(record)
        self.update_processing(
            phase=ProcessingPhase.INTERRUPTED,
            generation=interrupted_generation,
            active_window_id=new_window_id,
            note=reason,
        )
        debug(_logger, f"Interruption recorded",
              interruption_id=record.interruption_id,
              interrupted_generation=interrupted_generation,
              reason=reason)
        return record

    def apply_intent(
        self,
        *,
        relation: IntentRelation,
        summary: str,
        source_event_ids: list[str],
    ) -> IntentRecord:
        """应用新的意图或更新当前意图。

        根据 relation 类型决定如何处理：
        - AMEND: 修改当前意图的摘要
        - NEW/REPLACE: 创建新意图，旧意图标记为 SUPERSEDED
        - NOOP: 不改变意图（此方法不会被调用）

        Args:
            relation: 意图关系类型
            summary: 意图摘要文本
            source_event_ids: 生成此意图的源事件ID列表

        Returns:
            创建或更新的意图记录
        """
        now = time.time()

        # AMEND: 只修改当前意图的摘要
        if relation == IntentRelation.AMEND and self._current_intent_id:
            current = self._intents[self._current_intent_id]
            current.summary = summary
            current.updated_at = now
            current.source_event_ids.extend(source_event_ids)
            debug(_logger, f"Intent amended: {current.intent_id}",
                  intent_id=current.intent_id, relation=relation.value)
            return current

        # 如果存在当前意图，先标记为 SUPERSEDED
        if self._current_intent_id:
            previous = self._intents[self._current_intent_id]
            previous.status = IntentStatus.SUPERSEDED
            debug(_logger, f"Previous intent marked SUPERSEDED: {previous.intent_id}",
                  previous_intent_id=previous.intent_id)

        # 创建新意图
        intent = IntentRecord(
            intent_id=make_id("intent"),
            summary=summary,
            status=IntentStatus.ACTIVE,
            source_event_ids=list(source_event_ids),
            created_at=now,
            updated_at=now,
        )

        # REPLACE 模式下，建立意图替代关系
        if self._current_intent_id and relation == IntentRelation.REPLACE:
            self._intents[self._current_intent_id].superseded_by = intent.intent_id

        self._intents[intent.intent_id] = intent
        self._current_intent_id = intent.intent_id
        info(_logger, f"New intent created: {intent.intent_id}, relation={relation.value}",
             intent_id=intent.intent_id, relation=relation.value)
        return intent

    def add_context_summary(
        self,
        *,
        kind: ContextSummaryKind,
        content: str,
        related_event_ids: list[str],
    ) -> ContextSummaryRecord:
        """添加一条上下文摘要。

        上下文摘要用于存储：
        - 系统信息（SYSTEM）
        - 深度思考结果（DEEP_THOUGHT）
        - 对话历史（CONVERSATION）
        - 窗口摘要（WINDOW）

        Args:
            kind: 摘要类型
            content: 摘要内容
            related_event_ids: 相关的源事件ID

        Returns:
            创建的摘要记录
        """
        record = ContextSummaryRecord(
            summary_id=make_id("summary"),
            kind=kind,
            content=content,
            related_event_ids=list(related_event_ids),
        )
        self._context_summaries.append(record)
        return record

    def publish_task(self, task: TaskGoalCard, *, intent_id: str) -> TaskGoalCard:
        """发布一个任务到黑板。

        发布后任务状态为 ACTIVE，等待 Action Agent 执行。
        会深拷贝任务，绑定 intent_id，并设置时间戳。

        Args:
            task: 待发布的任务卡片
            intent_id: 关联的意图ID

        Returns:
            发布后的任务卡片（深拷贝）
        """
        task_copy = deepcopy(task)
        task_copy.intent_id = intent_id
        task_copy.updated_at = time.time()
        self._tasks[task_copy.task_id] = task_copy
        info(_logger, f"Task published: {task_copy.task_id}",
             task_id=task_copy.task_id, intent_id=intent_id, goal=task_copy.goal[:50])
        return task_copy

    def update_task_from_feedback(self, payload: TaskFeedbackPayload, *, source_event_id: str) -> TaskGoalCard | None:
        """根据 Action Agent 的反馈更新任务状态。

        当 Action Agent 执行完任务后，会发送 TASK_FEEDBACK 事件，
        此方法根据反馈更新对应任务的状态。

        Args:
            payload: 任务反馈载荷（包含 task_id, outcome, feedback_text）
            source_event_id: 源事件ID

        Returns:
            更新后的任务卡片，如果没有找到对应任务则返回 None
        """
        record = TaskExecutionRecord(
            record_id=make_id("feedback"),
            task_id=payload.task_id,
            outcome=payload.outcome,
            feedback_text=payload.feedback_text,
            source_event_id=source_event_id,
        )
        self._execution_records.append(record)

        task = self._tasks.get(payload.task_id)
        if task is not None:
            # 根据反馈结果更新任务状态
            task.status = _OUTCOME_TO_STATUS[payload.outcome]
            task.updated_at = time.time()
        return task

    def append_action_trace(self, payload: ActionInfoPayload, *, source_event_id: str) -> ActionTraceRecord:
        """追加 Action Agent 的动作轨迹。

        Action Agent 在执行任务过程中会发送 ACTION_INFO 事件，
        记录具体的执行动作（如文件创建、命令执行等）。

        Args:
            payload: 动作信息载荷
            source_event_id: 源事件ID

        Returns:
            创建的轨迹记录
        """
        record = ActionTraceRecord(
            trace_id=make_id("trace"),
            task_id=payload.task_id,
            action_name=payload.action_name,
            details=payload.details,
            source_event_id=source_event_id,
        )
        self._action_traces.setdefault(payload.task_id, []).append(record)
        return record

    def supersede_active_tasks(self, *, replacement_task_id: str | None = None) -> list[TaskGoalCard]:
        """将所有活跃任务标记为 SUPERSEDED（已替代）。

        当新任务被创建时，之前的活跃任务需要被替代。
        这确保同时只有一个活跃任务在执行。

        Args:
            replacement_task_id: 替代的新任务ID

        Returns:
            被替代的任务列表
        """
        updated: list[TaskGoalCard] = []
        for task in self._tasks.values():
            if task.status in {TaskStatus.ACTIVE, TaskStatus.PARTIAL, TaskStatus.BLOCKED}:
                task.status = TaskStatus.SUPERSEDED
                task.superseded_by = replacement_task_id
                task.updated_at = time.time()
                updated.append(task)
        if updated:
            debug(_logger, f"Superseded {len(updated)} active tasks",
                  task_ids=[t.task_id for t in updated], replacement_task_id=replacement_task_id)
        return updated

    def snapshot(self) -> BlackboardSnapshot:
        """获取黑板的只读快照。

        这是线程/协程安全的快照机制，返回深拷贝的所有状态。
        Fast Brain 和 Deep Brain 通过此方法获取处理时的一致状态。

        Returns:
            包含所有状态的深拷贝快照
        """
        current_intent = self._intents.get(self._current_intent_id) if self._current_intent_id else None
        return BlackboardSnapshot(
            # 深拷贝所有数据，确保快照的不变性
            current_intent=deepcopy(current_intent),
            intents=deepcopy(self._intents),
            context_summaries=deepcopy(self._context_summaries),
            tasks=deepcopy(self._tasks),
            processing=deepcopy(self._processing),
            interruptions=deepcopy(self._interruptions),
            recent_windows=deepcopy(self._recent_windows),
            execution_records=deepcopy(self._execution_records),
            action_traces=deepcopy(self._action_traces),
        )
