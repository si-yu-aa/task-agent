"""Minimal logging helpers for task-agent."""

from __future__ import annotations

import logging
import os
import sys
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

_log_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})


def set_log_context(**kwargs) -> None:
    current = _log_context.get()
    _log_context.set({**current, **kwargs})


def clear_log_context() -> None:
    _log_context.set({})


def get_log_context() -> dict[str, Any]:
    return deepcopy(_log_context.get())


def _get_level() -> int:
    level_str = os.getenv("TASK_AGENT_LOG_LEVEL", "").strip().upper()
    if level_str:
        return getattr(logging, level_str, logging.INFO)
    if os.getenv("TASK_AGENT_LOG_DEBUG", "").strip() == "1":
        return logging.DEBUG
    return logging.INFO


def _configure_root_logger() -> None:
    root = logging.getLogger("task_agent")
    if root.handlers:
        return
    level = _get_level()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    _configure_root_logger()
    return logging.getLogger(f"task_agent.{name}")


def get_session_logger() -> logging.Logger:
    return get_logger("session")


def get_blackboard_logger() -> logging.Logger:
    return get_logger("blackboard")


def get_brains_logger() -> logging.Logger:
    return get_logger("brains")


def get_handoff_logger() -> logging.Logger:
    return get_logger("handoff")


def get_service_logger() -> logging.Logger:
    return get_logger("service")


def get_tracing_logger() -> logging.Logger:
    return get_logger("tracing")


def _merge_extra(**extra: Any) -> dict[str, Any]:
    payload = get_log_context()
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def debug(logger: logging.Logger, message: str, **kwargs) -> None:
    logger.debug(message, extra=_merge_extra(**kwargs))


def info(logger: logging.Logger, message: str, **kwargs) -> None:
    logger.info(message, extra=_merge_extra(**kwargs))


def warning(logger: logging.Logger, message: str, **kwargs) -> None:
    logger.warning(message, extra=_merge_extra(**kwargs))


def error(logger: logging.Logger, message: str, **kwargs) -> None:
    logger.error(message, extra=_merge_extra(**kwargs))


def exception(logger: logging.Logger, message: str, **kwargs) -> None:
    logger.exception(message, extra=_merge_extra(**kwargs))
