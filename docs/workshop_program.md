---
layout: page
title: Workshop program
order: 10
---

*Draft — day boundaries and times still to be confirmed with the organisers.*

Three days, structured as one loop repeated at increasing resolution: **read the damage → propose a repair → execute it → measure what actually happened**.

| Day | Time | Phase |
|-----|------|-------|
| 1 | 09:00–10:00 | Introduction: damage as prompt, the Reasoning-to-Action workflow |
| 1 | 10:00–11:00 | Tour of the half-timbered wall and the damaged beams |
| 1 | 11:00–13:00 | Capture: photographs, notes, measurements, scans |
| 1 | 14:00–17:00 | Building the Assembly Model |
| 2 | 09:00–11:00 | Structured prompting: roles, goals, constraints |
| 2 | 11:00–13:00 | Generating and comparing repair strategies |
| 2 | 14:00–16:00 | Joint selection and cut geometry |
| 2 | 16:00–17:00 | Simulation and toolpath validation |
| 3 | 09:00–12:00 | Robotic execution: scanning, marking, milling |
| 3 | 12:00–14:00 | Manual execution: material removal, surface treatment, fitting |
| 3 | 14:00–15:30 | Scan-after-cut: measuring the result |
| 3 | 15:30–17:00 | Comparative presentations and discussion |

---

## Day 1 — Reading the structure

### 09:00–11:00 — Introduction and tour

**What happens.** Introduction to the workflow: damage as prompt, the path from heterogeneous observation to Assembly Model to Action Model. Participants then walk the half-timbered wall with the team, looking at each damaged beam in turn.

**What participants do.** Set up the toolkit on their own laptop and take a first set of observations on one beam.

**Deliverable.** Toolkit running, one beam chosen.

### 11:00–13:00 — Capture

**What happens.** Systematic documentation of the chosen elements.

**What participants do.** Photograph, measure, sketch, and scan. Name what they consider damage, and note what they are deliberately leaving out.

**Deliverable.** A capture set per team, sufficient to build a model from.

### 14:00–17:00 — Building the Assembly Model

**What happens.** The captured material is translated into a structured Assembly Model: components, connections, and damage states.

**What participants do.** Generate the model, then verify it against the beam in front of them and correct what the model abstracts, omits, or misreads.

**Deliverable.** A verified Assembly Model of the wall segment, with at least one damage state described well enough to act on.

---

## Day 2 — From strategy to toolpath

### 09:00–13:00 — Prompting and comparing strategies

**What happens.** Structured prompting with defined roles, repair goals, and constraints: available tools, stock material, robotic reach and capability.

**What participants do.** Generate **at least two alternative** repair strategies for the same damage, and make the successive intervention steps explicit enough to be argued about.

**Deliverable.** Two or more Action Models for the same beam, each an ordered sequence with steps allocated to human or robot.

### 14:00–17:00 — Joint, geometry, toolpath

**What happens.** One strategy is taken forward into geometry. Joint selection from the catalogue, parametrisation, derivation of the cut planes and the scribe lines for marking.

**What participants do.** Choose and position the joint, generate the cut geometry, then simulate the resulting toolpaths and check them against workspace and end-effector limits.

**Deliverable.** A validated toolpath set, ready to run.

---

## Day 3 — Execution and evaluation

### 09:00–14:00 — Making

**What happens.** The repair is executed. Robotic routines handle scanning, marking, and milling; the complementary steps stay manual.

**What participants do.** Run the robotic operations, then carry out material removal, surface treatment, and fitting by hand.

**Deliverable.** A repaired element.

### 14:00–15:30 — Scan-after-cut

**What happens.** The result is scanned and compared against the model that produced it.

**What participants do.** Measure the fit, locate where the idealised geometry and the real timber diverged, and record it.

**Deliverable.** A scan of what was actually produced, alongside the model that predicted it.

### 15:30–17:00 — Comparative presentation and discussion

**What happens.** Teams present their strategies side by side: what was proposed, what was chosen, what was built, and what the measurement showed.

**Deliverable.** Digital records — Assembly Models, Action Models, scans, and documentation of how each decision was reached.

---

## Where humans stay in the loop

The workflow is built to *propose*, not to decide. Participants intervene at four points:

- **Framing.** What gets photographed, measured, and named as damage is already an interpretation.
- **Verification.** The Assembly Model is a hypothesis, checked against the beam.
- **Comparison.** Alternatives are weighed against feasibility, care for the existing material, and future use.
- **Evaluation.** The scan closes the gap between idealised prismatic geometry and real, weathered, out-of-square timber.
