# Valentines Demo

This folder contains a fully file-driven valentines demo scenario:

- `scenario.yaml`: scenario, personas, runtime extension wiring
- `rules.py`: deterministic town time/location physics
- `cognition.py`: deterministic policy + LLM policy stack
- `run.sh`: fixed 15-tick verbose LLM run + judge summary

Run from repo root:

```bash
# Optional if env vars are not already exported:
# set -a; source .env; set +a

bash demo/valentines/run.sh
```

This demo requires exported LLM env vars:
- `LLM_PROVIDER`
- `LLM_MODEL`
- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`

Manual CLI run:

```bash
uv run miniverse run demo/valentines/scenario.yaml --llm --world-engine deterministic --verbose --seed 42 --ticks 15
```
