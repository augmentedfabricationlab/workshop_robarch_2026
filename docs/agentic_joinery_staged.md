# Agentic Joinery — five-component canvas

The workshop canvas uses five visible GH Python components. Each phase passes one
hash-checked JSON record to the next phase, while Rhino geometry stays on normal
geometry wires.

1. `gh_repair_01_setup.py` — select the member and collect its context
2. `gh_repair_02_brief.py` — draft and confirm the Workspace repair idea
3. `gh_repair_03_variations.py` — author one plane joint and browse local variations
4. `gh_repair_04_review.py` — measure the selection and optionally revise it once
5. `gh_repair_05_save.py` — record the decision and save a new Workspace ZIP

The smaller scripts remain available for debugging and advanced canvases.

## Canvas wiring

### 01 Setup

Inputs: `workspace_json, picker, refresh, box, centers, damage, threshold, repo`

Outputs: `setup_json, beam_id, capabilities_json, report`

Wire a Grasshopper Value List into `picker` and press `refresh` once. The list is
filled automatically from the Workspace. `beam_id` remains the familiar selected
part name. `centers` and `damage` are the existing cell field inputs.

### 02 Brief

Inputs: `setup_json, instruction, gemini_model, draft, review_note, confirm, reset, repo`

Outputs: `repair_json, brief_json, workspace_facts, llm_inferences, open_questions, status`

Press `draft`, read the three separated kinds of information, add a short
`review_note`, and press `confirm`. `repair_json` appears only for the exact brief
the participant confirmed.

### 03 Variations

Inputs: `repair_json, instruction, gemini_model, variation_count, generate, reset,
picker, refresh, box, centers, damage, threshold, neighbour_geometry, neighbour_ids,
execute_all, repo`

Outputs: `candidate_set_json, selection_json, candidate_id, candidate_ids,
candidate_json, candidate_code, summary, kept, prosthesis, frames, other,
candidate_geometry, execution_json, report`

Use a second Value List for `picker`; 5 is a useful default and 2–8 are accepted.
`generate` asks Gemini for one six-plane construction concept under the unchanged
confirmed repair idea. The corpus is supplied as optional geometric precedent. The
component first fits that concept to the damaged member end, then explores nearby
variations that Gemini authors in the same response. Each study names the relevant
cutting-plane roles and their intended construction effect. Plane changes are limited
to 0.25°–8° and optional whole-joint changes to 10°; quarter-turn duplicates are not
generated. Failed Rhino Booleans are skipped. The surviving exact Brep variations
are ranked first by damaged-cell coverage and then by measured sound-timber loss.
`summary` explains the authored change and compares damaged-cell removal and
sound-timber loss with the best surviving reference.

Complete damage coverage ranks ahead of conservation. When no study covers every
required cell, the component still returns the best partial studies and lists the
unremoved cell IDs explicitly in the picker summary and report.

Click through the list; `kept`, `prosthesis`, and `frames` switch immediately.
`frames` contains active cutting-plane rectangles and direction arrows. It is excluded from
`candidate_geometry`, Review and Save. Existing canvases may keep the `other` output:
the script exposes `frames` there as a compatibility alias. Rename that output to
`frames` when preparing the workshop canvas. The legacy `refresh` and `execute_all`
inputs may remain connected but are no longer required.

The prompt gives the model broad robotic-fabrication freedom where useful: freely
oriented planar cuts, compound directions, asymmetry, and unequal slopes. It
prescribes no joint family, angle, symmetry, or corpus topology.

### 04 Review / Revise

Inputs: `repair_json, selection_json, candidate_geometry, box, centers, damage,
threshold, neighbour_geometry, neighbour_ids, measure, feedback, gemini_model,
revise, use_revision, reset, repo`

Outputs: `active_json, active_geometry, facts_json, requirements_json,
diagnostic_geometry, change_summary, report`

Press `measure` after choosing a variation. Read the facts and diagnostics. For one
focused change, write `feedback` and press `revise`; the component authors, executes,
and measures the revision. `use_revision` switches between the selected authorship
and its revision. The selected version leaves the component as `active_json`.

The measurements are evidence for discussion. Sampled insertion checks do not prove
continuous collision clearance. Tool-path checks describe declared paths and their
coverage/excess overlap. Unresolved computations stay visible as unknown.

### 05 Save

Inputs: `workspace_json, active_json, active_geometry, decision, decision_note,
save_path, save, repo`

Outputs: `proposal_json, workspace_updated_json, saved_path, ready, report`

Choose a decision, write a non-empty reason, and provide a new `save_path`. Press
`save` to create a Workspace copy containing the proposal record and a hashed 3DM
geometry attachment. Saving over the input Workspace is rejected.

## Grasshopper access settings

Keep the existing input names. Set these ports to **List access**:

- Setup: `centers`, `damage`
- Variations: `centers`, `damage`, `neighbour_geometry`, `neighbour_ids`
- Review: `candidate_geometry`, `centers`, `damage`, `neighbour_geometry`, `neighbour_ids`
- Save: `active_geometry`

All other inputs use **Item access**. Geometry outputs and the textual report/status
outputs use List access; JSON records and scalar IDs use Item access.

Set the `box` input Type hint to **Box**, especially on Setup. Variations and Review
can also reconstruct the recorded Box frame when Grasshopper supplies a Rhino GUID.

Use momentary Buttons for `refresh`, `draft`, `confirm`, `generate`, `execute_all`,
`measure`, `revise`, `reset`, and `save`. Rising-edge protection prevents a Toggle
left on from repeatedly calling the model.

`tolerance` is intentionally absent. The components read Rhino's current Model
Absolute Tolerance automatically and record it in the session.

## Suggested 60-minute exercise

- 0–10 min: select the part and inspect Workspace facts versus LLM inferences.
- 10–18 min: edit and confirm the repair brief.
- 18–33 min: generate one concept and compare its local geometric variations.
- 33–45 min: choose one, measure it, and formulate one evidence-based revision.
- 45–55 min: compare the authored and revised versions; record a decision reason.
- 55–60 min: save the Workspace copy and discuss what the LLM proposed, what Rhino
  measured, and what the participant decided.

## Handoff

The Workspace proposal stores the manifest, authored Python, measurements, decision,
hashes, and a 3DM attachment. Import or bake that attachment in Rhino for subsequent
fabrication development. The Repair Workspace currently preserves this record during
import/export; its existing UI does not yet display the candidate geometry.
