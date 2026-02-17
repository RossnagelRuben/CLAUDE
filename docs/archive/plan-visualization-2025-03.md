# Miniverse Visualization Engine Plan

_Last updated: 2025-03-17_

## 1. Vision & Context
- Bring simulation runs to life with a lightweight, built-in visualization surface so users can observe state changes and agent behavior in real time, inspired by Stanford Generative Agents and Smallville dashboards.
- Maintain Miniverse’s modular design: the visualization layer plugs in via tick listeners (`miniverse/orchestrator.py`) and optional services without disturbing core orchestration, cognition, or persistence flows.
- Serve as both an interactive monitor during live runs and a playback console for recorded ticks, covering simulations that range from abstract KPI dashboards (Tier 0) to spatial grid worlds (Tier 2).

## 2. Product Goals
- **Immediate situational awareness**: Stream tick-by-tick updates (agents, environment, resources, events, actions) with <250 ms latency after each tick completes.
- **Tier-agnostic rendering**: Automatically adapt visual components to whichever environment fidelity a scenario exposes (`environment`, `environment_graph`, `environment_grid`).
- **Zero-friction opt-in**: Keep simulations headless by default; attaching the visualization should require at most 3 lines of wiring or a CLI flag.
- **Replayability**: Retain the last N ticks in memory and offer query endpoints for historical inspection and scrub controls in the UI.
- **Extensibility**: Expose a stable event stream + REST schema so power users can embed the data into custom dashboards without forking Miniverse.

## 3. Success Metrics (MVP)
- ✅ A demo scenario (3+ agents, Tier-1/2 world) can be launched with `uv run python -m miniverse.visualization.server --scenario examples/workshop/miniverse.json` and observed through a bundled web UI.
- ✅ The UI visualizes agent locations/activities, recent actions, and the world map within 1 tick of their occurrence.
- ✅ Users can pause auto-play and scrub through at least the last 200 ticks without rerunning the simulation.
- ✅ API schemas documented in `docs/visualization.md` with example payloads validated by automated tests.

## 4. Scope & Non-Goals
### In-Scope
- Tick listener that emits structured snapshots/deltas.
- Embedded FastAPI (or Starlette) service broadcasting Server-Sent Events (SSE) and exposing REST endpoints.
- Browser UI (React or Lit + Vite) served from the same process for MVP convenience.
- Visualization adapters for Tier 0 metrics, Tier 1 graphs, Tier 2 grids.
- Minimal persistence layer (in-memory ring buffer, optional persistence strategy hook).

### Out of Scope (MVP)
- 3D rendering or physics-level animation.
- Authoring tools for editing environments or agent profiles.
- Fine-grained access control or multi-user collaboration features.
- Dedicated native or mobile clients (leave for future work).

## 5. Key Use Cases
1. **Live monitoring**: Researchers watch agents coordinate in the Smallville-inspired workshop scenario and intervene if anomalies appear.
2. **Post-run analysis**: Replay a recorded run to inspect how a narrative evolved over 20 ticks and export screenshots of critical moments.
3. **Prompt/system debugging**: Enable `DEBUG_LLM=1` to correlate LLM prompts with visual state shifts, aiding cognition tuning.
4. **Scenario demos**: Share simplified experiences during talks/workshops without exposing raw logs.

## 6. System Architecture
```
Orchestrator ──▶ VisualizationListener ──▶ Snapshot Bus ──▶
                                            ├─ SSE Stream (/stream)
                                            ├─ REST Snapshots (/ticks/{id})
                                            └─ Ring Buffer Store (Last N ticks)
                                         Web UI (Vite bundle)
```

### 6.1 Backend Components
- **VisualizationListener** (`miniverse/visualization/listener.py`)
  - Implements `Callable[[int, WorldState, WorldState, List[AgentAction]], None]`.
  - Derives a `TickSnapshot` containing `world_state`, `actions`, diff metadata (metrics delta, occupancy changes).
  - Publishes to an asyncio Queue consumed by the broadcaster.
