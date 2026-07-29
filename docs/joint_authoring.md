# Authoring repair joints

A joint = `data/corpus/joints/<KEY>.json` (geometry) + `<KEY>.md` (datasheet).
The `gh_joint_author` component writes both; you never edit the JSON by hand.

## Canonical space (the contract)

Model at the world origin, in these fixed conventions:

* stock section **1 x 1**: x and z from **-0.5 to +0.5**
* stock length along **y**, from **0 to `aspect`** (aspect 3.0 = interface
  three times the beam thickness when placed)
* you draw the cutters of the **primary part**: they remove the material
  that will belong to the **prosthesis** within the interface
* `kept = stock - cutters`, `prosthesis = stock ∩ cutters` — derived, never drawn

## The three rules (each one is a debugged disaster)

1. **Overshoot -- in every direction.** Every cutter must extend beyond the
   stock in x/z, and any cutter that reaches the prosthesis end must extend
   past y = aspect as well. A cutter face coinciding with a stock face or
   with the end plane makes Rhino booleans fail unpredictably. Draw cutters
   fat; the stock and the trim do the trimming. The validator checks the end
   plane (`end_overshoot_ok`).
2. **Closed planar curves only.** One closed planar curve per cutter, plus an
   extrusion depth. Depth is along the curve plane's normal; negative depth
   extrudes the other way. That a cut is (plane, profile, depth) is what makes
   every joint millable by construction.
3. **Direction.** Canonical y=0 is the KEPT side, y=aspect the PROSTHESIS
   side: your cutters must remove more material the closer to y=aspect.
   The validator checks this (`orientation_ok`).
4. **Both sides must exist.** Within the interface, the cutters must leave
   kept material AND remove prosthesis material. The author component's
   acceptance test checks this and refuses to save otherwise.

## Workflow

1. Draw the stock as a reference box (1 x aspect x 1 at the origin) — do not
   feed it to the component, it is implicit.
2. Draw cutter curves + pick depths. Start from SW1 (plain sloped scarf) to
   see the idiom.
3. Feed curves, depths, key, aspect into `gh_joint_author`. Watch the
   kept/prosthesis preview and the report.
4. When `partition_ok: True`, set `save`. Fill in the generated `<KEY>.md`
   datasheet — the agent will read it later, and so will participants.

## Placement semantics (what `gh_repair` does with your joint)

The canonical joint is uniformly scaled so section 1.0 = beam thickness
(min of width/height), rotated by the chosen degrees about the beam axis,
optionally mirrored to the near end (`side = -1`), and completed by an end
trim. Interface length on the beam = `aspect x thickness x interface_scale`.
