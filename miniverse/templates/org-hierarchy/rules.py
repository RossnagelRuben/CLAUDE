"""Simulation rules for the org-hierarchy template.

Defines deterministic physics for a three-person maintenance crew scenario:
- Energy drains when working, recovers when resting
- Stress increases with work, decreases with rest
- Task backlog decreases with active work
- Optional stochastic task arrivals

Also includes deterministic cognition modules as fallbacks when LLM is disabled.
"""

from __future__ import annotations

import random
from dataclasses import asdict
from typing import Dict, Optional

from miniverse import (
    AgentAction,
    AgentCognition,
    AgentProfile,
    GraphOccupancy,
    Plan,
    PlanStep,
    ReflectionResult,
    SimulationRules,
    WorldState,
)
from miniverse.cognition import Scratchpad
from miniverse.cognition.context import PromptContext
from miniverse.cognition.executor import Executor
from miniverse.cognition.planner import Planner
from miniverse.cognition.reflection import ReflectionEngine


class OrgHierarchyRules(SimulationRules):
    """Deterministic updates with optional stochastic arrivals for org simulation."""

    def __init__(
        self,
        occupancy: Optional[GraphOccupancy] = None,
        *,
        rng: Optional[random.Random] = None,
        task_arrival_chance: float = 0.35,
        max_new_tasks: int = 2,
    ) -> None:
        """Initialize rules.

        Args:
            occupancy: Graph occupancy tracker for movement validation.
            rng: Random number generator for reproducible stochasticity.
            task_arrival_chance: Probability of new tasks each tick (0-1).
            max_new_tasks: Maximum new tasks per arrival event.
        """
        self.occupancy = occupancy
        self.rng = rng
        self.task_arrival_chance = max(0.0, task_arrival_chance)
        self.max_new_tasks = max(0, max_new_tasks)

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        """Apply deterministic physics each tick.

        Updates:
        - Agent energy (drains with work, recovers with rest)
        - Agent stress (increases with work, decreases with rest)
        - Task backlog (decreases with active agents)
        - Power consumption (based on active agents)
        - Optional stochastic task arrivals
        """
        updated = state.model_copy(deep=True)

        # Get or create metrics
        backlog = updated.resources.get_metric(
            "task_backlog", default=0, label="Pending Tasks"
        )
        power = updated.resources.get_metric(
            "power_kwh", default=120.0, unit="kWh", label="Battery Reserve"
        )

        # Update agent attributes based on activity
        active_agents = 0
        for agent in updated.agents:
            energy = agent.get_attribute("energy", default=80, unit="%")
            stress = agent.get_attribute("stress", default=25, unit="%")

            activity = (agent.activity or "").lower()
            if activity in {"work", "analyze", "repair"}:
                active_agents += 1
                energy.value = max(0.0, float(energy.value) - 5)
                stress.value = min(100.0, float(stress.value) + 2)
            else:
                energy.value = min(100.0, float(energy.value) + 3)
                stress.value = max(0.0, float(stress.value) - 1)

        # Stochastic task arrivals
        incoming_tasks = 0
        if (
            self.rng is not None
            and self.task_arrival_chance > 0.0
            and self.max_new_tasks > 0
            and self.rng.random() < self.task_arrival_chance
        ):
            incoming_tasks = self.rng.randint(1, self.max_new_tasks)

        # Update backlog: decrease by active workers, increase by arrivals
        backlog.value = max(0, int(backlog.value) - active_agents + incoming_tasks)

        # Power consumption
        drain_multiplier = 1.5
        if self.rng is not None:
            drain_multiplier += self.rng.uniform(-0.2, 0.2)
        power.value = max(0.0, float(power.value) - active_agents * drain_multiplier)

        updated.tick = tick
        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        """Validate if an action is physically possible.

        Checks room capacity for move actions.
        """
        if action.action_type == "move" and self.occupancy:
            target = action.target
            if not target:
                return False
            return self.occupancy.can_enter(target, action.agent_id)
        return True


# --- Deterministic Cognition Modules ---
# Used as fallbacks when LLM cognition is disabled


class DeterministicPlanner(Planner):
    """Role-based deterministic planner."""

    ROLE_PLANS = {
        "lead": ["coordinate", "check-in"],
        "technician": ["repair", "restock"],
        "analyst": ["analyze", "report"],
    }

    async def generate_plan(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        *,
        world_context: WorldState,
        context: PromptContext,
    ) -> Plan:
        profile: AgentProfile = context.agent_profile
        steps = [
            PlanStep(description=desc, metadata={"role": profile.role})
            for desc in self.ROLE_PLANS.get(profile.role, ["coordinate"])
        ]
        return Plan(steps=steps)


