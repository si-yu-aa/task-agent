import json

import pytest

from task_agent.brains import ModelConfig, TaggedStreamParser, _reasoning_kwargs_for_model, parse_fast_brain_result
from task_agent.types import DeepChunkKind, IntentRelation, TaskPriority, TaskStatus


def test_parse_fast_brain_result_handles_direct_task_creation():
    payload = {
        "intent_summary": "Go outside and find dinner.",
        "relation": "new",
        "response_text": "I can turn that into a task right away.",
        "delegate_to_deep": False,
        "delegation_message": None,
        "task": {
            "goal": "Go outside and find dinner",
            "context_summary": "User wants a dinner-finding task.",
            "constraints": ["Do not lose the current intent"],
            "priority": "normal",
            "completion_criteria": ["Action agent can begin execution immediately"],
            "status": "active",
        },
    }

    result = parse_fast_brain_result(json.dumps(payload))

    assert result.intent_summary == "Go outside and find dinner."
    assert result.relation == IntentRelation.NEW
    assert result.response_text == "I can turn that into a task right away."
    assert result.task is not None
    assert result.task.goal == "Go outside and find dinner"
    assert result.task.priority == TaskPriority.NORMAL
    assert result.task.status == TaskStatus.ACTIVE


def test_parse_fast_brain_result_coerces_single_string_lists():
    payload = {
        "intent_summary": "Dinner task",
        "relation": "new",
        "response_text": "ok",
        "delegate_to_deep": False,
        "task": {
            "goal": "Find dinner",
            "context_summary": "Need dinner",
            "constraints": "Stay nearby",
            "priority": "normal",
            "completion_criteria": "Pick one restaurant",
            "status": "active",
        },
    }

    result = parse_fast_brain_result(json.dumps(payload))

    assert result.task is not None
    assert result.task.constraints == ["Stay nearby"]
    assert result.task.completion_criteria == ["Pick one restaurant"]


def test_tagged_stream_parser_emits_stage_task_and_final_summary():
    parser = TaggedStreamParser(intent_id="intent-1")
    chunks = parser.feed(
        "<reasoning>thinking</reasoning><stage_task>{\"goal\": \"Pick up paper ball\", \"context_summary\": \"first step\", \"constraints\": [], \"priority\": \"high\", \"completion_criteria\": [\"paper ball removed\"], \"status\": \"draft\", \"stage_index\": 1}</stage_task><final_summary>done</final_summary>"
    )

    assert [chunk.kind for chunk in chunks] == [
        DeepChunkKind.REASONING,
        DeepChunkKind.STAGE_TASK,
        DeepChunkKind.FINAL_SUMMARY,
    ]
    assert chunks[1].task is not None
    assert chunks[1].task.goal == "Pick up paper ball"
    assert chunks[2].message == "done"


def test_model_config_from_env_requires_api_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TASK_AGENT_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("TASK_AGENT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("TASK_AGENT_FAST_MODEL", raising=False)
    monkeypatch.delenv("TASK_AGENT_DEEP_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    with pytest.raises(ValueError):
        ModelConfig.from_env()


def test_reasoning_effort_is_disabled_for_chatgpt_family_models():
    assert _reasoning_kwargs_for_model("gpt-5.4-mini") == {"reasoning_effort": "none"}
    assert _reasoning_kwargs_for_model("not-gpt") == {}
