# SJ7 – Tapered Dovetail Tenon Joint with Mitered Shoulders

DE: Stoß mit Gratschnitt und keilförmigen Schwalbenschwanzzapfen · JP: basara-tsugi · 婆娑羅継ぎ
**Osaka Castle Otemon gate pillar splice** (大阪城大手門控柱の継手)

A butt splice carrying a full-width blade whose top and bottom faces are
tilted in **two** directions at once — along the beam and across the width —
with the shoulders mitered at 40° in plan. The two tilts are what make the
joint go together: it will not slide along the beam and it will not lift, but
it slides straight in along one diagonal. First **A+B** joint in the
catalogue.

## Source

Modelled from the CAD, not from the book drawings. The Joinery publishes a
STEP file for this joint (`thejoinery.jp`, item 73438), and every plane below
is read directly out of it — normals and offsets, not traced pixels. The
orthographic views on pp. 240–241 could not resolve it: two earlier attempts
from those drawings produced a pyramid and then a wrong blade.

Section in the source is 18 mm square; everything here is divided by 18.

| plane | normal (x, y, z) | reads as |
|---|---|---|
| end face | (0, 1, 0) | the butt |
| P3 / P6 | (∓0.1300, ∓0.1607, ±1) | outer blade faces, thick side |
| P4 / P5 | (∓0.1350, ∓0.1607, ±1) | inner blade faces, thin side |
| miter ∓ | (∓0.840, 1, 0) | shoulders, 40.0° in plan |

## The relationship that defines the joint

Three numbers out of the CAD, to five decimals:

```
inner blade faces   dz/dx / dz/dy  =  0.13500 / 0.16071  =  0.84000
miter slope in plan                =  0.643192 / 0.765705 =  0.84000
```

**Exactly equal.** The blade's cross-width tilt and the shoulder miter are
the same 0.84, which means the direction

```
d  =  (±0.7657, +0.6432, 0)      40.0° off the beam axis, in plan
                                 sign follows the handedness, see below
```

lies **exactly** in the miter plane and **exactly** in both inner blade
faces — d·n = 0.000000 for all three. That is the assembly direction, and it
is a designed coincidence, not a measured near-miss.

So the joint is not "impossible", it is single-directional: it cannot be
drawn along the beam (the blade is a dovetail in y), cannot be lifted (the
blade is a dovetail in z), and cannot be pushed sideways (the miters block
it). It slides together on one diagonal in plan and locks in every other
direction. The outer pair P3/P6 runs at 0.80889 rather than 0.84 — about
0.4% off the slide direction, which reads as working clearance on the faces
that are not meant to bear.

## Correction to two earlier versions

- **v1** put the miter flanks inside the slot group. That tapers the pocket
  to a point at mid-width and renders as a pyramid.
- **v2** made the blade full-width with a plain root, and gave an *empty*
  insertion cone — the joint could not be assembled at all. Empty was the
  clue that the model was wrong, not that the joint was clever.
- **v3, this one** has the miter doing the stepping. There is no wall at
  x = 0: the step in the end-face outline, from 0.300 down to 0.1675, is
  bounded by the **miter plane**, which passes through the end face exactly
  at mid-width. That single fact is what the earlier readings missed.

## Handedness

The stored entry is **mirrored across the width** relative to a direct read of
the STEP — reflection in x, so the CAD's thick blade side moves from −x to
+x... and back again. Net transform from the source file: reflection in z.

This is a *reflection*, not a rotation, so the stored joint is the opposite
hand from the Osaka Castle original. Both halves come from this one entry, so
they mate with each other perfectly; it only matters if you ever cut one half
from this file and the other from the CAD.

Verified: the blade section at x = −0.45 now reads −0.2374 … +0.2366 where it
previously read −0.2241 … +0.2246, i.e. the two arris sections have swapped
sides exactly. `aspect`, the kept side at y = 0 and all four acceptance checks
are untouched; kept 0.4587 → 0.4588, sampling noise.

The slide direction mirrors with it: **d = (−0.7657, +0.6432, 0)**, still
40.0° off the beam axis in plan, now leaning the other way.

Two orientations you do **not** need a file for: `rotation` = 180 on
`gh_repair` turns the joint about the beam axis at placement time, and
`side` = −1 swaps which half is the prosthesis.

## Cut structure

The stored entry is the joint **reversed along the beam** — every cutting
plane turned 180° about z. That is not a transform of the removal, because
reversing which end is kept means complementing it. The complement of the
five original groups reduces cleanly:

```
~R  =  ~end & ( ~mit_n | ~P3 & ~P6 ) & ( ~mit_p | ~P4 & ~P5 )
```

which expands to four groups over the same seven planes, each plane negated
and then rotated:

| group | cuts | reads as |
|---|---|---|
| 0 | ~end, ~mit_n, ~mit_p | inside both miters, short of the butt face |
| 1 | ~end, ~mit_n, ~P4, ~P5 | inside the −x miter, between the thin blade faces |
| 2 | ~end, ~P3, ~P6, ~mit_p | between the thick blade faces, inside the +x miter |
| 3 | ~end, ~P3, ~P6, ~P4, ~P5 | between both blade face pairs |

Seven cuts, four groups. Verified exactly: the stored `kept` equals the
previous `prosthesis` rotated 180° about z, agreement 1.00000 over 400,000
sampled points. Kept fraction moves 0.4588 → 0.5417, which is the same
partition seen from the other side.

## Verified against the CAD

`aspect` 3.0, end face at y = 1.5, kept fraction 0.4589. Partition, both
sides, orientation and end overshoot all pass.

End-face section, model against CAD planes:

| x | model | CAD |
|---|---|---|
| −0.45 | −0.2381 … +0.2389 | −0.2383 … +0.2392 |
| −0.05 | −0.2901 … +0.2909 | −0.2903 … +0.2912 |
| +0.05 | −0.1722 … +0.1717 | −0.1725 … +0.1720 |
| +0.45 | −0.2261 … +0.2256 | −0.2265 … +0.2260 |

Agreement is 0.0004, which is the sampling step. The shoulder V read back at
y = 1.5 − 0.84·|x| to within 0.0003 at every station tested.

## Structural behaviour

Locked against tension along the beam, against lift, and against racking, by
geometry alone and without a peg — the only joint in the catalogue that
manages all three. The price is that it can only be assembled by sliding
along one line, which means it can only be *installed* where there is room to
move a member 40° off axis in plan. For a pillar splice in a standing gate
that is exactly the constraint you can satisfy and almost nothing else is.

**For the agent phase.** The insertion cone here has measure zero — a single
ray, not a solid angle — so a sampled cone test returns 0 and looks identical
to "impossible". The test has to be done as a linear program over the design
normals, not by sampling, and the answer has to distinguish *empty* from
*degenerate*. Both v2 and v3 sample to 0.00000; only one of them can be built.

## Provenance

"Timber Joints", chapter Splicing Joints, pp. 240–241. Japanese basara-tsugi,
婆娑羅継ぎ; German Stoß mit Gratschnitt und keilförmigen
Schwalbenschwanzzapfen. From the pillar splices of the Otemon gate at Osaka
Castle. Geometry from the STEP model at thejoinery.jp item 73438. Parts A
and B, not "2×".
