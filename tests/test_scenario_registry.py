"""Tests for scenario discovery and named resolution."""

from pathlib import Path

from miniverse.scenario_registry import discover_scenarios, resolve_scenario_entry


def test_discover_scenarios_includes_demo_workshop():
    scenarios = discover_scenarios()
    ids = {entry.scenario_id for entry in scenarios}
    assert "demo/workshop" in ids


def test_resolve_named_workshop_prefers_demo():
    entry = resolve_scenario_entry("workshop")
    assert "demo/workshop" in entry.scenario_id
    assert entry.scenario_file.name.startswith("scenario.")


def test_resolve_explicit_scenario_path():
    path = Path("demo/workshop/scenario.yaml").resolve()
    entry = resolve_scenario_entry(str(path))
    assert entry.scenario_file == path
    assert entry.source == "path"
