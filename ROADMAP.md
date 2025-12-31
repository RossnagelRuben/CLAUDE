# Miniverse Roadmap

_Last updated: 2025-12-30_

This document translates VISION.md into actionable implementation phases. Each phase is designed to be independently valuable—we ship working software at each milestone.

---

## Current State

**Phase 1 (CLI Foundation): COMPLETE** (2025-12-29)
**Phase 1.1 (LLM Performance): COMPLETE** (2025-12-30)

### What's implemented:
- `miniverse run/list/info` commands via Typer CLI
- Template system with dynamic rules loading
- `org-hierarchy` scenario template (3 agents, graph environment)
- JSON output mode for scripts/Claude
- Deterministic and LLM cognition modes
- `--world-engine` flag to decouple agent cognition from world updates
- Ollama support for local open-source models
- Default model: gpt-5-nano
- 43 tests passing

### What's next (Priority order):
1. **Memory system upgrade** - BM25 ranking, smarter retrieval (blocks Phase 2)
2. **Prompt optimization** - Reduce token usage, improve context building
3. **More scenario templates** - information-cascade, coordination-game (Phase 2)
4. **Branching/intervention** - Fork simulations, inject changes (Phase 3)

---

## Phase 1: CLI Foundation

**Goal**: Make Miniverse usable from the command line. Claude should be able to drive simulations without writing Python.

### Deliverables

1. **`miniverse` CLI entry point** (`miniverse/cli.py`)
   ```bash
   miniverse init <description|--template NAME>
   miniverse run [--ticks N] [--seed S] [--llm]
   miniverse status
   miniverse export [--format csv|json|parquet]
   ```

2. **Scenario templates** (`miniverse/templates/`)
   - `org-hierarchy`: Basic organizational structure (manager + reports)
   - `information-cascade`: Rumor spread through network
   - `coordination-game`: Team coordination with communication

3. **Structured output format**
   - JSON logs with tick-by-tick state
   - Summary statistics (actions per agent, message counts, etc.)
   - Export to CSV for analysis

### Implementation Notes

- Use `click` or `typer` for CLI framework
- Templates are JSON scenarios + Python rules bundled together
- `miniverse init` can either load a template or generate one from natural language description (requires LLM)

### Success Criteria

- [ ] `miniverse run --template org-hierarchy --ticks 20` works end-to-end
- [ ] Output is structured and parseable
- [ ] Claude can run simulations via Bash tool without writing Python

### Estimated Effort: 1-2 weeks

---

## Phase 2: Social Science Validation

**Goal**: Prove simulations capture real social dynamics by replicating a well-known finding.

### Deliverables

1. **Granovetter weak ties scenario**
   - Network with strong ties (frequent communication) and weak ties (occasional)
   - Information (job opportunity) introduced to one agent
   - Measure: Does information spread further through weak ties?
   - Compare simulation results to Granovetter's empirical findings

2. **Information cascade scenario**
   - Sequential decision-making with social observation
   - Agents observe others' choices before deciding
   - Measure: Do cascades form? Under what conditions do they break?

3. **Validation report**
   - Document methodology
   - Show correlation between simulation and expected behavior
   - Identify where simulation diverges and why

### Implementation Notes

- May need to enhance memory retrieval for better social dynamics
- Relationship strength should affect information flow
- Need metrics infrastructure to measure diffusion patterns

### Success Criteria

- [ ] Weak ties scenario shows expected information diffusion pattern
- [ ] Results are reproducible (same seed = same outcome)
- [ ] Validation report publishable as technical note

### Estimated Effort: 2-3 weeks

---

## Phase 3: Intervention API

**Goal**: Enable counterfactual analysis by forking simulations and injecting changes.

### Deliverables

1. **Simulation branching**
   ```python
   # Fork at decision point
   branch_a = sim.fork()
   branch_b = sim.fork()

   # Run different interventions
   branch_a.intervene(action="promote Alice")
   branch_b.intervene(action="promote Bob")

   # Compare outcomes
   diff = sim.compare([branch_a, branch_b])
   ```

