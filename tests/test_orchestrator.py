"""Tests covering the orchestrator flow with mocked LLM calls."""

from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from miniverse.orchestrator import Orchestrator
from miniverse.schemas import (
    AgentAction,
    AgentMemory,
    AgentProfile,
    AgentStatus,
    EnvironmentState,
    ResourceState,
    Stat,
    WorldState,
)
from miniverse.simulation_rules import SimulationRules
from miniverse.memory import MemoryStrategy
from miniverse.cognition import AgentCognition, Scratchpad
from miniverse.cognition.llm import LLMExecutor


class DummyRules(SimulationRules):
    """Minimal deterministic rules used for testing."""

    def __init__(self):
        self.ticks_applied = []

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        self.ticks_applied.append(tick)
        updated = state.model_copy(deep=True)
        updated.metadata["last_tick"] = tick
        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        return True


class StopAfterRules(DummyRules):
    """Rules that request early termination after a specific tick."""

    def __init__(self, stop_after: int):
        super().__init__()
        self.stop_after = stop_after

    def should_stop(self, state: WorldState, tick: int) -> bool:
        return tick >= self.stop_after


class RecordingMemory(MemoryStrategy):
    """Simple memory implementation that records writes for assertions."""

    def __init__(self):
        self.records: list[AgentMemory] = []

    async def initialize(self) -> None:  # pragma: no cover - trivial
        pass

    async def close(self) -> None:  # pragma: no cover - trivial
        pass

    async def add_memory(
        self,
        run_id,
        agent_id,
        tick,
        memory_type,
        content,
        importance=5,
        **kwargs,
    ):
        memory = AgentMemory(
            id=uuid4(),
            run_id=run_id,
            agent_id=agent_id,
            tick=tick,
            memory_type=memory_type,
            content=content,
            importance=importance,
            tags=kwargs.get("tags", []),
            metadata=kwargs.get("metadata", {}),
            embedding_key=kwargs.get("embedding_key"),
            branch_id=kwargs.get("branch_id"),
            created_at=datetime.now(timezone.utc),
        )
        self.records.append(memory)
        return memory

    async def get_recent_memories(self, run_id, agent_id, limit=10):
        return [mem.content for mem in self.records if mem.agent_id == agent_id][-limit:]

    async def get_relevant_memories(self, run_id, agent_id, query, limit=5):  # pragma: no cover - unused
        return []

    async def clear_agent_memories(self, run_id, agent_id):  # pragma: no cover - unused
        self.records = [record for record in self.records if record.agent_id != agent_id]


def test_resolve_agent_id_normalizes_name_variants():
    world_state = WorldState(
        tick=0,
        timestamp=datetime(2160, 3, 21, 10, 0, 0),
        environment=EnvironmentState(metrics={}),
        resources=ResourceState(metrics={}),
        agents=[
            AgentStatus(
                agent_id="lead",
                display_name="Morgan Reyes",
                role="lead",
                location="ops",
                activity=None,
                attributes={},
            )
        ],
        metadata={},
    )
    profile = AgentProfile(
        agent_id="lead",
        name="Morgan Reyes",
        age=33,
        background="Supervisor.",
        role="lead",
        personality="decisive",
        skills={},
        goals=[],
        relationships={},
    )
    orchestrator = Orchestrator(
        world_state=world_state,
        agents={"lead": profile},
        world_prompt="prompt",
        agent_prompts={"lead": "prompt"},
    )

    assert orchestrator._resolve_agent_id("lead") == "lead"
    assert orchestrator._resolve_agent_id("Morgan Reyes") == "lead"
    assert orchestrator._resolve_agent_id("morgan_reyes") == "lead"
    assert orchestrator._resolve_agent_id("MORGAN-REYES") == "lead"


