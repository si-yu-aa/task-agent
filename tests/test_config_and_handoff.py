from pathlib import Path

import pytest

from task_agent.brains import ModelConfig
from task_agent.handoff import build_action_agent_handoff, handoff_to_payload
from task_agent.types import TaskGoalCard, TaskPriority, TaskStatus


def test_model_config_loads_from_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / '.env'
    env_file.write_text(
        '\n'.join([
            'TASK_AGENT_MODEL_API_KEY=dotenv-key',
            'TASK_AGENT_MODEL_BASE_URL=https://litellm.example.com/',
            'TASK_AGENT_FAST_MODEL=gpt-fast',
            'TASK_AGENT_DEEP_MODEL=gpt-deep',
        ]),
        encoding='utf-8',
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('TASK_AGENT_MODEL_API_KEY', raising=False)
    monkeypatch.delenv('TASK_AGENT_MODEL_BASE_URL', raising=False)
    monkeypatch.delenv('TASK_AGENT_FAST_MODEL', raising=False)
    monkeypatch.delenv('TASK_AGENT_DEEP_MODEL', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)

    config = ModelConfig.from_env()

    assert config.api_key == 'dotenv-key'
    assert config.base_url == 'https://litellm.example.com/'
    assert config.fast_model == 'gpt-fast'
    assert config.deep_model == 'gpt-deep'


def test_action_agent_handoff_payload_contains_goal_card_contract():
    task = TaskGoalCard(
        task_id='task-1',
        intent_id='intent-1',
        goal='Find dinner nearby',
        context_summary='Need a nearby dinner plan.',
        constraints=['Stay within walking distance'],
        priority=TaskPriority.HIGH,
        completion_criteria=['Pick one dinner option'],
        status=TaskStatus.ACTIVE,
    )

    handoff = build_action_agent_handoff(
        session_id='session-1',
        window_id='window-1',
        generation=3,
        task=task,
        source='fast_brain',
    )
    payload = handoff_to_payload(handoff)

    assert payload['schema_version'] == 'task-agent.action-handoff.v1'
    assert payload['task']['task_id'] == 'task-1'
    assert payload['task']['goal'] == 'Find dinner nearby'
    assert payload['dispatch']['source'] == 'fast_brain'
    assert payload['dispatch']['generation'] == 3
