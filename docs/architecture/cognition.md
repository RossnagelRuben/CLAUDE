# Cognition Architecture

Miniverse cognition is a pluggable policy stack per agent. You can mix deterministic
and LLM-driven agents in the same run.

## Core Components

| Component | Responsibility |
|---|---|
| `Planner` | Produces a multi-step `Plan` from context and memories. |
| `Executor` | Chooses one `AgentAction` for the current tick. |
| `ReflectionEngine` | Generates synthesized reflection memories on a cadence. |
| `Scratchpad` | Stores per-agent working state (plan, indices, custom flags). |
| `AgentCognition` | Bundles planner/executor/reflection/scratchpad for an agent. |

## Tick-Time Flow

For each agent each tick:

1. Build perception + retrieve memories.
2. Optionally refresh plan (planner cadence).
3. Execute one action (executor always runs).
4. Optionally emit reflections (reflection cadence).
5. Persist action/memories and continue.

The orchestrator coordinates this flow and is agnostic to whether the policy is deterministic or LLM-backed.

## Deterministic vs LLM Policies

Both are supported through the same interfaces:

- **Deterministic cognition policy**: custom planner/executor/reflection classes with fixed logic.
- **LLM cognition policy**: `LLMPlanner`, `LLMExecutor`, `LLMReflectionEngine` from `miniverse/cognition/llm.py`.

A common pattern is to implement both in scenario-local `cognition.py` and switch by `use_llm`.

## Prompting Integration

LLM modules render prompt templates using `PromptContext` data:

- Agent profile and relationships
- Current perception
- Relevant memories
- Plan and scratchpad state
- Optional scenario-specific extras

See `../PROMPTS.md` for placeholder contracts and template composition.

## Communication Semantics

`AgentAction` can include `communication` payloads. During persistence:

- Action records keep structured action details.
- Message contents are persisted as sender/recipient memories.

This keeps communication available for retrieval and reflection while preserving clean action schemas.

## Policy Boundary

Keep this boundary explicit:

- **Cognition policy** answers: "What should this agent do now?"
- **World policy** answers: "What happens when that action is applied?"

World policy lives in `SimulationRules`; cognition policy lives in planners/executors/reflection engines.
