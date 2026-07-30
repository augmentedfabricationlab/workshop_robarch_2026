# SJ3 – Spear Point Half Lap Joint

DE: Gerades Blatt mit Gratschnitt · JP: naga-koshi-kake-yahazu-tsugi · 長腰掛矢筈継ぎ

A long half lap whose abutments are chevrons in plan: one half finishes in a
spear point, the other in the matching nock. The lap is flat and at
mid-height, and both abutments are square in elevation. Both parts identical
("2×").

SJ3 and SJ4 are the same joint. The scans differ in exactly one respect —
SJ4 adds *sogi-tsuki* (殺付) to the name, *schräg* to the German, and the
undercut to the abutments. Everything else measures the same to within 3%.

## Measured from the scan

Traced off p. 062. The plan views carry the chevron as a true outline, so
the taper could be fitted on four independent edges.

| | measured | used |
|---|---|---|
| beam height | 158 px | 1.0 |
| lap plane above the bottom | 79 px | 0.500 — exactly half |
| chevron slope, four edges | 0.667, 0.686, 0.684, 0.701 | **0.685** |
| chevron half-angle in plan | | **34.4°** |
| centre-line lap, parts 1 / 2 | 342 / 345 px | 2.165 / 2.184 → **2.17** |
| abutment in elevation | 2–4 px transitions | **square, no undercut** |

The square abutment is the finding that separates this joint from SJ4: the
elevation silhouette steps in four pixels, where SJ4's rakes over fifty.

Canonical placement: `aspect` 3.0, chevron apexes at y = 0.415 and y = 2.585,
point-symmetric about mid-length.

## Correction to the previous file

The `SJ3.json` in the catalogue did not match the drawing:

| | old file | scan |
|---|---|---|
| chevron slope | 0.500 | **0.685** |
| shoulder apex | y = 0.250 | **0.415** |
| tongue tip apex | y = 2.750 | **2.585** |
| centre-line lap | 2.500 | **2.170** |

The old chevron was 27° where the book draws 34°, and the lap ran 15% long.
The structure was right — two cuts, plain union — so only the numbers changed.
Rebuilt file attached; kept fraction moves 0.4950 → 0.4945.

## Cut structure

Two cuts, plain union, no `removal_groups`. Each is a half-thickness slab
extruded along z with a five-point chevron profile in the x–y plane. The nock
is a valley in the kept material, which normally forces an intersection — but
here the valley edge runs vertically, parallel to the extrusion, so a single
profile can hold the reflex corner. That is exactly the property SJ4's
undercut destroys, which is why SJ4 needs three intersect groups and SJ3
needs none.

## Purpose

The general-purpose splice of the chapter, and the one to reach for when the
repair has to resist sideways movement as well as bending. It costs two extra
saw cuts over SJ1 and buys a great deal.

## Structural behaviour

Shear crosses the lap as in SJ1, and bending is limited by the half section.
What the chevron adds is restraint against racking: any sideways displacement
drives the flanks of the point into the flanks of the nock, so the splice
cannot shear across the beam without splitting one of them.

It does not lock vertically — the halves still lift apart across the lap
plane, and the joint needs a peg. Adding that lock is precisely what SJ4's
undercut does.

Verified against the model: the spear point reaches y = 2.585 on the centre
line and 2.249 at the arris, a 0.34 overhang; the nock opens from 0.415 to
0.750. Model chevron slope 0.684 against 0.685 measured. Tip position is
identical at z = −0.05 and z = −0.45, confirming no undercut.

## Fabrication – sequence and tools

Six steps (A1–A6). Tools: try square, marking gauge, marking knife, ryoba,
paring chisel, mallet.

1. **Lay out** (A1) – lap plane right around; centre line on both wide faces;
   chevron from the abutment stations at 34° to the transverse.
2. **Shoulder crosscut** (A2) – down to the lap line, tracking the chevron,
   so the saw follows a V in plan rather than a straight line.
3. **Cheek rip** (A3) – along the lap plane to free the waste.
4. **Point flanks** (A4, A5) – saw the two flanks of the spear point.
5. **Nock** (A6) – pare the valley to the lines from both sides. The apex is
   short grain and the fragile part of the joint; never lever against it.
6. **Assemble** (A+A → AA) – identical parts, end for end.

## Effort

Two saw cuts more than SJ1, and the layout has to be right in plan as well as
elevation. No undercut, so no bevel setting is needed — that is the whole
difference in effort between this joint and SJ4.

## Provenance

"Timber Joints", chapter Splicing Joints, pp. 062–063. Japanese
naga-koshi-kake-yahazu-tsugi, 長腰掛矢筈継ぎ; German gerades Blatt mit
Gratschnitt. Marked "2×".
