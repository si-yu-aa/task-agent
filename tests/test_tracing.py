import asyncio

from task_agent.service import TaskAgentService
from task_agent.tracing import NoOpTracer, build_tracer_from_env
from task_agent.types import (
    ChatMessage,
    ChatMessageKind,
    EventEnvelope,
    EventType,
    FastBrainChunk,
    FastBrainTurnResult,
    IntentRelation,
    NlpMessagePayload,
    TaskGoalCard,
    TaskPriority,
    TaskStatus,
    make_id,
)


class FakeObservation:
    def __init__(self):
        self.updated = []

    def update(self, **kwargs):
        self.updated.append(kwargs)


class RecordingTracer:
    enabled = True

    def __init__(self):
        self.calls = []
        self.flush_calls = 0

    class _Ctx:
        def __init__(self, tracer, kwargs):
            self.tracer = tracer
            self.kwargs = kwargs
            self.obs = FakeObservation()

        def __enter__(self):
            self.tracer.calls.append(self.kwargs)
            return self.obs

        def __exit__(self, exc_type, exc, tb):
            return False

    def observation(self, **kwargs):
        return self._Ctx(self, kwargs)

    def flush(self):
        self.flush_calls += 1


class SimpleFastBrain:
    def acknowledge(self, window, snapshot):
        return "received"

    async def think(self, request):
        task = TaskGoalCard(
            task_id="task-1",
            intent_id="",
            goal="Find dinner nearby",
            context_summary="Create a dinner-finding task.",
            constraints=["Stay nearby"],
            priority=TaskPriority.NORMAL,
            completion_criteria=["One dinner task exists"],
            status=TaskStatus.ACTIVE,
        )
        yield FastBrainChunk(
            kind="result",
            result=FastBrainTurnResult(
                intent_summary="User wants a dinner task.",
                relation=IntentRelation.NEW,
                response_text="I created the dinner task.",
                task=task,
                delegate_to_deep=False,
            ),
        )

    def react_to_deep_chunk(self, chunk, snapshot):
        return ChatMessage(kind=ChatMessageKind.PROGRESS, text=chunk.message or "", generation=snapshot.processing.generation)


class SimpleDeepBrain:
    async def stream_think(self, request):
        if False:
            yield None


def test_build_tracer_from_env_returns_noop_without_langfuse_keys(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TASK_AGENT_MODEL_API_KEY=model-key\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    tracer = build_tracer_from_env()

    assert isinstance(tracer, NoOpTracer)


def test_build_tracer_from_env_returns_noop_when_package_missing(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    import task_agent.tracing as tracing

    monkeypatch.setattr(tracing, "get_client", None)

    tracer = tracing.build_tracer_from_env()

    assert isinstance(tracer, NoOpTracer)


def test_session_emits_tracing_observations_for_core_pipeline():
    tracer = RecordingTracer()
    service = TaskAgentService(fast_brain=SimpleFastBrain(), deep_brain=SimpleDeepBrain(), tracer=tracer)
    session = service.get_session("trace-room")
    event = EventEnvelope(
        event_id=make_id("event"),
        event_type=EventType.NLP_MESSAGE,
        payload=NlpMessagePayload(speaker="user", text="find dinner"),
    )

    async def run_flow():
        await session.submit_window([event])
        await session.next_chat_message(timeout=1.0)
        await session.next_chat_message(timeout=1.0)

    asyncio.run(run_flow())

    assert [call["name"] for call in tracer.calls] == [
        "task-agent.window.submit",
        "task-agent.fast-brain.run",
        "task-agent.handoff.build",
    ]


def test_service_flush_delegates_to_tracer():
    tracer = RecordingTracer()
    service = TaskAgentService(fast_brain=SimpleFastBrain(), deep_brain=SimpleDeepBrain(), tracer=tracer)

    service.flush()

    assert tracer.flush_calls == 1
