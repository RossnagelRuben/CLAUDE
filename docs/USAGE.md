# Miniverse Usage Guide

This guide covers the current, supported workflow for building and running
scenario-driven simulations.

## 1. Run Scenarios from the CLI

Miniverse discovers scenarios from `demo/` and `examples/`.

```bash
# list discovered scenarios
uv run miniverse list

# inspect one scenario
uv run miniverse info demo/workshop

# run deterministic
uv run miniverse run demo/workshop --ticks 10

# run with LLM cognition
uv run miniverse run demo/workshop --llm --ticks 10 --verbose
```

You can also pass a file path directly:

```bash
uv run miniverse run demo/workshop/scenario.yaml --ticks 10
```

Accepted file formats: `.yaml`, `.yml`, `.json`.

## 2. Author a Scenario File (`scenario.yaml`)

A scenario file defines initial world state and agents. The minimum required top-level keys are:

- `name`
- `description`
- `agents`
- `environment`
- `resources`

### Example

```yaml
name: Operations Workshop
description: Three-person maintenance crew coordinating tasks.
initial_timestamp: "2026-02-14T08:00:00"

agents:
  - profile:
      agent_id: lead
      name: Morgan Reyes
      role: lead
      personality: decisive, high-tempo
      goals:
        - Reduce backlog before handoff
    status:
      location: ops
      activity: idle
      attributes:
        energy:
          value: 74
          unit: "%"
        stress:
          value: 51
          unit: "%"

environment:
  metrics:
    temperature_c:
      value: 22
      unit: "C"

resources:
  metrics:
    task_backlog:
      value: 8
      label: Pending Tasks

environment_graph:
  nodes:
    ops:
      name: Operations Floor
      capacity: 2
    workbench:
      name: Workbench
      capacity: 1
  adjacency:
    ops: [workbench]
    workbench: [ops]

metadata:
  demo:
    scene: "08:00 shift handoff"
```

Notes:

- Metrics accept compact values or full stat objects.
- `environment_graph` and `environment_grid` are optional.
- `metadata.initial_memories` can seed tick-0 memories.

## 3. Add Scenario-Local Runtime Extensions

To make a scenario domain-specific, add `rules.py` and/or `cognition.py` next to `scenario.yaml` and wire them via `metadata.runtime`.

```yaml
metadata:
  runtime:
    rules:
      module: rules.py
      class: WorkshopRules
      kwargs:
        tick_minutes: 30
        task_arrival_chance: 0.35
    cognition:
      module: cognition.py
      builder: build_cognition
      kwargs:
        communication_mode: sidecar
```

### Runtime resolution behavior

- Module paths are resolved relative to the scenario directory.
- `rules.kwargs` are passed to the rules constructor.
- If a `seed` is provided and your rules ctor accepts `rng`, Miniverse injects `random.Random(seed)`.
- Cognition loader calls your builder with compatible signatures (supports `profiles`, optional `use_llm`, and optional kwargs).

## 4. Implement `rules.py` (World Policy)

`rules.py` defines deterministic or stochastic world mechanics. This is world policy,
not agent decision policy.

Implement a `SimulationRules` subclass. Common hooks:

- `apply_tick(state, tick)`
- `validate_action(action, state)`
- `process_actions(state, actions, tick)` (optional but recommended for rich domains)
- `on_simulation_start(state)` / `on_simulation_end(state)` (optional)

```python
from miniverse import SimulationRules, WorldState, AgentAction

class WorkshopRules(SimulationRules):
    def __init__(self, *, rng=None, task_arrival_chance: float = 0.35):
        self.rng = rng
        self.task_arrival_chance = task_arrival_chance

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        # update shared metrics, time signals, and agent attributes
        return state

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        # enforce capacity/prerequisite constraints
        return True
```

## 5. Implement `cognition.py` (Agent Policy)

`cognition.py` defines how agents decide actions. Keep deterministic and LLM policies in one place and switch by `use_llm`.

```python
from miniverse import AgentCognition
from miniverse.cognition import Scratchpad
from miniverse.cognition.llm import LLMExecutor, LLMPlanner, LLMReflectionEngine


def build_cognition(profiles, use_llm: bool = False, **kwargs):
    cognition = {}
    for agent_id, profile in profiles.items():
        if use_llm:
            cognition[agent_id] = AgentCognition(
                planner=LLMPlanner(template_name="plan"),
                executor=LLMExecutor(template_name="default"),
                reflection=LLMReflectionEngine(template_name="reflect_diary"),
                scratchpad=Scratchpad(),
            )
        else:
            cognition[agent_id] = AgentCognition(
                planner=MyDeterministicPlanner(profile.role),
                executor=MyDeterministicExecutor(profile.role),
                reflection=MyDeterministicReflection(),
                scratchpad=Scratchpad(),
            )
    return cognition
```

Use this policy split consistently:

- **World policy** (`rules.py`): what happens when actions are applied.
- **Cognition policy** (`cognition.py`): which actions agents choose.

## 6. Build a Custom Memory Adapter

Miniverse supports pluggable memory strategies via `MemoryStrategy`.

Interface methods to implement:

- `initialize()`
- `close()`
- `add_memory(...)`
- `get_recent_memories(...)`
- `get_relevant_memories(...)`
- `clear_agent_memories(...)`

Built-ins:

- `SimpleMemoryStream`
- `ImportanceWeightedMemory`
- `BM25MemoryStrategy` (default)

### Minimal custom adapter example

```python
from miniverse import MemoryStrategy, AgentMemory

class MyMemoryStrategy(MemoryStrategy):
    async def initialize(self):
        ...

    async def close(self):
        ...

    async def add_memory(self, run_id, agent_id, tick, memory_type, content, importance=5, **kwargs) -> AgentMemory:
        ...

    async def get_recent_memories(self, run_id, agent_id, limit=10):
        ...

    async def get_relevant_memories(self, run_id, agent_id, query, limit=5):
        ...

    async def clear_agent_memories(self, run_id, agent_id):
        ...
```

Inject it when constructing `Orchestrator` programmatically:

```python
from miniverse import Orchestrator

orchestrator = Orchestrator(
    world_state=world_state,
    agents=profiles,
    world_prompt="You oversee simulation state transitions.",
    agent_prompts=agent_prompts,
    simulation_rules=rules,
    agent_cognition=cognition_map,
    memory=MyMemoryStrategy(persistence_backend),
)
```

## 7. Logging and Execution Modes

`miniverse run` supports:

- `--quiet`: final output only
- `--verbose`: detailed LLM planning/memory/reflection logs
- `--debug`: full debug traces (prompts/perception/schema retries)
- `--output json`: machine-readable final run payload
- `--world-engine deterministic|llm|auto`

For LLM runs, set env vars:

- `LLM_PROVIDER`
- `LLM_MODEL`
- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`

## 8. Generative Agents Parity

Miniverse aligns with the paper on memory, planning, reflection, and social action loops,
while keeping deterministic world policy explicit and supporting both graph and tile movement models.

See `PARITY.md` for details and current gaps (for example, no built-in browser UI).
