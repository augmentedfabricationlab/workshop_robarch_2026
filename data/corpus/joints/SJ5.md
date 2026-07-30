# SJ5 – Splayed Ccarf Joint

DE: Schäftung · JP: sogi-tsugi · 殺継ぎ

A single flat plane raked through the whole section. No lap, no shoulder, no
step — the entire joint is one saw cut, and the two halves are identical
("2×"). It is the only entry in this group that is not a *Blatt* at all: a
scarf, not a lap.

## Measured from the scan

Taken off p. 156. The plan view is the reliable measurement here: the scarf
meets the near face and the far face at two clean vertical lines, one solid
and one hidden, and the distance between them is the rake directly.

| | measured | used |
|---|---|---|
| section, elevation / plan | 159 / 157 px | 1.0 |
| scarf stations in plan | x = 1050 (dashed) and 1523 (solid) | |
| scarf run | 473 px | |
| **rake** | **473 / 158 = 2.994** | **3.000** |
| rake angle to the beam axis | | **18.4°** |
| across the width | both stations dead vertical | uniform, no splay in plan |

Worth recording how this was measured, because the obvious route is worse.
Fitting the diagonal in the elevation gives 3.136 with an rms of 17.6 px —
the grain lines contaminate the fit and the scan sits about 0.7° off square,
which the long diagonal amplifies. The two plan stations are crisp (0.99 and
0.30 column coverage) and immune to both. The plan reading is the one to
trust.

## The existing file is correct

`SJ5.json` encodes a rake of exactly 3.000 against 2.994 measured — inside
one pixel over the run. Single cut, `aspect` 3.0, no groups, kept fraction
0.4978. Nothing to change; only this datasheet was missing.

The scarf spans the interface block exactly: it meets the top face at
y = 0.003 and the bottom face at y = 2.997. That is the natural placement for
a scarf, where `aspect` is not a margin choice but the rake itself.

## Purpose

The minimum-effort splice, and the one to use when the members cannot be
manipulated much — the two faces simply slide past each other. Also the right
choice when the splice will be reinforced anyway (plated, bolted, strapped),
since the joinery contributes nothing beyond the glue line and the geometry
just presents a long face to work with.

## Structural behaviour

The long rake is the whole idea: at 18.4° the joint face is 3.16 times the
section area, so any glue, bolt or plate acting on it has three times the
area it would have on a butt joint, and the stress crosses the grain at a
shallow angle rather than end to end.

Left to itself it carries almost nothing. The insertion cone is **0.500 of
the sphere** — half of all directions separate the halves, the largest of any
joint in the catalogue and the exact opposite of SJ2's undercut. It has no
mechanical interlock in any direction, resists tension only through friction,
and under bending the two halves simply slide. It must be fastened.

That makes it the useful baseline for the gallery: the joint that maximises
bonded area and minimises everything else.

## Fabrication – sequence and tools

Two steps in the book (A1, A2), the shortest sequence in the chapter.

1. **Lay out** (A1) – mark the two stations on opposite faces and connect
   them right around; the line on the two narrow faces is the saw's guide.
2. **Rake cut** (A2) – one continuous cut through the section, following the
   lines on both narrow faces. There is nothing to pare afterwards and no
   internal corner to reach.
3. **Assemble** (A+A → AA) – the second part is identical, end for end.

Tools: try square, marking knife, ryoba or a frame saw for the depth.

The difficulty is entirely in sawing a long true plane. A wandering cut
cannot be corrected by paring the way a lap cheek can, because both halves
must remain complementary.

## Provenance

"Timber Joints", chapter Splicing Joints, pp. 156–157. Japanese sogi-tsugi,
殺継ぎ; German Schäftung. Marked "2×".

Note on naming: an earlier draft of this datasheet called it *schräges Blatt*.
That is wrong — a *Blatt* is a lap, and this joint has none. *Schäftung* is
the term the book uses and the correct one.
