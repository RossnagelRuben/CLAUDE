"""Cognition policy modules for the workshop demo scenario.

Important distinction:
- `rules.py` controls deterministic world physics (resource/time/state transitions).
- `cognition.py` controls agent decision policy (how actions are chosen each tick).

This module intentionally contains TWO explicit cognition policies:
- rule-based policy (baseline, no LLM calls)
- LLM policy (persona-driven planning/execution/reflection)

Selection is controlled by `build_cognition(..., use_llm=...)`.
"""

from __future__ import annotations

from typing import Dict, Optional

from miniverse import (
    AgentAction,
    AgentCognition,
    AgentProfile,
    Plan,
    PlanStep,
    ReflectionResult,
)
from miniverse.cognition import Scratchpad
from miniverse.cognition.cadence import (
    CognitionCadence,
    PlannerCadence,
    ReflectionCadence,
    TickInterval,
)
from miniverse.cognition.context import PromptContext
from miniverse.cognition.executor import Executor
from miniverse.cognition.planner import Planner
from miniverse.cognition.reflection import ReflectionEngine


class RulePolicyPlanner(Planner):
    """Role-based planning policy for deterministic baseline runs (no LLM calls)."""

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
        world_context,
        context: PromptContext,
    ) -> Plan:
        profile: AgentProfile = context.agent_profile
        steps = [
            PlanStep(description=desc, metadata={"role": profile.role})
            for desc in self.ROLE_PLANS.get(profile.role, ["coordinate"])
        ]
        return Plan(steps=steps)


class RulePolicyExecutor(Executor):
    """Action-selection policy for deterministic baseline runs."""

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
            parameters={"intensity": "normal"} if action_type == "work" else {},
            reasoning=reasoning,
            communication=None,
        )


class RulePolicyReflection(ReflectionEngine):
    """Reflection policy for deterministic baseline runs."""

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


VALID_COMMUNICATION_MODES = {"exclusive", "sidecar"}


def _normalize_communication_mode(mode: Optional[str]) -> str:
    normalized = (mode or "exclusive").strip().lower()
    if normalized not in VALID_COMMUNICATION_MODES:
        raise ValueError(
            f"Invalid communication_mode={mode!r}. "
            "Expected one of: exclusive, sidecar."
        )
    return normalized


