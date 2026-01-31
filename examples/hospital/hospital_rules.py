"""Hospital-specific simulation rules.

Handles patient flow, treatment effects, department transfers, and resource management.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from miniverse.schemas import Stat, WorldState
from miniverse.simulation_rules import SimulationRules

if TYPE_CHECKING:
    from collections.abc import Sequence

    from miniverse.schemas import AgentAction, Event


class HospitalRules(SimulationRules):
    """Deterministic physics for hospital simulation.
    
    Manages:
    - Patient vital signs (health, stability, pain level)
    - Treatment effectiveness
    - Department capacity and transfers
    - Staff workload tracking
    - Emergency arrivals
    """
    
    def __init__(self, rng: random.Random | None = None):
        """Initialize with optional RNG for stochastic arrivals."""
        self.rng = rng or random.Random()
        self.arrival_rate = 0.3  # Probability of new patient per tick
    
    def apply_tick(
        self,
        state: WorldState,
        tick: int,
    ) -> WorldState:
        """Apply one tick of hospital physics.
        
        This runs BEFORE agent actions are gathered.
        Updates:
        1. Patient vitals (deterioration/improvement)
        2. Check for critical events (code blue)
        3. Generate new patient arrivals
        4. Update global metrics
        """
        world = state.model_copy(deep=True)
        
        # Update patient vitals
        for agent_status in world.agents:
            if agent_status.role != "patient":
                continue
            
            # Get current vitals from attributes
            health_stat = agent_status.attributes.get("health")
            stability_stat = agent_status.attributes.get("stability")
            pain_stat = agent_status.attributes.get("pain_level")
            
            if not health_stat or not stability_stat or not pain_stat:
                continue
            
            health = float(health_stat.value)
            stability = float(stability_stat.value)
            pain_level = float(pain_stat.value)
            
            # Patients in ICU may deteriorate faster
            if agent_status.location == "icu":
                if stability < 40:
                    stability -= 2
                    health -= 1
            
            # Patients in ER waiting may get worse
            elif agent_status.location == "er_waiting":
                if pain_level > 70:
                    stability -= 1
            
            # Natural recovery for stable patients
            if stability > 70 and health < 90:
                health += 1
                pain_level = max(0, pain_level - 2)
            
            # Update stats
            health_stat.value = max(0, min(100, health))
            stability_stat.value = max(0, min(100, stability))
            pain_stat.value = max(0, min(100, pain_level))
        
        # Check for critical events
        for agent_status in world.agents:
            if agent_status.role != "patient":
                continue
            
            health_stat = agent_status.attributes.get("health")
            stability_stat = agent_status.attributes.get("stability")
            
            if not health_stat or not stability_stat:
                continue
            
            health = float(health_stat.value)
            stability = float(stability_stat.value)
            
            # Code Blue - critical condition (would generate events in full implementation)
            if stability < 30 and health < 40:
                # Mark as critical in metadata or attributes
                pass
        
        # Generate new patient arrivals occasionally
        if self.rng.random() < self.arrival_rate:
            # In full implementation, would add new patient to world.agents
            pass
        
        # Update global metrics
        self._update_hospital_metrics(world)
        
        world.tick = tick
        return world
    
    def _update_patient_vitals(self, world: WorldState, events: list[Event]) -> None:
        """Update vital signs for all patients."""
        for status in world.agents:
            if status.role != "patient":
                continue
            
            # Get current vitals from attributes
            health_stat = status.attributes.get("health")
            stability_stat = status.attributes.get("stability")
            pain_stat = status.attributes.get("pain_level")
            
            if not health_stat or not stability_stat or not pain_stat:
                continue
            
            health = float(health_stat.value)
            stability = float(stability_stat.value)
            pain_level = float(pain_stat.value)
            
            # Patients in ICU may deteriorate faster
            if status.location == "icu":
                # Critical patients need continuous care
                if stability < 40:
                    stability -= 2
                    health -= 1
            
            # Patients in ER waiting may get worse
            elif status.location == "er_waiting":
                if pain_level > 70:
                    stability -= 1
            
            # Natural recovery for stable patients
            if stability > 70 and health < 90:
                health += 1
                pain_level = max(0, pain_level - 2)
            
            # Update stats
            health_stat.value = max(0, min(100, health))
            stability_stat.value = max(0, min(100, stability))
            pain_stat.value = max(0, min(100, pain_level))
    
    def _apply_treatments(
        self,
        world: WorldState,
        actions: Sequence[AgentAction],
        events: list[Event],
    ) -> None:
        """Apply treatment effects based on agent actions."""
        # Build agent lookup
        agent_lookup = {status.agent_id: status for status in world.agents}
        
        for action in actions:
            # Medication administration
            if "administer" in action.action_type.lower() or "medication" in action.action_type.lower():
                target = action.targets[0] if action.targets else None
                if target and target in agent_lookup:
                    status = agent_lookup[target]
                    pain_stat = status.attributes.get("pain_level")
                    if pain_stat:
                        pain_stat.value = max(0, float(pain_stat.value) - 20)
                    
                    events.append(Event(
                        tick=world.tick,
                        event_type="treatment",
                        description=f"{action.agent_id} administered medication to {target}",
                        severity=1,
                        affected_agents=[action.agent_id, target],
                    ))
            
            # Surgical intervention
            elif "surgery" in action.action_type.lower() or "operate" in action.action_type.lower():
                target = action.targets[0] if action.targets else None
                if target and target in agent_lookup:
                    status = agent_lookup[target]
                    health_stat = status.attributes.get("health")
                    stability_stat = status.attributes.get("stability")
                    if health_stat:
                        health_stat.value = min(100, float(health_stat.value) + 30)
                    if stability_stat:
                        stability_stat.value = min(100, float(stability_stat.value) + 20)
                    
                    events.append(Event(
                        tick=world.tick,
                        event_type="surgery",
                        description=f"{action.agent_id} performed surgery on {target}",
                        severity=2,
                        affected_agents=[action.agent_id, target],
                    ))
            
            # Diagnostic imaging
            elif "scan" in action.action_type.lower() or "imaging" in action.action_type.lower():
                target = action.targets[0] if action.targets else None
                if target:
                    events.append(Event(
                        tick=world.tick,
                        event_type="diagnostic",
                        description=f"{action.agent_id} performed imaging on {target}",
                        severity=1,
                        affected_agents=[action.agent_id, target] if target in agent_lookup else [action.agent_id],
                    ))
            
            # Monitoring/assessment
            elif "monitor" in action.action_type.lower() or "assess" in action.action_type.lower():
                target = action.targets[0] if action.targets else None
                if target and target in agent_lookup:
                    status = agent_lookup[target]
                    stability_stat = status.attributes.get("stability")
                    if stability_stat:
                        stability_stat.value = min(100, float(stability_stat.value) + 5)
    
    def _process_transfers(
        self,
        world: WorldState,
        actions: Sequence[AgentAction],
        events: list[Event],
    ) -> None:
        """Handle patient transfers between departments."""
        agent_lookup = {status.agent_id: status for status in world.agents}
        
        for action in actions:
            if "transfer" in action.action_type.lower():
                target = action.targets[0] if action.targets else None
                if not target or target not in agent_lookup:
                    continue
                
                status = agent_lookup[target]
                
                # Determine destination based on context
                if "icu" in action.reasoning.lower():
                    status.location = "icu"
                    events.append(Event(
                        tick=world.tick,
                        event_type="transfer",
                        description=f"{target} transferred to ICU",
                        severity=3,
                        affected_agents=[target],
                    ))
                elif "or" in action.reasoning.lower() or "operating" in action.reasoning.lower():
                    status.location = "operating_room"
                    events.append(Event(
                        tick=world.tick,
                        event_type="transfer",
                        description=f"{target} transferred to Operating Room",
                        severity=2,
                        affected_agents=[target],
                    ))
                elif "imaging" in action.reasoning.lower():
                    status.location = "imaging"
                    events.append(Event(
                        tick=world.tick,
                        event_type="transfer",
                        description=f"{target} transferred to Imaging",
                        severity=1,
                        affected_agents=[target],
                    ))
                elif "discharge" in action.reasoning.lower():
                    status.location = "discharged"
                    discharged_stat = status.attributes.get("discharged")
                    if discharged_stat:
                        discharged_stat.value = 1
                    events.append(Event(
                        tick=world.tick,
                        event_type="discharge",
                        description=f"{target} discharged from hospital",
                        severity=1,
                        affected_agents=[target],
                    ))
    
    def _check_critical_events(self, world: WorldState, events: list[Event]) -> None:
        """Check for critical patient events."""
        for status in world.agents:
            if status.role != "patient":
                continue
            
            health_stat = status.attributes.get("health")
            stability_stat = status.attributes.get("stability")
            
            if not health_stat or not stability_stat:
                continue
            
            health = float(health_stat.value)
            stability = float(stability_stat.value)
            
            # Code Blue - critical condition
            if stability < 30 and health < 40:
                events.append(Event(
                    tick=world.tick,
                    event_type="code_blue",
                    description=f"CODE BLUE: {status.agent_id} in critical condition!",
                    severity=5,
                    affected_agents=[status.agent_id],
                ))
    
    def _generate_patient_arrival(self, world: WorldState, events: list[Event]) -> None:
        """Generate a new patient arrival."""
        # Count existing patients
        patient_count = 0
        for status in world.agents:
            if status.role == "patient":
                discharged_stat = status.attributes.get("discharged")
                if not discharged_stat or float(discharged_stat.value) == 0:
                    patient_count += 1
        
        if patient_count >= 8:  # Cap at 8 active patients
            return
        
        # This would trigger an event; actual patient addition happens outside rules
        events.append(Event(
            tick=world.tick,
            event_type="new_arrival",
            description="New patient arrived at ER",
            severity=2,
            affected_agents=[],
        ))
    
    def _update_hospital_metrics(self, world: WorldState) -> None:
        """Update global hospital metrics."""
        # Count active patients by location
        er_count = 0
        icu_count = 0
        or_count = 0
        discharged_count = 0
        
        total_health = 0
        patient_count = 0
        
        for status in world.agents:
            if status.role != "patient":
                continue
            
            discharged_stat = status.attributes.get("discharged")
            if discharged_stat and float(discharged_stat.value) == 1:
                discharged_count += 1
                continue
            
            patient_count += 1
            health_stat = status.attributes.get("health")
            if health_stat:
                total_health += float(health_stat.value)
            
            if status.location == "er" or status.location == "er_waiting":
                er_count += 1
            elif status.location == "icu":
                icu_count += 1
            elif status.location == "operating_room":
                or_count += 1
        
        # Update global stats (resources)
        self._set_metric(world.resources, "patients_in_er", er_count)
        self._set_metric(world.resources, "patients_in_icu", icu_count)
        self._set_metric(world.resources, "patients_in_or", or_count)
        self._set_metric(world.resources, "total_discharged", discharged_count)
        
        if patient_count > 0:
            self._set_metric(world.resources, "avg_patient_health", int(total_health / patient_count))
    
    def _set_metric(self, resource_state, name: str, value: float) -> None:
        """Set resource metric value by name."""
        metric = resource_state.metrics.get(name)
        if metric:
            metric.value = value
    
    def validate_action(self, action: AgentAction, state: WorldState) -> bool:
        """Validate if an action is allowed in the current state.
        
        For hospital simulation, we allow most actions but could add checks for:
        - Location capacity constraints
        - Staff permissions (e.g., only doctors can prescribe certain treatments)
        - Patient safety rules
        """
        # For now, allow all actions
        return True
