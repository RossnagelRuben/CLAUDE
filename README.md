<p align="center"><img src=".github/images/header.png" alt="Miniverse header" width="75%"></p>

<p align="center"><em>In silico social science. Simulate what you can't experiment on.</em></p>

<p align="center">
  <a href="https://github.com/miniverse-ai/miniverse"><img src="https://img.shields.io/badge/status-alpha-orange" alt="Status"></a>
  <a href="https://github.com/miniverse-ai/miniverse"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python"></a>
  <a href="https://github.com/miniverse-ai/miniverse"><img src="https://img.shields.io/badge/designed%20with-Claude%20%26%20GPT--5-6f42c1" alt="Designed with AI"></a>
  <a href="https://x.com/local0ptimist"><img src="https://img.shields.io/badge/created%20by-@local0ptimist-1d9bf0" alt="Creator"></a>
</p>

---

## What is Miniverse?

Miniverse is a **CLI-first platform for computational social science and organizational simulation**.

Traditional social science has a fundamental limitation: you can't run experiments on societies. You can't A/B test policy changes, replay historical decisions, or observe counterfactuals.

Miniverse changes this. Build believable agent simulations, inject interventions, and analyze what emerges—all reproducibly and at scale.

**Use cases:**
- How does a rumor spread through an organization?
- What happens when you restructure teams?
- Will this policy improve coordination or create friction?
- How do information cascades form and break?

**Alpha:** Core architecture is stable. CLI and research tooling under active development.

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/miniverse-ai/miniverse.git
cd miniverse
uv sync

# Run workshop example (deterministic mode)
uv run python examples/workshop/run.py --ticks 10

# Run with LLM cognition
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-4o
export OPENAI_API_KEY=your_key
uv run python examples/workshop/run.py --llm --ticks 10
```

---

## How It Works

Miniverse combines **deterministic physics** with **emergent LLM cognition**:

```
┌─────────────────────────────────────────────────────────────────┐
│                         TICK LOOP                                │
├─────────────────────────────────────────────────────────────────┤
│  1. Physics      │  SimulationRules update resources, events    │
│  2. Perception   │  Build partial observability for each agent  │
│  3. Cognition    │  Planner/executor decide actions (LLM/rule)  │
│  4. Actions      │  Process actions, update world state         │
│  5. Memory       │  Store observations for future context       │
│  6. Reflection   │  Periodic synthesis of experiences           │
│  7. Persistence  │  Save state for analysis and replay          │
└─────────────────────────────────────────────────────────────────┘
```

**Physics is predictable.** You control resource dynamics, constraints, and events.

**Cognition is emergent.** Agents plan, communicate, and adapt based on their goals, personality, and memories.

---

## Examples

### Workshop Scenario

A team coordination simulation with mechanics, analysts, and supervisors managing a repair backlog.

```bash
# Deterministic baseline
uv run python examples/workshop/run.py --ticks 20

# With LLM cognition
uv run python examples/workshop/run.py --llm --ticks 20

# Monte Carlo (100 trials with different seeds)
uv run python examples/workshop/monte_carlo.py --runs 100 --ticks 20
```

### Snake (Grid World)

Tier-2 spatial simulation demonstrating grid-based movement and ASCII perception.

```bash
uv run python examples/snake/run.py --ticks 40
```

### Smallville Valentine's

Recreation of Stanford Generative Agents' party coordination scenario.

```bash
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-4o
uv run python examples/smallville/valentines_party.py
```

---

## Architecture

### Core Components

| Component | Purpose |
|-----------|---------|
| **Orchestrator** | Tick loop, dependency injection, persistence |
| **SimulationRules** | Deterministic physics (resources, constraints, events) |
| **Cognition Stack** | Planner, executor, reflection, scratchpad |
| **Memory Strategy** | Store and retrieve agent experiences |
| **Persistence** | Save state (in-memory, JSON, PostgreSQL) |
| **Environment** | Tier 0 (abstract), Tier 1 (graph), Tier 2 (grid) |

### Design Principles

1. **CLI-First**: Every feature usable from command line
2. **Dependency Injection**: Swap strategies without modifying core
3. **Reproducibility**: Seed everything, log everything
4. **Research-Ready**: Structured outputs for statistical analysis

---

## Debugging

```bash
# Show LLM prompts and responses
DEBUG_LLM=true uv run python examples/workshop/run.py --llm

# Show memory operations
DEBUG_MEMORY=true uv run python examples/workshop/run.py --llm

# Show agent perceptions
DEBUG_PERCEPTION=true uv run python examples/workshop/run.py --llm

# Maximum verbosity
DEBUG_LLM=true DEBUG_MEMORY=true MINIVERSE_VERBOSE=true \
  uv run python examples/workshop/run.py --llm
```

---

## Documentation

| Document | Purpose |
|----------|---------|
| [VISION.md](VISION.md) | Project direction and goals |
| [ROADMAP.md](ROADMAP.md) | Implementation phases |
| [CLAUDE.md](CLAUDE.md) | Development guidelines |
| [ISSUES.md](ISSUES.md) | Known issues and priorities |
| [docs/USAGE.md](docs/USAGE.md) | Building simulations |
| [docs/PROMPTS.md](docs/PROMPTS.md) | Prompt system guide |
| [docs/architecture/](docs/architecture/) | Deep dives |

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for full details.

**Phase 1: CLI Foundation** – `miniverse init/run/analyze/export`
**Phase 2: Validation** – Replicate known social science findings
**Phase 3: Intervention** – Fork simulations, inject changes, compare outcomes
**Phase 4: Scoring** – Multi-dimensional behavioral evaluation
**Phase 5: Skill** – Claude Code integration for guided workflows
**Phase 6: Export** – Research-ready data formats
**Phase 7: Calibration** – Validate against real-world data

---

## Contributing

```bash
# Run tests before submitting
UV_CACHE_DIR=.uv-cache uv run pytest
```

- Read [VISION.md](VISION.md) to understand direction
- Check [ROADMAP.md](ROADMAP.md) for what to work on
- Keep changes focused; include test coverage
- Update docs when changing behavior

---

## Inspirations

- [Stanford Generative Agents](https://arxiv.org/abs/2304.03442) – Original emergent behavior research
- [Anthropic Petri](https://github.com/anthropics/petri) – Auditing patterns (branching, scoring)
- [AgentTorch](https://github.com/AgentTorch/AgentTorch) – Large-scale policy simulation
- [Mesa](https://github.com/projectmesa/mesa) – Python ABM framework

---

## Credits

- Creator: [Kenneth / @local0ptimist](https://x.com/local0ptimist)
- Built with: Claude, GPT-5 Codex
- Research notes: [docs/RESEARCH.md](docs/RESEARCH.md)

## License

MIT. Fork responsibly.