def _build_workshop_prompt_library(communication_mode: str = "exclusive"):
    from miniverse.cognition import PromptLibrary, PromptTemplate

    communication_mode = _normalize_communication_mode(communication_mode)
    if communication_mode == "sidecar":
        communication_instruction = (
            "Communication mode is sidecar.\n"
            "You MAY include a directed communication payload alongside non-communicate actions "
            "to coordinate while still executing.\n"
            "If you include communication, set communication.to to a teammate agent_id and send "
            "a concrete assignment, request, or blocker update.\n"
            "Use action_type='communicate' only when messaging is the primary action."
        )
        communication_guidance = (
            "- Sidecar mode is enabled: send at most one short directed message alongside your "
            "primary action only when it introduces a NEW blocker/assignment/update.\n"
            "- Prefer sidecar on work/repair actions; do not attach sidecar by default to repeated "
            "analyze loops.\n"
            "- Use pure communicate only when messaging itself is the highest-priority action.\n"
        )
    else:
        communication_instruction = (
            "Communication mode is exclusive.\n"
            "If action_type is 'communicate', target MUST be a teammate agent_id and "
            "communication.to MUST match target, with a concrete assignment or request."
        )
        communication_guidance = (
            "- Exclusive mode: communicate uses the full action turn.\n"
        )

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
                "Planning rules:\n"
                "- Use shift clock and remaining minutes to pace the plan.\n"
                "- Keep steps practical and executable this shift.\n"
                "- If backlog is above 0, include direct execution steps early.\n"
                "- For lead/technician personas, first two steps should not both be pure analysis.\n"
                "- For lead/technician personas with backlog > 0, include at least one explicit work step in the first 3 steps.\n"
                "- Use coordination to assign owners and blockers, then transition to execution.\n"
                "- If an agent is low-energy, include a short recovery/handoff step instead of forcing continuous work.\n"
                "- If backlog is already 0, prefer stabilize/monitor/handoff steps over repeated stand-ups.\n\n"
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
    library.register(
        PromptTemplate(
            name="execute_org",
            system=(
                "{{character_prompt}}\n\n"
                "You are operating in a maintenance workshop with a shared backlog.\n"
                "Choose one action that best advances team progress this tick.\n"
                "Backlog clears primarily from concrete execution (work), not repeated analysis.\n"
                "For work actions, set parameters.intensity to either 'normal' or 'push'.\n"
                "Push mode gives higher throughput but burns substantially more energy and raises stress.\n"
                "Shift clock and energy/stress are critical: pace work so the team sustains throughput through handoff.\n"
                "Rest can be strategically valuable when energy is low; avoid team-wide simultaneous rest while backlog is open.\n"
                "When backlog is near zero, avoid stand-up loops and prioritize closeout, monitoring, and handoff readiness.\n"
                f"{communication_instruction}\n\n"
                "Available actions:\n{{action_catalog}}"
            ),
            user=(
                "{{initial_state_agent_prompt}}\n\n"
                "Perception:\n{{perception_json}}\n\n"
                "Plan:\n{{plan_json}}\n\n"
                "Recent memories:\n{{memories_text}}\n\n"
                "Context summary:\n{{context_summary}}\n\n"
                "Prioritization guidance:\n"
                "- Lead/technician should prefer work or directed communication while backlog > 0.\n"
                "- Analyst should prioritize analyze/communicate to unblock decisions.\n"
                "- If you communicated last tick, follow through with execution unless new evidence blocks it.\n"
                f"{communication_guidance}"
                "- Use shift_minutes_remaining, shift_phase, and energy/stress to pace decisions.\n"
                "- If personal energy is low (<35), consider rest or explicit handoff.\n"
                "- If execution_capacity is 0 while backlog > 0, lead or technician should choose work unless blocked by a new safety issue.\n"
                "- Do not default to repeated stand-up communication when backlog <= 1 unless a blocker is new.\n"
                "- Use work intensity deliberately: choose push only when urgency is high and a recovery/handoff plan exists.\n"
                "- Keep reasoning concise and concrete.\n"
            ),
        )
    )
    return library


def _build_workshop_available_actions(
    communication_mode: str = "exclusive",
) -> list[dict]:
    communication_mode = _normalize_communication_mode(communication_mode)
    non_communicate_schema = (
        {
            "to": "<agent_id>",
            "message": "<string>",
        }
        if communication_mode == "sidecar"
        else None
    )

    work_examples = [
        {
            "action_type": "work",
            "target": "workbench",
            "parameters": {"intensity": "normal"},
            "reasoning": "Backlog is high; I should execute ticket work now.",
            "communication": None,
        },
        {
            "action_type": "work",
            "target": "ticket_T42",
            "parameters": {"intensity": "push"},
            "reasoning": "Urgent handoff window; I will push this tick, then plan a recovery handoff.",
            "communication": None,
        },
    ]
    if communication_mode == "sidecar":
        work_examples.append(
            {
                "action_type": "work",
                "target": "ticket_T19",
                "parameters": {"intensity": "normal"},
                "reasoning": "I can execute while notifying the lead about a blocker.",
                "communication": {
                    "to": "lead",
                    "message": "Started T19. Need inventory confirmation for replacement part.",
                },
            }
        )

    return [
        {
            "name": "work",
            "schema": {
                "action_type": "work",
                "target": "<location_or_task>",
                "parameters": {"intensity": "normal|push"},
                "reasoning": "<string>",
                "communication": non_communicate_schema,
            },
            "examples": work_examples,
        },
        {
            "name": "communicate",
            "schema": {
                "action_type": "communicate",
                "target": "<agent_id>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": {"to": "<agent_id>", "message": "<string>"},
            },
            "examples": [
                {
                    "action_type": "communicate",
                    "target": "tech",
                    "parameters": {},
                    "reasoning": "Need to align ticket order before execution.",
                    "communication": {
                        "to": "tech",
                        "message": "Please take ticket T-42 first; I will clear the blockers.",
                    },
                }
            ],
        },
        {
            "name": "analyze",
            "schema": {
                "action_type": "analyze",
                "target": "<topic>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": non_communicate_schema,
            },
            "examples": [
                {
                    "action_type": "analyze",
                    "target": "task_backlog",
                    "parameters": {},
                    "reasoning": "Need evidence to prioritize the next execution step.",
                    "communication": (
                        {
                            "to": "tech",
                            "message": "Top risk is T42; confirm your execution ETA after triage.",
                        }
                        if communication_mode == "sidecar"
                        else None
                    ),
                }
            ],
        },
        {
            "name": "move",
            "schema": {
                "action_type": "move",
                "target": "<location_id>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": non_communicate_schema,
            },
            "examples": [
                {
                    "action_type": "move",
                    "target": "inventory",
                    "parameters": {},
                    "reasoning": "Need tools from inventory for repair work.",
                    "communication": None,
                }
            ],
        },
        {
            "name": "monitor",
            "schema": {
                "action_type": "monitor",
                "target": "<system_or_signal>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": non_communicate_schema,
            },
            "examples": [
                {
                    "action_type": "monitor",
                    "target": "task_flow",
                    "parameters": {},
                    "reasoning": "Track arrivals/completions before assigning heavy jobs.",
                    "communication": None,
                }
            ],
        },
        {
            "name": "rest",
            "schema": {
                "action_type": "rest",
                "target": None,
                "parameters": {},
                "reasoning": "<string>",
                "communication": non_communicate_schema,
            },
            "examples": [
                {
                    "action_type": "rest",
                    "target": None,
                    "parameters": {},
                    "reasoning": "Energy is low; recover before resuming critical tasks.",
                    "communication": (
                        {
                            "to": "lead",
                            "message": "Taking one recovery tick, then I can return to work.",
                        }
                        if communication_mode == "sidecar"
                        else None
                    ),
                }
            ],
        },
    ]


