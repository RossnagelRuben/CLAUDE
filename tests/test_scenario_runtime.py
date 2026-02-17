"""Tests for scenario-local runtime extension loading."""

from pathlib import Path

from miniverse.scenario_runtime import (
    load_scenario_cognition,
    load_scenario_rules,
)
from miniverse.schemas import AgentProfile


def _make_profile(agent_id: str) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        name=f"Agent {agent_id}",
        age=30,
        background="Test profile",
        role="worker",
        personality="steady",
        skills={},
        goals=[],
        relationships={},
    )


def test_load_scenario_rules_with_runtime_config(tmp_path: Path) -> None:
    rules_py = tmp_path / "custom_rules.py"
    rules_py.write_text(
        "\n".join(
            [
                "from miniverse import SimulationRules, AgentAction, WorldState",
                "",
                "class CustomRules(SimulationRules):",
                "    def __init__(self, *, knob=0, rng=None):",
                "        self.knob = knob",
                "        self.rng = rng",
                "",
                "    def apply_tick(self, state: WorldState, tick: int) -> WorldState:",
                "        return state",
                "",
                "    def validate_action(self, action: AgentAction, state: WorldState) -> bool:",
                "        return True",
            ]
        )
    )

    rules = load_scenario_rules(
        tmp_path,
        seed=42,
        runtime={
            "rules": {
                "module": "custom_rules.py",
                "class": "CustomRules",
                "kwargs": {"knob": 7},
            }
        },
    )

    assert rules is not None
    assert type(rules).__name__ == "CustomRules"
    assert getattr(rules, "knob", None) == 7
    assert getattr(rules, "rng", None) is not None


def test_load_scenario_cognition_with_runtime_config(tmp_path: Path) -> None:
    cognition_py = tmp_path / "custom_cognition.py"
    cognition_py.write_text(
        "\n".join(
            [
                "from miniverse.cognition.runtime import build_default_cognition",
                "",
                "def build_custom_cognition(profiles, *, use_llm=False):",
                "    return {agent_id: build_default_cognition() for agent_id in profiles}",
            ]
        )
    )

    profiles = {"agent": _make_profile("agent")}
    cognition_map = load_scenario_cognition(
        tmp_path,
        profiles,
        runtime={
            "cognition": {
                "module": "custom_cognition.py",
                "builder": "build_custom_cognition",
            }
        },
    )

    assert set(cognition_map.keys()) == {"agent"}
