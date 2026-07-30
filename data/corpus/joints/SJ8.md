# SJ8 – Lapped dovetail joint

DE: Schwalbenschwanzzapfen mit geradem Blatt · JP: koshi-kake-ari-tsugi · 腰掛蟻継ぎ

A half lap at mid-height whose engagement is a dovetail rather than a plain
tongue. Part A carries a short seat and a dovetail socket cut into its upper
half; part B carries the matching pin on the end of its upper-half tongue.
The parts drop together vertically and then cannot be pulled apart along the
beam. **A+B**, not "2×".

## Measured from the scan

Taken off p. 224. Both plan views carry the dovetail as a true outline — the
socket in A and the pin in B — so it was measured twice, independently.

| | socket (A) | pin (B) | used |
|---|---|---|---|
| neck width, at the shoulder | 52 px | 53 px | **0.332** |
| head width | 76 px | 77 px | **0.484** |
| dovetail length | 78 px | 75 px | **0.484** |
| flare per side | | | **0.157 → 1:6.4, 8.9°** |

| | measured | used |
|---|---|---|
| section | 158 px | 1.0 |
| lap plane above the bottom | 77–79 px | 0.500 — mid-height |
| seat beyond the shoulder | 48 px | 0.304 |
| total engagement | seat + dovetail = 126 px | 0.788 |

The two independent readings of the dovetail agree to within a pixel on all
three dimensions, which is the tightest cross-check in the catalogue. 1:6.4 is
a conventional timber dovetail slope.

Canonical placement: `aspect` 3.0, shoulder at y = 1.500, seat end at 1.804,
socket root at 1.016. Kept fraction 0.5195 — part A is the larger half, since
it keeps its full section past the shoulder everywhere except the socket.

## Cut structure

Six half-space cuts, three groups:

| group | cuts | reads as |
|---|---|---|
| 0 | seat_end | everything past the seat |
| 1 | lap_up, shoulder | the upper half beyond the shoulder |
| 2 | lap_up, dt_root, dt_xp, dt_xn | the socket: upper half, past the root, between the two flared flanks |

Group 2 is why this joint needs `removal_groups`. The socket is a four-plane
pocket sitting inside solid material, and the two flank planes converge — it
is an intersection, and no union of prisms reaches it.

Read back from the model: the pin measures 0.3353 at the neck and 0.4808 at
the head against 0.3323 and 0.4842 measured, which is the sampling step. Part
A is full height at the arris up to y = 1.49 and half height from 1.60, and
half height on the centre line throughout — the socket is open at the top and
buried in the width, which is why the elevation shows no notch.

## Structural behaviour

The dovetail carries **tension along the beam**, which is what separates this
joint from every half lap in the catalogue: SJ1 through SJ4 all need a peg for
that, and SJ8 does not. The flanks bear directly, and 1:6.4 is shallow enough
that the short grain at the neck is not the first thing to fail.

Insertion cone: a **single ray, straight up (0, 0, 1)**. Axial draw fails at
−0.155 on the dovetail flanks; sideways fails at −0.988 on the flanks. So the
joint has exactly one assembly motion — lower B onto A — and resists
everything else by geometry.

That makes it the natural pair to SJ7. Both lock in all but one direction;
SJ7's one direction is a 40° diagonal in plan, SJ8's is vertical. SJ8 is far
easier to install and correspondingly easier to lift out again, so it wants a
peg against uplift, not against tension.

**Note for the agent phase.** Like SJ7 this cone has measure zero and samples
to 0.00000. Two joints in a row where the honest answer is "one direction
exactly" and a sampled test reports "impossible". The Fügbarkeit check has to
be an LP over the design normals.

## Fabrication – sequence and tools

Part A takes four steps (A1–A4), part B five (B1–B5), then A+B → AB.

1. **Lay out both parts** – lap plane at mid-height right around; shoulder
   lines; the dovetail centred on the width, marked from a single template so
   socket and pin come off the same lines.
2. **Part A: shoulder and seat** (A1–A3) – crosscut the shoulder to the lap
   line, rip the cheek back to free the seat.
3. **Part A: socket** (A4) – saw the two flared flanks down to the lap plane,
   chisel the waste out between them. Blind at the bottom, open at the top.
4. **Part B: tongue** (B1–B3) – shoulder and cheek as for a plain half lap.
5. **Part B: pin** (B4, B5) – saw the flanks to the flare lines and pare to
   fit. Cut the pin fat and fit to the socket, not the other way round.
6. **Assemble** (A+B → AB) – lower B vertically into A. It will not slide in
   along the beam; if it seems to, the flare is on backwards.

Tools: try square, sliding bevel at 1:6.4, marking gauge and knife, ryoba,
paring chisel, mallet.

## Effort

More than any half lap and less than SJ7. The dovetail flanks are the only
demanding cuts, and unlike SJ7's blade they are open and visible while sawing.
The neck is the fragile part in handling before assembly.

## Provenance

"Timber Joints", chapter Splicing Joints, pp. 224–225. Japanese
koshi-kake-ari-tsugi, 腰掛蟻継ぎ; German Schwalbenschwanzzapfen mit geradem
Blatt. Parts A and B, not "2×".
