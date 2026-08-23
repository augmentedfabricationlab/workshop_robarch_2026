# SJ5 – Undersquinted stop-splayed cogged scarf joint

DE: schräges Hakenblatt mit schräger Brüstung
JP: ryaku-kama-tsugi (sogi-tsuki) · 略鎌継ぎ（殺付）

A scarf whose splay is *stopped* at both ends by short shoulders instead of
running out to a feather edge, and interrupted at mid-length by a **cog** — a
square step in the seam. The shoulders and the cog face are **undersquinted**:
leaned rather than square to the axis, so the two halves wedge together across
the depth as they are drawn up.

Three things are happening at once, and each is one face:

- the **splay** carries the compression over a long raked bearing
- the **cog** stops the halves sliding past one another along the member
- the **undersquint** stops them separating across the section

## How it is written here

Six planes, three directions. The splay is one direction used twice, at two
offsets — the same rake below and above the cog, which is what makes the step
read as a step rather than as two unrelated cuts. The shoulders and the cog
face share a second direction, used three times, with its opposite used once to
close the near tongue against the cog.

    P0  near shoulder, undersquinted     direction S
    P1  lower splay face                 direction R
    P2  cog face, kept side              direction -S
    P3  cog face, replaced side          direction S
    P4  upper splay face                 direction R      (P1's rake, offset)
    P5  far shoulder, undersquinted      direction S

    removal = (P0 & P1 & P2) | (P3 & P4) | P5

Three groups, and each is closed on the kept side. The first is the tongue
below the lower splay, stopped at the cog. The second is everything below the
upper splay beyond the cog. The third is the whole section past the far
shoulder.

| | value |
|---|---|
| aspect | 4.0 |
| splay rake | 0.20 in z per 1.0 in y (11.3° to the axis) |
| undersquint | 0.25 (14.0° off square) |
| near shoulder | y 0.75, from z −0.50 to −0.34 |
| cog | y 2.00, step of 0.18 in z, centred on z = 0 |
| far shoulder | y 3.25, from z +0.34 to +0.50 |
| distinct plane directions | 3 over 6 planes |

The depth is spent symmetrically, and that is the check to make on any redraw:

    stop 0.16 + splay 0.25 + cog 0.18 + splay 0.25 + stop 0.16 = 1.00

Both stops the same, both splays the same rake and the same rise, the cog
centred on the middle of the section, and the whole joint centred in its window
at y 0.75 to 3.25. A first attempt had the stops at 0.22 and 0.12, which reads
immediately as wrong in elevation — the two shoulders of a stop-splayed scarf
are the same face doing the same job at each end.

## Where the numbers came from — read this before trusting them

**These proportions are read off a photograph of the book page, not measured
from a scan.** The other datasheets in this folder quote pixel measurements
with residuals; this one cannot. What is faithful here is the *grammar* — stop
shoulders at both ends, two parallel splay faces offset by a cog, shoulders and
cog sharing one lean, the seam full width with no splay in plan. What is a
reading are the exact rake, the undersquint angle, the cog height and the two
shoulder stations.

They are proportioned to look like the drawn elevation on p. 124 and to behave
correctly when placed, not fitted to it. If the page is ever scanned properly,
the six offsets are the only thing that needs revisiting; the directions and
the grouping will not change.

## Behaviour, measured

Placed on a 100 × 100 mm post rotten at the foot, swept through the full circle
and slid to its best station:

| | |
|---|---|
| every rotten cell taken | yes, `rotLeft` 0 of 64 |
| sound timber spent | 9.1% of the member |
| locks | `-u`, `+v`, `-w` |

`+v` is the cog earning its place: the joint resists the replacement piece
being drawn along the member, which a plain splayed scarf does not do at all.
That is the whole reason for the step, and it is the one thing this entry adds
to the corpus that no other splice in it has.

## Note on the archive

This entry **replaces** the previous SJ5, which was a plain splayed scarf — a
single raked plane, no shoulders, no cog (`sogi-tsugi`, 殺継ぎ, p. 156). The
old plate-form record still sits at `plate-form/SJ5.json` and now describes a
different joint from the one in `SJ5.json`. Either re-author that archive or
read it as a record of what the entry used to be.
