"""Turning described damage into a number on every cell.

The survey says "wet rot to the foot, worst on the north-west arris, softening
to about 220 mm up, with a separate beetle-flighted patch under the rail". The
rest of the pipeline needs one value per cell. Between the two sits one explicit
step: a model reads the conditions and the photographs and returns REGIONS in
the member's own coordinates, and this module paints those regions onto the
cells.

Regions rather than four hundred numbers, for the same reason a joint is planes
rather than a mesh. It is the shortest description that carries the intent, it
cannot come back the wrong length, and what the model got wrong stays legible --
you can read "vRange 0.00..0.22" against the survey and disagree with it.

Coordinates are the member's own, metres from its corner, the same ones
`joinery` and `neighbours` use:

    u   across the width    0 .. width
    v   along the member    0 .. length
    w   across the height   0 .. height
"""
from __future__ import annotations

import numpy as np

FACES = {"-u": (0, -1), "+u": (0, +1),
         "-v": (1, -1), "+v": (1, +1),
         "-w": (2, -1), "+w": (2, +1)}


# ------------------------------------------------------------------ painting


def _span(value, high: float) -> tuple:
    """A [lo, hi] pair in metres, or the whole extent when it is not given."""
    if value is None:
        return 0.0, float(high)
    try:
        lo, hi = float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return 0.0, float(high)
    if hi < lo:
        lo, hi = hi, lo
    return max(0.0, lo), min(float(high), hi)


def paint(regions, local, size) -> np.ndarray:
    """One 0..1 value per cell, from boxes in member coordinates.

    Each region is a box with a severity. `falloff` is how far, in metres, the
    damage fades to nothing outside that box -- decay has no edge, and a hard
    step at the boundary would make the repair look tidier than the timber is.
    Regions combine by maximum, so overlapping them is how you say "rotten at
    the arris, softer towards the core": a small severe box inside a larger mild
    one.
    """
    local = np.asarray(local, float)
    out = np.zeros(len(local), float)
    size = [float(s) for s in size]
    for region in regions or []:
        lo = np.array([_span(region.get("uRange"), size[0])[0],
                       _span(region.get("vRange"), size[1])[0],
                       _span(region.get("wRange"), size[2])[0]], float)
        hi = np.array([_span(region.get("uRange"), size[0])[1],
                       _span(region.get("vRange"), size[1])[1],
                       _span(region.get("wRange"), size[2])[1]], float)
        def number(key, fallback):
            try:
                return float(region.get(key, fallback))
            except (TypeError, ValueError):
                return float(fallback)

        severity = min(1.0, max(0.0, number("severity", 1.0)))
        far = min(1.0, max(0.0, number("severityFar", severity)))
        fade = max(0.0, number("falloff", 0.0))

        # Severity ramps along v from `severity` at vRange[0] to `severityFar`
        # at vRange[1]. Decay reaching an end is total at the end and peters
        # out inland; one box at one severity says the loss stops dead, which
        # is never what the timber does.
        if abs(far - severity) > 1e-9 and hi[1] - lo[1] > 1e-9:
            along = np.clip((local[:, 1] - lo[1]) / (hi[1] - lo[1]), 0.0, 1.0)
            level = severity + (far - severity) * along
        else:
            level = severity

        gap = np.maximum(np.maximum(lo - local, local - hi), 0.0)
        distance = np.sqrt((gap ** 2).sum(axis=1))
        if fade <= 1e-9:
            value = np.where(distance <= 1e-9, level, 0.0)
        else:
            value = level * np.clip(1.0 - distance / fade, 0.0, 1.0)
        out = np.maximum(out, value)
    return np.clip(out, 0.0, 1.0)


def per_region(regions, local, size, threshold: float) -> list:
    """How many cells each region ends up owning, so a silent one is visible.

    A region the model wrote but that lands on no cell at all is the failure
    worth catching here -- usually a range in millimetres where metres were
    asked for, or an axis confused for another.
    """
    out = []
    for region in regions or []:
        values = paint([region], local, size)
        out.append({
            "id": str(region.get("id") or "region"),
            "what": region.get("what"),
            "cells": int((values > 0).sum()),
            "atOrAbove": int((values >= float(threshold)).sum()),
            "peak": round(float(values.max()) if len(values) else 0.0, 2),
        })
    return out