class DeterministicExecutor(Executor):
    """Executor that maps plan steps to predefined actions."""

    ROLE_ACTIONS = {
        "lead": {"coordinate": ("work", "ops"), "check-in": ("communicate", "ops")},
        "technician": {"repair": ("work", "workbench"), "restock": ("move", "inventory")},
        "analyst": {"analyze": ("analyze", "ops"), "report": ("communicate", "ops")},
    }

    async def choose_action(
        self,
        agent_id: str,
        perception,
        scratchpad: Scratchpad,
        *,
        plan: Plan,
        plan_step: Optional[PlanStep],
        context: PromptContext,
    ) -> AgentAction:
        profile: AgentProfile = context.agent_profile
        role_map = self.ROLE_ACTIONS.get(profile.role, {})

        if plan_step is None:
            action_type, target = ("rest", perception.location)
        else:
            action_type, target = role_map.get(
                plan_step.description, ("work", perception.location)
            )

        reasoning = (
            f"Executing plan step '{plan_step.description}'"
            if plan_step
            else "No plan available, defaulting to rest"
        )

        return AgentAction(
            agent_id=agent_id,
            tick=perception.tick,
            action_type=action_type,
            target=target,
            parameters={},
            reasoning=reasoning,
            communication=None,
        )


class DeterministicReflection(ReflectionEngine):
    """Reflection engine generating lightweight diary notes."""

    async def maybe_reflect(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        recent_memories,
        *,
        trigger_context=None,
        context: Optional[PromptContext] = None,
    ) -> list[ReflectionResult]:
        if not trigger_context or trigger_context.get("tick", 0) % 3 != 0:
            return []

        latest = next(iter(recent_memories), None)
        content = (
            "Reviewed progress and adjusted plan."
            if latest is None
            else f"Noted: {latest.content}"
        )
        return [ReflectionResult(content=content, importance=6)]


def build_cognition(
    profiles: Dict[str, AgentProfile],
    *,
    use_llm: bool = False,
) -> Dict[str, AgentCognition]:
    """Build cognition map for all agents.

    This function is called by the CLI when loading the template.

    Args:
        profiles: Dict mapping agent_id to AgentProfile.
        use_llm: Whether to use LLM-based cognition.

    Returns:
        Dict mapping agent_id to AgentCognition.
    """
    if use_llm:
        # Use library's LLM cognition
        from miniverse.cognition import (
            LLMPlanner,
            LLMReflectionEngine,
            PromptLibrary,
            PromptTemplate,
        )
        from miniverse.cognition.llm import LLMExecutor

        # Build prompt library with template-specific prompts
        library = PromptLibrary()
        library.register(
            PromptTemplate(
                name="plan_org",
                system=(
                    "You plan the team's upcoming tasks. Use the context to produce a JSON plan "
                    "following the schema in the example."
                ),
                user=(
                    "Context summary:\n{{context_summary}}\n\n"
                    "Environment JSON:\n{{context_json}}\n\n"
                    "Example output:\n"
                    "{\n"
                    '  "steps": [\n'
                    '    {"description": "coordinate stand-up", "metadata": {"duration_minutes": 30}},\n'
                    '    {"description": "review backlog", "metadata": {"priority": "high"}}\n'
                    "  ],\n"
                    '  "metadata": {"planning_horizon": "next 3 hours"}\n'
                    "}\n\n"
                    "Respond with JSON only."
                ),
            )
        )
        library.register(
            PromptTemplate(
                name="reflect_org",
                system=(
                    "Write a brief diary entry summarizing key takeaways. Return JSON with a 'reflections' list."
                ),
                user=(
                    "Context summary:\n{{context_summary}}\n\n"
                    "Full JSON:\n{{context_json}}\n\n"
                    "Example output:\n"
                    "{\n"
                    '  "reflections": [\n'
                    '    {"content": "Coordinated early with team, backlog dropped. Need more resources.", "importance": 6}\n'
                    "  ]\n"
                    "}\n\n"
                    "Respond with JSON only."
                ),
            )
        )

        available_actions = [
            {"action_type": "work", "description": "Work on current task"},
            {"action_type": "communicate", "description": "Send message to another agent"},
            {"action_type": "move", "description": "Move to different location"},
            {"action_type": "rest", "description": "Rest to recover energy"},
            {"action_type": "analyze", "description": "Analyze situation or data"},
            {"action_type": "monitor", "description": "Monitor systems or environment"},
        ]

        cognition_map = {}
        for agent_id in profiles:
            cognition_map[agent_id] = AgentCognition(
                planner=LLMPlanner(template_name="plan_org", prompt_library=library),
                executor=LLMExecutor(template_name="default", available_actions=available_actions),
                reflection=LLMReflectionEngine(template_name="reflect_org", prompt_library=library),
                scratchpad=Scratchpad(),
                prompt_library=library,
            )
        return cognition_map

    else:
        # Use deterministic cognition
        cognition_map = {}
        for agent_id in profiles:
            cognition_map[agent_id] = AgentCognition(
                planner=DeterministicPlanner(),
                executor=DeterministicExecutor(),
                reflection=DeterministicReflection(),
                scratchpad=Scratchpad(),
            )
        return cognition_map
