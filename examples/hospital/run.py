"""Hospital simulation with ER, ICU, OR, and imaging departments.

By default runs in deterministic mode (no LLM calls):

    uv run python examples/hospital/run.py --ticks 10

To enable LLM-driven planning/execution/reflection:

    uv run python examples/hospital/run.py --llm --ticks 10

Environment variables expected when `--llm` is used:
- `LLM_PROVIDER` (e.g., `openai`)
- `LLM_MODEL` (e.g., `gpt-4o-mini`)
- Provider-specific API key (e.g., `OPENAI_API_KEY`)
"""

from __future__ import annotations

import argparse
import asyncio
import random
from pathlib import Path
from typing import TYPE_CHECKING

from miniverse import (
    AgentAction,
    AgentCognition,
    EnvironmentGraph,
    EnvironmentGraphState,
    GraphOccupancy,
    LocationNode,
    Orchestrator,
    Plan,
    PlanStep,
    ReflectionResult,
    WorldState,
)
from miniverse.cognition import (
    LLMPlanner,
    LLMReflectionEngine,
    PromptLibrary,
    PromptTemplate,
    Scratchpad,
)
from miniverse.cognition.executor import Executor
from miniverse.cognition.llm import LLMExecutor
from miniverse.cognition.planner import Planner
from miniverse.cognition.reflection import ReflectionEngine
from miniverse.cognition.context import PromptContext
from miniverse.config import Config
from miniverse.scenario import ScenarioLoader

from hospital_rules import HospitalRules

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_environment_graph(state: EnvironmentGraphState | None) -> EnvironmentGraph | None:
    """Build environment graph from state."""
    if state is None:
        return None
    nodes = {
        node_id: LocationNode(
            name=node_state.name,
            capacity=node_state.capacity,
            metadata=node_state.metadata,
        )
        for node_id, node_state in state.nodes.items()
    }
    return EnvironmentGraph(nodes=nodes, adjacency=dict(state.adjacency))


class DeterministicHospitalPlanner(Planner):
    """Role-based deterministic planner for hospital staff."""
    
    ROLE_PLANS = {
        "er_doctor": [
            "assess patients in triage",
            "stabilize critical patients",
            "order diagnostic tests",
            "consult with specialists",
        ],
        "icu_doctor": [
            "review ICU patient vitals",
            "adjust treatment protocols",
            "coordinate with nursing staff",
            "plan discharge or transfers",
        ],
        "surgeon": [
            "review surgical schedule",
            "prepare for procedures",
            "perform surgery",
            "post-op follow-up",
        ],
        "radiologist": [
            "review imaging requests",
            "interpret scans",
            "communicate findings",
        ],
        "er_nurse": [
            "triage incoming patients",
            "administer medications",
            "monitor patient vitals",
            "assist with procedures",
        ],
        "icu_nurse": [
            "monitor ICU patients",
            "administer critical medications",
            "document vital changes",
            "alert physicians to changes",
        ],
        "or_nurse": [
            "prepare operating room",
            "assist with surgery",
            "monitor patient during procedure",
            "manage surgical supplies",
        ],
        "imaging_tech": [
            "prepare imaging equipment",
            "position patients safely",
            "perform scans",
            "ensure image quality",
        ],
        "admin_staff": [
            "process admissions",
            "coordinate transfers",
            "manage discharge paperwork",
            "communicate with families",
        ],
        "patient": [
            "wait for care",
            "communicate symptoms",
            "follow treatment plan",
        ],
        "visitor": [
            "visit patient",
            "speak with medical staff",
            "provide emotional support",
        ],
    }
    
    async def generate_plan(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        *,
        world_context: WorldState,
        context: PromptContext,
    ) -> Plan:
        """Generate role-based plan."""
        role = context.agent_profile.role
        steps = [
            PlanStep(description=desc, metadata={"role": role})
            for desc in self.ROLE_PLANS.get(role, ["monitor situation"])
        ]
        return Plan(steps=steps)


