"""双脑协议与模型实现。

这个模块同时承担三件事：

- 定义 fast/deep brain 的协议接口
- 封装 OpenAI 兼容模型调用
- 解析 deep brain 的流式标签输出
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, AsyncIterator, Protocol

try:
    from openai import AsyncOpenAI as OpenAIAsyncOpenAI
except ImportError:  # pragma: no cover
    OpenAIAsyncOpenAI = None

from task_agent.prompts import DEFAULT_ROLE_PROMPT, build_ack_prompt, build_deep_stream_prompt, build_fast_prompt
from task_agent.types import (
    ChatMessage,
    ChatMessageKind,
    DeepBrainChunk,
    DeepBrainRequest,
    DeepChunkKind,
    FastBrainChunk,
    FastBrainRequest,
    FastBrainTurnResult,
    IntentRelation,
    TaskGoalCard,
    TaskPriority,
    TaskStatus,
    make_id,
)
from task_agent.env import load_project_env
from task_agent.logging_config import get_brains_logger, debug, info

_logger = get_brains_logger()


class FastBrain(Protocol):
    """快速推理脑协议。"""
    def acknowledge(self, window, snapshot) -> str | None: ...

    async def think(self, request: FastBrainRequest) -> AsyncIterator[FastBrainChunk]: ...

    def react_to_deep_chunk(self, chunk: DeepBrainChunk, snapshot) -> ChatMessage | None: ...


class DeepBrain(Protocol):
    """深度推理脑协议。"""
    async def stream_think(self, request: DeepBrainRequest) -> AsyncIterator[DeepBrainChunk]: ...


@dataclass(slots=True)
class ModelConfig:
    api_key: str
    base_url: str
    fast_model: str = "gpt-5.4-nano"
    deep_model: str = "gpt-5.4-mini"
    app_name: str = "task-agent"
    role_prompt: str = DEFAULT_ROLE_PROMPT
    timeout_seconds: float = 45.0
    acknowledge_max_tokens: int = 80
    fast_max_tokens: int = 800
    deep_max_tokens: int = 1200
    temperature: float = 0.1

    @classmethod
    def from_env(cls) -> ModelConfig:
        load_project_env()
        api_key = os.getenv("TASK_AGENT_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("Missing TASK_AGENT_MODEL_API_KEY or OPENAI_API_KEY for task-agent model access")
        base_url = os.getenv("TASK_AGENT_MODEL_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://litellm.mybigai.ac.cn/"
        role_prompt = os.getenv("TASK_AGENT_ROLE_PROMPT") or DEFAULT_ROLE_PROMPT
        config = cls(
            api_key=api_key,
            base_url=base_url,
            fast_model=os.getenv("TASK_AGENT_FAST_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.4-nano")),
            deep_model=os.getenv("TASK_AGENT_DEEP_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.4-mini")),
            role_prompt=role_prompt,
        )
        info(_logger, "ModelConfig loaded", fast_model=config.fast_model, deep_model=config.deep_model, base_url=config.base_url)
        return config


class OpenAICompatibleClient:
    """OpenAI 兼容接口封装。

    当前同时支持普通补全、JSON 输出和流式文本输出。
    """
    def __init__(self, config: ModelConfig, tracer=None):
        if OpenAIAsyncOpenAI is None:
            raise RuntimeError("The 'openai' package is required for model-backed task-agent brains")
        self.config = config
        self.tracer = tracer
        langfuse_client_cls = _load_langfuse_async_client() if getattr(tracer, "enabled", False) else None
        client_cls = langfuse_client_cls or OpenAIAsyncOpenAI
        self._langfuse_enabled = langfuse_client_cls is not None
        self._client = client_cls(
            api_key=config.api_key,
            base_url=_normalize_base_url(config.base_url),
            timeout=config.timeout_seconds,
        )

    async def complete_text(self, *, model: str, system_prompt: str, user_prompt: str, max_tokens: int, request_name: str, metadata: dict[str, Any] | None = None, tags: list[str] | None = None) -> str:
        response = await self._client.chat.completions.create(
            model=model,
            temperature=self.config.temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **_request_kwargs(enabled=self._langfuse_enabled, request_name=request_name, metadata=metadata, tags=tags, model=model),
        )
        return _extract_content(response.choices[0].message.content)

    async def complete_json(self, *, model: str, system_prompt: str, user_prompt: str, max_tokens: int, request_name: str, metadata: dict[str, Any] | None = None, tags: list[str] | None = None) -> str:
        response = await self._client.chat.completions.create(
            model=model,
            temperature=self.config.temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **_request_kwargs(enabled=self._langfuse_enabled, request_name=request_name, metadata=metadata, tags=tags, model=model),
        )
        return _extract_content(response.choices[0].message.content)

    async def stream_text(self, *, model: str, system_prompt: str, user_prompt: str, max_tokens: int, request_name: str, metadata: dict[str, Any] | None = None, tags: list[str] | None = None) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=model,
            temperature=self.config.temperature,
            max_tokens=max_tokens,
            stream=True,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **_request_kwargs(enabled=self._langfuse_enabled, request_name=request_name, metadata=metadata, tags=tags, model=model),
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None)
            if text:
                yield _extract_content(text)


class ModelFastBrain:
    def __init__(self, client: OpenAICompatibleClient, config: ModelConfig):
        self.client = client
        self.config = config

    async def acknowledge(self, window, snapshot) -> str | None:
        text = await self.client.complete_text(
            model=self.config.fast_model,
            system_prompt=build_ack_prompt(self.config.role_prompt),
            user_prompt=f"Recent context:\n{_format_snapshot(snapshot)}\n\nCurrent event window:\n{_format_window(window)}\n",
            max_tokens=self.config.acknowledge_max_tokens,
            request_name="task-agent.fast-brain.ack",
            metadata={"window_id": window.window_id, "phase": "ack"},
            tags=["task-agent", "fast-brain", "ack"],
        )
        cleaned = text.strip()
        return None if not cleaned or cleaned.upper() == "NONE" else cleaned

    async def think(self, request: FastBrainRequest) -> AsyncIterator[FastBrainChunk]:
        """返回 fast brain 的结构化结果。

        当前 fast brain 仍然是单个结构化结果为主，而不是 token 级流式输出。
        """
        raw = await self.client.complete_json(
            model=self.config.fast_model,
            system_prompt=build_fast_prompt(self.config.role_prompt),
            user_prompt=f"Blackboard snapshot:\n{_format_snapshot(request.snapshot)}\n\nEvent window to interpret:\n{_format_window(request.window)}\n",
            max_tokens=self.config.fast_max_tokens,
            request_name="task-agent.fast-brain.reason",
            metadata={"window_id": request.window.window_id, "generation": request.generation, "phase": "fast_reasoning"},
            tags=["task-agent", "fast-brain", "reason"],
        )
        yield FastBrainChunk(kind="result", result=parse_fast_brain_result(raw))

    def react_to_deep_chunk(self, chunk: DeepBrainChunk, snapshot) -> ChatMessage | None:
        kind_map = {
            DeepChunkKind.MILESTONE: ChatMessageKind.PROGRESS,
            DeepChunkKind.STAGE_TASK: ChatMessageKind.STAGE_RESULT,
            DeepChunkKind.WARNING: ChatMessageKind.BLOCKER,
            DeepChunkKind.FINAL_SUMMARY: ChatMessageKind.FINAL,
        }
        if chunk.kind not in kind_map or not chunk.message:
            return None
        return ChatMessage(kind=kind_map[chunk.kind], text=chunk.message, generation=snapshot.processing.generation, task_id=chunk.task.task_id if chunk.task else None)


class ModelDeepBrain:
    def __init__(self, client: OpenAICompatibleClient, config: ModelConfig):
        self.client = client
        self.config = config

    async def stream_think(self, request: DeepBrainRequest) -> AsyncIterator[DeepBrainChunk]:
        """流式执行 deep brain，并把标签化文本解析成 chunk。"""
        parser = TaggedStreamParser(intent_id=request.intent.intent_id)
        async for piece in self.client.stream_text(
            model=self.config.deep_model,
            system_prompt=build_deep_stream_prompt(self.config.role_prompt),
            user_prompt=(
                f"Current intent:\nintent_id={request.intent.intent_id}\nsummary={request.intent.summary}\n\n"
                f"Blackboard snapshot:\n{_format_snapshot(request.snapshot)}\n\n"
                f"Current event window:\n{_format_window(request.window)}\n"
            ),
            max_tokens=self.config.deep_max_tokens,
            request_name="task-agent.deep-brain.stream",
            metadata={"window_id": request.window.window_id, "generation": request.generation, "intent_id": request.intent.intent_id, "phase": "deep_stream"},
            tags=["task-agent", "deep-brain", "stream"],
        ):
            for chunk in parser.feed(piece):
                yield chunk
        for chunk in parser.finish():
            yield chunk


class HeuristicFastBrain:
    def acknowledge(self, window, snapshot) -> str | None:
        return None

    async def think(self, request: FastBrainRequest) -> AsyncIterator[FastBrainChunk]:
        yield FastBrainChunk(kind="result", result=FastBrainTurnResult(response_text="Model-backed fast brain is not configured."))

    def react_to_deep_chunk(self, chunk: DeepBrainChunk, snapshot) -> ChatMessage | None:
        return None


class HeuristicDeepBrain:
    async def stream_think(self, request: DeepBrainRequest) -> AsyncIterator[DeepBrainChunk]:
        yield DeepBrainChunk(kind=DeepChunkKind.FINAL_SUMMARY, message="Deep planning is not configured.")


class TaggedStreamParser:
    """把 deep brain 的标签化文本流切成结构化 chunk。"""
    TAGS = {
        "reasoning": DeepChunkKind.REASONING,
        "milestone": DeepChunkKind.MILESTONE,
        "stage_task": DeepChunkKind.STAGE_TASK,
        "warning": DeepChunkKind.WARNING,
        "final_summary": DeepChunkKind.FINAL_SUMMARY,
    }

    def __init__(self, *, intent_id: str):
        self.intent_id = intent_id
        self.buffer = ""
        self.saw_structured_output = False

    def feed(self, text: str) -> list[DeepBrainChunk]:
        self.buffer += text
        chunks: list[DeepBrainChunk] = []
        while True:
            parsed = self._extract_next()
            if parsed is None:
                break
            chunks.append(parsed)
        return chunks

    def finish(self) -> list[DeepBrainChunk]:
        """处理流结束后的兜底文本。"""
        remaining = self.buffer.strip()
        if remaining and not self.saw_structured_output:
            return [DeepBrainChunk(kind=DeepChunkKind.FINAL_SUMMARY, message=remaining)]
        return []

    def _extract_next(self) -> DeepBrainChunk | None:
        first_match: tuple[int, str] | None = None
        for tag in self.TAGS:
            idx = self.buffer.find(f"<{tag}>")
            if idx != -1 and (first_match is None or idx < first_match[0]):
                first_match = (idx, tag)
        if first_match is None:
            return None
        start, tag = first_match
        close = f"</{tag}>"
        close_idx = self.buffer.find(close, start)
        if close_idx == -1:
            return None
        open_tag = f"<{tag}>"
        inner_start = start + len(open_tag)
        inner = self.buffer[inner_start:close_idx].strip()
        self.buffer = self.buffer[close_idx + len(close):]
        self.saw_structured_output = True
        kind = self.TAGS[tag]
        if kind == DeepChunkKind.STAGE_TASK:
            task = _build_task(_load_json_object(inner), intent_id=self.intent_id)
            return DeepBrainChunk(kind=kind, message=task.goal, task=task)
        return DeepBrainChunk(kind=kind, message=inner)


def parse_fast_brain_result(raw_text: str) -> FastBrainTurnResult:
    payload = _load_json_object(raw_text)
    task_payload = payload.get("task")
    return FastBrainTurnResult(
        intent_summary=_optional_string(payload.get("intent_summary")),
        relation=_parse_intent_relation(payload.get("relation")),
        response_text=_optional_string(payload.get("response_text")),
        task=_build_task(task_payload, intent_id="") if isinstance(task_payload, dict) else None,
        delegate_to_deep=bool(payload.get("delegate_to_deep", False)),
        delegation_message=_optional_string(payload.get("delegation_message")),
    )


def _load_langfuse_async_client():
    try:
        from langfuse.openai import AsyncOpenAI as LangfuseAsyncOpenAI
    except ImportError:  # pragma: no cover
        return None
    return LangfuseAsyncOpenAI


def _normalize_base_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        return trimmed
    return f"{trimmed}/v1"


def _reasoning_kwargs_for_model(model: str) -> dict[str, Any]:
    normalized = model.lower()
    if normalized.startswith("gpt") or normalized.startswith("chatgpt"):
        return {"reasoning_effort": "none"}
    return {}


def _request_kwargs(*, enabled: bool, request_name: str, metadata: dict[str, Any] | None, tags: list[str] | None, model: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    kwargs.update(_reasoning_kwargs_for_model(model))
    if enabled:
        merged_metadata = dict(metadata or {})
        if tags:
            merged_metadata["langfuse_tags"] = tags
        kwargs.update({"name": request_name, "metadata": merged_metadata})
    return kwargs


def _extract_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content).strip()
    return str(content).strip()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_intent_relation(value: Any) -> IntentRelation:
    try:
        return IntentRelation(str(value).strip().lower())
    except ValueError:
        return IntentRelation.NOOP


def _parse_task_priority(value: Any) -> TaskPriority:
    try:
        return TaskPriority(str(value).strip().lower())
    except ValueError:
        return TaskPriority.NORMAL


def _parse_task_status(value: Any) -> TaskStatus:
    try:
        return TaskStatus(str(value).strip().lower())
    except ValueError:
        return TaskStatus.DRAFT


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    return [str(item).strip() for item in value if str(item).strip()]


def _build_task(payload: dict[str, Any], *, intent_id: str) -> TaskGoalCard:
    return TaskGoalCard(
        task_id=str(payload.get("task_id") or make_id("task")),
        intent_id=intent_id,
        goal=str(payload.get("goal", "")).strip() or "Follow the latest intent",
        context_summary=str(payload.get("context_summary", "")).strip() or "Generated from model output.",
        constraints=_coerce_string_list(payload.get("constraints")),
        priority=_parse_task_priority(payload.get("priority")),
        completion_criteria=_coerce_string_list(payload.get("completion_criteria")),
        status=_parse_task_status(payload.get("status")),
        parent_task_id=_optional_string(payload.get("parent_task_id")),
        root_intent_id=intent_id,
        stage_index=int(payload.get("stage_index", 0) or 0),
        stage_label=_optional_string(payload.get("stage_label")),
        is_final=bool(payload.get("is_final", False)),
    )


def _load_json_object(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"Model did not return a JSON object: {raw_text}") from None
        payload = json.loads(text[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError(f"Model response must decode to an object: {raw_text}")
    return payload


def _format_window(window) -> str:
    lines: list[str] = []
    for event in window.events:
        payload = event.payload
        if hasattr(payload, "text"):
            body = payload.text
        elif hasattr(payload, "feedback_text"):
            body = payload.feedback_text
        elif hasattr(payload, "content"):
            body = payload.content
        elif hasattr(payload, "action_name"):
            body = f"{payload.action_name}: {getattr(payload, 'details', '')}".strip()
        else:
            body = str(payload)
        lines.append(f"- {event.event_type.value}: {body}")
    return "\n".join(lines)


def _format_snapshot(snapshot) -> str:
    current_intent = snapshot.current_intent.summary if snapshot.current_intent else "none"
    tasks = ", ".join(f"{task.task_id}:{task.goal}:{task.status.value}" for task in snapshot.tasks.values()) or "none"
    summaries = " | ".join(summary.content for summary in snapshot.context_summaries[-3:]) or "none"
    return f"current_intent={current_intent}\nprocessing={snapshot.processing.phase.value}\ntasks={tasks}\nrecent_summaries={summaries}"
