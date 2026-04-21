"""前台会话编排层。

当前版本的 `TaskAgentSession` 负责把窗口化事件流接入双脑系统，并区分：

- `chat`：真正对外可见的输出
- `_emit`：只用于内部观测、测试和 tracing 的事件流
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import inspect

from task_agent.blackboard import TaskBlackboard
from task_agent.chat import QueueChatAdapter
from task_agent.handoff import build_action_agent_handoff, handoff_to_payload
from task_agent.types import (
    AgentEvent,
    ChatMessage,
    ChatMessageKind,
    ContextSummaryKind,
    DeepBrainChunk,
    DeepBrainRequest,
    DeepChunkKind,
    EventType,
    EventWindow,
    FastBrainRequest,
    FastBrainTurnResult,
    IntentRelation,
    ProcessingPhase,
    SubmitResult,
    SystemInfoPayload,
    TaskGoalCard,
    TaskPriority,
    TaskStatus,
    make_id,
)
from task_agent.tracing import NoOpTracer
from task_agent.logging_config import get_session_logger, set_log_context, info, debug, warning, error

_logger = get_session_logger()


class TaskAgentSession:
    def __init__(self, session_id: str, fast_brain, deep_brain, blackboard: TaskBlackboard | None = None, tracer=None, chat_adapter=None):
        self.session_id = session_id
        self.fast_brain = fast_brain
        self.deep_brain = deep_brain
        self.blackboard = blackboard or TaskBlackboard()
        self.tracer = tracer or NoOpTracer()
        self._events: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._chat_adapter = chat_adapter or QueueChatAdapter()
        self._generation = 0
        self._active_window_id: str | None = None
        self._active_task: asyncio.Task | None = None
        self._deep_tasks: set[asyncio.Task] = set()
        set_log_context(session_id=session_id)
        info(_logger, "Session initialized", session_id=session_id)

    async def submit_window(self, events) -> SubmitResult:
        """提交一批新的原始事件。

        每次提交都会生成新的 `window_id` 和 `generation`，并中断上一轮前台工作。
        """
        window = EventWindow(window_id=make_id("window"), events=list(events))
        with self.tracer.observation(
            name="task-agent.window.submit",
            as_type="span",
            input={"window_id": window.window_id, "event_count": len(window.events), "events": self._window_for_trace(window)},
            metadata={"phase": "submit_window"},
            tags=["task-agent", "window", "submit"],
            session_id=self.session_id,
        ) as obs:
            self.blackboard.record_window(window)
            self._ingest_window(window)
            await self._cancel_active_work(window.window_id)
            self._generation += 1
            generation = self._generation
            self._active_window_id = window.window_id
            set_log_context(generation=generation, window_id=window.window_id)
            self.blackboard.update_processing(
                phase=ProcessingPhase.FAST_THINKING,
                generation=generation,
                active_window_id=window.window_id,
                note="fast brain processing window",
            )
            self._active_task = asyncio.create_task(self._run_fast_brain(generation, window))
            event_texts = [e.payload.text if hasattr(e.payload, "text") else str(e.payload) for e in window.events]
            debug(_logger, f"Window submitted: {len(window.events)} events, window_id={window.window_id}, generation={generation}, events={event_texts}")
            obs.update(output={"generation": generation, "active_window_id": window.window_id})
        return SubmitResult(session_id=self.session_id, window_id=window.window_id, generation=generation)

    async def next_event(self, timeout: float = 1.0) -> AgentEvent:
        try:
            return await asyncio.wait_for(self._events.get(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("No event arrived before timeout") from exc

    async def next_chat_message(self, timeout: float = 1.0) -> ChatMessage:
        return await self._chat_adapter.next_message(timeout=timeout)

    def _ingest_window(self, window: EventWindow) -> None:
        """把窗口里的低风险事实先写入黑板。

        这里只做状态归档，不做重要性判断。
        """
        for event in window.events:
            if event.event_type == EventType.SYSTEM_INFO:
                payload = event.payload
                assert isinstance(payload, SystemInfoPayload)
                self.blackboard.add_context_summary(
                    kind=ContextSummaryKind.SYSTEM,
                    content=payload.content,
                    related_event_ids=[event.event_id],
                )
            elif event.event_type == EventType.TASK_FEEDBACK:
                self.blackboard.update_task_from_feedback(event.payload, source_event_id=event.event_id)
            elif event.event_type == EventType.ACTION_INFO:
                self.blackboard.append_action_trace(event.payload, source_event_id=event.event_id)

    async def _cancel_active_work(self, new_window_id: str) -> None:
        """取消旧 generation 的前台工作和深度任务。"""
        if self._active_task and not self._active_task.done():
            self.blackboard.mark_interruption(
                interrupted_generation=self._generation,
                interrupted_window_id=self._active_window_id,
                new_window_id=new_window_id,
                reason="new_window_arrived",
            )
            await self._emit("interrupting", "Active work interrupted by a newer window.", generation=self._generation, window_id=new_window_id)
            self._active_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._active_task
        for task in list(self._deep_tasks):
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def _run_fast_brain(self, generation: int, window: EventWindow) -> None:
        debug(_logger, f"Fast brain starting, generation={generation}")
        with self.tracer.observation(
            name="task-agent.fast-brain.run",
            as_type="agent",
            input={"window_id": window.window_id, "generation": generation, "events": self._window_for_trace(window)},
            metadata={"phase": "fast_brain", "window_id": window.window_id, "generation": generation},
            tags=["task-agent", "fast-brain"],
            session_id=self.session_id,
        ) as obs:
            # acknowledge 和 fast think 并行启动，避免确认语阻塞推理。
            ack_sent = asyncio.Event()
            ack_task = asyncio.create_task(self._run_acknowledge(generation, window, ack_sent))
            await asyncio.sleep(0)
            try:
                request = FastBrainRequest(window=window, snapshot=self.blackboard.snapshot(), generation=generation)
                debug(_logger, f"Fast brain calling model, generation={generation}")
                async for chunk in self.fast_brain.think(request):
                    if generation != self._generation:
                        debug(_logger, f"Fast brain stale generation={generation}, current={self._generation}, returning")
                        return
                    debug(_logger, f"Fast brain chunk: kind={chunk.kind}, message={chunk.message!r}")
                    if chunk.kind == "result" and chunk.result is not None:
                        await self._apply_fast_result(generation, window, chunk.result, ack_task, ack_sent)
                    elif chunk.kind in {"status", "message"} and chunk.message:
                        await self._emit(f"fast.{chunk.kind}", chunk.message, generation=generation, window_id=window.window_id)
                debug(_logger, f"Fast brain stream exhausted, generation={generation}")
                obs.update(output={"status": "completed", "window_id": window.window_id, "generation": generation})
            except asyncio.CancelledError:
                debug(_logger, f"Fast brain cancelled, generation={generation}")
                obs.update(output={"status": "cancelled", "window_id": window.window_id, "generation": generation})
                raise
            except Exception as exc:
                error(_logger, f"Fast brain error: {exc}", generation=generation, window_id=window.window_id)
                obs.update(output={"status": "error", "error": str(exc)})
                await self._emit("error", f"Fast-brain processing failed: {exc}", generation=generation, window_id=window.window_id)
            finally:
                if ack_task and not ack_task.done():
                    ack_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await ack_task
                if generation == self._generation and not any(not task.done() for task in self._deep_tasks):
                    self.blackboard.update_processing(
                        phase=ProcessingPhase.IDLE,
                        generation=generation,
                        active_window_id=window.window_id,
                        note="",
                    )

    async def _run_acknowledge(self, generation: int, window: EventWindow, ack_sent: asyncio.Event) -> None:
        try:
            debug(_logger, f"Acknowledge starting, generation={generation}")
            ack = await self._resolve_maybe_async(self.fast_brain.acknowledge(window, self.blackboard.snapshot()))
            debug(_logger, f"Acknowledge returned: {ack!r}, generation={generation}")
            if generation != self._generation or not ack:
                return
            await self._send_chat(
                ChatMessage(kind=ChatMessageKind.ACKNOWLEDGEMENT, text=ack, generation=generation, window_id=window.window_id)
            )
            ack_sent.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error(_logger, f"Acknowledge error: {exc}", generation=generation, window_id=window.window_id)
            await self._emit("ack.error", f"Acknowledge failed: {exc}", generation=generation, window_id=window.window_id)

    async def _apply_fast_result(self, generation: int, window: EventWindow, result: FastBrainTurnResult, ack_task: asyncio.Task | None, ack_sent: asyncio.Event) -> None:
        """把 fast brain 的结构化结果落到黑板、chat 和 handoff。"""
        debug(_logger,
            f"Fast brain result: relation={result.relation.value}, "
            f"delegate_to_deep={result.delegate_to_deep}, "
            f"response_text={result.response_text!r}, "
            f"task_goal={result.task.goal if result.task else None}")
        if ack_task and not ack_task.done() and result.response_text:
            ack_task.cancel()
            with suppress(asyncio.CancelledError):
                await ack_task

        intent = self.blackboard.snapshot().current_intent
        source_event_ids = [event.event_id for event in window.events]
        if result.relation != IntentRelation.NOOP and result.intent_summary:
            intent = self.blackboard.apply_intent(
                relation=result.relation,
                summary=result.intent_summary,
                source_event_ids=source_event_ids,
            )
            await self._emit("intent.updated", result.intent_summary, generation=generation, window_id=window.window_id, intent_id=intent.intent_id)

        if result.relation == IntentRelation.REPLACE and result.task:
            superseded = self.blackboard.supersede_active_tasks(replacement_task_id=result.task.task_id)
            for task in superseded:
                await self._emit("task.superseded", f"Superseded task: {task.goal}", generation=generation, window_id=window.window_id, intent_id=intent.intent_id if intent else None, task_id=task.task_id)

        if result.task is not None and intent is not None:
            await self._publish_task(generation, window, intent.intent_id, result.task, source="fast_brain")

        if result.response_text:
            await self._send_chat(
                ChatMessage(
                    kind=ChatMessageKind.FINAL if not result.delegate_to_deep else ChatMessageKind.PROGRESS,
                    text=result.response_text,
                    generation=generation,
                    window_id=window.window_id,
                    intent_id=intent.intent_id if intent else None,
                )
            )

        if result.delegate_to_deep and intent is not None:
            # 如果已经有 response_text，对外就不重复播报 delegation_message。
            if result.delegation_message and not result.response_text:
                await self._send_chat(
                    ChatMessage(
                        kind=ChatMessageKind.PROGRESS,
                        text=result.delegation_message,
                        generation=generation,
                        window_id=window.window_id,
                        intent_id=intent.intent_id,
                    )
                )
            self.blackboard.update_processing(
                phase=ProcessingPhase.DEEP_THINKING,
                generation=generation,
                active_window_id=window.window_id,
                note="deep brain streaming",
            )
            deep_task = asyncio.create_task(self._run_deep_brain(generation, window, intent))
            self._deep_tasks.add(deep_task)
            deep_task.add_done_callback(self._deep_tasks.discard)

    async def _run_deep_brain(self, generation: int, window: EventWindow, intent) -> None:
        debug(_logger, f"Deep brain starting, generation={generation}, intent_id={intent.intent_id}")
        with self.tracer.observation(
            name="task-agent.deep-brain.run",
            as_type="agent",
            input={"window_id": window.window_id, "generation": generation, "intent_id": intent.intent_id},
            metadata={"phase": "deep_brain", "window_id": window.window_id, "generation": generation, "intent_id": intent.intent_id},
            tags=["task-agent", "deep-brain"],
            session_id=self.session_id,
        ) as obs:
            request = DeepBrainRequest(window=window, snapshot=self.blackboard.snapshot(), generation=generation, intent=intent)
            try:
                async for chunk in self.deep_brain.stream_think(request):
                    if generation != self._generation:
                        debug(_logger, f"Deep brain stale generation={generation}, discarding chunk")
                        await self._emit("deep.discarded", "A stale deep-planning chunk was discarded.", generation=generation, window_id=window.window_id, intent_id=intent.intent_id)
                        obs.update(output={"status": "discarded", "window_id": window.window_id, "generation": generation})
                        return
                    await self._handle_deep_chunk(generation, window, intent, chunk)
                debug(_logger, f"Deep brain stream exhausted, generation={generation}")
                obs.update(output={"status": "completed", "window_id": window.window_id, "generation": generation})
            except asyncio.CancelledError:
                debug(_logger, f"Deep brain cancelled, generation={generation}")
                obs.update(output={"status": "cancelled", "window_id": window.window_id, "generation": generation})
                raise
            except Exception as exc:
                error(_logger, f"Deep brain error: {exc}", generation=generation, window_id=window.window_id, intent_id=intent.intent_id)
                obs.update(output={"status": "error", "error": str(exc)})
                await self._emit("error", f"Deep-brain processing failed: {exc}", generation=generation, window_id=window.window_id, intent_id=intent.intent_id)
            finally:
                if generation == self._generation:
                    self.blackboard.update_processing(
                        phase=ProcessingPhase.IDLE,
                        generation=generation,
                        active_window_id=window.window_id,
                        note="",
                    )

    async def _handle_deep_chunk(self, generation: int, window: EventWindow, intent, chunk: DeepBrainChunk) -> None:
        """处理 deep brain 的流式 chunk。

        当前策略是：
        - stage_task 立刻转成真实任务并下发
        - final_summary 写入黑板并可转成对外 final
        - milestone 默认只做内部观测，不直接对外播报
        """
        msg_preview = repr(chunk.message[:80]) if chunk.message else None
        task_id = chunk.task.task_id if chunk.task else None
        debug(_logger, f"Deep chunk: kind={chunk.kind.value}, message={msg_preview}, task_id={task_id}")
        await self._emit(
            f"deep.{chunk.kind.value}",
            chunk.message or chunk.kind.value,
            generation=generation,
            window_id=window.window_id,
            intent_id=intent.intent_id,
            task_id=chunk.task.task_id if chunk.task else None,
        )
        if chunk.kind == DeepChunkKind.STAGE_TASK and chunk.task is not None:
            chunk.task.intent_id = intent.intent_id
            chunk.task.root_intent_id = intent.intent_id
            if chunk.task.status == TaskStatus.DRAFT:
                chunk.task.status = TaskStatus.ACTIVE
            await self._publish_task(generation, window, intent.intent_id, chunk.task, source="deep_brain")
        elif chunk.kind == DeepChunkKind.FINAL_SUMMARY and chunk.message:
            context = self.blackboard.add_context_summary(
                kind=ContextSummaryKind.DEEP_THOUGHT,
                content=chunk.message,
                related_event_ids=[event.event_id for event in window.events],
            )
            await self._emit("context.updated", chunk.message, generation=generation, window_id=window.window_id, intent_id=intent.intent_id, payload={"summary_id": context.summary_id})

        # 里程碑默认不直接发给外部，避免前台变成高频旁白。
        if chunk.kind != DeepChunkKind.MILESTONE:
            chat_message = self.fast_brain.react_to_deep_chunk(chunk, self.blackboard.snapshot())
            if chat_message is not None:
                chat_message.generation = generation
                chat_message.window_id = window.window_id
                chat_message.intent_id = intent.intent_id
                if chunk.task is not None:
                    chat_message.task_id = chunk.task.task_id
                await self._send_chat(chat_message)

    async def _publish_task(self, generation: int, window: EventWindow, intent_id: str, task: TaskGoalCard, *, source: str) -> TaskGoalCard:
        with self.tracer.observation(
            name="task-agent.handoff.build",
            as_type="tool",
            input={"source": source, "task_goal": task.goal, "window_id": window.window_id},
            metadata={"phase": "handoff", "window_id": window.window_id, "generation": generation},
            tags=["task-agent", "handoff", source.replace("_", "-")],
            session_id=self.session_id,
        ) as obs:
            saved_task = self.blackboard.publish_task(task, intent_id=intent_id)
            handoff = build_action_agent_handoff(
                session_id=self.session_id,
                window_id=window.window_id,
                generation=generation,
                task=saved_task,
                source=source,
            )
            handoff_payload = handoff_to_payload(handoff)
            obs.update(output={"handoff_id": handoff.handoff_id, "task_id": saved_task.task_id})
        await self._emit(
            "task.created",
            f"Task ready for action agent: {saved_task.goal}",
            generation=generation,
            window_id=window.window_id,
            intent_id=intent_id,
            task_id=saved_task.task_id,
            payload={"handoff": handoff_payload},
        )
        return saved_task

    def _window_for_trace(self, window: EventWindow) -> list[dict]:
        items = []
        for event in window.events:
            payload = event.payload
            if hasattr(payload, "__dataclass_fields__"):
                payload_dict = {name: str(getattr(payload, name)) for name in payload.__dataclass_fields__}
            else:
                payload_dict = {"value": str(payload)}
            items.append({"event_id": event.event_id, "event_type": event.event_type.value, "payload": payload_dict})
        return items

    async def _send_chat(self, message: ChatMessage) -> None:
        """发送真正对外可见的消息。"""
        await self._chat_adapter.send(message)
        await self._emit(
            "chat.sent",
            message.text,
            generation=message.generation,
            window_id=message.window_id,
            intent_id=message.intent_id,
            task_id=message.task_id,
            payload={"kind": message.kind.value},
        )

    async def _emit(self, event_type: str, message: str, *, generation: int, window_id: str | None = None, intent_id: str | None = None, task_id: str | None = None, payload: dict | None = None) -> None:
        """发送内部观测事件，不作为产品侧输出。"""
        await self._events.put(
            AgentEvent(
                event_type=event_type,
                message=message,
                generation=generation,
                window_id=window_id,
                intent_id=intent_id,
                task_id=task_id,
                payload=payload,
            )
        )

    async def _resolve_maybe_async(self, value):
        if inspect.isawaitable(value):
            return await value
        return value
