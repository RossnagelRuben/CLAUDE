# Environment Tiers

_Last updated: 2025-03-15_

Miniverse supports multiple levels of environment fidelity so simulations can scale from abstract KPI dashboards to spatial sandboxes.

## Tier 0 – Abstract State (Default)

- World state only includes metrics (`EnvironmentState`, `ResourceState`).
- Deterministic rules update shared stats; agents reason about numbers and high-level events.
- No concept of location or movement.

## Tier 1 – Logical Graphs

- Scenario defines an `EnvironmentGraphState` (`miniverse/environment/schemas.py`):
  - Nodes: logical locations such as rooms, teams, channels. Each node may specify capacity and metadata (department, shift, etc.).
  - Adjacency: directed or undirected connections between nodes (valid moves, communication links).
- Deterministic helpers (to be implemented) will support:
  - Occupancy checks and capacity enforcement.
  - Path planning on the logical graph (shortest path, random walks, etc.).
  - Event routing (e.g., broadcast messages to all adjacent nodes).
- Agents reference nodes in plan metadata ("move to operations") and the executor ensures actions respect graph constraints. See `examples/workshop/scenario.yaml` for a Tier 1 example.

## Tier 2 – Spatial Grids

- Scenario adds an `EnvironmentGridState` with explicit width/height and a sparse map of tiles → metadata (`GridTileState`).
- Tiles can encode world/sector/arena/object names (compatible with Stanford’s Reverie maze exports) and collision flags.
- Helper utilities (planned) will include:
  - A* pathfinding over walkable tiles.
  - Reverse indices for quick lookup of all tiles matching an object name.
  - Automatic population of events based on tile metadata (e.g., place equipment events at object coordinates).
- Agent perception now exposes `grid_visibility` (local window of tiles around the agent) when `environment_grid` and `grid_position` are present, enabling LLM agents to reason about nearby walls, objects, and goals.

## Scenario Loader & World State

- `WorldState` carries optional `environment_graph` and `environment_grid` fields. Scenarios can populate one or both depending on fidelity needs.
- Existing scenarios remain valid because the fields default to `None`.
- `ScenarioLoader` now parses `environment_graph` and `environment_grid` keys from scenario files (YAML/JSON), returning populated `EnvironmentGraphState` / `EnvironmentGridState` objects that deterministic rules can consume.

## Deterministic Rules

`SimulationRules` subclasses can introspect the tier:

```python
if state.environment_grid:
    # run spatial physics
elif state.environment_graph:
    # run logical graph logic
else:
    # KPI-only updates
```

Helper modules in `miniverse/environment/` will eventually provide reusable utilities for the above branches.

### Helper Utilities

- `GraphOccupancy` keeps track of how many agents are inside each logical node and enforces capacity limits.
- `shortest_path(graph, start, goal)` returns a list of node IDs using BFS.
- `grid_shortest_path(grid, start, goal)` finds a walkable path on the tile grid while avoiding collision tiles.
- `EnvironmentGrid.is_walkable(row, col)` and `EnvironmentGraph.neighbors(node_id)` offer convenience checks for deterministic rules.
- `get_visible_tiles(grid_state, center, radius)` produces the local window of tiles used by agent perception to populate `grid_visibility`.
- `render_ascii_window(grid_state, center, radius)` creates a compact ASCII summary of the same window—useful for perception summaries or debugging output.

## Next Steps

1. ✅ Helper utilities available (`GraphOccupancy`, `shortest_path`, `grid_shortest_path`).
2. ✅ Scenario loader parses graph/grid descriptions.
3. Update examples (workshop baseline shipped; add more domain variants).
4. Build a Stanford-style map using Tier 2 to replicate the Valentine’s Day scenario.

See `NEXT_STEPS.md` for the broader roadmap.
