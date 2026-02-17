#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCENARIO_PATH="$ROOT_DIR/demo/workshop/scenario.yaml"
LOG_DIR="$ROOT_DIR/demo/workshop/logs"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
BASELINE_JSON="$LOG_DIR/workshop_baseline_only_${STAMP}.json"
DEMO_TICKS=10

if [ "$#" -gt 0 ]; then
  echo "This script uses fixed demo settings and does not accept arguments."
  echo "Run: bash demo/workshop/run_baseline.sh"
  exit 2
fi

cd "$ROOT_DIR"

echo ""
echo "================================================================================"
echo "Workshop Baseline Demo"
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
resources = (data.get("resources") or {}).get("metrics", {})
for key, stat in resources.items():
    if isinstance(stat, dict):
        label = stat.get("label", key)
        value = stat.get("value", "?")
        unit = stat.get("unit", "")
        print(f"  - Resource {label}: {value} {unit}".rstrip())
PY

echo ""
echo "Stage: deterministic baseline"
echo "Cognition policy: rule-based deterministic policy from demo/workshop/cognition.py (use_llm=false)"
echo "Ticks: $DEMO_TICKS (fixed)"
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
print("  Interpretation: Pending Tasks is the live queue (starting backlog + new arrivals - completed work).")
print("  Interpretation: Execution Capacity is this tick's aggregate worker throughput potential.")
print("")
print(f"Baseline artifact: {sys.argv[1]}")
PY
