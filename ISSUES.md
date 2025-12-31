# Outstanding Issues

_Last updated: 2025-12-30_

This file tracks known issues aligned with the current direction (see VISION.md, ROADMAP.md).

---

## Resolved (Phase 1 + 1.1 + 1.5)

These were critical path blockers, now complete:

- ~~P0: CLI Entry Point~~ → `miniverse run/list/info` implemented
- ~~P1: No Scenario Templates~~ → `org-hierarchy` template complete
- ~~LLM Performance~~ → `--world-engine deterministic` flag, gpt-5-nano default, Ollama support
- ~~A1: Memory Retrieval~~ → `BM25MemoryStrategy` with IDF scoring, recency decay, importance weighting
- ~~A2: Prompt Bloat~~ → `execute_tick` prompt reduced from ~85 to ~20 lines (3 concise examples)

---

## Active Issues

### A3: No Branching/Fork Capability

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

### A4: No Behavioral Scoring

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

### A5: No Research Export

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

### D1: Visualization Dashboard

The old `plan.md` described a web visualization system. Deferred until core research platform is solid.

### D2: Embedding-Based Memory

Semantic memory retrieval with embeddings. Deferred until BM25 proves insufficient.

### D3: Multi-Model Comparison

Run same scenario across GPT-4, Claude, Gemini. Useful for research but later.

---

## Test/Build Status

- Unit/integration tests: 43 passing
- Branch: `cev-redesign` (active development)
- Last test run: `UV_CACHE_DIR=.uv-cache uv run python -m pytest`

---

## Priority Order

1. **A3: Branching** - Core research capability (Phase 3)
2. **A4: Scoring** - Quantify results (Phase 4)
3. **A5: Export** - Research workflow integration (Phase 6)

---

_This file complements ROADMAP.md. Roadmap = what we're building. Issues = what's blocking us._

-- Shoshin | 2025-12-30