2. **CLI intervention support**
   ```bash
   # Run baseline
   miniverse run --ticks 50 --save baseline

   # Fork and intervene
   miniverse fork baseline --at-tick 25 --name treatment
   miniverse intervene treatment --action "CEO announces layoffs"
   miniverse run treatment --ticks 50

   # Compare
   miniverse compare baseline treatment --metrics trust,productivity
   ```

3. **Intervention catalog**
   - Personnel changes (hire, fire, promote, reorg)
   - Information events (announcements, rumors, leaks)
   - Resource changes (budget cuts, expansions)
   - Policy changes (new rules, removed constraints)

### Implementation Notes

- Requires state serialization/deserialization
- Memory must be forkable (copy-on-write or deep copy)
- Orchestrator needs checkpoint/restore capability

### Success Criteria

- [ ] Can fork a running simulation and run two branches in parallel
- [ ] Interventions modify simulation state predictably
- [ ] Comparison outputs quantitative differences

### Estimated Effort: 2-3 weeks

---

## Phase 4: Multi-Dimensional Scoring

**Goal**: Evaluate agent behavior systematically, inspired by Petri's 36-dimension scoring.

### Deliverables

1. **Behavioral dimensions for social simulation**
   ```python
   SOCIAL_DIMENSIONS = {
       "goal_alignment": "Agent pursues stated goals effectively",
       "information_fidelity": "Agent accurately transmits information",
       "relationship_maintenance": "Agent maintains social connections",
       "coordination_success": "Agent coordinates effectively with others",
       "trust_calibration": "Agent's trust levels match actual reliability",
       "emergent_leadership": "Agent takes initiative appropriately",
       "norm_compliance": "Agent follows group norms",
       "innovation": "Agent introduces novel solutions",
   }
   ```

2. **Scorer implementation**
   - Post-simulation analysis of action transcripts
   - LLM-based evaluation against dimension definitions
   - Structured output with evidence citations

3. **Aggregate metrics**
   - Per-agent scores across dimensions
   - Population-level statistics
   - Temporal evolution (how scores change over simulation)

### Implementation Notes

