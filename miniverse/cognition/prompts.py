"""Prompt template scaffolding for cognition stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class PromptTemplate:
    """Represents a templated prompt with placeholders."""

    name: str
    system: str
    user: str
    description: str = ""


class PromptLibrary:
    """Container for named prompt templates per cognition stage."""

    def __init__(self) -> None:
        self.templates: Dict[str, PromptTemplate] = {}

    def register(self, template: PromptTemplate) -> None:
        self.templates[template.name] = template

    def get(self, name: str) -> PromptTemplate:
        return self.templates[name]


# Default placeholders ---------------------------------------------------------

DEFAULT_PROMPTS = PromptLibrary()

DEFAULT_PROMPTS.register(
    PromptTemplate(
        name="plan",
        system=(
            "You are the agent's planning assistant. Review the provided context and produce a JSON schedule "
            "for the next few hours. Always follow the JSON schema shown in the example."
        ),
        user=(
            "Context summary:\n{{context_summary}}\n\n"
            "Full context JSON:\n{{context_json}}\n\n"
            "Example output:\n"
            "{\n"
            "  \"steps\": [\n"
            "    {\"description\": \"coordinate morning stand-up\", \"metadata\": {\"duration_minutes\": 45}},\n"
            "    {\"description\": \"inspect life-support systems\", \"metadata\": {\"priority\": \"high\"}}\n"
            "  ],\n"
            "  \"metadata\": {\"planning_horizon\": \"next 4 hours\"}\n"
            "}\n\n"
            "Respond with JSON only."
        ),
        description="Generates an agenda based on goals and memories.",
    )
)

DEFAULT_PROMPTS.register(
    PromptTemplate(
        name="default",
        system=(
            "{{character_prompt}}\n\n"
            "{{simulation_instructions}}\n\n"
            "Available actions:\n{{action_catalog}}\n"
        ),
        user=(
            "{{initial_state_agent_prompt}}\n\n"
            "Perception:\n{{perception_json}}\n"
        ),
        description="Minimal default executor template using character/base/action_catalog placeholders.",
    )
)

DEFAULT_PROMPTS.register(
    PromptTemplate(
        name="execute_tick",
        system=(
            "You are the agent's execution module. Decide the next action based on the plan and context. "
            "Respond with valid AgentAction JSON."
        ),
        user=(
            "Perception:\n{{perception_json}}\n\n"
            "Plan:\n{{plan_json}}\n\n"
            "Memories:\n{{memories_text}}\n\n"
            "Context:\n{{context_summary}}\n\n"
            "Rules:\n"
            "- Use agent_ids for targets (\"beta\"), not names\n"
            "- Use location ids for places (\"lab\")\n"
            "- Include \"communication\" only for communicate actions\n"
            "- action_type: work, communicate, move_to, rest, investigate, or custom\n\n"
            "Examples:\n\n"
            "Work: {\"agent_id\": \"lead\", \"tick\": 5, \"action_type\": \"work\", \"target\": \"ops\", "
            "\"parameters\": {\"focus\": \"coordinate\"}, \"reasoning\": \"Brief the team\", \"communication\": null}\n\n"
            "Communicate: {\"agent_id\": \"lead\", \"tick\": 5, \"action_type\": \"communicate\", \"target\": \"beta\", "
            "\"parameters\": null, \"reasoning\": \"Sync on priorities\", "
            "\"communication\": {\"to\": \"beta\", \"message\": \"Can we sync up?\"}}\n\n"
            "Move: {\"agent_id\": \"lead\", \"tick\": 5, \"action_type\": \"move_to\", \"target\": \"lab\", "
            "\"parameters\": {}, \"reasoning\": \"Go to lab\", \"communication\": null}\n\n"
            "Return JSON only."
        ),
        description="Chooses an action for the current tick.",
    )
)

DEFAULT_PROMPTS.register(
    PromptTemplate(
        name="reflect_diary",
        system=(
            "You are the reflection module. Summarize key learnings as a short diary entry. Use the JSON schema in "
            "the example so the system can store your reflections."
        ),
        user=(
            "Context summary:\n{{context_summary}}\n\n"
            "Full context JSON:\n{{context_json}}\n\n"
            "Example output:\n"
            "{\n"
            "  \"reflections\": [\n"
            "    {\"content\": \"Coordinating early keeps the backlog manageable. Need to request more filters.\", \"importance\": 6}\n"
            "  ]\n"
            "}\n\n"
            "Respond with JSON only."
        ),
        description="Produces diary entries when reflection triggers fire.",
    )
)
