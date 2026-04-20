import asyncio
from dataclasses import dataclass, field

import pytest

from task_agent.service import TaskAgentService
from task_agent.types import (
    ChatMessage,
    ChatMessageKind,
    DeepBrainChunk,
    DeepChunkKind,
    EventEnvelope,
    EventType,
    FastBrainChunk,
    FastBrainTurnResult,
    IntentRelation,
    NlpMessagePayload,
    SystemInfoPayload,
    TaskFeedbackOutcome,
    TaskFeedbackPayload,
    TaskGoalCard,
    TaskPriority,
    TaskStatus,
    make_id,
)


@dataclass
class ScriptedFastScenario:
    ack: str | None = None
    chunks: list[FastBrainChunk] = field(default_factory=list)
    result: FastBrainTurnResult | None = None
    ack_delay: float = 0.0
    chunk_delay: float = 0.0


class ScriptedFastBrain:
    def __init__(self, scenarios: dict[str, ScriptedFastScenario]):
        self.scenarios = scenarios
        self.think_started = asyncio.Event()

    async def acknowledge(self, window, snapshot):
        scenario = self.scenarios.get(event_key(window.events[-1]))
        if scenario is None:
            return None
        if scenario.ack_delay:
            await asyncio.sleep(scenario.ack_delay)
        return scenario.ack

    async def think(self, request):
        self.think_started.set()
        scenario = self.scenarios.get(event_key(request.window.events[-1]))
        if scenario is None:
            return
        for chunk in scenario.chunks:
            if scenario.chunk_delay:
                await asyncio.sleep(scenario.chunk_delay)
            yield chunk
        if scenario.result is not None:
            if scenario.chunk_delay:
                await asyncio.sleep(scenario.chunk_delay)
            yield FastBrainChunk(kind="result", result=scenario.result)

    def react_to_deep_chunk(self, chunk, snapshot):
        mapping = {
            DeepChunkKind.MILESTONE: ChatMessageKind.PROGRESS,
            DeepChunkKind.STAGE_TASK: ChatMessageKind.STAGE_RESULT,
            DeepChunkKind.WARNING: ChatMessageKind.BLOCKER,
            DeepChunkKind.FINAL_SUMMARY: ChatMessageKind.FINAL,
        }
        kind = mapping.get(chunk.kind)
        if kind is None or not chunk.message:
            return None
        return ChatMessage(kind=kind, text=chunk.message, generation=snapshot.processing.generation)


class ScriptedDeepBrain:
    def __init__(self, scripts: dict[str, list[tuple[float, DeepBrainChunk]]]):
        self.scripts = scripts

    async def stream_think(self, request):
        for delay, chunk in self.scripts.get(event_key(request.window.events[-1]), []):
            if delay:
                await asyncio.sleep(delay)
            yield chunk


def nlp_event(text: str) -> EventEnvelope:
    return EventEnvelope(
        event_id=make_id("event"),
        event_type=EventType.NLP_MESSAGE,
        payload=NlpMessagePayload(speaker="user", text=text),
    )


def system_event(content: str) -> EventEnvelope:
    return EventEnvelope(
        event_id=make_id("event"),
        event_type=EventType.SYSTEM_INFO,
        payload=SystemInfoPayload(content=content),
    )


def feedback_event(task_id: str, outcome: TaskFeedbackOutcome, text: str) -> EventEnvelope:
    return EventEnvelope(
        event_id=make_id("event"),
        event_type=EventType.TASK_FEEDBACK,
        payload=TaskFeedbackPayload(task_id=task_id, outcome=outcome, feedback_text=text),
    )


def event_key(event: EventEnvelope) -> str:
    payload = event.payload
    if hasattr(payload, "text"):
        return payload.text
    if hasattr(payload, "feedback_text"):
        return payload.feedback_text
    if hasattr(payload, "content"):
        return payload.content
    return event.event_type.value


async def collect_chat(session, count: int, timeout: float = 1.0):
    items = []
    for _ in range(count):
        items.append(await session.next_chat_message(timeout=timeout))
    return items


async def drain_events(session, timeout: float = 0.05):
    items = []
    while True:
        try:
            items.append(await session.next_event(timeout=timeout))
        except TimeoutError:
            break
    return items


