# Building Simulations with Miniverse

This guide walks through the workflow for authoring a new simulation using the Miniverse library. The goal is to make it easy for developers (or LLM agents) to bootstrap a scenario from scratch.

---

## 1. Define the Scenario

Create a YAML (or JSON) file describing the initial world:

- **Agents** – profiles (identity, skills, goals, relationships) plus status blocks (location, attributes, tags).
- **Resources** – shared metrics such as power, backlog, or budget (via `ResourceState`).
- **Environment** – optional metrics and tier descriptions:
  - Tier 0: metrics only.
  - Tier 1: `environment_graph` (nodes + adjacency) for logical locations.
  - Tier 2: `environment_grid` (width/height + tiles) for spatial worlds.
- **Events** – optional initial events.

Use `ScenarioLoader` to parse the file into `WorldState` and `AgentProfile` objects (`miniverse/scenario.py`). The workshop example (`examples/workshop/scenario.yaml`) shows a Tier-1 setup.

```python
loader = ScenarioLoader(scenarios_dir=Path("examples/workshop"))
world_state, profiles = loader.load("scenario")
```

Optional: seed starter memories in scenario metadata so agents begin with prior context:

```yaml
metadata:
  initial_memories:
    lead:
      - content: "Previous shift reported intermittent recycler instability."
        memory_type: observation
        importance: 7
```

Miniverse persists these at tick `0` before the first decision tick.

---

## 2. Provide Deterministic Rules

Subclass `SimulationRules` to encode domain physics:

- `apply_tick` – consume resources, adjust agent attributes, generate events.
- `validate_action` – reject impossible actions (room capacity, prerequisites).
- Optional hooks: `on_simulation_start`, `on_simulation_end`, `format_resource_summary`.

These rules run before cognition each tick and ensure predictable system dynamics.
This logic currently lives in Python (`rules.py`), not YAML, because update
formulas and action-processing are executable behavior rather than static state.
Provide optional stochasticity by passing a `random.Random` instance (or omit it
for strict determinism):

```python
class WorkshopRules(SimulationRules):
    def __init__(
        self,
        occupancy: GraphOccupancy | None = None,
        *,
        rng: random.Random | None = None,
        task_arrival_chance: float = 0.35,
        max_new_tasks: int = 2,
    ) -> None:
        self.occupancy = occupancy
        self.rng = rng
        self.task_arrival_chance = task_arrival_chance
        self.max_new_tasks = max_new_tasks

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        ...  # adjust resources, optionally sample new work from rng

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        ...  # reject moves into rooms that exceed capacity
```

For environments, use the helper module (`miniverse/environment/helpers.py`) to manage capacities (`GraphOccupancy`) or compute paths (`shortest_path`, `grid_shortest_path`).

Runtime wiring for scenario-local extensions can be declared in scenario YAML:

```yaml
metadata:
  runtime:
    rules:
      module: rules.py
      class: WorkshopRules
      kwargs:
        tick_minutes: 30
    cognition:
      module: cognition.py
      builder: build_cognition
```

`kwargs` are passed into the scenario rule constructor so you can tune behavior
from YAML without hardcoding values in CLI code.

### Ticks, Time, and Planning Cadence

- Each iteration of the orchestrator increments `WorldState.tick`. Scenarios decide how that maps to real time (minute, hour, daily loop, etc.)—the library does not assume a day-length tick.
- Planners currently execute once per tick. Use scratchpad flags or custom logic inside your planner to refresh less frequently (e.g., only generate a new plan every 6 ticks) until native scheduling helpers land.
- Reflection engines can trigger on whatever cadence you need (the workshop example reflects every third tick). Future cognition work will expose higher-level scheduling utilities, but today cadence is scenario-defined.

---

## 3. Assemble Cognition Modules

Each agent has an `AgentCognition` bundle containing:

- `Planner` (async) – outputs a `Plan` given `PromptContext`.
- `Executor` – turns plan steps + perception into `AgentAction`.
- `ReflectionEngine` (async) – generates `ReflectionResult` entries for memory.
- `Scratchpad` – shared working memory (plan state, open tasks, custom flags).
- `PromptLibrary` (optional) – named templates for plan/execute/reflect stages.

