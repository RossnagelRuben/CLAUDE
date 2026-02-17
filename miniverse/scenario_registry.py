"""Scenario discovery and name resolution for CLI workflows.

Scenarios are discovered from user-facing roots (`demo/`, `examples/`) rather
than an internal templates package.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from .config import Config
from .scenario_files import is_supported_scenario_file, find_named_scenario_file


@dataclass(frozen=True)
class ScenarioEntry:
    """Resolved scenario record from discovery roots."""

    scenario_id: str
    rel_name: str
    short_name: str
    source: str
    scenario_file: Path

    @property
    def scenario_dir(self) -> Path:
        return self.scenario_file.parent

    @property
    def scenario_name(self) -> str:
        return self.scenario_file.stem


def default_scenario_roots() -> List[Path]:
    """Return roots scanned for named scenarios in priority order."""
    return [
        Config.PROJECT_ROOT / "demo",
        Config.PROJECT_ROOT / "examples",
    ]


def _iter_scenario_files(root: Path) -> Iterable[Path]:
    if not root.exists() or not root.is_dir():
        return []
    files: List[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("_"):
            continue
        if not is_supported_scenario_file(path):
            continue
        if path.stem != "scenario":
            continue
        files.append(path)
    return files


def discover_scenarios(roots: Optional[List[Path]] = None) -> List[ScenarioEntry]:
    """Discover scenario files under configured roots."""
    entries: List[ScenarioEntry] = []
    for root in roots or default_scenario_roots():
        source = root.name
        for scenario_file in _iter_scenario_files(root):
            rel_path = scenario_file.relative_to(root)
            if scenario_file.stem == "scenario":
                if rel_path.parent == Path("."):
                    rel_name = "scenario"
                else:
                    rel_name = rel_path.parent.as_posix()
                short_name = scenario_file.parent.name
            else:
                rel_name = rel_path.with_suffix("").as_posix()
                short_name = scenario_file.stem

            entries.append(
                ScenarioEntry(
                    scenario_id=f"{source}/{rel_name}",
                    rel_name=rel_name,
                    short_name=short_name,
                    source=source,
                    scenario_file=scenario_file,
                )
            )

    # Deterministic ordering by root priority then id.
    root_order = {root.name: idx for idx, root in enumerate(roots or default_scenario_roots())}
    entries.sort(key=lambda e: (root_order.get(e.source, 99), e.scenario_id))
    return entries


def resolve_scenario_entry(
    value: str,
    roots: Optional[List[Path]] = None,
) -> ScenarioEntry:
    """Resolve a user-provided scenario reference to a concrete scenario file.

    Resolution order:
    1) Explicit file path (`.../scenario.yaml`)
    2) Explicit directory path containing `scenario.*`
    3) Canonical ID (`demo/workshop`, `examples/workshop`)
    4) Relative name (`workshop`, `smallville/foo`) with root-priority tie-break
    """
    candidate = Path(value)
    if candidate.exists() and candidate.is_file() and is_supported_scenario_file(candidate):
        resolved = candidate.resolve()
        return ScenarioEntry(
            scenario_id=str(resolved),
            rel_name=resolved.stem,
            short_name=resolved.stem,
            source="path",
            scenario_file=resolved,
        )

    if candidate.exists() and candidate.is_dir():
        scenario_file = find_named_scenario_file(candidate, "scenario")
        if scenario_file is None:
            raise ValueError(
                f"Directory '{value}' does not contain scenario.yaml/.yml/.json"
            )
        resolved = scenario_file.resolve()
        return ScenarioEntry(
            scenario_id=str(resolved),
            rel_name=resolved.stem,
            short_name=resolved.parent.name,
            source="path",
            scenario_file=resolved,
        )

    entries = discover_scenarios(roots=roots)
    if not entries:
        raise ValueError("No scenarios found under demo/ or examples/.")

    # 1) Canonical ID exact match.
    id_matches = [entry for entry in entries if entry.scenario_id == value]
    if id_matches:
        return id_matches[0]

    # 2) Relative-name match (id without source prefix).
    rel_matches = [entry for entry in entries if entry.rel_name == value]
    if rel_matches:
        return rel_matches[0]

    # 3) Short-name match with root-priority fallback.
    short_matches = [entry for entry in entries if entry.short_name == value]
    if len(short_matches) == 1:
        return short_matches[0]
    if len(short_matches) > 1:
        # Root-priority deterministic fallback (demo before examples).
        return short_matches[0]

    available_preview = ", ".join(entry.scenario_id for entry in entries[:12])
    raise ValueError(
        f"Scenario '{value}' not found. Available examples: {available_preview}"
    )