@pytest.mark.asyncio
async def test_acknowledge_and_fast_think_run_in_parallel():
    fast_brain = ScriptedFastBrain(
        {
            "parallel": ScriptedFastScenario(
                ack="quick ack",
                ack_delay=0.2,
                result=FastBrainTurnResult(
                    intent_summary="Parallel intent",
                    relation=IntentRelation.NEW,
                    response_text="fast result",
                ),
            )
        }
    )
    service = TaskAgentService(fast_brain=fast_brain, deep_brain=ScriptedDeepBrain({}))
    session = service.get_session("parallel")

    await session.submit_window([nlp_event("parallel")])
    await asyncio.wait_for(fast_brain.think_started.wait(), timeout=0.05)
    first_chat = await session.next_chat_message(timeout=1.0)

    assert first_chat.kind == ChatMessageKind.FINAL
    assert first_chat.text == "fast result"


@pytest.mark.asyncio
async def test_chat_is_the_only_user_visible_surface():
    task = TaskGoalCard(
        task_id="task-1",
        intent_id="",
        goal="Find dinner",
        context_summary="Need dinner plan",
        constraints=["Stay nearby"],
        priority=TaskPriority.NORMAL,
        completion_criteria=["One plan exists"],
        status=TaskStatus.ACTIVE,
    )
    fast_brain = ScriptedFastBrain(
        {
            "dinner": ScriptedFastScenario(
                ack="I heard you.",
                result=FastBrainTurnResult(
                    intent_summary="Find dinner tonight",
                    relation=IntentRelation.NEW,
                    response_text="I created a dinner task.",
                    task=task,
                ),
            )
        }
    )
    service = TaskAgentService(fast_brain=fast_brain, deep_brain=ScriptedDeepBrain({}))
    session = service.get_session("chat-only")

    await session.submit_window([nlp_event("dinner")])
    chat_items = await collect_chat(session, 2)
    events = await drain_events(session)

    assert [item.kind for item in chat_items] == [
        ChatMessageKind.ACKNOWLEDGEMENT,
        ChatMessageKind.FINAL,
    ]
    assert "acknowledged" not in {event.event_type for event in events}
    assert "response" not in {event.event_type for event in events}
    assert "task.created" in {event.event_type for event in events}


@pytest.mark.asyncio
async def test_deep_brain_streams_stage_task_before_final_summary():
    stage_task = TaskGoalCard(
        task_id="stage-1",
        intent_id="",
        goal="Pick up the paper ball and put it in the trash",
        context_summary="Start with the most obvious clutter in front of you.",
        constraints=["Do the nearest easy cleanup first"],
        priority=TaskPriority.HIGH,
        completion_criteria=["The visible paper ball is removed"],
        status=TaskStatus.DRAFT,
        stage_index=1,
        stage_label="first cleanup action",
    )
    fast_brain = ScriptedFastBrain(
        {
            "clean the room": ScriptedFastScenario(
                ack="I will start planning the cleanup.",
                result=FastBrainTurnResult(
                    intent_summary="Clean the room thoroughly.",
                    relation=IntentRelation.NEW,
                    response_text="I am starting a deeper cleanup plan.",
                    delegate_to_deep=True,
                    delegation_message="I will plan this in stages.",
                ),
            )
        }
    )
    deep_brain = ScriptedDeepBrain(
        {
            "clean the room": [
                (0.01, DeepBrainChunk(kind=DeepChunkKind.MILESTONE, message="I found an easy first cleanup step.")),
                (0.01, DeepBrainChunk(kind=DeepChunkKind.STAGE_TASK, message=stage_task.goal, task=stage_task)),
                (0.01, DeepBrainChunk(kind=DeepChunkKind.FINAL_SUMMARY, message="After the paper ball, continue with desk and floor zones.")),
            ]
        }
    )
    service = TaskAgentService(fast_brain=fast_brain, deep_brain=deep_brain)
    session = service.get_session("staged")

    await session.submit_window([nlp_event("clean the room")])
    chat_items = await collect_chat(session, 4, timeout=2.0)
    events = await drain_events(session, timeout=0.2)

    assert [item.kind for item in chat_items] == [
        ChatMessageKind.ACKNOWLEDGEMENT,
        ChatMessageKind.PROGRESS,
        ChatMessageKind.STAGE_RESULT,
        ChatMessageKind.FINAL,
    ]
    created = [event for event in events if event.event_type == "task.created"]
    assert created
    snapshot = session.blackboard.snapshot()
    task = snapshot.tasks[created[0].task_id]
    assert task.goal == stage_task.goal
    assert task.stage_index == 1
    assert task.status == TaskStatus.ACTIVE
    assert snapshot.context_summaries[-1].content.startswith("After the paper ball")