Terminology:
- **Cognition policy** means "how agents choose actions."
- Cognition policy can be deterministic (rule-based) or LLM-backed.
- This is separate from **world policy** in `SimulationRules`, which handles deterministic physics.

Miniverse ships two sets of implementations:

1. **Deterministic (example)** – see `examples/workshop/run.py` (`DeterministicPlanner`, `DeterministicExecutor`, `DeterministicReflection`).
2. **LLM-backed** – `LLMPlanner` and `LLMReflectionEngine` in `miniverse/cognition/llm.py`. They render templates, call the LLM via `call_llm_with_retries`, and parse structured JSON responses.

Why deterministic planner/executor classes can live in `cognition.py`:
- Deterministic runs still require per-agent actions each tick.
- Those actions come from a deterministic cognition policy (not from `SimulationRules` directly).
- `SimulationRules` then applies/validates/processes those actions in the world.

### Prompt templates: explicit usage

- `LLMExecutor`, `LLMPlanner`, and `LLMReflectionEngine` now require an explicit template selection.
- Provide either `template_name` (registered in a `PromptLibrary`) or an inline `PromptTemplate` object.
- A friendly alias `template_name="default"` is available for the minimal built-in executor template.
- If neither `template` nor `template_name` is provided, a `ValueError` is raised.

Inline template example (executor only):

```python
from miniverse.cognition.prompts import PromptTemplate
from miniverse.cognition import AgentCognition
from miniverse.cognition.llm import LLMExecutor

my_inline = PromptTemplate(
    name="inline",
    system="You choose the next AgentAction. Respond with valid JSON only.",
    user="Perception:\n{{perception_json}}\n\nPlan:\n{{plan_json}}"
)

cognition = {
    "agent": AgentCognition(
        executor=LLMExecutor(template=my_inline)  # inline template takes precedence
    )
}
```

## Choosing Cognition Modules: Deterministic vs LLM

**Key concept:** Only `executor` is required. Planner, reflection, and scratchpad are **optional enhancements** that can be added independently based on your needs.

### Pattern 1: Minimal Deterministic (Just Executor)

**Use case:** Simple reactive agents, testing, CI/CD, baseline comparison

**Characteristics:**
- Fastest (no network latency, no planning overhead)
- Reproducible (same seed → same behavior)
- Zero API costs
- Predictable actions based on hardcoded if/then logic

**Example:**
```python
from miniverse.cognition import AgentCognition

# Define custom deterministic executor with hardcoded logic
class FixedStrategyExecutor:
    async def choose_action(self, agent_id, perception, scratchpad, *, plan, plan_step, context):
        # Simple rule: if backlog > 50, do fulfillment; else inventory
        backlog = perception.visible_resources.get("backlog", {}).get("value", 0)
        action_type = "fulfillment" if backlog > 50 else "inventory"
        return AgentAction(
            agent_id=agent_id,
            tick=perception.tick,
            action_type=action_type,
            reasoning=f"Backlog at {backlog}"
        )

cognition_map = {
    "worker1": AgentCognition(
        executor=FixedStrategyExecutor()  # Only executor - purely reactive agent
    )
}
```

### Pattern 2: Simple Reactive LLM

**Use case:** LLM agents that don't need planning or memory synthesis

**Characteristics:**
- Agent reacts intelligently to current state
- No long-term planning or reflection overhead
- Good for short simulations or simple decision-making

**Example:**
```python
from miniverse.cognition import AgentCognition, LLMExecutor

cognition_map = {
    "worker1": AgentCognition(
        executor=LLMExecutor(template_name="default")  # explicit minimal template
    )
}
```

### Pattern 3: LLM with Planning

**Use case:** Agents that need multi-step coherence and goal pursuit

**Characteristics:**
- LLM generates daily/hourly plans
- Actions align with long-term goals
- Requires scratchpad to store plan state

**Example:**
```python
from miniverse.cognition import (
    AgentCognition,
    LLMPlanner,
    LLMExecutor,
    Scratchpad
)

cognition_map = {
    "supervisor": AgentCognition(
        executor=LLMExecutor(template_name="warehouse_execute"),
        planner=LLMPlanner(template_name="warehouse_plan"),
        scratchpad=Scratchpad()  # Needed to store plan state
    )
}
```

### Pattern 4: Full Stanford-Style Agent

**Use case:** Long-running simulations with emergent social behavior