@pytest.mark.asyncio
async def test_orchestrator_runs_single_tick(monkeypatch):
    world_state = WorldState(
        tick=0,
        timestamp=datetime(2160, 3, 21, 10, 0, 0),
        environment=EnvironmentState(
            metrics={"temperature": Stat(value=21.0, unit="°C", label="Ambient Temp")}
        ),
        resources=ResourceState(
            metrics={"power": Stat(value=110.0, unit="kWh", label="Battery Reserve")}
        ),
        agents=[
            AgentStatus(
                agent_id="alpha",
                display_name="Morgan Reyes",
                role="floor_lead",
                location="operations",
                activity=None,
                attributes={
                    "energy": Stat(value=80, unit="%", label="Energy"),
                    "stress": Stat(value=30, unit="%", label="Stress"),
                },
            )
        ],
        metadata={},
    )

    profile = AgentProfile(
        agent_id="alpha",
        name="Morgan Reyes",
        age=32,
        background="Workshop supervisor.",
        role="floor_lead",
        personality="driven",
        skills={"coaching": "expert"},
        goals=["Keep throughput high"],
        relationships={},
    )

    rules = DummyRules()
    memory = RecordingMemory()

    orchestrator = Orchestrator(
        world_state=world_state,
        agents={"alpha": profile},
        world_prompt="You are the workshop engine.",
        agent_prompts={"alpha": "You coordinate the workshop."},
        llm_provider="openai",
        llm_model="gpt-5-nano",
        simulation_rules=rules,
        memory=memory,
        agent_cognition={"alpha": AgentCognition(executor=LLMExecutor(template_name="default"))},
    )

    mocked_action = AgentAction(
        agent_id="alpha",
        tick=1,
        action_type="work",
        target="station_b",
        parameters=None,
        reasoning="Need to inspect station B",
        communication={"message": "Offer the extended warranty"},
    )
    mocked_state = world_state.model_copy(update={"tick": 1})

    # Patch LLMExecutor path to avoid real LLM calls
    get_action_mock = AsyncMock(return_value=mocked_action)
    world_update_mock = AsyncMock(return_value=mocked_state)

    # LLMExecutor uses call_llm_with_retries under miniverse.cognition.llm
    monkeypatch.setattr("miniverse.cognition.llm.call_llm_with_retries", get_action_mock)
    monkeypatch.setattr("miniverse.orchestrator.process_world_update", world_update_mock)

    result = await orchestrator.run(num_ticks=1)

    assert result["final_state"].tick == 1
    assert rules.ticks_applied == [1]
    get_action_mock.assert_awaited()
    world_update_mock.assert_awaited()
    assert any("Need to inspect" in record.content for record in memory.records)
    assert any(record.tags and "communication" in record.tags for record in memory.records)

    # A4: Verify action communication is sanitized on persistence (no message body)
    actions = await orchestrator.persistence.get_actions(orchestrator.run_id, 1)
    for act in actions:
        if act.action_type == "work":
            continue
        if act.communication is not None:
            assert isinstance(act.communication, dict)
            assert "to" in act.communication
            assert "message" not in act.communication


@pytest.mark.asyncio
async def test_orchestrator_stops_when_rules_signal(monkeypatch):
    world_state = WorldState(
        tick=0,
        timestamp=datetime(2160, 3, 21, 10, 0, 0),
        environment=EnvironmentState(metrics={}),
        resources=ResourceState(metrics={"power": Stat(value=100, unit="kWh")}),
        agents=[
            AgentStatus(
                agent_id="alpha",
                display_name="Morgan Reyes",
                role="floor_lead",
                location="operations",
                activity=None,
                attributes={},
            )
        ],
        metadata={},
    )

    profile = AgentProfile(
        agent_id="alpha",
        name="Morgan Reyes",
        age=32,
        background="Workshop supervisor.",
        role="floor_lead",
        personality="driven",
        skills={},
        goals=[],
        relationships={},
    )

    rules = StopAfterRules(stop_after=2)
    memory = RecordingMemory()

    orchestrator = Orchestrator(
        world_state=world_state,
        agents={"alpha": profile},
        world_prompt="",
        agent_prompts={"alpha": ""},
        llm_provider="openai",
        llm_model="gpt-5-nano",
        simulation_rules=rules,
        memory=memory,
        agent_cognition={"alpha": AgentCognition(executor=LLMExecutor(template_name="default"))},
    )

    mocked_action = AgentAction(
        agent_id="alpha",
        tick=1,
        action_type="work",
        target=None,
        parameters=None,
        reasoning="",
        communication=None,
    )

    action_mock = AsyncMock(return_value=mocked_action)
    state_tick_1 = world_state.model_copy(update={"tick": 1})
    state_tick_2 = world_state.model_copy(update={"tick": 2})
    world_update_mock = AsyncMock(side_effect=[state_tick_1, state_tick_2])

    monkeypatch.setattr("miniverse.cognition.llm.call_llm_with_retries", action_mock)
    monkeypatch.setattr("miniverse.orchestrator.process_world_update", world_update_mock)

    result = await orchestrator.run(num_ticks=5)

    assert result["final_state"].tick == 2
    assert rules.ticks_applied == [1, 2]
    assert action_mock.await_count == 2
    assert world_update_mock.await_count == 2


