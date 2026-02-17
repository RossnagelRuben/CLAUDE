"""Helpers for discovering and loading scenario files.

Scenarios support JSON and YAML for readability while preserving backwards
compatibility with existing JSON files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
except Exception:  # pragma: no cover - import guard for minimal environments
    yaml = None


SCENARIO_EXTENSIONS: tuple[str, ...] = (".yaml", ".yml", ".json")


def is_supported_scenario_file(path: Path) -> bool:
    """Return whether a file path has a supported scenario extension."""
    return path.suffix.lower() in SCENARIO_EXTENSIONS


def find_named_scenario_file(directory: Path, scenario_name: str) -> Optional[Path]:
    """Find `scenario_name` with a supported extension (YAML-first)."""
    for extension in SCENARIO_EXTENSIONS:
        candidate = directory / f"{scenario_name}{extension}"
        if candidate.exists():
            return candidate
    return None


def resolve_scenario_file(directory: Path, scenario_name: str) -> Path:
    """Resolve scenario file path from a directory and name or filename."""
    direct_path = directory / scenario_name
    if direct_path.exists() and is_supported_scenario_file(direct_path):
        return direct_path

    requested = Path(scenario_name)
    if requested.suffix.lower() in SCENARIO_EXTENSIONS:
        # Compatibility: if caller requests scenario.json but only scenario.yaml
        # exists, resolve by base name.
        requested_name = requested.stem
    else:
        requested_name = scenario_name

    resolved = find_named_scenario_file(directory, requested_name)
    if resolved is not None:
        return resolved

    supported = ", ".join(SCENARIO_EXTENSIONS)
    raise FileNotFoundError(
        f"Scenario '{scenario_name}' not found in {directory}. "
        f"Expected one of: {requested_name}{{{supported}}}"
    )


def list_scenario_names(directory: Path) -> list[str]:
    """List scenario base names for all supported extensions in a directory."""
    if not directory.exists():
        return []

    names: set[str] = set()
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if path.name.startswith("_"):
            continue
        if is_supported_scenario_file(path):
            names.add(path.stem)
    return sorted(names)


def load_structured_data_file(path: Path) -> Dict[str, Any]:
    """Load JSON or YAML into a dictionary."""
    suffix = path.suffix.lower()
    raw_text = path.read_text()

    if suffix == ".json":
        data = json.loads(raw_text)
    elif suffix in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError(
                "YAML scenario loading requires PyYAML. Install dependency: pyyaml"
            )
        data = yaml.safe_load(raw_text)
    else:
        raise ValueError(
            f"Unsupported file format for {path}. Expected one of: {SCENARIO_EXTENSIONS}"
        )

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Scenario file must contain a top-level mapping: {path}")
    return data
