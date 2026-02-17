#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCENARIO_PATH="$ROOT_DIR/demo/workshop/scenario.yaml"
LOG_DIR="$ROOT_DIR/demo/workshop/logs"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
BASELINE_JSON="$LOG_DIR/workshop_baseline_${STAMP}.json"
LLM_LOG="$LOG_DIR/workshop_llm_${STAMP}.log"
DEMO_TICKS=10

if [ "$#" -gt 0 ]; then
  echo "This script uses fixed demo settings and does not accept arguments."
  echo "Run: bash demo/workshop/run_compare.sh"
  exit 2
fi

cd "$ROOT_DIR"

echo ""
echo "================================================================================"
echo "Workshop Comparison Demo"
echo "================================================================================"
uv run python - "$SCENARIO_PATH" <<'PY'
import sys
from pathlib import Path
import yaml

scenario_path = Path(sys.argv[1])
data = yaml.safe_load(scenario_path.read_text()) or {}

print("")
print("Scenario setup (from file):")
print(f"  - File: {scenario_path}")
print(f"  - Name: {data.get('name', '-')}")
print(f"  - Description: {data.get('description', '-')}")
print(f"  - Agents: {len(data.get('agents', []))}")

runtime = ((data.get("metadata") or {}).get("runtime") or {})
cognition = (runtime.get("cognition") or {})
cognition_kwargs = (cognition.get("kwargs") or {})
comm_mode = cognition_kwargs.get("communication_mode")
if comm_mode:
    print(f"  - Communication mode: {comm_mode}")

demo_meta = ((data.get("metadata") or {}).get("demo") or {})
if demo_meta.get("scene"):
    print(f"  - Scene: {demo_meta.get('scene')}")
if demo_meta.get("persona_set"):
    print(f"  - Persona set: {demo_meta.get('persona_set')}")

resources = (data.get("resources") or {}).get("metrics", {})
for key, stat in resources.items():
    if isinstance(stat, dict):
        label = stat.get("label", key)
        value = stat.get("value", "?")
        unit = stat.get("unit", "")
        print(f"  - Resource {label}: {value} {unit}".rstrip())

print("")
print("Agents:")
for agent in data.get("agents", []):
    profile = agent.get("profile") or {}
    status = agent.get("status") or {}
    meta = status.get("metadata") or {}
    print(f"  - {profile.get('name', profile.get('agent_id', '-'))} ({profile.get('role', '-')})")
    if profile.get("personality"):
        print(f"    personality: {profile.get('personality')}")
    if meta.get("persona"):
        print(f"    persona: {meta.get('persona')}")
PY

echo ""
echo "Stage 1/2: deterministic baseline"
echo "Cognition policy: rule-based deterministic policy from demo/workshop/cognition.py (use_llm=false)"
echo "Ticks per stage: $DEMO_TICKS (fixed)"
echo "Command: uv run miniverse run \"$SCENARIO_PATH\" --world-engine deterministic --seed 42 --ticks $DEMO_TICKS --output json"
echo ""
uv run miniverse run "$SCENARIO_PATH" --world-engine deterministic --seed 42 --ticks "$DEMO_TICKS" --output json > "$BASELINE_JSON"
uv run python - "$BASELINE_JSON" "$SCENARIO_PATH" <<'PY'
import json
import sys
from pathlib import Path

import yaml

payload = json.loads(Path(sys.argv[1]).read_text())
scenario = yaml.safe_load(Path(sys.argv[2]).read_text()) or {}
metrics = ((payload.get("final_state") or {}).get("resources") or {}).get("metrics", {})
initial_metrics = ((scenario.get("resources") or {}).get("metrics") or {})

def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def find(label: str, default: str = "-"):
    for stat in metrics.values():
        if isinstance(stat, dict) and stat.get("label") == label:
            return stat.get("value", default)
    return default

initial_pending = None
for stat in initial_metrics.values():
    if isinstance(stat, dict) and stat.get("label") == "Pending Tasks":
        initial_pending = _as_float(stat.get("value"))
        break
if initial_pending is None:
    initial_pending = 0.0