- **Snapshot Store**
  - Configurable ring buffer (default 200 ticks) storing serialized `TickSnapshot`s keyed by tick number and run UUID.
  - Optionally delegates long-term retention to existing persistence strategies (`miniverse/persistence.py`).
- **API Server** (`miniverse/visualization/server.py`)
  - FastAPI app started via `uv run python -m miniverse.visualization.server`.
  - Endpoints:
    - `GET /health`
    - `GET /metadata` (run id, agent roster, environment meta)
    - `GET /stream` (SSE: pushes `TickSnapshot` JSON)
    - `GET /ticks/{tick}` (single snapshot)
    - `GET /ticks` (index of buffered ticks)
  - Static file serving for compiled front-end bundle.
- **Run Orchestrator Integration**
  - CLI arguments to launch orchestrator in-process or connect to an external orchestrator via e.g. Redis/WebSocket in future phases.

### 6.2 Frontend Components
- **Application Shell**: HTML scaffold + global styles (Tailwind or CSS modules) focused on legibility.
- **Data Layer**: SSE subscription hook with auto-reconnect; REST fallback for manual fetch.
- **Panels**:
  - **Timeline Toolbar**: shows current tick, run clock, controls (pause/play, step, jump to tick).
  - **Agent Roster**: grid of cards sorted by location or activity, each showing name, emoji/icon, current action, key stats (energy/morale) with sparkline of recent history.
  - **World Canvas**:
    - Tier 2: SVG grid (50–80px tiles). Tiles colored by `world/sector`, collision walls outlined. Agents rendered as layered badges positioned at their `grid_position`; occupancy counts aggregated if multiple agents share a tile.
    - Tier 1: Node-link diagram using force layout or manual coordinates provided in metadata. Nodes display capacity + occupancy (progress ring).
    - Tier 0: Metric dashboard (gauges/bar charts) highlighting environment/resources from `MetricsBlock`.
  - **Event & Action Feed**: chronological list combining `AgentAction` descriptions and `WorldEvent` entries with severity color coding.
  - **Inspector Drawer**: opens when an agent or tile is clicked. Shows memories (recent entries from persistence), attributes, relationships.

## 7. Data Contracts
### 7.1 TickSnapshot (JSON)
```json
{
  "run_id": "uuid",
  "tick": 12,
  "timestamp": "2025-03-17T10:05:00Z",
  "world": {
    "environment": {...},
    "resources": {...},
    "agents": {
      "ayesha": {"status": {...}, "diff": {...}},
      "marco": {...}
    },
    "graph": {...},
    "grid": {...},
    "recent_events": [...]
  },
  "actions": [{"agent_id": "ayesha", "action_type": "move", ...}],
  "metrics_delta": {
    "energy": -5,
    "inventory.widgets": +2
  }
}
```
- `agents` mapped by `agent_id` for O(1) lookups.
- `diff` sections include computed deltas (e.g., `location_changed: true`, `attributes_changed: ["energy"]`).
- Grid representation includes flattened tiles plus optional precomputed `visible_window` slices per agent to simplify rendering partial observability views.

### 7.2 SSE Envelope
```json
{ "type": "tick", "payload": TickSnapshot }
{ "type": "status", "payload": {"state": "connected"} }
```
- Allows future extension for notifications (`type: "warning"`, etc.).

## 8. Visual Design Notes
- **Agent Representation**
  - Primary element: rectangular card (120×160) with agent portrait/emoji, name, current activity, location badge, top 2 attributes (e.g., energy/stress) displayed as horizontal bars.
  - Spatial overlay: circular tokens used on the grid/graph map; color-coded by faction/team (drawn from `AgentProfile.tags`). Token border pulses when the agent just acted.
  - Status indicators: small icons for planner/executor state (e.g., plan step progress) pulled from cognition scratchpad when available.
