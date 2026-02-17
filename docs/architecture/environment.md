# Environment Architecture

Miniverse supports three environment tiers so you can choose the fidelity level
that fits your simulation.

## Tier 0: Abstract Metrics

- No explicit map or location graph.
- Agents reason over shared metrics and events.
- Best for policy and coordination simulations where geometry does not matter.

## Tier 1: Logical Graph (`environment_graph`)

- Nodes represent logical places (rooms, teams, channels, zones).
- Adjacency defines valid movement or linkage.
- Node capacity can constrain occupancy.

Use this for town/organization style movement similar to graph-based location models.

## Tier 2: Tile Grid (`environment_grid`)

- Explicit width/height with tile metadata and collision flags.
- Agents can carry `grid_position`.
- Perception includes local `grid_visibility` windows.

Use this when spatial pathing and neighborhood visibility matter.

## Loader and Runtime Support

Scenario files can include either or both:

- `environment_graph`
- `environment_grid`

`ScenarioLoader` parses these into `EnvironmentGraphState` and `EnvironmentGridState`.
Deterministic rules can branch on availability:

```python
if state.environment_grid:
    # spatial logic
elif state.environment_graph:
    # graph logic
else:
    # abstract metric logic
```

## Helpers

Miniverse includes helper utilities for environment-aware rules:

- `GraphOccupancy` capacity tracking
- `shortest_path(...)` for graph traversal
- `grid_shortest_path(...)` for tile traversal
- `get_visible_tiles(...)` and `render_ascii_window(...)` for local perception

## Parity Context

For Stanford-style simulations:

- Use `environment_graph` for social/location structure.
- Add `environment_grid` when you need explicit tile movement.

See `../PARITY.md` for a full parity matrix.
