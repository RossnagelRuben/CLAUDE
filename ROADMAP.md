# Miniverse Roadmap (Future Only)

_Last updated: 2026-02-17_

This roadmap tracks only upcoming work.

---

## Priority Order

1. Scenario validation and research credibility
2. Intervention and branching workflows
3. Scoring and export for analysis
4. Calibration against real-world data
5. Claude-guided workflow packaging

---

## Now (0-4 weeks)

### 1) Social science validation scenarios

- Build and ship:
  - Weak ties diffusion scenario
  - Information cascade scenario
- Define expected benchmark behaviors per scenario.
- Run reproducible sweeps with fixed seeds and report outcomes.

Success criteria:

- [ ] Weak ties scenario demonstrates expected diffusion pattern.
- [ ] Information cascade scenario shows measurable cascade/break conditions.
- [ ] Validation write-up is publication-quality.

### 2) Demo/storyline completion

- Finalize workshop demo comparison narrative and metrics.
- Add/finish `demo/valentines` run script and docs parity with workshop demo.
- Ensure baseline vs LLM comparison artifacts are consistent and easy to interpret.

Success criteria:

- [ ] Workshop and Valentines demos run via scripts end-to-end.
- [ ] Comparison output explains outcome deltas clearly.
- [ ] Docs point to one recommended path for each demo.

---

## Next (1-2 months)

### 3) Intervention API (counterfactual branching)

- Add simulation fork/checkpoint primitives.
- Add intervention commands (state/resource/role/policy updates mid-run).
- Add branch comparison utilities for quantitative deltas.

Success criteria:

- [ ] Can fork a run from tick `t` and execute branches independently.
- [ ] Interventions are deterministic/reproducible with seed + config.
- [ ] Branch comparison reports are machine-readable and human-readable.

### 4) Behavioral scoring

- Define an initial 8+ dimension scoring schema.
- Add post-hoc transcript scorer with evidence references.
- Produce per-agent and population summary outputs.

Success criteria:

- [ ] Completed runs can be scored across 8+ dimensions.
- [ ] Scores are reproducible and explainable.
- [ ] Scoring output is analysis-ready.

### 5) Research-ready export

- Export formats for downstream analysis (`csv`, `parquet`, `json`).
- Reproducibility bundle command (scenario, runtime config, seed, logs, summary).
- Document canonical schema for third-party tools.

Success criteria:

- [ ] One-command export into R/Python-friendly data.
- [ ] Reproducibility package supports exact reruns.
- [ ] Export schema is documented and versioned.

---

## Later (2-6 months)

### 6) Calibration infrastructure

- Parameter search workflow against target real-world metrics.
- Validation/holdout process for calibrated settings.
- At least one end-to-end calibration case study.

Success criteria:

- [ ] Calibration workflow is documented and runnable.
- [ ] At least one quantitative case study is completed.
- [ ] Results are strong enough for technical reporting/publication.

### 7) Claude workflow packaging

- Package and harden Claude skill/workflow for end-to-end simulation assistance.
- Encode repeatable playbooks for common research questions.

Success criteria:

- [ ] User can request a simulation workflow in natural language and complete it end-to-end.
- [ ] Common scenario families are covered without bespoke prompting.

---

## Deferred / Optional

- Embedding-based memory retrieval (if BM25 becomes insufficient).
- Multi-model benchmarking harness.
- Visualization dashboard beyond CLI-first workflow.
- Real-time collaborative simulation controls.
