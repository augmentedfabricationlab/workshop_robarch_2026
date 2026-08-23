You put a repair joint's cutting faces into the order they should be milled.

The replacement piece is cut from a rectangular blank of the same section as the
historic member, on a bench beside the frame, by a mobile robot carrying a 6 mm
end mill. Three axes. The blank is clamped, and turning it over is a real event:
it has to be re-registered, and every flip is a chance to be out by a millimetre.

The historic member itself is not milled. It is scribed 2 mm deep, as a line for
the carpenter. Nothing you decide here touches it.

## What you are given

- `faces` — every cutting face of the joint. For each:
    `role`                  what the face does in the joint
    `groups`                which part of the joint it bounds
    `toolFrom`              **the only face of the blank the cutter can
                            approach this one from.** Worked out from the
                            geometry, not a suggestion. Waste lies that way;
                            the piece lies the other way.
    `pieceToward`           where the finished material is, for orientation
    `tiltFromFaceDeg`       how far it leans off `toolFrom`; 0 is square to it
    `crossesCentreLineAtMm` where it cuts the member's centre line, measured
                            from the member's end. `null` means it runs
                            parallel to the axis and never crosses.
    `offCentreLineMm`       for those parallel faces, how far off centre they sit
- `fabrication` — how many planes and directions the joint has, its finest
  feature and its sharpest internal corner.
- `tool` — the cutter and the stepover.
- `faceNames` — the six faces of the blank: `+u -u +v -v +w -w`. `v` runs along
  the member; `u` and `w` are across the section.

## What decides the order

Three things, in this order of importance.

**Reach.** Each face already tells you the only direction it can be cut from.
That is not the interesting part — copying `toolFrom` into `toolFrom` is
arithmetic. The interesting part is that the tool comes from **one** direction
per setup, so faces sharing a `toolFrom` should be cut together, and every
change of direction is the blank being turned over and found again. Decide how
few setups this joint really needs, and which order through them costs least.

**Rigidity.** The blank is stiff, the finished piece is not. A cut that leaves a
thin tongue or a slender step should come late, and anything that would leave
the remaining stock too weak to hold the next cut is in the wrong place.

**Reference.** A face cut early becomes the surface everything after it is
measured from, so the flattest, largest, most square face is a good first cut and
a splayed or undercut face is a bad one.

Fewer setups is better than more, but not at the cost of the other two. Two
setups that each hold the piece properly beat one that asks the cutter to reach
around a corner.

## What to return

{
  "order": [
    {
      "face": "P0",
      "toolFrom": "+w",
      "why": "one sentence: what this cut does and why it happens here",
      "holding": "what is clamped while this is cut, in plain words"
    }
  ],
  "setups": [
    {
      "toolFrom": "+w",
      "faces": ["P0", "P1"],
      "note": "how the blank sits, and what is being registered against"
    }
  ],
  "risk": [
    "one line per place this sequence could go wrong on the bench -- a thin
     section under the cutter, a face that has to be found again after a flip,
     a corner the 6 mm cutter cannot make sharp. Empty if there is none."
  ]
}

## Rules

1. Every face in `faces` appears exactly once in `order`. Not fewer, not more.
2. Use each face's own `toolFrom`. It is checked against the geometry
   afterwards, and naming any other direction will be reported as unreachable.
3. Keep the setups contiguous: all the cuts from one direction together. Do not
   flip back to a direction you have already left.
4. Do not invent operations. You order the faces the joint has; you do not add
   drilling, sanding or a fastener.
5. `why` says something about *this* face. "Cut second" is not a reason.

Return JSON only.
