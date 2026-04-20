"""Simple CLI for task-agent."""

from __future__ import annotations

import asyncio

from task_agent.service import TaskAgentService
from task_agent.types import EventEnvelope, EventType, NlpMessagePayload, make_id


async def main() -> None:
    service = TaskAgentService()
    session = service.get_session("default")
    print("task-agent demo. type empty line to quit.")
    try:
        while True:
            raw = input("> ").strip()
            if not raw:
                break
            event = EventEnvelope(
                event_id=make_id("event"),
                event_type=EventType.NLP_MESSAGE,
                payload=NlpMessagePayload(speaker="user", text=raw),
            )
            await session.submit_window([event])
            while True:
                try:
                    message = await session.next_chat_message(timeout=0.2)
                except TimeoutError:
                    break
                print(f"[{message.kind.value}] {message.text}")
    finally:
        service.flush()


if __name__ == "__main__":
    asyncio.run(main())
