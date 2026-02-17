"""Cognition policy modules for the valentines demo scenario.

Important distinction:
- `rules.py` controls deterministic world physics (time progression, movement effects).
- `cognition.py` controls agent decision policy (how actions are chosen each tick).
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
    """Rule-based planning policy for deterministic non-LLM runs."""

    ROLE_PLANS = {
        "cafe_owner": ["coordinate community", "invite neighbors"],
        "student": ["study at cafe", "reconnect socially"],
        "musician": ["compose", "check in with friends"],
        "journalist": ["collect local updates", "share event information"],
        "shopkeeper": ["run store operations", "support local businesses"],
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
            for desc in self.ROLE_PLANS.get(profile.role, ["social check-in"])
        ]
        return Plan(steps=steps)


class RulePolicyExecutor(Executor):
    """Rule-based action selection for deterministic non-LLM runs."""

    ROLE_ACTIONS = {
        "cafe_owner": {"coordinate community": ("communicate", "maria"), "invite neighbors": ("work", "hobbs_cafe")},
        "student": {"study at cafe": ("work", "hobbs_cafe"), "reconnect socially": ("communicate", "klaus")},
        "musician": {"compose": ("work", "music_studio"), "check in with friends": ("communicate", "maria")},
        "journalist": {"collect local updates": ("investigate", "community_events"), "share event information": ("communicate", "tom")},
        "shopkeeper": {"run store operations": ("work", "hardware_store"), "support local businesses": ("communicate", "isabella")},
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
        mapping = self.ROLE_ACTIONS.get(profile.role, {})

        if plan_step is None:
            action_type, target = ("monitor", "town")
        else:
            action_type, target = mapping.get(plan_step.description, ("work", perception.location))

        communication = None
        if action_type == "communicate" and isinstance(target, str):
            communication = {
                "to": target,
                "message": "Quick check-in about local plans for tomorrow evening.",
            }

        return AgentAction(
            agent_id=agent_id,
            tick=perception.tick,
            action_type=action_type,
            target=target,
            parameters={},
            reasoning=(
                f"Executing plan step '{plan_step.description}'"
                if plan_step
                else "No plan step available; monitoring current situation."
            ),
            communication=communication,
        )


class RulePolicyReflection(ReflectionEngine):
    """Simple periodic reflection for deterministic non-LLM runs."""

    async def maybe_reflect(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        recent_memories,
        *,
        trigger_context=None,
        context: Optional[PromptContext] = None,
    ) -> list[ReflectionResult]:
        if not trigger_context or trigger_context.get("tick", 0) % 4 != 0:
            return []
        latest = next(iter(recent_memories), None)
        if latest is None:
            text = "Noted social atmosphere and planned next outreach step."
        else:
            text = f"Observed: {latest.content}"
        return [ReflectionResult(content=text, importance=6)]


def _build_valentines_prompt_library():
    from miniverse.cognition import PromptLibrary, PromptTemplate

    library = PromptLibrary()
    library.register(
        PromptTemplate(
            name="plan_valentines",
            system=(
                "You plan this character's next social/professional steps in town. "
                "Use context to produce JSON that follows the example schema."
            ),
            user=(
                "Context summary:\n{{context_summary}}\n\n"
                "Environment JSON:\n{{context_json}}\n\n"
                "Planning rules:\n"
                "- Respect current date/time and location context.\n"
                "- Keep steps realistic for a small-town social setting.\n"
                "- Use communication and movement to create plausible interactions.\n"
                "- If party-related information appears, include a concrete follow-through step.\n\n"
                "Example output:\n"
                "{\n"
                "  \"steps\": [\n"
                "    {\"description\": \"check in with a friend at Hobbs Cafe\", \"metadata\": {\"priority\": \"high\"}},\n"
                "    {\"description\": \"share update about tomorrow's event\", \"metadata\": {\"channel\": \"direct\"}}\n"
                "  ],\n"
                "  \"metadata\": {\"planning_horizon\": \"next 6 hours\"}\n"
                "}\n\n"
                "Respond with JSON only."
            ),
        )
    )
    library.register(
        PromptTemplate(
            name="execute_valentines",
            system=(
                "{{character_prompt}}\n\n"
                "You are in a small-town social simulation around a Valentine's community event.\n"
                "Choose one action that best advances this character's goals this tick.\n"
                "Use natural social behavior: move, communicate, investigate, work, monitor, or rest.\n"
                "If action_type is 'communicate', target MUST be a teammate agent_id and "
                "communication.to MUST match target with a concrete message.\n"
                "Do not invent new agent IDs.\n\n"
                "Available actions:\n{{action_catalog}}"
            ),
            user=(
                "{{initial_state_agent_prompt}}\n\n"
                "Perception:\n{{perception_json}}\n\n"
                "Plan:\n{{plan_json}}\n\n"
                "Recent memories:\n{{memories_text}}\n\n"
                "Context summary:\n{{context_summary}}\n\n"
                "Guidance:\n"
                "- Favor believable interpersonal pacing over generic repetition.\n"
                "- Communicate with concrete details (who, what, when).\n"
                "- Use movement when location matters for social interaction.\n"
                "- Keep reasoning concise and grounded.\n"
            ),
        )
    )
    library.register(
        PromptTemplate(
            name="reflect_valentines",
            system=(
                "Write a brief diary-style reflection about social dynamics and next steps. "
                "Return JSON with a 'reflections' list."
            ),
            user=(
                "Context summary:\n{{context_summary}}\n\n"
                "Full JSON:\n{{context_json}}\n\n"
                "Example output:\n"
                "{\n"
                "  \"reflections\": [\n"
                "    {\"content\": \"I shared the event details with Maria; next I should confirm Klaus' availability.\", \"importance\": 6}\n"
                "  ]\n"
                "}\n\n"
                "Respond with JSON only."
            ),
        )
    )
    return library


def _build_valentines_available_actions() -> list[dict]:
    return [
        {
            "name": "communicate",
            "schema": {
                "action_type": "communicate",
                "target": "<agent_id>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": {
                    "to": "<agent_id>",
                    "message": "<string>",
                },
            },
            "examples": [
                {
                    "action_type": "communicate",
                    "target": "maria",
                    "parameters": {},
                    "reasoning": "Share a relevant update with Maria.",
                    "communication": {
                        "to": "maria",
                        "message": "Isabella is hosting the party tomorrow at 5pm at Hobbs Cafe.",
                    },
                }
            ],
        },
        {
            "name": "move_to",
            "schema": {
                "action_type": "move_to",
                "target": "<location_id>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "action_type": "move_to",
                    "target": "hobbs_cafe",
                    "parameters": {},
                    "reasoning": "Move to Hobbs Cafe to meet others.",
                    "communication": None,
                }
            ],
        },
        {
            "name": "work",
            "schema": {
                "action_type": "work",
                "target": "<task_or_domain>",
                "parameters": {"task": "<string>"},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "action_type": "work",
                    "target": "event_preparation",
                    "parameters": {"task": "prepare cafe seating"},
                    "reasoning": "Prepare for the upcoming event.",
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
                "communication": None,
            },
            "examples": [
                {
                    "action_type": "rest",
                    "target": None,
                    "parameters": {},
                    "reasoning": "Recharge before evening plans.",
                    "communication": None,
                }
            ],
        },
        {
            "name": "investigate",
            "schema": {
                "action_type": "investigate",
                "target": "<topic>",
                "parameters": {"focus": "<string>"},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "action_type": "investigate",
                    "target": "community_events",
                    "parameters": {"focus": "tomorrow evening plans"},
                    "reasoning": "Gather details before sharing updates.",
                    "communication": None,
                }
            ],
        },
        {
            "name": "monitor",
            "schema": {
                "action_type": "monitor",
                "target": "<subject>",
                "parameters": {},
                "reasoning": "<string>",
                "communication": None,
            },
            "examples": [
                {
                    "action_type": "monitor",
                    "target": "town_square_activity",
                    "parameters": {},
                    "reasoning": "Observe social activity before choosing next step.",
                    "communication": None,
                }
            ],
        },
    ]


def build_rule_policy_cognition(
    profiles: Dict[str, AgentProfile],
) -> Dict[str, AgentCognition]:
    """Build deterministic rule-policy cognition map."""
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
) -> Dict[str, AgentCognition]:
    """Build LLM policy cognition map for valentines scenario."""
    from miniverse.cognition import LLMPlanner, LLMReflectionEngine
    from miniverse.cognition.llm import LLMExecutor

    library = _build_valentines_prompt_library()
    available_actions = _build_valentines_available_actions()

    cognition_map: Dict[str, AgentCognition] = {}
    for agent_id, profile in profiles.items():
        planner_every = 2 if profile.role in {"journalist", "cafe_owner"} else 3
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
            planner=LLMPlanner(template_name="plan_valentines", prompt_library=library),
            executor=LLMExecutor(
                template_name="execute_valentines",
                prompt_library=library,
                available_actions=available_actions,
            ),
            reflection=LLMReflectionEngine(
                template_name="reflect_valentines",
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
) -> Dict[str, AgentCognition]:
    """Build valentines cognition map.

    - `use_llm=False`: deterministic rule policy
    - `use_llm=True`: LLM policy
    """
    if use_llm:
        return build_llm_policy_cognition(profiles)
    return build_rule_policy_cognition(profiles)
