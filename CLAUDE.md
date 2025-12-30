# Claude Code Guide – Miniverse

Authoritative instructions for working on the **Miniverse** codebase.

---

## Start Here

**Read these documents in order:**

1. **VISION.md** – The north star. What Miniverse should become. Read this first.
2. **ROADMAP.md** – Phased implementation plan. What we're building and when.
3. **This file (CLAUDE.md)** – Operational guidance for development.
4. **ISSUES.md** – Known issues and immediate priorities.

---

## What is Miniverse?

Miniverse is a **CLI-first platform for computational social science and organizational simulation**.

The goal: let researchers run "what if" experiments on social dynamics that are impossible in the real world. Simulate how rumors spread, how reorgs affect trust, how policies change behavior—all reproducibly and at scale.

### Core Capabilities

- **CLI interface**: `miniverse init`, `run`, `analyze`, `compare`, `export`
- **LLM-driven agents**: Personality, goals, memory, planning, reflection
- **Deterministic physics**: Controllable rules layer for domain-specific constraints
- **Branching/intervention**: Fork simulations, inject changes, compare outcomes
- **Research-ready outputs**: Structured data for statistical analysis

### Current Status

The library core is solid:
- Orchestrator with dependency injection
- Cognition stack (planner, executor, reflection)
- Memory and persistence strategies
- Environment tiers (abstract → graph → grid)
- 39 passing tests

**Active work** (branch `cev-redesign`): Building CLI, scenario templates, validation infrastructure per ROADMAP.md.

---

## Development Priorities

### 1. CLI First

Every feature should be usable from the command line. The library exists to support the CLI, not the other way around.

```bash
# This is the target interface
miniverse init --template org-hierarchy
miniverse run --ticks 100 --seed 42
miniverse analyze --metrics diffusion,coordination
miniverse export --format csv
```

### 2. Research Validity

Simulations must produce believable, reproducible behavior. Prioritize:
- Deterministic seeding (same seed = same outcome)
- Full logging (every decision is traceable)
- Validation against known social science findings

### 3. AI-Native Workflows

Design for Claude to operate. The `/miniverse` skill should let Claude guide users through:
- Research question → scenario design
- Simulation execution → analysis
- Interpretation → export

---

## Quick Orientation

### Key Files

| File | Purpose |
|------|---------|
| `miniverse/cli.py` | CLI entry point (target: Phase 1) |
| `miniverse/orchestrator.py` | Simulation loop, dependency injection |
| `miniverse/schemas.py` | All Pydantic models |
| `miniverse/cognition/` | Planner, executor, reflection, prompts |
| `miniverse/memory.py` | Memory strategies |
| `miniverse/persistence.py` | State persistence backends |
| `miniverse/templates/` | Scenario templates (target: Phase 1) |

### Common Commands

```bash
# Install dependencies
uv sync

# Run tests
UV_CACHE_DIR=.uv-cache uv run pytest

# Run workshop example (deterministic)
uv run python examples/workshop/run.py --ticks 6

# Run workshop example (LLM cognition)
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-4o
export OPENAI_API_KEY=your_key
uv run python examples/workshop/run.py --llm --ticks 8

# Debug flags
DEBUG_LLM=true      # Show all LLM prompts/responses
DEBUG_MEMORY=true   # Show memory operations
DEBUG_PERCEPTION=true  # Show agent perceptions
MINIVERSE_VERBOSE=true # Show action details
```

---

## Architecture Overview

### Tick Flow

1. **Physics**: `SimulationRules.apply_tick()` updates deterministic state
2. **Perception**: Build partial observability view for each agent
3. **Cognition**: Planner/executor decide agent actions
4. **Actions**: Process actions (deterministic or LLM world engine)
5. **Memory**: Store observations for future context
6. **Reflection**: Periodic synthesis of experiences
7. **Persistence**: Save state, actions, memories

### Core Principles

1. **Dependency Injection**: Orchestrator receives all dependencies as constructor args
2. **Protocols for Interfaces**: `Planner`, `Executor`, `ReflectionEngine`, `MemoryStrategy`, `PersistenceStrategy`
3. **Structured Data**: Pydantic models everywhere
4. **Deterministic + Emergent**: Physics is predictable; cognition is creative

---

## How to Work Safely

### Before You Code

1. Check VISION.md – does this align with where we're going?
2. Check ROADMAP.md – which phase does this belong to?
3. Check ISSUES.md – is there a related known issue?

### While You Code

1. Write tests alongside features
2. Use existing patterns (see `tests/` for examples)
3. Keep changes focused – one thing per PR
4. Update docs if you change behavior

### Before You Submit

1. Run `UV_CACHE_DIR=.uv-cache uv run pytest`
2. Verify examples still work
3. Update ISSUES.md if you resolve something
4. Update ROADMAP.md if you complete a deliverable

---

## Code Style

### Patterns to Follow

- Pydantic for all data models
- Protocols for pluggable interfaces
- Async for IO-bound operations (LLM, persistence)
- Type hints everywhere

### Naming Conventions

- `*Strategy` for pluggable backends (Persistence, Memory)
- `*Engine` for processing modules (Reflection)
- `*State` for Pydantic schemas (WorldState, AgentStatus)
- `*Rules` for deterministic physics (WorkshopRules)

### Error Handling

- Tenacity for LLM retries
- Schema feedback for self-correction
- Clear exceptions for invalid states

---

## What NOT to Build (Yet)

Per ROADMAP.md, these are deferred:

- **Visualization dashboard** – Valuable but not on critical path
- **Advanced memory retrieval** – BM25/embeddings are nice-to-have
- **Multi-model comparison** – Useful for research but later
- **Real-time collaboration** – Enterprise feature, much later

Focus on: **CLI → Validation → Intervention → Export**

---

## Petri-Inspired Patterns

We're adopting patterns from Anthropic's [Petri](https://github.com/anthropics/petri) project:

### Branching/Rollback
```python
# Fork at decision point
branch = sim.fork()
branch.intervene(action="promote Alice")
result = branch.run(ticks=50)
```

### Multi-Dimensional Scoring
```python
SOCIAL_DIMENSIONS = {
    "goal_alignment": "Agent pursues stated goals",
    "information_fidelity": "Agent transmits info accurately",
    "coordination_success": "Agent coordinates effectively",
}
```

### Transcript Citations
```python
# Evidence linking for analysis
citation = Citation(
    tick=23,
    agent="alice",
    action="communicate",
    quote="Did you hear about the reorg?"
)
```

---

## Quick Checklist for New Features

- [ ] Aligns with VISION.md?
- [ ] Fits a ROADMAP.md phase?
- [ ] Has CLI interface (or contributes to one)?
- [ ] Has tests?
- [ ] Updates relevant docs?
- [ ] Maintains backward compatibility?
- [ ] Passes `uv run pytest`?

---

## Getting Help

- **Understanding the vision**: Read VISION.md
- **Finding what to work on**: Read ROADMAP.md
- **Known issues**: Read ISSUES.md
- **Architecture questions**: Read `docs/architecture/`
- **When in doubt**: Ask before implementing new patterns

---

## Document Hierarchy

```
VISION.md          ← North star (what we're building toward)
    │
    ▼
ROADMAP.md         ← Implementation phases (how we get there)
    │
    ▼
CLAUDE.md          ← Operational guidance (how to work)
    │
    ▼
ISSUES.md          ← Known issues (what's broken/missing)
    │
    ▼
docs/              ← Deep dives (architecture, usage, research)
```

---

_Stay within these guardrails and Miniverse becomes the standard platform for computational social science._

-- Claude | 2025-12-29