class DeterministicHospitalExecutor(Executor):
    """Executor that maps hospital plan steps to actions."""
    
    async def choose_action(
        self,
        agent_id: str,
        perception,
        scratchpad: Scratchpad,
        *,
        plan: Plan,
        plan_step: PlanStep | None,
        context: PromptContext,
    ) -> AgentAction:
        """Choose action based on role and plan step."""
        role = context.agent_profile.role
        
        if plan_step is None:
            return AgentAction(
                agent_id=agent_id,
                tick=perception.tick,
                action_type="wait",
                target=perception.location,
                parameters={},
                reasoning="No plan available",
                communication=None,
            )
        
        step_desc = plan_step.description.lower()
        
        # Determine action based on step description
        if "triage" in step_desc or "assess" in step_desc:
            action_type = "assess"
            targets = self._find_patients_at_location(perception, ["er_waiting", "er_triage", "er"])
            target = targets[0] if targets else None
        elif "administer" in step_desc or "medication" in step_desc:
            action_type = "administer_medication"
            targets = self._find_patients_at_location(perception, [perception.location])
            target = targets[0] if targets else None
        elif "surgery" in step_desc or "operate" in step_desc:
            action_type = "perform_surgery"
            targets = self._find_patients_at_location(perception, ["operating_room"])
            target = targets[0] if targets else None
        elif "scan" in step_desc or "imaging" in step_desc:
            action_type = "perform_imaging"
            targets = self._find_patients_at_location(perception, ["imaging"])
            target = targets[0] if targets else None
        elif "transfer" in step_desc:
            action_type = "transfer_patient"
            targets = self._find_critical_patients(perception)
            target = targets[0] if targets else None
        elif "monitor" in step_desc:
            action_type = "monitor"
            targets = self._find_patients_at_location(perception, [perception.location])
            target = targets[0] if targets else None
        elif "communicate" in step_desc or "alert" in step_desc or "coordinate" in step_desc:
            action_type = "communicate"
            target = self._find_relevant_staff(perception, role)
        else:
            action_type = "work"
            target = perception.location
        
        return AgentAction(
            agent_id=agent_id,
            tick=perception.tick,
            action_type=action_type,
            target=target or perception.location,
            targets=[target] if target else [],
            parameters={},
            reasoning=f"Executing: {plan_step.description}",
            communication=None,
        )
    
    def _find_patients_at_location(self, perception, locations: list[str]) -> list[str]:
        """Find patient IDs at given locations."""
        # In this simple implementation, just return a generic patient ID
        # In full implementation, would parse perception.recent_memories to find patients
        return ["patient_001"] if "er" in perception.location else []
    
    def _find_critical_patients(self, perception) -> list[str]:
        """Find patients in critical condition."""
        # Simple heuristic
        return ["patient_003"] if "icu" in perception.location else []
    
    def _find_relevant_staff(self, perception, role: str) -> str | None:
        """Find relevant staff member to communicate with."""
        # Map roles to who they typically communicate with
        communication_map = {
            "er_doctor": ["nurse_patel", "dr_martinez", "dr_kumar"],
            "icu_doctor": ["nurse_davis", "dr_chen"],
            "surgeon": ["nurse_thompson", "dr_chen", "dr_martinez"],
            "er_nurse": ["dr_chen", "nurse_johnson"],
            "icu_nurse": ["dr_martinez", "nurse_davis"],
        }
        
        targets = communication_map.get(role, [])
        return targets[0] if targets else None


class DeterministicHospitalReflection(ReflectionEngine):
    """Simple reflection engine for hospital staff."""
    
    async def maybe_reflect(
        self,
        agent_id: str,
        scratchpad: Scratchpad,
        recent_memories,
        *,
        trigger_context=None,
        context: PromptContext | None = None,
    ) -> list[ReflectionResult]:
        """Generate reflections periodically."""
        if not trigger_context or trigger_context.get("tick", 0) % 5 != 0:
            return []
        
        content = f"Reviewed patient status and priorities. Continuing with care protocols."
        return [ReflectionResult(content=content, importance=5)]