@pytest.mark.asyncio
async def test_feedback_updates_stage_task_and_can_trigger_followup_planning():
    task = TaskGoalCard(
        task_id="stage-1",
        intent_id="intent-seed",
        goal="Throw away the paper ball",
        context_summary="First cleanup stage",
        constraints=[],
        priority=TaskPriority.NORMAL,
        completion_criteria=["Paper ball removed"],
        status=TaskStatus.ACTIVE,
        stage_index=1,
    )
    fast_brain = ScriptedFastBrain(
        {
            "paper ball removed": ScriptedFastScenario(
                ack="I saw the cleanup feedback.",
                result=FastBrainTurnResult(
                    intent_summary="The first cleanup stage is complete.",
                    relation=IntentRelation.AMEND,
                    response_text="Good, the first cleanup step is done.",
                    delegate_to_deep=True,
                    delegation_message="I will continue with the next stage.",
                ),
            )
        }
    )
    next_task = TaskGoalCard(
        task_id="stage-2",
        intent_id="",
        goal="Clear the desk surface",
        context_summary="Move to the next visible clutter zone.",
        constraints=[],
        priority=TaskPriority.NORMAL,
        completion_criteria=["Desk surface is clear"],
        status=TaskStatus.DRAFT,
        stage_index=2,
    )
    deep_brain = ScriptedDeepBrain(
        {
            "paper ball removed": [
                (0.01, DeepBrainChunk(kind=DeepChunkKind.STAGE_TASK, message=next_task.goal, task=next_task)),
            ]
        }
    )
    service = TaskAgentService(fast_brain=fast_brain, deep_brain=deep_brain)
    session = service.get_session("feedback")
    session.blackboard.publish_task(task, intent_id="intent-seed")
    session.blackboard.apply_intent(relation=IntentRelation.NEW, summary="Clean the room", source_event_ids=["seed"])

    await session.submit_window([feedback_event("stage-1", TaskFeedbackOutcome.SUCCESS, "paper ball removed")])
    chat_items = await collect_chat(session, 3, timeout=2.0)
    snapshot = session.blackboard.snapshot()

    assert snapshot.tasks["stage-1"].status == TaskStatus.COMPLETED
    assert any(item.kind == ChatMessageKind.STAGE_RESULT for item in chat_items)
    assert any(task.goal == "Clear the desk surface" for task in snapshot.tasks.values())


@pytest.mark.asyncio
async def test_stale_deep_stage_task_is_discarded_after_newer_window():
    stale_task = TaskGoalCard(
        task_id="stale-stage",
        intent_id="",
        goal="Do the old cleanup step",
        context_summary="Outdated plan",
        constraints=[],
        priority=TaskPriority.NORMAL,
        completion_criteria=["Old step done"],
        status=TaskStatus.DRAFT,
    )
    replacement_task = TaskGoalCard(
        task_id="task-now",
        intent_id="",
        goal="Walk outside now",
        context_summary="User changed to a simpler immediate plan.",
        constraints=[],
        priority=TaskPriority.HIGH,
        completion_criteria=["Outside movement starts"],
        status=TaskStatus.ACTIVE,
    )
    fast_brain = ScriptedFastBrain(
        {
            "think deeply": ScriptedFastScenario(
                ack="I will think carefully.",
                result=FastBrainTurnResult(
                    intent_summary="Think deeply first",
                    relation=IntentRelation.NEW,
                    response_text="Starting the deeper pass.",
                    delegate_to_deep=True,
                    delegation_message="Let me reason through this.",
                ),
            ),
            "switch now": ScriptedFastScenario(
                ack="Switching immediately.",
                result=FastBrainTurnResult(
                    intent_summary="Walk outside now",
                    relation=IntentRelation.REPLACE,
                    response_text="Switching to the immediate outside plan.",
                    task=replacement_task,
                ),
            ),
        }
    )
    deep_brain = ScriptedDeepBrain(
        {
            "think deeply": [
                (0.2, DeepBrainChunk(kind=DeepChunkKind.STAGE_TASK, message=stale_task.goal, task=stale_task)),
            ]
        }
    )
    service = TaskAgentService(fast_brain=fast_brain, deep_brain=deep_brain)
    session = service.get_session("interrupt")

    await session.submit_window([nlp_event("think deeply")])
    await asyncio.sleep(0.05)
    await session.submit_window([nlp_event("switch now")])
    await asyncio.sleep(0.3)

    snapshot = session.blackboard.snapshot()
    assert all(task.goal != stale_task.goal for task in snapshot.tasks.values())
    assert any(task.goal == replacement_task.goal for task in snapshot.tasks.values())
