"""Service container for task-agent sessions."""

from __future__ import annotations

from task_agent.blackboard import TaskBlackboard
from task_agent.brains import ModelConfig, ModelDeepBrain, ModelFastBrain, OpenAICompatibleClient
from task_agent.session import TaskAgentSession
from task_agent.tracing import build_tracer_from_env
from task_agent.logging_config import get_service_logger, info, debug

_logger = get_service_logger()


class TaskAgentService:
    def __init__(self, fast_brain=None, deep_brain=None, model_config: ModelConfig | None = None, tracer=None, chat_adapter_factory=None):
        self.tracer = tracer or build_tracer_from_env()
        self.chat_adapter_factory = chat_adapter_factory
        if fast_brain is None or deep_brain is None:
            config = model_config or ModelConfig.from_env()
            client = OpenAICompatibleClient(config, tracer=self.tracer)
            fast_brain = fast_brain or ModelFastBrain(client=client, config=config)
            deep_brain = deep_brain or ModelDeepBrain(client=client, config=config)
        self.fast_brain = fast_brain
        self.deep_brain = deep_brain
        self._sessions: dict[str, TaskAgentSession] = {}
        info(_logger, "TaskAgentService initialized", session_count=0)

    def get_session(self, session_id: str) -> TaskAgentSession:
        session = self._sessions.get(session_id)
        if session is None:
            chat_adapter = self.chat_adapter_factory() if self.chat_adapter_factory else None
            session = TaskAgentSession(
                session_id=session_id,
                fast_brain=self.fast_brain,
                deep_brain=self.deep_brain,
                blackboard=TaskBlackboard(),
                tracer=self.tracer,
                chat_adapter=chat_adapter,
            )
            self._sessions[session_id] = session
            info(_logger, "New session created", session_id=session_id, total_sessions=len(self._sessions))
        else:
            debug(_logger, "Existing session returned", session_id=session_id)
        return session

    def flush(self) -> None:
        debug(_logger, "Flushing tracer")
        self.tracer.flush()