def build_prompt_library() -> PromptLibrary:
    """Build hospital-specific prompt templates."""
    library = PromptLibrary()
    
    library.register(
        PromptTemplate(
            name="plan_hospital",
            system=(
                "You are a healthcare professional planning your shift activities. "
                "Consider patient needs, department priorities, and coordination with colleagues. "
                "Generate a plan with clear, actionable steps."
            ),
            user=(
                "Agent Profile:\n{{agent_profile_json}}\n\n"
                "Current Context:\n{{context_summary}}\n\n"
                "Environment:\n{{context_json}}\n\n"
                "Generate a JSON plan with steps for managing patients and coordinating care."
            ),
        )
    )
    
    library.register(
        PromptTemplate(
            name="execute_hospital",
            system=(
                "You are a healthcare professional deciding your next action. "
                "Consider patient safety, clinical priorities, and team coordination. "
                "Return a valid AgentAction JSON."
            ),
            user=(
                "Current Perception:\n{{perception_json}}\n\n"
                "Your Plan:\n{{plan_json}}\n\n"
                "Recent Memories:\n{{memories_text}}\n\n"
                "Choose an appropriate action that addresses patient needs and follows protocols."
            ),
        )
    )
    
    library.register(
        PromptTemplate(
            name="reflect_hospital",
            system=(
                "You are a healthcare professional reflecting on recent events. "
                "Consider patient outcomes, team dynamics, and areas for improvement. "
                "Generate brief, meaningful reflections."
            ),
            user=(
                "Context:\n{{context_summary}}\n\n"
                "Recent Activities:\n{{context_json}}\n\n"
                "Write a brief reflection on key observations and insights."
            ),
        )
    )
    
    return library


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Hospital simulation")
    parser.add_argument("--llm", action="store_true", help="Use LLM-based cognition")
    parser.add_argument("--ticks", type=int, default=10, help="Number of ticks to simulate")
    parser.add_argument("--seed", type=int, help="Random seed for reproducibility")
    return parser.parse_args()


