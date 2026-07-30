# SJ6 – Half Lap Joint with Mitered Ends

DE: Stehendes gerades Blatt mit beidseitig schrägem Stoß · JP: ai-sogi-tsugi · 相殺継ぎ

A half lap whose dividing plane **stands vertical**, splitting the width
rather than the depth, with both abutments cut as 45° miters instead of
square shoulders. Both parts identical ("2×"). Set the lap lying instead of
standing via `rotation` = 90°.

## Measured from the scan

Taken off p. 158. Two views, and they agree on the two things that matter.

| | measured | model |
|---|---|---|
| section, elevation / plan | 157 / 155 px | 1.0 |
| **miter angle**, four independent lines | 0.960, 0.967, 1.013, 1.013 | **45.0°** ✓ |
| **lap length** from the elevation stations | 316 px = **2.01** | 2.00 ✓ |
| **lap length** from the plan steps | 312 px = **1.99** | 2.00 ✓ |
| lap orientation | plan shows a half-width step | vertical ✓ |

The two views were measured independently and land on the same abutment
stations — 1194 and 1510 px in the elevation, 1195 and 1507 in the plan. The
existing `SJ6.json` needs no change: `check_joint` green, kept 0.4990.

## One feature I could not resolve

In the elevation each abutment appears as **two parallel 45° lines spaced
1.20 sections apart**, not one. Four lines in total, in two mirrored pairs;
the midpoint of each pair is exactly the abutment station confirmed by the
plan.

The current model projects one line per abutment, so something is
unaccounted for. Two readings fit the pixels equally well and I cannot
separate them from the scan alone:

1. **The elevation shows the two parts side by side, not assembled.** The
   drawing is 944 px long — exactly 6.01 sections, i.e. 2 × `aspect`. Then
   each part carries two parallel miters 1.20 apart and the pairing is an
   artefact of reading it as one assembly. But two *parallel* abutments on
   one part cannot be point-symmetric, and the book marks this joint "2×",
   so this reading contradicts the catalogue.
2. **The abutment is a compound bevel** — raked in plan as well as
   elevation, so its near-face and far-face edges project to two offset
   lines. That would make *beidseitig schräg* mean bevelled in both
   directions, and 1.20 sections of offset implies about 50° in plan. But
   the plan view shows the width stepping over only ~30 px where a 50° rake
   would need 188.

Neither reading is clean, and both would change the joint materially. I have
left the file as it stands, since the two measurements that do reconcile —
miter angle and lap length — both confirm it. Worth a look at the original
book page before deciding.

## Purpose

General splice for moderate damage, and the natural choice when the beam is
loaded about the vertical axis, since a standing lap keeps both halves full
depth and divides the width instead. A single part geometry serves both
sides, which simplifies fabrication and replacement.

## Structural behaviour

The lap transfers shear across the dividing plane. The 45° miters seat under
compression and remove the fracture-prone square shoulder with its short end
grain at the lap tip — the same weakness SJ3 attacks with a chevron and SJ2
with an undercut, solved a third way.

Insertion cone **0.126 of the sphere**, the tightest of the group: narrower
than SJ4's 0.177 and an order below SJ5's 0.500. The two opposed miters
restrict separation to a narrow band of directions, though the joint is
still weak in tension until pegged or bolted through the lap.

## Fabrication – sequence and tools

Four steps (A1–A4). Tools: square, marking gauge, marking knife; ryoba (rip
for the cheek, fine crosscut for the bevels) or a fine backsaw; chisel;
clamp or vise.

1. **Layout** – scribe lap length, dividing plane and both 45° bevels right
   around all four faces. Clean layout decides everything: both parts must
   be congruent.
2. **Cheek cut** (A2) – piece clamped vertical, rip along the dividing plane
   down to the shoulder line.
3. **Tip miter** (A3) – piece horizontal; saw the small 45° bevel at the lap
   tip, and the wedge falls away.
4. **Shoulder miter** (A4) – crosscut the long 45° bevel at the lap root;
   the large wedge frees the waste and exposes the lap.
5. **Cleanup** – pare cheek and both bevels flat, working to the scribed
   lines and not past them.
6. **Assembly** (A+A → AA) – slide the two identical parts together
   lengthwise; the miters pull the splice tight as it settles. Peg or bolt
   through the lap against tension.

## Effort

A few saw cuts per part and no mortising — one of the simplest splices, well
suited to cutting in situ. The bevels need a consistent 45° setting on all
four faces; that is the only fussy part.

## Provenance

"Timber Joints", chapter Splicing Joints, pp. 158–159. Japanese ai-sogi-tsugi,
相殺継ぎ; German stehendes gerades Blatt mit beidseitig schrägem Stoß.
Marked "2×".