def build_rule_policy_cognition(
    profiles: Dict[str, AgentProfile],
) -> Dict[str, AgentCognition]:
    """Build deterministic baseline cognition policy for workshop."""
    cognition_map = {}
    for agent_id in profiles:
        cognition_map[agent_id] = AgentCognition(
            planner=RulePolicyPlanner(),
            executor=RulePolicyExecutor(),
            reflection=RulePolicyReflection(),
            scratchpad=Scratchpad(),
        )
    return cognition_map


def build_llm_policy_cognition(
    profiles: Dict[str, AgentProfile],
    *,
    communication_mode: str = "exclusive",
) -> Dict[str, AgentCognition]:
    """Build LLM cognition policy for workshop."""
    from miniverse.cognition import LLMPlanner, LLMReflectionEngine
    from miniverse.cognition.llm import LLMExecutor

    communication_mode = _normalize_communication_mode(communication_mode)
    library = _build_workshop_prompt_library(communication_mode)
    available_actions = _build_workshop_available_actions(communication_mode)

    cognition_map = {}
    for agent_id in profiles:
        profile = profiles[agent_id]
        planner_every = 3 if profile.role == "analyst" else 4
        cadence = CognitionCadence(
            planner=PlannerCadence(
                interval=TickInterval(every=planner_every, offset=1),
                run_when_empty=True,
            ),
            reflection=ReflectionCadence(
                interval=TickInterval(every=4, offset=2),
                require_new_memories=True,
            ),
        )
        cognition_map[agent_id] = AgentCognition(
            planner=LLMPlanner(template_name="plan_org", prompt_library=library),
            executor=LLMExecutor(
                template_name="execute_org",
                prompt_library=library,
                available_actions=available_actions,
            ),
            reflection=LLMReflectionEngine(
                template_name="reflect_org",
                prompt_library=library,
            ),
            scratchpad=Scratchpad(),
            prompt_library=library,
            cadence=cadence,
        )
    return cognition_map


def build_cognition(
    profiles: Dict[str, AgentProfile],
    *,
    use_llm: bool = False,
    communication_mode: str = "exclusive",
) -> Dict[str, AgentCognition]:
    """Build workshop cognition policy map.

    Policy switch:
    - `use_llm=False`: rule-based cognition policy (deterministic baseline)
    - `use_llm=True`: LLM cognition policy (persona/emergent behavior)
    - `communication_mode`: LLM communication behavior
      - `exclusive`: communicate consumes the action turn
      - `sidecar`: allow directed message alongside non-communicate actions
    """
    if use_llm:
        return build_llm_policy_cognition(
            profiles,
            communication_mode=communication_mode,
        )
    return build_rule_policy_cognition(profiles)
