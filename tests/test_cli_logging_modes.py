"""Tests for CLI log mode selection behavior."""

import os

from miniverse.cli import (
    _build_agent_prompts,
    _configure_logging_environment,
    _select_log_mode,
)
from miniverse.schemas import AgentProfile


def test_deterministic_default_is_final_only():
    mode = _select_log_mode(
        use_llm=False,
        output_format="text",
        quiet=False,
        verbose=False,
        debug=False,
    )
    assert mode == "none"


def test_llm_default_is_concise():
    mode = _select_log_mode(
        use_llm=True,
        output_format="text",
        quiet=False,
        verbose=False,
        debug=False,
    )
    assert mode == "concise"


def test_verbose_or_debug_force_verbose_mode():
    assert (
        _select_log_mode(
            use_llm=False,
            output_format="text",
            quiet=False,
            verbose=True,
            debug=False,
        )
        == "verbose"
    )
    assert (
        _select_log_mode(
            use_llm=True,
            output_format="text",
            quiet=False,
            verbose=False,
            debug=True,
        )
        == "verbose"
    )


def test_json_or_quiet_force_none():
    assert (
        _select_log_mode(
            use_llm=True,
            output_format="json",
            quiet=False,
            verbose=True,
            debug=True,
        )
        == "none"
    )


def test_verbose_mode_clears_debug_env(monkeypatch):
    monkeypatch.setenv("DEBUG_LLM", "true")
    monkeypatch.setenv("DEBUG_MEMORY", "true")
    monkeypatch.setenv("DEBUG_PERCEPTION", "true")
    monkeypatch.setenv("MINIVERSE_VERBOSE", "true")

    _configure_logging_environment(debug=False, verbose=True)

    assert "DEBUG_LLM" not in os.environ
    assert "DEBUG_MEMORY" not in os.environ
    assert "DEBUG_PERCEPTION" not in os.environ
    assert os.environ.get("MINIVERSE_VERBOSE") == "true"


def test_debug_mode_sets_all_debug_env(monkeypatch):
    _configure_logging_environment(debug=True, verbose=False)

    env = os.environ
    assert env.get("DEBUG_LLM") == "true"
    assert env.get("DEBUG_MEMORY") == "true"
    assert env.get("DEBUG_PERCEPTION") == "true"
    assert env.get("MINIVERSE_VERBOSE") == "true"
    assert (
        _select_log_mode(
            use_llm=True,
            output_format="text",
            quiet=True,
            verbose=True,
            debug=True,
        )
        == "none"
    )


def test_build_agent_prompts_uses_scenario_overrides():
    profiles = {
        "alpha": AgentProfile(
            agent_id="alpha",
            name="Alpha",
            age=30,
            background="A",
            role="lead",
            personality="steady",
            skills={},
            goals=[],
            relationships={},
        ),
        "beta": AgentProfile(
            agent_id="beta",
            name="Beta",
            age=30,
            background="B",
            role="analyst",
            personality="steady",
            skills={},
            goals=[],
            relationships={},
        ),
    }
    scenario_data = {
        "metadata": {
            "agent_prompts": {
                "alpha": "Alpha prompt from metadata.",
            }
        },
        "agents": [
            {
                "profile": {"agent_id": "beta"},
                "status": {
                    "metadata": {
                        "initial_state_prompt": "Beta prompt from status metadata."
                    }
                },
            }
        ],
    }

    prompts = _build_agent_prompts(
        profiles_map=profiles,
        scenario_data=scenario_data,
    )

    assert prompts["alpha"] == "Alpha prompt from metadata."
    assert prompts["beta"] == "Beta prompt from status metadata."