async def run_simulation(
    ticks: int,
    *,
    use_llm: bool = False,
    seed: int | None = None,
) -> dict:
    """Run the hospital simulation."""
    # Load scenario
    loader = ScenarioLoader(scenarios_dir=Path(__file__).parent)
    world_state, profiles = loader.load("scenario")
    profiles_map = {profile.agent_id: profile for profile in profiles}
    
    # Build environment
    env_graph = build_environment_graph(world_state.environment_graph)
    occupancy = GraphOccupancy(env_graph) if env_graph else None
    if occupancy:
        for agent_status in world_state.agents:
            if agent_status.location:
                occupancy.enter(agent_status.location, agent_status.agent_id)
    
    # Initialize RNG
    rng = random.Random(seed) if seed is not None else random.Random()
    
    # Validate LLM config if needed
    if use_llm:
        Config.validate()
        prompt_library = build_prompt_library()
    else:
        prompt_library = None
    
    # Define available actions for LLM executor
    available_actions = [
        {
            "action_type": "assess",
            "description": "Assess patient condition and triage level",
            "schema": {"action_type": "assess", "target": "<patient_id>", "targets": ["<patient_id>"], "parameters": {}, "reasoning": "<string>", "communication": None},
        },
        {
            "action_type": "administer_medication",
            "description": "Administer medication to patient",
            "schema": {"action_type": "administer_medication", "target": "<patient_id>", "targets": ["<patient_id>"], "parameters": {"medication": "<string>"}, "reasoning": "<string>", "communication": None},
        },
        {
            "action_type": "perform_surgery",
            "description": "Perform surgical procedure",
            "schema": {"action_type": "perform_surgery", "target": "<patient_id>", "targets": ["<patient_id>"], "parameters": {}, "reasoning": "<string>", "communication": None},
        },
        {
            "action_type": "perform_imaging",
            "description": "Perform diagnostic imaging (X-ray, CT, MRI)",
            "schema": {"action_type": "perform_imaging", "target": "<patient_id>", "targets": ["<patient_id>"], "parameters": {"type": "<xray|ct|mri>"}, "reasoning": "<string>", "communication": None},
        },
        {
            "action_type": "transfer_patient",
            "description": "Transfer patient to another department",
            "schema": {"action_type": "transfer_patient", "target": "<destination>", "targets": ["<patient_id>"], "parameters": {"destination": "<icu|er|or|imaging>"}, "reasoning": "<string>", "communication": None},
        },
        {
            "action_type": "monitor",
            "description": "Monitor patient vitals and condition",
            "schema": {"action_type": "monitor", "target": "<patient_id>", "targets": ["<patient_id>"], "parameters": {}, "reasoning": "<string>", "communication": None},
        },
        {
            "action_type": "communicate",
            "description": "Communicate with colleague or family member",
            "schema": {"action_type": "communicate", "target": "<location>", "parameters": {}, "reasoning": "<string>", "communication": {"to": "<agent_id>", "message": "<string>"}},
        },
        {
            "action_type": "wait",
            "description": "Wait for care or next task",
            "schema": {"action_type": "wait", "target": "<location>", "parameters": {}, "reasoning": "<string>", "communication": None},
        },
    ]
    
    # Build cognition for each agent
    cognition_map: dict[str, AgentCognition] = {}
    for agent_id, profile in profiles_map.items():
        if use_llm and prompt_library is not None:
            cognition_map[agent_id] = AgentCognition(
                planner=LLMPlanner(
                    template_name="plan_hospital",
                    prompt_library=prompt_library,
                ),
                executor=LLMExecutor(
                    template_name="default",
                    available_actions=available_actions,
                ),
                reflection=LLMReflectionEngine(
                    template_name="reflect_hospital",
                    prompt_library=prompt_library,
                ),
                scratchpad=Scratchpad(),
                prompt_library=prompt_library,
            )
        else:
            cognition_map[agent_id] = AgentCognition(
                planner=DeterministicHospitalPlanner(),
                executor=DeterministicHospitalExecutor(),
                reflection=DeterministicHospitalReflection(),
                scratchpad=Scratchpad(),
            )
    
    # Build agent prompts
    agent_prompts = {}
    for agent_id, profile in profiles_map.items():
        if profile.role == "patient":
            agent_prompts[agent_id] = (
                f"You are {profile.name}, a patient. Communicate your symptoms clearly "
                "and follow medical advice."
            )
        elif profile.role == "visitor":
            agent_prompts[agent_id] = (
                f"You are {profile.name}, a visitor. Support your loved one and "
                "communicate respectfully with medical staff."
            )
        else:
            goals_str = ", ".join(profile.goals) if profile.goals else "provide excellent care"
            agent_prompts[agent_id] = (
                f"You are {profile.name}, a {profile.role}. {profile.personality} "
                f"Your goals: {goals_str}"
            )
    
    # Create orchestrator
    orchestrator = Orchestrator(
        world_state=world_state,
        agents=profiles_map,
        world_prompt="You manage hospital state transitions, ensuring patient safety and care quality.",
        agent_prompts=agent_prompts,
        llm_provider=Config.LLM_PROVIDER if use_llm else None,
        llm_model=Config.LLM_MODEL if use_llm else None,
        simulation_rules=HospitalRules(rng=rng),
        agent_cognition=cognition_map,
    )
    
    # Run simulation
    print(f"\n{'='*80}")
    print(f"HOSPITAL SIMULATION - {'LLM Mode' if use_llm else 'Deterministic Mode'}")
    print(f"{'='*80}\n")
    
    result = await orchestrator.run(num_ticks=ticks)
    
    # Print final summary
    final_state: WorldState = result["final_state"]
    print(f"\n{'='*80}")
    print("FINAL HOSPITAL METRICS")
    print(f"{'='*80}")
    
    for metric_name, metric in final_state.resources.metrics.items():
        print(f"  {metric.label or metric_name}: {metric.value}")
    
    print(f"\n{'='*80}\n")
    
    return result


async def main(args: argparse.Namespace) -> None:
    """Main entry point."""
    try:
        await run_simulation(
            args.ticks,
            use_llm=args.llm,
            seed=args.seed,
        )
    except ValueError as exc:
        print(f"[Error] {exc}")
        if args.llm:
            print("[Info] Falling back to deterministic mode")
            await run_simulation(
                args.ticks,
                use_llm=False,
                seed=args.seed,
            )


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args))
