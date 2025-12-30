"""Template discovery and loading utilities.

Templates are self-contained simulation scenarios located in miniverse/templates/.
Each template directory contains:
- scenario.json: World state, agents, environment configuration
- rules.py: SimulationRules subclass for deterministic physics
- cognition.py (optional): Custom cognition modules
- README.md: Documentation
"""

from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from miniverse import AgentCognition, AgentProfile, SimulationRules

TEMPLATES_DIR = Path(__file__).parent


def list_templates() -> List[str]:
    """Return list of available template names.

    Scans the templates directory for subdirectories containing scenario.json.

    Returns:
        Sorted list of template names.
    """
    templates = []
    for path in TEMPLATES_DIR.iterdir():
        if path.is_dir() and not path.name.startswith("_"):
            if (path / "scenario.json").exists():
                templates.append(path.name)
    return sorted(templates)


def get_template_path(name: str) -> Path:
    """Get path to a template directory.

    Args:
        name: Template name (e.g., 'org-hierarchy')

    Returns:
        Path to the template directory.

    Raises:
        ValueError: If template not found.
    """
    template_path = TEMPLATES_DIR / name
    if not template_path.exists():
        available = list_templates()
        if available:
            raise ValueError(
                f"Template '{name}' not found. Available: {', '.join(available)}"
            )
        else:
            raise ValueError(f"Template '{name}' not found. No templates available.")
    if not (template_path / "scenario.json").exists():
        raise ValueError(f"Template '{name}' is missing scenario.json")
    return template_path


def get_template_info(name: str) -> Dict[str, Any]:
    """Get template metadata without fully loading the scenario.

    Args:
        name: Template name.

    Returns:
        Dictionary with scenario.json contents.
    """
    template_path = get_template_path(name)
    scenario_file = template_path / "scenario.json"
    with open(scenario_file) as f:
        return json.load(f)


def load_template_rules(
    template_path: Path,
    *,
    seed: Optional[int] = None,
) -> Optional["SimulationRules"]:
    """Dynamically load SimulationRules from a template's rules.py.

    Args:
        template_path: Path to template directory.
        seed: Optional random seed for reproducibility.

    Returns:
        SimulationRules instance, or None if rules.py doesn't exist.
    """
    from miniverse import SimulationRules

    rules_path = template_path / "rules.py"
    if not rules_path.exists():
        return None

    # Load module dynamically
    spec = importlib.util.spec_from_file_location("rules", rules_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Find first SimulationRules subclass
    rules_class = None
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if (
            isinstance(obj, type)
            and issubclass(obj, SimulationRules)
            and obj is not SimulationRules
        ):
            rules_class = obj
            break

    if rules_class is None:
        return None

    # Instantiate with seed if the constructor accepts it
    rng = random.Random(seed) if seed is not None else None

    # Try to instantiate with rng parameter
    try:
        return rules_class(rng=rng)
    except TypeError:
        # Constructor doesn't accept rng, try without
        try:
            return rules_class()
        except TypeError:
            return None


def load_template_cognition(
    template_path: Path,
    profiles: Dict[str, "AgentProfile"],
    *,
    use_llm: bool = False,
) -> Dict[str, "AgentCognition"]:
    """Load cognition configuration for all agents in a template.

    Looks for build_cognition function in cognition.py or rules.py.
    Falls back to library defaults if not found.

    Args:
        template_path: Path to template directory.
        profiles: Dict mapping agent_id to AgentProfile.
        use_llm: Whether to use LLM-based cognition.

    Returns:
        Dict mapping agent_id to AgentCognition.
    """
    from miniverse import AgentCognition
    from miniverse.cognition import Scratchpad

    # Try cognition.py first, then rules.py
    for filename in ["cognition.py", "rules.py"]:
        module_path = template_path / filename
        if module_path.exists():
            spec = importlib.util.spec_from_file_location(filename.replace(".py", ""), module_path)
            if spec is not None and spec.loader is not None:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Look for build_cognition function
                if hasattr(module, "build_cognition"):
                    return module.build_cognition(profiles, use_llm=use_llm)

    # Fall back to defaults
    if use_llm:
        return _build_llm_cognition(profiles)
    else:
        return _build_deterministic_cognition(profiles)


def _build_llm_cognition(profiles: Dict[str, "AgentProfile"]) -> Dict[str, "AgentCognition"]:
    """Build LLM-based cognition for all agents."""
    from miniverse import AgentCognition
    from miniverse.cognition import (
        LLMPlanner,
        LLMReflectionEngine,
        Scratchpad,
    )
    from miniverse.cognition.llm import LLMExecutor

    # Default available actions
    available_actions = [
        {
            "action_type": "work",
            "description": "Work on current task",
        },
        {
            "action_type": "communicate",
            "description": "Send message to another agent",
        },
        {
            "action_type": "move",
            "description": "Move to different location",
        },
        {
            "action_type": "rest",
            "description": "Rest to recover energy",
        },
        {
            "action_type": "analyze",
            "description": "Analyze situation or data",
        },
        {
            "action_type": "monitor",
            "description": "Monitor systems or environment",
        },
    ]

    cognition_map = {}
    for agent_id in profiles:
        cognition_map[agent_id] = AgentCognition(
            planner=LLMPlanner(template_name="default"),
            executor=LLMExecutor(template_name="default", available_actions=available_actions),
            reflection=LLMReflectionEngine(template_name="default"),
            scratchpad=Scratchpad(),
        )

    return cognition_map


def _build_deterministic_cognition(profiles: Dict[str, "AgentProfile"]) -> Dict[str, "AgentCognition"]:
    """Build deterministic cognition for all agents."""
    from miniverse import AgentCognition
    from miniverse.cognition import Scratchpad
    from miniverse.cognition.runtime import build_default_cognition

    cognition_map = {}
    for agent_id in profiles:
        cognition_map[agent_id] = build_default_cognition()

    return cognition_map