def _sidecar_runtime_metadata() -> dict:
    return {
        "runtime": {
            "cognition": {
                "kwargs": {"communication_mode": "sidecar"},
            }
        }
    }


@pytest.mark.asyncio
async def test_sidecar_budget_drops_non_work_sidecar_messages():
    world_state = WorldState(
        tick=0,
        timestamp=datetime(2160, 3, 21, 10, 0, 0),
        environment=EnvironmentState(metrics={}),
        resources=ResourceState(metrics={}),
        agents=[
            AgentStatus(
                agent_id="lead",
                display_name="Morgan Reyes",
                role="lead",
                location="ops",
                activity=None,
                attributes={},
            )
        ],
        metadata=_sidecar_runtime_metadata(),
    )
    profile = AgentProfile(
        agent_id="lead",
        name="Morgan Reyes",
        age=33,
        background="Supervisor.",
        role="lead",
        personality="decisive",
        skills={},
        goals=[],
        relationships={},
    )
    cognition = AgentCognition(
        executor=LLMExecutor(template_name="default"),
        scratchpad=Scratchpad(),
    )
    orchestrator = Orchestrator(
        world_state=world_state,
        agents={"lead": profile},
        world_prompt="prompt",
        agent_prompts={"lead": "prompt"},
        agent_cognition={"lead": cognition},
    )

    action = AgentAction(
        agent_id="lead",
        tick=1,
        action_type="analyze",
        target="task_backlog",
        parameters={},
        reasoning="Triage blockers",
        communication={"to": "lead", "message": "Stand-up now"},
    )
    orchestrator._apply_sidecar_budget(
        tick=1,
        agent_id="lead",
        cognition=cognition,
        action=action,
    )
    assert action.communication is None


@pytest.mark.asyncio
async def test_sidecar_work_stores_recipient_memory_without_sender_copy():
    world_state = WorldState(
        tick=0,
        timestamp=datetime(2160, 3, 21, 10, 0, 0),
        environment=EnvironmentState(metrics={}),
        resources=ResourceState(metrics={}),
        agents=[
            AgentStatus(
                agent_id="lead",
                display_name="Morgan Reyes",
                role="lead",
                location="ops",
                activity=None,
                attributes={},
            ),
            AgentStatus(
                agent_id="tech",
                display_name="Lin Zhao",
                role="technician",
                location="workbench",
                activity=None,
                attributes={},
            ),
        ],
        metadata=_sidecar_runtime_metadata(),
    )
    profiles = {
        "lead": AgentProfile(
            agent_id="lead",
            name="Morgan Reyes",
            age=33,
            background="Supervisor.",
            role="lead",
            personality="decisive",
            skills={},
            goals=[],
            relationships={},
        ),
        "tech": AgentProfile(
            agent_id="tech",
            name="Lin Zhao",
            age=29,
            background="Technician.",
            role="technician",
            personality="methodical",
            skills={},
            goals=[],
            relationships={},
        ),
    }
    memory = RecordingMemory()
    orchestrator = Orchestrator(
        world_state=world_state,
        agents=profiles,
        world_prompt="prompt",
        agent_prompts={"lead": "prompt", "tech": "prompt"},
        memory=memory,
    )

    action = AgentAction(
        agent_id="lead",
        tick=1,
        action_type="work",
        target="ticket_t1",
        parameters={"intensity": "normal"},
        reasoning="Execute top ticket",
        communication={"to": "tech", "message": "Starting T1, verify parts."},
    )
    await orchestrator._update_memories(1, [action], world_state)

    sender_comm = [
        m for m in memory.records if m.memory_type == "communication" and m.agent_id == "lead"
    ]
    recipient_comm = [
        m for m in memory.records if m.memory_type == "communication" and m.agent_id == "tech"
    ]
    assert sender_comm == []
    assert any((m.metadata or {}).get("role") == "recipient" for m in recipient_comm)