# --------------------------------------------------------------- orientation


def orientation(frame: dict, around=None) -> list:
    """Which way each face of the member points, and what is against it.

    Without this the model is asked to place "the foot" on an axis it has no way
    of tying to the world. With it, the end whose neighbour is the sill and
    whose outward direction points down IS the foot, and no guessing is needed.
    """
    size = [float(frame["width"]), float(frame["length"]), float(frame["height"])]
    axis_vectors = [np.asarray(frame[k], float) for k in ("u", "v", "w")]
    seats = {}
    for item in around or []:
        for face in item.get("againstFaces") or []:
            seats.setdefault(face, []).append(item.get("label") or item.get("id"))

    out = []
    for name, (axis, sign) in FACES.items():
        world = axis_vectors[axis] * sign
        up = float(world[2])
        out.append({
            "face": name,
            "isEnd": axis == 1,
            "atMetres": 0.0 if sign < 0 else round(size[axis], 3),
            "worldDirection": [round(float(v), 3) for v in world],
            "pointing": ("up" if up > 0.7 else "down" if up < -0.7 else "sideways"),
            "against": seats.get(name) or [],
        })
    return out


def at_faces(uvw, extents, share: float = 0.25) -> dict:
    """Which faces of the member a marked point sits on.

    A surveyor dropping a marker on a model clicks a surface they can see, and
    the surface they could see the damage on is the surface the damage is on.
    That makes the marker the one hard statement in the record about the section
    -- written descriptions give a distance back from an end and almost never
    which face -- but only if somebody reads it as a face rather than as three
    numbers. This does the reading.

    -> {"onFaces", "onSectionFaces", "atEnd", "says"}
    """
    faces = []
    for axis, name in enumerate("uvw"):
        span = float(extents[axis])
        near = share * span
        value = float(uvw[axis])
        if value <= near:
            faces.append("-" + name)
        elif value >= span - near:
            faces.append("+" + name)

    section = [f for f in faces if f[1] != "v"]
    end = next((f for f in faces if f[1] == "v"), None)
    if not section:
        says = ("the marker sits away from every face -- it says nothing about "
                "which side of the section the damage is on")
    elif len(section) == 1:
        says = "the marker sits on the %s face" % section[0]
    else:
        says = "the marker sits on the %s arris, between the %s faces" % (
            "".join(section), " and ".join(section))
    if end:
        says += ", at the %s end" % end
    return {"onFaces": faces, "onSectionFaces": section, "atEnd": end,
            "says": says}


def marks(conditions, frame: dict, world=None) -> list:
    """Each condition's marker, read into member coordinates and into faces.

    The marker is the one hard number in the record about *where*. Converting it
    is arithmetic, so it is done here rather than asked of a model.
    """
    from . import neighbours

    origin = np.asarray(frame["origin"], float)
    basis = neighbours.basis_of(frame)
    size = np.array([frame["width"], frame["length"], frame["height"]], float)
    matrix = np.eye(3) if world is None else np.asarray(world, float)

    out = []
    for condition in conditions or []:
        point = neighbours.point_of(condition.get("coordinates"))
        if point is None:
            continue
        at = (matrix @ point) - origin
        uvw = at @ basis
        out.append(dict(at_faces(uvw, size),
                        id=str(condition.get("id")),
                        label=str(condition.get("type") or condition.get("id")),
                        world=(matrix @ point).tolist(),
                        atMemberUVW=[round(float(c), 3) for c in uvw],
                        insideTheMember=bool(np.all(uvw >= -0.01)
                                             and np.all(uvw <= size + 0.01))))
    return out