- **World Representation**
  - Tier 2 grid: tiles tinted by `sector`; walls rendered via stroke; interactive hover shows `game_object` metadata. Background uses muted palette to highlight agents/events.
  - Tier 1 graph: nodes rendered as stacked pill components with occupancy progress ring + tooltip for metadata.
  - Tier 0 metrics: large numerics, delta arrows, color-coded (green/red) based on direction of change.
- **Events Feed**
  - Uses severity colors (info/blue, warning/amber, critical/red). Each entry links back to affected agents/tiles.
- **Timeline Controls**
  - Slider allowing quick navigation. When paused, tick selection updates world view to historical snapshot.

## 9. Implementation Roadmap
1. **Foundation (Week 1)**
   - Implement `TickSnapshot` models and diff utilities with unit tests.
   - Build VisualizationListener and ring buffer store; add integration test using a fake orchestrator run.
   - Expose CLI entry point that runs orchestrator + listener but prints JSON to console (smoke test).
2. **Backend Service (Week 2)**
   - Add FastAPI app with `/health`, `/stream`, `/ticks`. Use SSE via `EventSourceResponse`.
   - Implement background task to consume snapshots from queue and broadcast to connected clients.
   - Validate JSON schemas with `pydantic` + snapshot tests.
3. **Frontend MVP (Week 3)**
   - Scaffold Vite app (React + TypeScript).
   - Implement SSE hook, global store, agent roster, event feed, and minimal grid renderer.
   - Serve static assets from FastAPI using `StaticFiles`.
4. **Spatial Enhancements (Week 4)**
   - Add Tier 1 graph visualization (D3 force or manual coordinates) and Tier 0 metric widgets.
   - Implement inspector drawer with memory fetches (reusing `persistence.get_recent_memories`).
   - Polish pause/rewind controls.
5. **Docs & Demo (Week 5)**
   - Write `docs/visualization.md` quickstart.
   - Ship example scenario + recorded run in `examples/visualizer`.
   - Record usage gifs for README.

## 10. Testing & Verification
- Unit tests for diffing logic and serialization (pytest).
- Async integration tests using `pytest-asyncio` to simulate orchestrator + server with SSE client.
- Frontend component tests (Vitest + Testing Library) covering roster, grid rendering for sample payloads.
- Manual validation: run workshop scenario, confirm UI updates, pause/rewind behavior, error handling when SSE disconnects.

## 11. Tooling & Dependencies
- Python: FastAPI, sse-starlette (or native FastAPI SSE), `asyncio.Queue`, `pydantic` (already in tree).
- Frontend: React (or Lit) + Vite, Tailwind (optional). Keep dependencies minimal to ease maintenance.
- Build: Add `visualization` extra to `pyproject.toml` for FastAPI dependency; add `package.json` scripts under `miniverse/visualization/ui` (or `frontend/`).

## 12. Risks & Mitigations
- **Performance with many agents (100+)**: mitigate via diff streaming and throttling updates when queue backs up; allow client to request reduced payloads.
- **SSE reliability on certain proxies**: document WebSocket fallback as future enhancement; ensure heartbeat messages keep connections alive.
- **Schema drift**: enforce typed contracts and jsonschema tests; version API responses (`payload_version`).
- **UI complexity creep**: keep layout modular; prioritize readability over fidelity for MVP.

## 13. Open Questions
1. Should visualization run in the same process as orchestrator or support remote attachment out of the gate?
2. Do we need authentication for hosted environments (likely post-MVP)?
3. How many ticks should we retain by default before memory usage becomes problematic? (Investigate run size vs RAM.)
4. Should we expose a plugin API for custom renderers (e.g., hooking into web components) in soon-to-follow iterations?
5. What’s the minimal metadata we need from scenarios to position Tier 1 nodes? (Potential requirement for x/y coordinates.)

## 14. Future Extensions (Post-MVP)
- WebSocket + binary delta channel for large-scale simulations.
- Export to video/gif and annotated reports.
- Multi-run comparison mode to visualize branching scenarios.
- Collaborative annotation tools (pin notes on timeline).
- VR/3D exploration for high-fidelity environments.