pending_final = _as_float(find("Pending Tasks"))
arrived_total = _as_float(find("Tasks Arrived (Total)"))
completed_total = _as_float(find("Tasks Completed (Total)"))
queue_expected = None
if arrived_total is not None and completed_total is not None:
    queue_expected = initial_pending + arrived_total - completed_total

print("")
print(f"Run ID: {payload.get('run_id', '-')}")
print(f"Completed {payload.get('ticks_completed', '-') } ticks")
print("")
print("Final resources:")
print(f"  Pending Tasks: {find('Pending Tasks')}")
print(f"  Execution Capacity: {find('Execution Capacity')}")
print(f"  Tasks Arrived (Total): {find('Tasks Arrived (Total)')}")
print(f"  Tasks Completed (Total): {find('Tasks Completed (Total)')}")
if queue_expected is not None and pending_final is not None:
    print("")
    print("Queue accounting:")
    print(
        f"  Pending Tasks = initial ({initial_pending:.0f}) + arrivals ({arrived_total:.0f}) "
        f"- completions ({completed_total:.0f}) = {queue_expected:.0f}"
    )
PY

echo ""
echo "Stage 2/2: LLM personas"
echo "Cognition policy: LLM policy from demo/workshop/cognition.py (use_llm=true)"
echo "Command: PYTHONUNBUFFERED=1 uv run miniverse run \"$SCENARIO_PATH\" --llm --world-engine deterministic --verbose --seed 42 --ticks $DEMO_TICKS"
echo "Note: set LLM_PROVIDER, LLM_MODEL, and API key env vars before running this stage."
echo ""

set +e
PYTHONUNBUFFERED=1 uv run miniverse run "$SCENARIO_PATH" --llm --world-engine deterministic --verbose --seed 42 --ticks "$DEMO_TICKS" | tee "$LLM_LOG"
LLM_EXIT=${PIPESTATUS[0]}
set -e

echo ""
echo "Artifacts:"
echo "  - Baseline JSON: $BASELINE_JSON"
echo "  - LLM verbose log: $LLM_LOG"

if [ "$LLM_EXIT" -ne 0 ]; then
  echo ""
  echo "LLM stage failed (exit code: $LLM_EXIT). See log artifact above."
  exit "$LLM_EXIT"
fi

echo ""
echo "Stage comparison"
uv run python - "$BASELINE_JSON" "$LLM_LOG" <<'PY'
import json
import re
import sys
from pathlib import Path

baseline = json.loads(Path(sys.argv[1]).read_text())
llm_text = Path(sys.argv[2]).read_text()

metrics = ((baseline.get("final_state") or {}).get("resources") or {}).get("metrics", {})

def baseline_value(label: str):
    for stat in metrics.values():
        if isinstance(stat, dict) and stat.get("label") == label:
            return stat.get("value")
    return None

def llm_value(label: str):
    matches = re.findall(rf"^\s*{re.escape(label)}:\s*([-+]?\d+(?:\.\d+)?)", llm_text, flags=re.M)
    return float(matches[-1]) if matches else None

labels = [
    "Pending Tasks",
    "Tasks Arrived (Total)",
    "Tasks Completed (Total)",
    "Execution Capacity",
]
rows = []
for label in labels:
    b = baseline_value(label)
    l = llm_value(label)
    rows.append((label, b, l))

for label, b, l in rows:
    if b is None or l is None:
        print(f"  {label}: baseline={b} | llm={l}")
        continue
    delta = l - float(b)
    sign = "+" if delta >= 0 else ""
    print(f"  {label}: baseline={float(b):.2f} | llm={l:.2f} | delta={sign}{delta:.2f}")

print("")
print("Interpretation:")
print("  Pending Tasks is a live queue (initial backlog + arrivals - completions).")
print("  Compare arrivals/completions totals to see whether LLM coordination changed net throughput.")
PY

echo ""
echo "LLM judge summary"
uv run python demo/judge_summary.py --scenario workshop --baseline-json "$BASELINE_JSON" --llm-log "$LLM_LOG"
