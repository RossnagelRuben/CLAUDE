# Workshop Demo Scenario

This folder contains the preconfigured workshop demo scenario:

- `scenario.yaml`: single-file demo setup (world + persona-enriched agents)
- `rules.py`: workshop deterministic world physics
- `cognition.py`: workshop cognition policy stack (deterministic baseline policy + LLM policy)
- `run_baseline.sh`: setup + deterministic run
- `run_compare.sh`: setup + deterministic run + LLM run

Use directly from repo root:

```bash
# Optional if env vars are not already exported:
# set -a; source .env; set +a

bash demo/workshop/run_baseline.sh
bash demo/workshop/run_compare.sh
```

`run_compare.sh` requires exported LLM env vars:
- `LLM_PROVIDER`
- `LLM_MODEL`
- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`

`run_compare.sh` uses concise deterministic output for Stage 1 and verbose LLM
output for Stage 2 so planning/memory/reflection/action dynamics are visible.
It also saves run artifacts under `demo/workshop/logs/` and prints a final
baseline-vs-LLM metric comparison plus an LLM judge executive summary.
Both scripts use fixed demo ticks and do not accept runtime arguments.
Verbose output uses terminal-width-aware wrapping so long planner/reflection
lines stay aligned in narrow terminal panes.

Why `rules.py` exists:
- `scenario.yaml` configures initial state and persona data.
- Deterministic update formulas and action-processing are executable logic.
- That behavior can be tuned from `metadata.runtime.rules.kwargs` in `scenario.yaml`.
- Cognition customization (planner/executor/reflection policy + templates/cadence)
  lives in `cognition.py` and is wired via `metadata.runtime.cognition`.
- Workshop LLM communication behavior is configurable via
  `metadata.runtime.cognition.kwargs.communication_mode`:
  `exclusive` or `sidecar`.
- In `sidecar` mode, runtime guardrails suppress repetitive/non-execution
  sidecar chatter and keep memory retrieval more execution-focused.

Why deterministic classes are in `cognition.py`:
- Deterministic baseline still needs a decision policy for picking actions each tick.
- In workshop, baseline policy is rule-based (`RulePolicyPlanner/Executor/Reflection`).
- LLM mode uses a different policy from the same file (`LLMPlanner/Executor/ReflectionEngine`).
- Same world physics + different cognition policy is what makes the comparison meaningful.

Or run the scenario manually with core CLI:

```bash
uv run miniverse run demo/workshop/scenario.yaml --ticks 10
uv run miniverse run demo/workshop/scenario.yaml --llm --ticks 10
```
