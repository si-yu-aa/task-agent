"""Langfuse-backed tracing for task-agent."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
from typing import Any

from task_agent.env import load_project_env

try:
    from langfuse import get_client, propagate_attributes
except ImportError:  # pragma: no cover
    get_client = None
    propagate_attributes = None


class NoOpObservation:
    def update(self, **kwargs) -> None:
        return None


class NoOpTracer:
    enabled = False

    @contextmanager
    def observation(
        self,
        *,
        name: str,
        as_type: str = "span",
        input: Any | None = None,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        level: str | None = None,
        model: str | None = None,
    ):
        yield NoOpObservation()

    def flush(self) -> None:
        return None


class LangfuseTracer(NoOpTracer):
    enabled = True

    def __init__(self):
        if get_client is None or propagate_attributes is None:
            raise RuntimeError("langfuse package is not available")
        self._client = get_client()

    @contextmanager
    def observation(
        self,
        *,
        name: str,
        as_type: str = "span",
        input: Any | None = None,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        level: str | None = None,
        model: str | None = None,
    ):
        propagation: dict[str, Any] = {}
        if tags:
            propagation["tags"] = [tag for tag in tags if tag]
        if session_id:
            propagation["session_id"] = session_id
        if user_id:
            propagation["user_id"] = user_id
        if metadata:
            propagation["metadata"] = _stringify_metadata(_sanitize_metadata(metadata))

        with self._client.start_as_current_observation(
            name=name,
            as_type=as_type,
            input=input,
            output=output,
            metadata=_sanitize_metadata(metadata),
            level=level,
            model=model,
        ) as obs:
            if propagation:
                with propagate_attributes(**propagation):
                    yield obs
            else:
                yield obs

    def flush(self) -> None:
        self._client.flush()


def build_tracer_from_env(start_dir: str | Path | None = None):
    load_project_env(start_dir=start_dir)
    if os.getenv("PYTEST_CURRENT_TEST"):
        return NoOpTracer()
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    if not public_key or not secret_key or get_client is None:
        return NoOpTracer()
    return LangfuseTracer()


def _stringify_metadata(metadata: dict[str, Any] | None) -> dict[str, str] | None:
    if metadata is None:
        return None
    return {key: str(value) for key, value in metadata.items()}


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if metadata is None:
        return None
    cleaned: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
        else:
            cleaned[key] = str(value)
    return cleaned
