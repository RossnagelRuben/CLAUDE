# Generative Agents Parity

This document maps Miniverse concepts to the Stanford Generative Agents paper and
clarifies where Miniverse intentionally differs.

## Concept Mapping

| Stanford Generative Agents | Miniverse Equivalent | Status |
|---|---|---|
| Agent identity and biography | `AgentProfile` in scenario files | Implemented |
| Memory stream | `MemoryStrategy` + `AgentMemory` persistence | Implemented |
| Reflection | `ReflectionEngine` / `LLMReflectionEngine` | Implemented |
| Planning | `Planner` / `LLMPlanner` + scratchpad plan state | Implemented |
| Action selection | `Executor` / `LLMExecutor` returning `AgentAction` | Implemented |
| Inter-agent communication | `AgentAction.communication` + persisted communication memories | Implemented |
| Location model (town graph) | Tier 1 `environment_graph` | Implemented |
| Spatial movement (tile map) | Tier 2 `environment_grid` + `grid_position`/visibility | Implemented |
| World dynamics | `SimulationRules` deterministic/stochastic policies | Implemented |
| Human-facing simulation UI | CLI run logs + JSON artifacts (no built-in web UI yet) | Partial |

## Parity Notes

- Miniverse follows the same high-level cognition loop: memory retrieval -> plan -> action -> reflection.
- Miniverse keeps world dynamics explicit in `SimulationRules`, while cognition policy is separate and injectable.
- The paper's location hierarchy uses a graph-like world representation; Miniverse supports that via `environment_graph` and also supports tile-based movement via `environment_grid`.
- The paper's demo includes a browser UI; Miniverse currently focuses on CLI-first workflows and artifact-driven analysis.

## Why This Separation Matters

Miniverse intentionally separates:

- **World policy**: deterministic/stochastic physics and action processing (`rules.py`, `SimulationRules`).
- **Cognition policy**: how agents decide (`cognition.py`, planner/executor/reflection stack).

This lets you compare deterministic policy baselines against LLM behavior under the same world mechanics.

## Practical Guidance

If your goal is close paper-style replication:

1. Model locations with `environment_graph` (town/rooms/channels).
2. Add tile-level movement only when spatial fidelity is needed (`environment_grid`).
3. Use `LLMPlanner` + `LLMExecutor` + `LLMReflectionEngine` with memory retrieval enabled.
4. Keep deterministic `SimulationRules` explicit so interventions are auditable.
