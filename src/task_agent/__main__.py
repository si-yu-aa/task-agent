"""task-agent 的简单命令行入口。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import sys
from pathlib import Path

from task_agent.service import TaskAgentService
from task_agent.types import EventEnvelope, EventType, NlpMessagePayload, make_id

# 静默 langfuse 的 stderr 警告（它会 print 到控制台）
_langfuse_err = Path("task-agent-langfuse.log").open("a")
sys.stderr = _langfuse_err


async def consume_chat(session) -> None:
    """持续消费 chat 队列，并把对外消息打印到终端。"""
    while True:
        try:
            message = await session.next_chat_message(timeout=1.0)
        except TimeoutError:
            continue
        print(f"[{message.kind.value}] {message.text}")


async def main() -> None:
    service = TaskAgentService()
    session = service.get_session("default")
    consumer_task = asyncio.create_task(consume_chat(session))
    print("task-agent demo. type empty line to quit.")
    try:
        while True:
            raw = (await asyncio.to_thread(input, "> ")).strip()
            if not raw:
                break
            event = EventEnvelope(
                event_id=make_id("event"),
                event_type=EventType.NLP_MESSAGE,
                payload=NlpMessagePayload(speaker="user", text=raw),
            )
            await session.submit_window([event])
    finally:
        consumer_task.cancel()
        with suppress(asyncio.CancelledError):
            await consumer_task
        service.flush()
        _langfuse_err.close()
        sys.stderr = sys.__stderr__


if __name__ == "__main__":
    asyncio.run(main())