- Scorer can run post-hoc (doesn't need to be in the loop)
- Use Petri's citation pattern for evidence linking
- Consider caching scores for efficiency

### Success Criteria

- [ ] Can score a completed simulation across 8+ dimensions
- [ ] Scores are reproducible and explainable
- [ ] Output format supports statistical analysis

### Estimated Effort: 1-2 weeks

---

## Phase 5: Claude Code Skill

**Goal**: Create a skill that lets Claude guide users through simulation design and analysis.

### Deliverables

1. **`/miniverse` skill**
   - Understands simulation design patterns
   - Knows all CLI commands and options
   - Can generate scenario configurations from natural language
   - Guides users through hypothesis → simulation → analysis workflow

2. **Skill components**
   - Research question clarification
   - Scenario template selection/customization
   - Parameter tuning guidance
   - Results interpretation

3. **Example workflows encoded**
   - "How does information spread in my org?"
   - "What happens if we restructure teams?"
   - "Will this policy change improve coordination?"

### Implementation Notes

- Skill lives in `.claude/skills/miniverse.md` or similar
- Should reference CLI commands, not library internals
- Include common pitfalls and best practices

### Success Criteria

- [ ] User can say "help me simulate X" and Claude guides them end-to-end
- [ ] Skill handles common research scenarios without additional prompting
- [ ] Works with Claude Code marketplace (when available)

### Estimated Effort: 1 week (documentation-heavy)

---

## Phase 6: Research-Ready Export

**Goal**: Make outputs directly usable in academic workflows.

### Deliverables

1. **Export formats**
   - CSV with one row per tick per agent
   - Parquet for large simulations
   - JSON for programmatic access
   - R data frames via `pyarrow`

2. **Standard variables**
   - Tick, agent_id, action_type, target, content
   - Relationship snapshots
   - Resource/attribute values
   - Custom metrics from scoring

3. **Reproducibility package**
   - `miniverse export --reproducibility` bundles:
     - Scenario configuration
     - Random seeds
     - Model versions
     - Full transcript
     - Summary statistics

### Implementation Notes

- Build on existing persistence layer
- Add export commands to CLI
- Consider Jupyter notebook integration

### Success Criteria

- [ ] Researcher can load simulation data into R/Python in one command
- [ ] Reproducibility package allows exact replication
- [ ] Format documented for third-party tools

### Estimated Effort: 1 week

---

## Phase 7: Calibration Infrastructure

**Goal**: Enable validation of simulations against real-world data.

### Deliverables

1. **Calibration workflow**
   ```bash
   # Load real org data (anonymized)
   miniverse calibrate --data org_network.csv --target information_flow

   # Run parameter search
   miniverse calibrate --search personality_weights,memory_decay

   # Validate on held-out data
   miniverse validate --model calibrated.json --test holdout.csv
   ```

2. **Metrics for calibration**
   - Information diffusion rate
   - Network clustering
   - Action frequency distributions
   - Communication patterns

3. **Case study**
   - Partner with org or use public dataset (email networks, Slack dumps)
   - Calibrate simulation parameters
   - Report correlation between simulation and reality

### Implementation Notes

- This is research-heavy, not just engineering
- May require academic collaboration
- Start with publicly available datasets

### Success Criteria

- [ ] Calibration workflow documented
- [ ] One case study with quantitative validation
- [ ] Results publishable as technical report

### Estimated Effort: 4-8 weeks (research timeline)

---

## Phase 1.5: Memory & Prompt Quality (NEW)

**Goal**: Improve agent cognition quality before adding more features.

### Deliverables

1. **BM25 Memory Retrieval**
   - Port BM25 implementation from Stanford Valentines project
   - Replace naive substring matching in `get_relevant_memories()`
   - Weight by: term frequency, recency, importance

2. **Prompt Optimization**
   - Reduce executor prompt from ~150 lines of examples
   - Smarter context building (not just JSON dumps)
   - Template-specific prompt tuning

3. **Memory Debugging**
   - `--debug` mode shows what memories are retrieved
   - Visibility into what context agents actually see

### Success Criteria
- [ ] `get_relevant_memories()` uses BM25 ranking
- [ ] Executor prompts < 50 lines of examples
- [ ] Debug mode shows memory retrieval clearly

### Estimated Effort: 1 week

---

## Deferred / Future Work

These are valuable but not on the critical path:

### Visualization Dashboard
The existing `plan.md` describes a visualization system. This is still valuable but secondary to research utility. Defer until core platform is solid.

### Embedding-Based Memory
Semantic search using embeddings. Deferred until BM25 proves insufficient.

### Multi-Model Comparison
Run same scenario across GPT-4, Claude, Gemini. Useful for robustness testing.

### Real-Time Collaboration
Multiple researchers observing/intervening in same simulation. Enterprise feature.

---

## Dependencies Between Phases

```
Phase 1 (CLI) ──────────────────────────────────┐
     │                                          │
     ▼                                          ▼
Phase 2 (Validation) ────────────────────► Phase 5 (Skill)
     │                                          │
     ▼                                          │
Phase 3 (Intervention) ◄────────────────────────┘
     │
     ▼
Phase 4 (Scoring) ──────► Phase 6 (Export) ──────► Phase 7 (Calibration)
```

**Critical path**: Phase 1 → Phase 2 → Phase 3 → Phase 6

Phases 4 and 5 can proceed in parallel once Phase 1 is complete.

---

## How to Contribute

1. **Pick a phase** that interests you
2. **Check ISSUES.md** for related known issues
3. **Create a branch** from `cev-redesign`
4. **Implement incrementally** with tests
5. **Update this roadmap** as you learn

---

_This roadmap is a living document. Update it as implementation reveals new constraints or opportunities._

-- Claude | 2025-12-29