**Characteristics:**
- Planning + Execution + Reflection (Stanford Generative Agents pattern)
- Agents synthesize insights from accumulated experiences
- Reflections stored as high-importance memories
- Most realistic but highest LLM cost

**Example:**
```python
from miniverse.cognition import (
    AgentCognition,
    LLMPlanner,
    LLMExecutor,
    LLMReflectionEngine,
    Scratchpad
)

cognition_map = {
    "agent1": AgentCognition(
        executor=LLMExecutor(template_name="agent_execute"),
        planner=LLMPlanner(template_name="agent_plan"),
        reflection=LLMReflectionEngine(template_name="agent_reflect"),
        scratchpad=Scratchpad()  # Stores plan state + working memory
    )
}
```

**Note:** All LLM modules (LLMPlanner, LLMExecutor, LLMReflectionEngine) raise clear errors if LLM not configured. Use `planner=None` / `reflection=None` to explicitly skip those phases.

`PromptLibrary` and `render_prompt` handle the template substitution using data from `PromptContext` (profile, perception, plan, memories, scratchpad state). Default templates live in `miniverse/cognition/prompts.py` and already include JSON examples.

#### Agent prompts injection

- Pass per-agent instructions via `agent_prompts={agent_id: "..."}` to the `Orchestrator`.
- These are injected as `base_agent_prompt` and automatically prepended to the system prompt before template text.
- Use this for situational guidance; identity and personality should primarily come from `AgentProfile`.

#### Action Catalog injection

- Provide `available_actions` to `LLMExecutor(...)` to inject a formatted list via `{{action_catalog}}` in templates.

```python
actions = [
    {
        "name": "communicate",
        "schema": {"action_type": "communicate", "target": "agent_id", "parameters": None, "reasoning": "string", "communication": {"to": "agent_id", "message": "string"}},
        "examples": [{
            "action_type": "communicate",
            "target": "agent_b",
            "parameters": None,
            "reasoning": "Coordinate with another agent",
            "communication": {"to": "agent_b", "message": "Hello! Let's sync."}
        }],
    },
    {"name": "move_to", "schema": {"action_type": "move_to", "target": "location_id", "parameters": {}}, "examples": [{"action_type": "move_to", "target": "location_alpha", "parameters": {}, "reasoning": "Move to a new location", "communication": None}]},
]

cognition = AgentCognition(
    executor=LLMExecutor(template_name="default", available_actions=actions)
)
```

### Communication Persistence Model (Canonical Source)

- Actions can include a `communication` payload during execution, but when persisted, actions are sanitized to include only a minimal reference (e.g., `{ "to": "bob" }`). The message text is not persisted with actions.
- The full communication content is stored as memories for both sender and recipient:
  - Sender memory: `I told bob: <message>` (metadata includes `role=sender`, `recipient`)
  - Recipient memory: `<SenderName> told me: <message>` (metadata includes `role=recipient`, `sender`)
- Implication: Read transcripts/chat history from memories, not from actions.

### Controlling Planner/Reflection Cadence

Use `CognitionCadence` to throttle how often planners and reflection engines execute. The orchestrator stores the last run tick in each agent's scratchpad, so you don't have to manage bookkeeping yourself:

```python
from miniverse.cognition import (
    AgentCognition,
    CognitionCadence,
    PlannerCadence,
    ReflectionCadence,
    TickInterval,
)

cadence = CognitionCadence(
    planner=PlannerCadence(interval=TickInterval(every=2, offset=1)),
    reflection=ReflectionCadence(
        interval=TickInterval(every=3, offset=1),
        require_new_memories=True,
    ),
)

cognition = AgentCognition(
    planner=my_planner,
    executor=my_executor,
    reflection=my_reflection,
    scratchpad=Scratchpad(),
    cadence=cadence,
)
```

Need to translate ticks into higher-level units (day/shift/sprint)? Call `tick_to_time_block(tick=tick, ticks_per_block=8, block_label="shift")` inside your prompts or analytics helpers to expose the block index and per-block offset.

---

## 4. Configure the Orchestrator

Instantiate `Orchestrator` with the initial state, agents, rules, and cognition map:

```python
import random

from miniverse.orchestrator import Orchestrator

orchestrator = Orchestrator(
    world_state=world_state,
    agents=profiles_map,
    world_prompt="You oversee operations.",
    agent_prompts={aid: prompt for aid, prompt in base_prompts.items()},
    llm_provider=Config.LLM_PROVIDER,
    llm_model=Config.LLM_MODEL,
    simulation_rules=WorkshopRules(
        occupancy,
        rng=random.Random(42),        # omit rng for a purely deterministic run
        task_arrival_chance=0.4,
        max_new_tasks=2,
    ),
    agent_cognition=cognition_map,
)
```

### Deterministic vs LLM World Updates

Control world updates via `world_update_mode`:

- `auto` (default):
  - If your rules override `process_actions(state, actions, tick)`, that deterministic handler is used.
  - Else if `llm_provider`/`llm_model` are set, the world-engine LLM runs (fail-fast on misconfig/validation).
  - Else, basic deterministic updates apply (clone state, update tick, apply activities/locations).
- `deterministic`: force deterministic world updates (prefer rules.process_actions; otherwise basic deterministic).
- `llm`: force LLM world engine; raises if not configured or validation fails.

Preflight prints the selected mode and why at the start of the run.

---

## 5. Run the Simulation

Use `orchestrator.run(num_ticks=N)` inside `asyncio.run(...)` or your own loop. The method handles persistence strategy initialization, tick iteration, persistence/memory updates, and optional reflections.

```python
result = await orchestrator.run(num_ticks=8)
final_state = result["final_state"]
```

The workshop examples are CI-friendly: use `WORLD_UPDATE_MODE=deterministic` for faster runs during development; switch to `llm` to test world-engine behavior. Pass DEBUG flags to inspect prompts and perceptions: `DEBUG_LLM`, `DEBUG_PERCEPTION`, `MINIVERSE_VERBOSE`.

Structured schema errors are also surfaced automatically. If an LLM response fails validation, the retry loop prints each offending field (with the received value preview) and appends the same checklist to the next prompt so the model corrects itself without guesswork.

If you need additional diagnostics, pass `tick_listeners` to the orchestrator. Each listener receives `(tick, previous_state, new_state, actions)`—the workshop example wires `TickAnalyzer` (see `examples/workshop/run.py`) to print per-tick backlog deltas and aggregate stats.

For a dialogue-centric walkthrough, see `examples/standup/run.py`. That scenario emits structured `communication` payloads, prints per-tick transcripts, and uses the same `--llm` / `--debug` / `--analysis` switches to compare deterministic and LLM-driven stand-ups.
Key tuning points are documented inline (`StandupPlanner.ROLE_STEPS`, `StandupExecutor.ROLE_MESSAGES`, `ConversationPostTick` heuristics). Debug mode will dump the generated plan/execute payloads so you can verify the prompts are consuming conversation history stored in each agent’s scratchpad.

---

## 6. Expand or Customize

- **Memory retrieval:** `SimpleMemoryStream` offers recency with lightweight keyword boosting; `ImportanceWeightedMemory` blends recency and importance so critical events stay near the top. Both store `tags`/`metadata`, which you can populate when calling `add_memory` (the orchestrator now records action/communication/event tags by default).
- **Branching timelines:** reserved fields (`branch_id`) let you experiment with Loom-style branching later.
- **Testing:** mock `LLMPlanner`/`LLMReflectionEngine` for unit tests (see `tests/test_cognition_flow.py`).
- **Docs to consult:**
  - `docs/architecture/cognition.md` – deep dive into the cognition runtime.
  - `docs/architecture/environment.md` – environment tiers and helpers.
  - `docs/examples/PLAN.md` – upcoming scenarios (Stanford replication, Mars habitat, etc.).

With this workflow you can craft simulations ranging from KPI-only organizational planning (Tier 0) to spatial sandboxes with LLM-driven agents (Tier 2). Start with the workshop example and adapt the templates or rules to your own domain.

---

## 7. Testing

- Run all tests with: `uv run pytest`
- Core unit tests mock LLM calls for reliability and speed.
- One integration test performs a real LLM call and is marked `@pytest.mark.llm`.
  - Enable by setting env vars: `LLM_PROVIDER`, `LLM_MODEL` (and provider API key)
  - Run: `uv run pytest -m llm`

Tip: Use `-k` to target subsets (e.g., `-k world_update_modes`).
