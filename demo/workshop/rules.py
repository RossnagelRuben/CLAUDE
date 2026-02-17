"""Deterministic simulation rules for the workshop demo scenario."""

from __future__ import annotations

from datetime import timedelta
import random
from typing import Dict, Optional

from miniverse import (
    AgentAction,
    GraphOccupancy,
    SimulationRules,
    WorldState,
)


class WorkshopRules(SimulationRules):
    """Deterministic updates with optional stochastic arrivals for workshop simulation."""

    def __init__(
        self,
        occupancy: Optional[GraphOccupancy] = None,
        *,
        rng: Optional[random.Random] = None,
        task_arrival_chance: float = 0.35,
        max_new_tasks: int = 2,
        tick_minutes: int = 30,
        shift_duration_minutes: int = 300,
    ) -> None:
        """Initialize rules.

        Args:
            occupancy: Graph occupancy tracker for movement validation.
            rng: Random number generator for reproducible stochasticity.
            task_arrival_chance: Probability of new tasks each tick (0-1).
            max_new_tasks: Maximum new tasks per arrival event.
            tick_minutes: Simulated minutes represented by one tick.
            shift_duration_minutes: Total duration of the active shift window.
        """
        self.occupancy = occupancy
        self.rng = rng
        self.task_arrival_chance = max(0.0, task_arrival_chance)
        self.max_new_tasks = max(0, max_new_tasks)
        self.tick_minutes = max(1, int(tick_minutes))
        self.shift_duration_minutes = max(1, int(shift_duration_minutes))

    def get_tick_duration_seconds(self) -> int:
        """Expose workshop tick duration as simulated wall-clock seconds."""
        return self.tick_minutes * 60

    def on_simulation_start(self, state: WorldState) -> WorldState:
        """Initialize shift-awareness metrics visible to all agents."""
        updated = state.model_copy(deep=True)
        updated.metadata["shift_duration_minutes"] = self.shift_duration_minutes
        updated.metadata["tasks_arrived_total"] = int(
            updated.metadata.get("tasks_arrived_total", 0)
        )
        updated.metadata["tasks_completed_total"] = int(
            updated.metadata.get("tasks_completed_total", 0)
        )

        backlog = updated.resources.get_metric(
            "task_backlog", default=0, label="Pending Tasks"
        )
        previous_backlog = int(backlog.value)
        self._update_task_flow_metrics(
            updated,
            previous_backlog=previous_backlog,
            arrived=0,
            completed=0,
            execution_capacity=0.0,
            new_backlog=previous_backlog,
        )
        self._update_shift_metrics(updated, elapsed_minutes=0)
        return updated

    def _update_task_flow_metrics(
        self,
        state: WorldState,
        *,
        previous_backlog: int,
        arrived: int,
        completed: int,
        execution_capacity: float,
        new_backlog: int,
    ) -> None:
        """Publish per-tick and cumulative task-flow stats as resource metrics."""
        net_change = int(new_backlog - previous_backlog)

        state.resources.get_metric(
            "execution_capacity",
            default=0.0,
            label="Execution Capacity",
        ).value = round(float(execution_capacity), 2)

        state.resources.get_metric(
            "tasks_arrived_tick",
            default=0,
            label="Tasks Arrived (Tick)",
        ).value = int(arrived)

        state.resources.get_metric(
            "tasks_completed_tick",
            default=0,
            label="Tasks Completed (Tick)",
        ).value = int(completed)

        state.resources.get_metric(
            "task_net_change_tick",
            default=0,
            label="Pending Task Change (Tick)",
        ).value = net_change

        state.resources.get_metric(
            "tasks_arrived_total",
            default=0,
            label="Tasks Arrived (Total)",
        ).value = int(state.metadata.get("tasks_arrived_total", 0))

        state.resources.get_metric(
            "tasks_completed_total",
            default=0,
            label="Tasks Completed (Total)",
        ).value = int(state.metadata.get("tasks_completed_total", 0))

    def _update_shift_metrics(self, state: WorldState, *, elapsed_minutes: int) -> None:
        """Publish shift timing/urgency as environment metrics."""
        elapsed = max(0, elapsed_minutes)
        remaining = max(0, self.shift_duration_minutes - elapsed)
        progress = min(100.0, (elapsed / self.shift_duration_minutes) * 100.0)

        if remaining <= 30:
            phase = "handoff window"
        elif remaining <= 90:
            phase = "late shift"
        elif remaining <= 180:
            phase = "mid shift"
        else:
            phase = "early shift"

        local_time = state.timestamp.strftime("%H:%M")
        state.environment.metrics["shift_time_local"] = state.environment.get_metric(
            "shift_time_local",
            default=local_time,
            label="Shift Clock",
        )
        state.environment.metrics["shift_time_local"].value = local_time

        state.environment.metrics["shift_minutes_remaining"] = state.environment.get_metric(
            "shift_minutes_remaining",
            default=remaining,
            unit="min",
            label="Shift Minutes Remaining",
        )
        state.environment.metrics["shift_minutes_remaining"].value = remaining

        state.environment.metrics["shift_progress_pct"] = state.environment.get_metric(
            "shift_progress_pct",
            default=progress,
            unit="%",
            label="Shift Progress",
        )
        state.environment.metrics["shift_progress_pct"].value = round(progress, 1)

        state.environment.metrics["shift_phase"] = state.environment.get_metric(
            "shift_phase",
            default=phase,
            label="Shift Phase",
        )
        state.environment.metrics["shift_phase"].value = phase

    def apply_tick(self, state: WorldState, tick: int) -> WorldState:
        """Apply deterministic physics each tick.

        Updates:
        - Agent energy (drains with work, recovers with rest)
        - Agent stress (increases with work, decreases with rest)
        - Task backlog (decreases with effective execution capacity)
        - Optional stochastic task arrivals
        """
        updated = state.model_copy(deep=True)

        # Get or create metrics
        backlog = updated.resources.get_metric(
            "task_backlog", default=0, label="Pending Tasks"
        )
        previous_backlog = int(backlog.value)

        # Update agent attributes based on activity.
        # Only direct task work clears backlog.
        active_workers = 0
        execution_capacity = 0.0
        worker_energies: Dict[str, float] = {}
        worker_intensities: Dict[str, str] = {}
        for agent in updated.agents:
            activity = (agent.activity or "").lower()
            if activity in {"work", "repair"}:
                active_workers += 1
                worker_energy = float(
                    agent.get_attribute("energy", default=80, unit="%").value
                )
                worker_energies[agent.agent_id] = worker_energy

                intensity = str(agent.metadata.get("work_intensity", "normal")).lower()
                if intensity not in {"normal", "push"}:
                    intensity = "normal"
                worker_intensities[agent.agent_id] = intensity

                if worker_energy >= 45:
                    worker_capacity = 1.0
                elif worker_energy >= 25:
                    worker_capacity = 0.75
                else:
                    worker_capacity = 0.5

                if intensity == "push":
                    worker_capacity *= 2.0
                execution_capacity += worker_capacity

        high_load = active_workers >= 2 and int(backlog.value) > 0

        for agent in updated.agents:
            energy = agent.get_attribute("energy", default=80, unit="%")
            stress = agent.get_attribute("stress", default=25, unit="%")

            activity = (agent.activity or "").lower()
            if activity in {"work", "repair"}:
                worker_energy = worker_energies.get(agent.agent_id, float(energy.value))
                intensity = worker_intensities.get(agent.agent_id, "normal")
                work_drain = 6.0
                stress_increase = 2.0
                if intensity == "push":
                    # Push mode intentionally burns more energy for higher throughput.
                    work_drain *= 2.5
                    stress_increase += 3.0
                if high_load:
                    work_drain += 2.0
                    stress_increase += 1.0
                if worker_energy <= 35:
                    work_drain += 1.5

                energy.value = max(0.0, float(energy.value) - work_drain)
                stress.value = min(100.0, float(stress.value) + stress_increase)
            elif activity in {"analyze", "monitor", "communicate"}:
                # Cognitive/social actions cost some energy but less than direct repair work.
                energy.value = max(0.0, float(energy.value) - 2.5)
                stress.value = min(100.0, float(stress.value) + 1.0)
            elif activity == "rest":
                # Rest is intentionally strong to allow rotation strategies to emerge.
                rest_gain = 9.0 + (1.0 if high_load else 0.0)
                energy.value = min(100.0, float(energy.value) + rest_gain)
                stress.value = max(0.0, float(stress.value) - 4.0)
            else:
                energy.value = min(100.0, float(energy.value) + 2.0)
                stress.value = max(0.0, float(stress.value) - 1)

        # Stochastic task arrivals
        incoming_tasks = 0
        if (
            self.rng is not None
            and self.task_arrival_chance > 0.0
            and self.max_new_tasks > 0
            and self.rng.random() < self.task_arrival_chance
        ):
            incoming_tasks = self.rng.randint(1, self.max_new_tasks)

        completed_tasks = min(previous_backlog, int(round(execution_capacity)))
        new_backlog = max(0, previous_backlog - completed_tasks + incoming_tasks)
        backlog.value = int(new_backlog)

        arrived_total = int(updated.metadata.get("tasks_arrived_total", 0)) + incoming_tasks
        completed_total = int(updated.metadata.get("tasks_completed_total", 0)) + completed_tasks
        updated.metadata["tasks_arrived_total"] = arrived_total
        updated.metadata["tasks_completed_total"] = completed_total

        self._update_task_flow_metrics(
            updated,
            previous_backlog=previous_backlog,
            arrived=incoming_tasks,
            completed=completed_tasks,
            execution_capacity=execution_capacity,
            new_backlog=new_backlog,
        )

        # Advance simulation clock and surface shift timing context.
        updated.timestamp = state.timestamp + timedelta(minutes=self.tick_minutes)
        elapsed_minutes = tick * self.tick_minutes
        self._update_shift_metrics(updated, elapsed_minutes=elapsed_minutes)
        updated.tick = tick
        return updated

    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        """Validate if an action is physically possible.

        Checks room capacity for move actions.
        """
        if action.action_type == "move" and self.occupancy:
            target = action.target
            if not target:
                return False
            return self.occupancy.can_enter(target, action.agent_id)
        return True

    def format_resource_summary(self, state: WorldState) -> str:
        """Keep workshop resource output focused on execution and queue state."""
        backlog = state.resources.metrics.get("task_backlog")
        execution_capacity = state.resources.metrics.get("execution_capacity")
        parts: list[str] = []
        if backlog is not None:
            parts.append(f"Pending Tasks={int(backlog.value)}")
        if execution_capacity is not None:
            parts.append(f"Execution Capacity={float(execution_capacity.value):.2f}")
        return ", ".join(parts)

    def process_actions(
        self, state: WorldState, actions: list[AgentAction], tick: int
    ) -> WorldState:
        """Process actions deterministically and persist per-agent work intensity."""
        updated = state.model_copy(deep=True)
        updated.tick = tick

        for action in actions:
            status = next(
                (agent for agent in updated.agents if agent.agent_id == action.agent_id),
                None,
            )
            if status is None:
                continue

            status.activity = action.action_type

            if action.action_type == "move" and action.target:
                status.location = action.target

            if action.action_type in {"work", "repair"}:
                intensity = "normal"
                parameters = action.parameters or {}
                if isinstance(parameters, dict):
                    raw = parameters.get("intensity")
                    if isinstance(raw, str) and raw.lower() in {"normal", "push"}:
                        intensity = raw.lower()
                status.metadata["work_intensity"] = intensity
            else:
                status.metadata.pop("work_intensity", None)
        return updated


# Backward-compatible alias for earlier naming.
OrgHierarchyRules = WorkshopRules
