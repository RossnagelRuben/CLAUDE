# Outstanding Issues

_Last updated: 2025-12-29_

This file tracks known issues aligned with the current direction (see VISION.md, ROADMAP.md).

---

## Critical Path Issues

These block Phase 1-3 of the roadmap.

### P0: CLI Entry Point Missing

**Problem**: Miniverse is library-only. No `miniverse` command exists.

**Impact**:
- Users must write Python to run simulations
- Claude can't operate Miniverse via Bash
- Blocks AI-native workflow vision

**Plan** (Phase 1):
1. Create `miniverse/cli.py` using `click` or `typer`
2. Implement: `init`, `run`, `status`, `export`
3. Wire into `pyproject.toml` entry points
4. Add scenario templates to `miniverse/templates/`

**Effort**: 1-2 weeks

---

### P1: No Scenario Templates

**Problem**: Users must create scenarios from scratch. No starting points for common research questions.

**Impact**:
- High barrier to entry
- Can't demonstrate value quickly
- Blocks validation work (Phase 2)

**Plan** (Phase 1):
1. Create `miniverse/templates/` directory
2. Build 3 initial templates:
   - `org-hierarchy` – Basic org structure
   - `information-cascade` – Rumor spread
   - `coordination-game` – Team coordination
3. Each template includes: scenario.json + rules.py + README

**Effort**: 1 week

---

### P2: No Branching/Fork Capability

**Problem**: Can't fork a simulation to test counterfactuals. No intervention API.

**Impact**:
- Can't answer "what if" questions
- Limits research utility
- Blocks Phase 3 deliverables

**Plan** (Phase 3):
1. Add state serialization to Orchestrator
2. Implement `sim.fork()` method
3. Add `sim.intervene(action=...)` API
4. Create `miniverse compare` CLI command

**Effort**: 2-3 weeks

---

## Important Issues

These affect quality but don't block critical path.

### A3: Memory Retrieval Quality

**Problem**: `SimpleMemoryStream.get_relevant_memories()` uses naive substring matching.

**Impact**:
- Poor ranking for semantically similar queries
- Users forced to write custom retrieval for quality scenarios

**Plan**:
- Short-term: Document limitations, provide example embedding adapter
- Mid-term: Implement importance + recency + semantic retrieval
- Future: BM25 hybrid as optional package

**Effort**: 2-4 hours for docs; more for algorithmic upgrade

---

### A8: Logging UX

**Problem**: Debug output is hard to follow. Agent decisions scattered across phases.

**Impact**:
- 888KB log files with 5% signal
- Key information buried in JSON
- Hard to debug agent behavior

**Plan**:
1. Add `MINIVERSE_LOG_LEVEL=0|1|2|3` (minimal/summary/detailed/debug)
2. Group output by agent, not by phase
3. Show message content in summaries

**Effort**: 1-2 days for Phase 1

---

### A9: No Behavioral Scoring

**Problem**: No systematic way to evaluate agent behavior across dimensions.

**Impact**:
- Can't quantify simulation quality
- Subjective analysis only
- Blocks Phase 4 deliverables

**Plan** (Phase 4):
1. Define social science dimensions (goal alignment, coordination, trust, etc.)
2. Implement post-hoc scorer using LLM evaluation
3. Add transcript citation for evidence linking

**Effort**: 1-2 weeks

---

### A10: No Research Export

**Problem**: Outputs not directly usable in R/Python/Stata workflows.

**Impact**:
- Researchers must manually extract data
- Reduces adoption
- Blocks Phase 6 deliverables

**Plan** (Phase 6):
1. Add `miniverse export --format csv|json|parquet`
2. Standardize output variables
3. Create reproducibility bundle

**Effort**: 1 week

---

## Deferred Issues

These are valuable but not on the critical path.

### D1: Visualization Dashboard

The old `plan.md` described a web visualization system. This remains valuable but is deferred until core research platform is solid.

**Archived**: `docs/archive/plan-visualization-2025-03.md`

---

### D2: Advanced Memory (Embeddings/BM25)

Semantic memory retrieval would improve agent behavior realism. Deferred until validation confirms baseline is useful.

---

### D3: Multi-Model Comparison

Run same scenario across GPT-4, Claude, Gemini to test robustness. Useful for research but later.

---

## Test/Build Status

- Unit/integration tests: 39 passing
- Branch: `cev-redesign` (active development)
- Last test run: `UV_CACHE_DIR=.uv-cache uv run pytest`

---

## Issue Lifecycle

1. **New issues** go in appropriate priority section
2. **Working on it** → move to top of section, add "WIP:" prefix
3. **Resolved** → remove from this file, note in commit/PR
4. **Deferred** → move to Deferred section with rationale

---

## Document Owner

Kenneth ([@local0ptimist](https://x.com/local0ptimist))

Next review: After Phase 1 complete

---

_This file complements ROADMAP.md. Roadmap = what we're building. Issues = what's blocking us._

-- Claude | 2025-12-29
