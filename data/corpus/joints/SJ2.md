# SJ2 – Undersquinted Half Lap Joint

DE: Gerades Blatt mit hinterschnittener Brüstung · JP: naga-koshi-kake-tsugi (sogi-tsuki) · 長腰掛継ぎ（殺付）

SJ1 with two changes: the lap is long (*naga*), and both abutment faces are
undercut rather than square. The undercut is the whole point — it is what
stops the halves lifting apart, which plain SJ1 cannot do. Both parts are
identical ("2×").

**Terminology, because it matters elsewhere in the catalogue:**
*undersquinted* = *hinterschnittene Brüstung* = **undercut shoulder**. It is
an abutment leaning out of square in elevation. It is not a gable, ridge or
Gratschnitt of any kind.

## Measured from the scan

Traced off the two elevations on p. 056 at single-pixel resolution:

| | measured | used |
|---|---|---|
| beam height | 159 px | 1.0 |
| lap plane above the bottom | 79.5 px | 0.500 — exactly half |
| lap length, part 1 | 274 px | 1.718 |
| lap length, part 2 | 273 px | 1.712 → **1.72** |
| abutment bevel, run / half-depth | 40.3 / 79.5 px | slope 0.507 → **0.500** |
| abutment angle off square | | **26.6°** |
| plan view, both parts | full-height straight lines | no chevron, no taper in width |

The bevel is the same on both parts and leans the same way on both: each
tongue reaches **furthest at the lap plane** and is cut back toward the outer
face. That direction is what locks the joint.

Canonical placement: `aspect` 3.0, abutments at y = 0.64 and y = 2.36, giving
the measured 1.72 lap with 0.64 of solid beam either side. Point-symmetric
about the mid-length, consistent with the "2×" mark.

## One deliberate departure from the drawing

The scan shows the **shoulders square and only the tongue tips bevelled** —
each part has a vertical shoulder and a 26.6° tip. Assembled, that leaves a
wedge-shaped void opening toward the outer face at each abutment: the classic
undercut relief, bearing at the lap plane and clearance everywhere else.

The kernel cannot represent this, because `kept` and `prosthesis` are exact
complements of one stock by construction — there is no room for a designed
gap. So the model carries the **bevel on both faces**: the tongue tips are
exactly as measured, and the shoulders are their exact complements rather
than square. Nominal geometry, with the relief left to the fabricator.

Practically this means: cut the tongue tips to the model, then relieve the
shoulders by a millimetre or two before fitting. Step 5 below.

## Structural behaviour

The undercut is a mechanical lock. Taking the design-face normals and asking
which directions the prosthesis can separate along:

| | insertion cone | lift (+z) | draw (+y) |
|---|---|---|---|
| SJ1 | 0.250 of the sphere | **yes** | yes |
| SJ2 | 0.177 | **no** | yes |

SJ1 comes apart by lifting one half off the other, and needs a peg to stop
it. SJ2 cannot: any upward motion drives the tongue tips into the undercut
abutments. It can only be drawn apart along the beam. For a repair splice
that is the useful property — the prosthesis stays seated under load
reversal and vibration without depending on the fastener.

Bending and shear are as SJ1, limited by the half-section. The long lap
spreads the shear over 1.72 thicknesses instead of 1.0, roughly halving the
bearing stress on the cheek. Tension along the beam is still resisted only
by the fastening; the undercut does nothing against pure pull-out.

## Fabrication – sequence and tools

Tools: try square, sliding bevel set to 26.6°, marking gauge, marking knife;
ryoba or backsaw; wide paring chisel; mallet.

1. **Lay out** (A1) – gauge the lap plane at mid-height right around, square
   the two abutment lines across the faces, then set the bevel and carry the
   26.6° lines down the two side faces from each abutment. Both lean the same
   way: leading edge at the lap plane.
2. **Shoulder cut** (A2) – crosscut down at the shoulder, on the waste side,
   stopping on the gauge line.
3. **Cheek cut** (A3) – rip from the end along the lap plane until the waste
   frees at the shoulder kerf.
4. **Undercut** (A4) – saw the tip bevel following the 26.6° lines, from the
   outer face in toward the lap plane. This is the cut SJ1 does not have.
5. **Relieve and pare** – back off the shoulder face slightly so bearing
   falls at the lap plane, not at the arris. Flatten the cheek.
6. **Assemble** (A+A → AA) – the second part is identical, end for end. It
   will only go together by sliding along the beam; if it wants to drop in
   vertically, the bevel is leaning the wrong way.

## Effort

One more saw cut per abutment than SJ1, plus a bevel setting to carry
around. Still entirely hand-tool work and still cuttable in situ, but the
layout is less forgiving: the two bevels must lean the same way or the joint
will not close.

## Provenance

"Timber Joints", chapter Splicing Joints, pp. 056–057. Japanese
naga-koshi-kake-tsugi (sogi-tsuki), 長腰掛継ぎ（殺付）; German gerades Blatt
mit hinterschnittener Brüstung. Marked "2×": one part geometry serves both
halves.
