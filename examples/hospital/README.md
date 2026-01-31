# Hospital Simulation

A comprehensive hospital simulation demonstrating complex agent-based modeling with:

- **Emergency Room (ER)**: Triage, emergency treatment, stabilization
- **Intensive Care Unit (ICU)**: Critical care monitoring and treatment
- **Operating Rooms (OR)**: Surgical procedures
- **Imaging Department**: X-ray, CT, MRI diagnostics
- **Multiple Agent Types**: Doctors, nurses, patients, visitors, administrative staff

## Features

- **Patient Flow**: Emergency arrivals, triage assessment, treatment pathways, transfers between departments, discharge
- **Staff Coordination**: Role-based responsibilities, shift management, communication protocols
- **Resource Management**: Bed occupancy, equipment availability, staff workload tracking
- **Emergent Behavior**: LLM-driven decision-making for treatment plans, communication, and care coordination

## Running the Simulation

### Deterministic Mode (No LLM)
```bash
uv run python examples/hospital/run.py --ticks 10
```

### LLM Mode (Emergent Behavior)
```bash
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-4o-mini
export OPENAI_API_KEY=your_key
uv run python examples/hospital/run.py --llm --ticks 10
```

### Debug Mode
```bash
DEBUG_MEMORY=true DEBUG_LLM=true uv run python examples/hospital/run.py --llm --ticks 5
```

## Scenario Structure

- **Environment**: Logical graph (Tier 1) with departments as nodes
- **Agents**: 33 agents with realistic staffing ratios
- **Simulation Rules**: Hospital-specific physics (patient vitals, treatment effects, transfers)
- **Metrics**: Patient outcomes, wait times, bed occupancy, staff workload

## Staffing Ratios

Following realistic hospital staffing standards:
- **12 nurses** (3:1 nurse-to-doctor ratio)
- **15 technicians** (greater than nurse count)
- **ICU ratio**: Maximum 2 patients per ICU nurse (currently 3 ICU nurses for up to 6 patients)

## Agent Roles

### Medical Staff (4 doctors)
- **ER Doctor**: Emergency assessments and stabilization
- **ICU Doctor**: Critical care management
- **Surgeon**: Perform operations in OR
- **Radiologist**: Imaging interpretation

### Nursing Staff (12 nurses)
- **ER Nurses (4)**: Triage, monitoring, medication administration
- **ICU Nurses (3)**: Intensive monitoring and interventions
- **OR Nurses (2)**: Surgical assistance and prep
- **Float Nurses (2)**: Fill staffing gaps across departments
- **Charge Nurse (1)**: Staff coordination and shift management

### Technical Staff (15 technicians)
- **Imaging Technicians (3)**: X-ray, CT, MRI operations
- **Lab Technicians (2)**: Process blood work and specimens
- **Respiratory Therapists (2)**: Ventilator management
- **Surgical Tech (1)**: OR support
- **Patient Care Techs (2)**: Assist with patient mobility and hygiene
- **Phlebotomist (1)**: Blood collection
- **EMT (1)**: Patient transport and emergency care
- **Unit Clerk (1)**: Administrative coordination
- **Biomedical Tech (1)**: Equipment maintenance
- **Pharmacy Tech (1)**: Medication preparation
- **ECG Tech (1)**: Cardiac monitoring

### Other
- **Administrative Staff**: Admissions, discharge coordination
- **Patients**: Varying severity levels and conditions
- **Visitors**: Family members providing support