def read_report(answer: dict, local, extents, threshold: float,
                pinned=None, sent=None) -> list:
    """What the model answered, and whether to believe it. -> report lines.

    Three questions, none of which the answer can be trusted to volunteer:
    did the photographs reach the reading, does each region bound the section it
    claims to, and does any region throw away a marker that named a face.
    """
    lines, regions = [], answer.get("regions") or []
    sent = {str(s) for s in (sent or [])}
    saw = [s for s in (answer.get("sawPhotographs") or []) if isinstance(s, dict)]
    named = {str(s.get("id")) for s in saw}

    if sent and not named:
        lines.append("WARNING: %d photograph(s) were sent and the model reports "
                     "seeing none -- this reading came from the written record "
                     "alone" % len(sent))
    for item in saw:
        lines.append("   saw %s: %s" % (str(item.get("id"))[:22],
                                        item.get("shows") or "(said nothing)"))
        # The real test: the damage can be described from `conditions` without
        # looking at anything; what is on the floor behind the timber cannot.
        lines.append("      not in the record: %s"
                     % (item.get("notInTheRecord") or
                        "NOTHING SAID -- this reading may be from the words alone"))
    if named - sent:
        lines.append("WARNING: the model named photograph(s) that were never sent: "
                     "%s -- do not trust this reading" % ", ".join(sorted(named - sent)))
    if named and sent - named:
        lines.append("%d photograph(s) sent but not reported: %s"
                     % (len(sent - named), ", ".join(sorted(sent - named))))

    if answer.get("readsAs"):
        lines.append(str(answer["readsAs"]))
    lines.append("%d region(s):" % len(regions))
    for item, region in zip(per_region(regions, local, extents, threshold), regions):
        lines.append("   %-14s %-3d cell(s), %d at or above %.2f, peak %.2f  %s"
                     % (item["id"][:14], item["cells"], item["atOrAbove"],
                        threshold, item["peak"], item["what"] or ""))
        # What the ranges actually COVER, not merely that they were given: a
        # uRange of the member's full width is not a bound, and reporting it as
        # one hides exactly the failure this line exists to show.
        u0, u1 = _span(region.get("uRange"), extents[0])
        w0, w1 = _span(region.get("wRange"), extents[2])
        share = ((u1 - u0) / extents[0]) * ((w1 - w0) / extents[2])
        claim = str(region.get("acrossTheSection") or "not stated").strip().lower()
        ramp = ("" if region.get("severityFar") is None else
                ", severity %.2f -> %.2f along it"
                % (float(region.get("severity", 1.0)), float(region["severityFar"])))
        lines.append("      along v %s%s; takes %.0f%% of the section (%s)"
                     % ("%.3f..%.3f" % tuple(region["vRange"])
                        if region.get("vRange") else "the whole member",
                        ramp, 100 * share, claim))
        across = share < 0.8
        if claim not in ("through", "not stated") and not across:
            lines.append("      WARNING: called '%s' but the ranges still take "
                         "%.0f%% of the section" % (claim, 100 * share))
        # A box cannot narrow -- its section ranges are the same at both ends --
        # so a long through-section region says the member is severed for that
        # whole length. Past a depth or so it never is; the loss draws in to a
        # face or an arris, and that wants a second region.
        depth = min(float(extents[0]), float(extents[2]))
        run = _span(region.get("vRange"), extents[1])
        if not across and (run[1] - run[0]) > 1.2 * depth:
            lines.append("      WARNING: takes the full section for %.0f mm, %.1f "
                         "section depths -- that says the member is severed all "
                         "that way. Split it: a short through core, then a face "
                         "or arris taper"
                         % (1000 * (run[1] - run[0]), (run[1] - run[0]) / depth))
        if not across:
            # Against the region's own citation when it made one, and against
            # every marker on the member when it did not -- a region that names
            # no condition is not thereby excused from the one measurement of
            # the section anybody made.
            cited = {str(c) for c in (region.get("fromConditions") or [])}
            for pin in pinned or []:
                if pin["onSectionFaces"] and (not cited or pin["id"] in cited):
                    lines.append("      WARNING: %s was marked on %s, but this "
                                 "region condemns the whole section%s"
                                 % (pin["id"], " and ".join(pin["onSectionFaces"]),
                                    "" if cited else " (the region cites no condition)"))
        if item["cells"] == 0:
            lines.append("      WARNING: this region lands on no cell -- check its "
                         "ranges are metres and on the right axis")
    for line in answer.get("openQuestions") or []:
        lines.append("open question: %s" % line)
    return lines


def summary(values: np.ndarray, threshold: float) -> str:
    values = np.asarray(values, float)
    if not len(values):
        return "no cells"
    return ("%d of %d cell(s) at or above %.2f; peak %.2f, mean %.3f"
            % (int((values >= float(threshold)).sum()), len(values),
               float(threshold), float(values.max()), float(values.mean())))
