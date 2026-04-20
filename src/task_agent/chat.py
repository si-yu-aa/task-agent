"""chat 输出边界。

这里约束了当前系统真正对外可见的消息出口。
如果后面要接真实聊天工具，只需要实现 `ChatAdapter`。
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from task_agent.types import ChatMessage


class ChatAdapter(Protocol):
    """对外聊天适配器协议。"""
    async def send(self, message: ChatMessage) -> None: ...


class QueueChatAdapter:
    """基于内存队列的简单 chat 适配器。

    主要用于本地调试、CLI 和测试。
    """
    def __init__(self) -> None:
        self._queue: asyncio.Queue[ChatMessage] = asyncio.Queue()

    async def send(self, message: ChatMessage) -> None:
        await self._queue.put(message)

    async def next_message(self, timeout: float = 1.0) -> ChatMessage:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("No chat message arrived before timeout") from exc
