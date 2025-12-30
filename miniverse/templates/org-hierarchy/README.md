# Org Hierarchy Template

A three-person maintenance crew coordinating tasks across a workshop floor.

## Overview

This template simulates a small organizational hierarchy with:
- A floor lead coordinating operations
- A technician handling repairs
- An analyst monitoring metrics

It demonstrates information flow, task delegation, and emergent coordination patterns.

## Agents

| ID | Name | Role | Goals |
|----|------|------|-------|
| `lead` | Morgan Reyes | Floor Lead | Keep operations smooth, reduce task backlog |
| `tech` | Lin Zhao | Technician | Clear mechanical tickets, monitor systems |
| `analyst` | Jamie Rivera | Data Analyst | Highlight anomalies, support scheduling |

## Environment

**Type**: Graph (Tier 1)

**Locations**:
- `ops` - Operations Floor (capacity: 2)
- `workbench` - Workbench (capacity: 1)
- `inventory` - Inventory Bay (capacity: 1)

## Resources

- `task_backlog` - Number of pending maintenance tasks
- `power_kwh` - Battery reserve for operations

## Physics

The deterministic rules simulate:
- **Energy drain**: Working reduces energy (-5%), resting recovers (+3%)
- **Stress accumulation**: Working increases stress (+2%), resting decreases (-1%)
- **Backlog reduction**: Each active worker clears 1 task per tick
- **Power consumption**: 1.5 kWh per active worker per tick
- **Stochastic arrivals**: 35% chance of 1-2 new tasks per tick (when seeded)

## Usage

```bash
# Deterministic mode (quick test, no API key needed)
miniverse run org-hierarchy --ticks 10

# LLM mode (emergent behavior, requires API key)
miniverse run org-hierarchy --ticks 20 --llm

# Reproducible run with seed
miniverse run org-hierarchy --ticks 20 --seed 42

# JSON output for analysis
miniverse run org-hierarchy --ticks 10 --output json > results.json
```

## Research Questions

This template is useful for exploring:

1. **Information flow**: How do agents share status updates?
2. **Coordination**: Do agents naturally divide labor effectively?
3. **Bottlenecks**: What happens when the workbench is at capacity?
4. **Stress management**: Do agents rest before burnout?

## Customization

Edit `scenario.json` to:
- Change agent personalities or goals
- Adjust initial resource levels
- Modify room capacities

Edit `rules.py` to:
- Change energy/stress dynamics
- Adjust task arrival rates
- Add new resource types
