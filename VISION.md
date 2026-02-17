# Miniverse Vision: CEV-Maximized Direction

_Last updated: 2025-12-29_

---

## What Is This Document?

This document captures the **Coherent Extrapolated Volition** of Miniverse—what this project should become if we had unlimited time, resources, and clarity. It's the north star that guides all implementation decisions.

**Read this first.** Then read ROADMAP.md for how we get there.

---

## The Core Insight

Traditional social science has a fundamental limitation: **you can't run experiments on societies**. You can't A/B test policy interventions, replay historical decisions with different parameters, or observe counterfactuals.

Organizational research has the same problem: you can't clone a company and test two reorg strategies simultaneously.

**In silico simulation changes this.**

If agents are believable enough, researchers get:
- Counterfactual analysis ("what if we'd promoted X instead of Y?")
- Policy stress-testing before deployment
- Hypothesis generation for field research
- Training environments for managers/leaders

---

## The CEV-Maximized Vision

**Miniverse becomes the standard platform for computational social science and organizational simulation.**

Not just another agent framework. Not just a Stanford replication. A **research platform with AI-native workflows** that lets researchers ask "what if" questions without running expensive field studies.

### What This Looks Like

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER (Researcher)                         │
│  "I want to simulate how a rumor spreads through a 50-person org"│
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     CLAUDE + MINIVERSE SKILL                     │
│  - Understands simulation design patterns                        │
│  - Knows Miniverse API deeply                                    │
│  - Can generate scenarios, run simulations, analyze results      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      MINIVERSE CLI                               │
│  $ miniverse init --template org-rumor-spread                    │
│  $ miniverse run --ticks 100 --branches 10                       │
│  $ miniverse analyze --metrics diffusion,polarization            │
│  $ miniverse compare baseline treatment                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    OUTPUTS (Research-Ready)                      │
│  - Transcript logs with citations                                │
│  - Quantitative metrics (diffusion rate, cascade size, etc.)    │
│  - Branching analysis (what-if comparisons)                      │
│  - Export to R/Python for statistical analysis                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Three Pillars

### Pillar 1: CLI-First Design

**Current state**: Miniverse is a Python library. You write code to use it.

**Target state**: Miniverse is a CLI tool that Claude (and humans) can drive directly.

```bash
# Initialize a simulation
miniverse init "50-person tech startup with 3 teams"

# Run with automatic scenario generation
miniverse run --ticks 100 --seed 42

# Inject an intervention
miniverse intervene --tick 50 --action "CEO announces layoffs"

# Analyze outcomes
miniverse analyze --compare baseline intervention

# Export for publication
miniverse export --format csv --metrics all
```

The CLI becomes the **interface Claude operates through**. Claude doesn't need to understand internals if it has good tools.

### Pillar 2: The Miniverse Skill

A Claude Code skill that encodes:

1. **Simulation design expertise**: How to structure scenarios for research validity
2. **Miniverse API knowledge**: All commands, options, patterns
3. **Social science methodology**: What makes a good research design
4. **Interpretation guidance**: How to analyze emergent behavior

When a user says "help me simulate information spread in my org," Claude invokes the skill and guides them through the full workflow—from hypothesis to analysis.

### Pillar 3: Petri-Inspired Infrastructure

Anthropic's [Petri](https://github.com/anthropics/petri) project demonstrates patterns we should adopt:

1. **Branching/Rollback**: Fork simulations at decision points, explore counterfactuals
2. **Multi-Dimensional Scoring**: Evaluate agent behavior across defined dimensions
3. **Transcript + Citation System**: Structured logs with evidence linking
4. **Intervention API**: Programmatic scenario manipulation mid-simulation

---

## What "Good Enough" Means

For in silico social science, agents don't need AGI. They need to:

1. **Have consistent personalities** that persist across interactions
2. **Form and update relationships** based on experience
3. **Respond to incentives** in legible ways
4. **Communicate and coordinate** with realistic friction
5. **Make mistakes** that humans would make (biases, bounded rationality)

Stanford's Generative Agents showed this is achievable. Miniverse already has the bones. The gap is **calibration and validation**—proving simulations track reality well enough to be useful.

---

## Target Users

### Primary: Computational Social Scientists
- Run experiments impossible in the real world
- Generate hypotheses for field validation
- Publish with reproducible simulations

### Secondary: Organizational Researchers / Consultants
- Model org dynamics before recommending changes
- Stress-test reorgs, policy changes, cultural interventions
- Provide clients with scenario analysis

### Tertiary: AI Researchers
- Study multi-agent coordination
- Test alignment hypotheses in social contexts
- Benchmark emergent behavior

---

## Success Metrics

### Phase 1: Proof of Concept
- [ ] CLI can init/run/analyze a simulation end-to-end
- [ ] One well-known social science finding replicated (e.g., Granovetter's weak ties)
- [ ] Claude Code skill exists and can guide a novice through simulation design

### Phase 2: Research Platform
- [ ] 5+ scenario templates covering common research questions
- [ ] Branching/intervention API functional
- [ ] Export formats compatible with R/Python/Stata
- [ ] One academic collaboration or citation

### Phase 3: Standard Tool
- [ ] Calibration studies published (simulation vs. real data correlation)
- [ ] Community adoption (forks, issues, contributions)
- [ ] Integration with existing research workflows

---

## What We're NOT Building

- **A game engine**: No 3D rendering, physics simulation, or real-time graphics
- **A chatbot framework**: Agents simulate behavior, not customer service
- **A general-purpose AI platform**: Focused on social/org simulation specifically
- **A Stanford clone**: We take inspiration but optimize for research utility

---

## Design Principles

### 1. CLI-First, Library-Second
The CLI is the primary interface. The library supports the CLI and enables advanced use cases.

### 2. Reproducibility Over Flexibility
Seed everything. Log everything. Make simulations deterministic by default.

### 3. Research-Ready Outputs
Every output should be usable in a publication. Structured data, not pretty logs.

### 4. AI-Native Workflows
Claude should be able to run Miniverse autonomously. Design for AI operators, not just humans.

### 5. Validate Against Reality
Simulations are only valuable if they track real phenomena. Prioritize calibration work.

---

## Inspirations

1. **Stanford Generative Agents**: The original research demonstrating LLM-driven agent behavior
2. **Anthropic Petri**: Auditing harness with branching, scoring, intervention patterns
3. **Steipete's workflow**: CLI-first tools that AI can operate, skills as reusable expertise
4. **AgentTorch**: Large-scale agent simulation for policy research
5. **Mesa**: Python ABM framework (we complement, not compete)

---

## How to Use This Document

1. **Before implementing anything**: Check if it aligns with this vision
2. **When making tradeoffs**: Prioritize what serves the core use case (social science research)
3. **When uncertain**: Ask "does this help researchers run better simulations?"

---

_This document is the source of truth for Miniverse's direction. ROADMAP.md translates this into implementation phases. CLAUDE.md provides operational guidance for development._

-- Claude | 2025-12-29
