"""Repair joinery core -- everything that does not need Rhino or an LLM.

A joint is planes and groups, nothing else:

    joint = {
        "id":     "SJ3-ish",
        "aspect": 3.0,                       # joint length in section depths
        "planes": [{"id": "P0", "normal": [x, y, z], "d": 0.31, "role": "lap cheek"}, ...],
        "groups": [["P0", "P1", "P2"], ["P3", "P4"]],   # removal = union of intersections
    }

Coordinates are the joint window, exactly as the corpus stores them:
`x, z` in -0.5..0.5 across the section, `y` in 0..aspect along the member,
`y = 0` at the end that stays. The window is anchored by `station`, which is
computed from the damage -- never searched.

Everything is measured on the damage cells. Nothing here refuses; it returns
numbers and lets the caller decide.
"""
from __future__ import annotations

import re

import numpy as np

from . import kernel, neighbours, scoring

DIRECTIONS = {
    "+u": (0, +1), "-u": (0, -1),
    "+v": (1, +1), "-v": (1, -1),
    "+w": (2, +1), "-w": (2, -1),
}


# --------------------------------------------------------------- the member


def infer_grid(local: np.ndarray) -> tuple:
    """(nu, nv, nw) read off the cell centres themselves.

    BEAM CELLS lays the cells out on a regular grid, so counting the distinct
    coordinates along each axis gives the subdivisions back exactly. Asking the
    user to retype them is three more wires and three more ways to be wrong.
    """
    counts = []
    for axis in range(3):
        values = np.sort(np.unique(np.round(local[:, axis], 9)))
        if len(values) < 2:
            counts.append(len(values) or 1)
            continue
        gaps = np.diff(values)
        eps = max(1e-9, float(np.median(gaps)) * 0.5)
        counts.append(1 + int((gaps > eps).sum()))
    return tuple(counts)


def member(frame: dict, centres, damage, grid=None, probe: int = 30000) -> dict:
    """Everything the rest of this module needs about the timber.

    `frame` is the member frame (origin at a corner, u/v/w unit axes, extents).
    `centres` are cell centroids in world coordinates, `damage` one value each.
    `grid` is (nu, nv, nw); leave it out and it is read from the centres.
    """
    points = np.asarray([[float(p[0]), float(p[1]), float(p[2])] for p in centres], float)
    values = np.asarray([float(v) for v in damage], float)
    if len(points) != len(values):
        raise ValueError("centres and damage must be the same length")
    local_points = scoring.to_local(points, frame)
    if grid is None:
        grid = infer_grid(local_points)
    nu, nv, nw = (int(n) for n in grid)
    if nu * nv * nw != len(points):
        found = infer_grid(local_points)
        if found[0] * found[1] * found[2] == len(points):
            nu, nv, nw = found
        else:
            raise ValueError(
                "grid %dx%dx%d = %d does not match %d cells, and the centres read "
                "as %dx%dx%d" % (nu, nv, nw, nu * nv * nw, len(points), *found)
            )
    # A dense sample of the same timber, for measuring volume rather than cells.
    # One cell of a 3x30x4 grid is 0.28% of the member, so a cell count moves in
    # steps far coarser than the geometry it is judging -- and when a cut face
    # lands parallel to the grid a whole row flips at once, which reads as a
    # spike that is not there. Cells stay exact for "which decayed cell
    # survives", because damage IS a cell property. Volume is for everything else.
    rng = np.random.default_rng(20260822)
    size = np.array([frame["width"], frame["length"], frame["height"]], float)
    basis = np.column_stack([np.asarray(frame[k], float) for k in ("u", "v", "w")])
    local_probe = rng.uniform(np.zeros(3), size, size=(int(probe), 3))
    index = np.minimum((local_probe / size * np.array([nu, nv, nw])).astype(int),
                       np.array([nu - 1, nv - 1, nw - 1]))
    flat = (index[:, 0] * nv + index[:, 1]) * nw + index[:, 2]

    # The eight corners of every cell. A cell is otherwise classified by one
    # point -- its centre -- so a cut face passing through 49% of it removes
    # none of it and one at 51% removes all of it, an error of half a cell on
    # every face. For sound timber that washes out on the dense probe. For
    # decay it does not: "rotLeft = 0" would mean every rotten CENTRE is inside
    # the removal, while up to half of each of those cells stays in the wall.
    # With the corners `measure` can ask the stronger question instead, and it
    # costs about 4% because they ride along in the same classifier call.
    # Clipped inward: a corner lying exactly on the beam face falls on the
    # boundary of the stock cutter, where the point-in-solid test is a coin toss.
    cell = size / np.array([nu, nv, nw], float)
    signs = np.array([[a, b, c] for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)],
                     float)
    local_corners = np.clip(local_points[:, None, :] + 0.5 * signs * cell,
                            1e-6, size - 1e-6)

    return {
        "frame": frame,
        "grid": (nu, nv, nw),
        "points": points,
        "local": local_points,                          # metres from the corner
        "damage": values,
        "section": min(float(frame["width"]), float(frame["height"])),
        "length": float(frame["length"]),
        "probe": local_probe @ basis.T + np.asarray(frame["origin"], float),
        "probeDamage": values[flat],                    # each probe point's cell
        "corners": local_corners @ basis.T + np.asarray(frame["origin"], float),
    }


def damaged(mem: dict, threshold: float) -> np.ndarray:
    return mem["damage"] >= float(threshold)


def clusters(mem: dict, threshold: float, gap: float = None) -> list:
    """Separate runs of decay along the member.

    One member is not one repair. A post rotten at the foot and holed by beetle
    a metre higher up has two damages, and answering both with a single joint
    stretches its window across everything in between -- which is how a repair
    ends up cutting the seats of two rails that had nothing wrong with them.

    A cluster is a run of stations along `v` carrying rot, separated from the
    next by more sound timber than `gap`. Default gap is one section depth: less
    than that and no joint could fit between them anyway, so they are one damage.

    Each cluster carries its own `reachesTheEnd`, which is what decides splice
    or patch -- asked of the cluster, not of the member, because the foot may
    reach an end while the pocket above it does not.
    """
    nu, nv, nw = mem["grid"]
    rot = damaged(mem, threshold).reshape(nu, nv, nw)
    hot = rot.any(axis=(0, 2))
    step = mem["length"] / float(nv)
    span = int(np.ceil(float(mem["section"] if gap is None else gap) / step))

    runs, start = [], None
    for index in range(nv):
        if hot[index] and start is None:
            start = index
        elif not hot[index] and start is not None:
            runs.append([start, index - 1])
            start = None
    if start is not None:
        runs.append([start, nv - 1])
    if not runs:
        return []

    merged = [runs[0]]
    for lo, hi in runs[1:]:
        if lo - merged[-1][1] - 1 <= span:
            merged[-1][1] = hi
        else:
            merged.append([lo, hi])

    out = []
    for lo, hi in merged:
        block = rot[:, lo:hi + 1, :]
        columns = [[int(a), int(b)] for a, b in zip(*np.where(block.any(axis=1)))]
        out.append({
            "vRange": [round(lo * step, 4), round((hi + 1) * step, 4)],
            "stations": [int(lo), int(hi)],
            "cells": int(block.sum()),
            "columns": columns,
            "reachesTheEnd": bool(lo == 0 or hi == nv - 1),
            "atEnd": -1 if lo == 0 else (+1 if hi == nv - 1 else None),
        })
    return out


def anchor(mem: dict, threshold: float, aspect: float, margin: float = 0.2,
           straddle: bool = False, kind: str = "splice", within=None):
    """Where the joint sits and which end it replaces -- from the damage alone.

    Returns (station, side, note). `side` is -1 when the decay reaches the low
    end of the member axis and +1 when it reaches the high end.

    `within` is a (lo, hi) span in metres, from `clusters`. Give it and only the
    decay inside that span is considered -- which is what makes a repair own one
    damage rather than all of them. Without it the anchor answers to every
    rotten cell in the member at once, and on a member with two separated
    damages that is a station between them, in sound timber, serving neither.

    Two placements, and the difference decides whether the joint's shape can do
    anything at all. Clear of the decay, no face of the joint ever meets rot, so
    every catalogue joint costs the same and the design is decoration. Straddling
    it, the decay front runs through the middle of the window, every face has
    something to cut around, and a joint that follows the front reaches much
    further in than one that fights it. `place()` slides from either start, but
    the front can only be *described* to a designer from the straddling one.
    """
    rot = damaged(mem, threshold)
    if within is not None:
        axis = mem["local"][:, 1]
        rot = rot & (axis >= float(within[0]) - 1e-9) & (axis <= float(within[1]) + 1e-9)
    if not rot.any():
        raise ValueError("no cell reaches the threshold")
    v = mem["local"][rot, 1]
    lo, hi = float(v.min()), float(v.max())
    length, section = mem["length"], mem["section"]
    half = 0.5 * float(aspect) * section + float(margin) * section
    side = -1 if lo <= (length - hi) else +1
    edge = hi if side < 0 else (length - lo)

    if str(kind).lower() == "patch":
        # A patch is let INTO the member. It is bounded on every side by its own
        # faces, so there is no end to clear and no end to replace -- it sits on
        # the decay, and the sweep moves it either way from there.
        station = 0.5 * (lo + hi)
        return station, side, ("decay sits %.3f..%.3f m with sound timber beyond "
                               "it; patch centred on it at %.3f m"
                               % (lo, hi, station))
    if straddle:
        station = (edge if side < 0 else length - edge)
        note = ("decay reaches the %s end; window straddling the front at %.3f m, "
                "so the joint's faces have the decay to work around"
                % ("low" if side < 0 else "high", station))
    else:
        station = (edge + half) if side < 0 else (length - edge - half)
        note = ("decay reaches the %s end; anchored clear of the deepest rot "
                "at %.3f m" % ("low" if side < 0 else "high", edge))
    return station, side, note


def extents(mem: dict) -> dict:
    """How far the window reaches across the section, in the model's own units.

    Everything is scaled by the SMALLER section dimension, so on a rectangular
    member `x, z in -0.5..0.5` is true of the narrow direction only. On an
    80 x 100 rail, z = 0.5 lands 40 mm off centre in a beam whose face is 50 mm
    off centre -- so a joint drawn as reaching the face falls 10 mm short of it,
    every time, and a joint drawn symmetric is not. Telling the designer the
    real half-extents costs nothing and fixes it without touching the kernel.
    """
    frame = mem["frame"]
    unit = mem["section"]
    nu, nv, nw = mem["grid"]
    # Locks are measured on the cell grid, so a feature smaller than a cell --
    # a dovetail flare, a shallow step -- is drawn, paid for in oak, and never
    # seen. The designer has to know how fine the measurement actually is.
    across = max(float(frame["width"]) / nu, float(frame["height"]) / nw)
    return {"xHalf": round(0.5 * float(frame["width"]) / unit, 4),
            "zHalf": round(0.5 * float(frame["height"]) / unit, 4),
            "unitMm": round(1000.0 * unit, 1),
            "cell": round(across / unit, 4),
            "cellMm": round(1000.0 * across, 1)}


def decay(mem: dict, threshold: float, station: float, side: int,
          aspect: float, within=None) -> dict:
    """Where the decay is, in the coordinates the designer draws in.

    For each column of the section, the SPANS of `y` that are rotten. Spans, not
    a single depth: decay reaching in from the end gives one span running to the
    far edge of the window, but a pocket mid-span gives a bounded one with sound
    timber on both sides, and a beam can have several. Reporting only where rot
    begins says "everything past here is gone", which on a pocket is a lie about
    a great deal of perfectly good oak.

    `reachesTheEnd` is what tells the two apart, and it is the difference
    between a splice and a patch.
    """
    window = to_window(mem, station, side, aspect)
    rot = damaged(mem, threshold)
    if within is not None:
        # one damage's worth. Describing every rotten cell in the member to a
        # designer working on one of two separated damages tells it to answer
        # decay it is not responsible for, and `reachesTheEnd` -- the whole
        # splice-or-patch question -- comes back as the member's answer rather
        # than this damage's.
        axis = mem["local"][:, 1]
        rot = rot & (axis >= float(within[0]) - 1e-9) & (axis <= float(within[1]) + 1e-9)
    step = float(mem["length"]) / mem["grid"][1] / mem["section"]
    columns, ends = [], []
    if rot.any():
        keys = np.round(window[:, [0, 2]], 3)
        for key in np.unique(keys[rot], axis=0):
            pick = rot & np.all(np.isclose(keys, key, atol=1e-6), axis=1)
            ys = np.sort(window[pick, 1])
            spans, start, last = [], float(ys[0]), float(ys[0])
            for y in ys[1:]:
                if float(y) - last > 1.5 * step:        # a gap of sound timber
                    spans.append([round(start, 2), round(last, 2)])
                    start = float(y)
                last = float(y)
            spans.append([round(start, 2), round(last, 2)])
            columns.append({"x": float(key[0]), "z": float(key[1]), "rot": spans})
            ends.append(spans[0][0])
    columns.sort(key=lambda c: (c["z"], c["x"]))
    # asked of the MEMBER, not the window: does the decay actually run out to an
    # end of the beam? That is the splice-or-patch question, and answering it
    # against the window edge instead gets it wrong on any pocket that happens
    # to fill the window.
    edge = float(mem["length"]) / mem["grid"][1]
    along = mem["local"][rot, 1] if rot.any() else np.array([])
    reaches = bool(len(along)) and (float(along.min()) <= edge
                                    or float(along.max()) >= mem["length"] - edge)
    return {
        "columns": columns,
        "reachesTheEnd": reaches,
        "nearestRotY": round(min(ends), 2) if ends else None,
        "rakeAcrossSection": round(max(ends) - min(ends), 2) if ends else 0.0,
        "meaning": ("each column lists the y spans that are decayed and must end "
                    "up in the replacement piece. Timber outside those spans is "
                    "sound. Columns not listed are sound right through. When "
                    "reachesTheEnd is false the decay is a pocket with sound "
                    "timber beyond it, and cutting past it wastes that timber."),
    }


# ---------------------------------------------------------------- the joint


def to_template(joint: dict):
    """joint dict -> the kernel's cut list, in window coordinates."""
    ids = [str(p["id"]) for p in joint["planes"]]
    index = {name: i for i, name in enumerate(ids)}
    aspect = float(joint.get("aspect", 3.0))
    cuts = []
    for plane in joint["planes"]:
        normal = np.asarray(plane["normal"], float)
        length = float(np.linalg.norm(normal))
        if not np.isfinite(normal).all() or length < 1e-9:
            raise ValueError("plane %s has a degenerate normal" % plane.get("id"))
        normal = normal / length
        offset = float(plane["d"]) / length
        cuts.append(kernel.half_space_cut(
            "lhf_%d" % (len(cuts) + 1), normal, offset * normal, aspect
        ))
    groups = [[index[str(name)] for name in group] for group in joint["groups"]]
    return {
        "schema": "repair-joint@1",
        "key": str(joint.get("id", "joint")),
        "kind": str(joint.get("kind", "splice")),
        "aspect": aspect,
        "section": 1.0,
        "cuts": [cut.to_json() for cut in cuts],
        "removal_groups": groups,
    }


def open_at_kept_side(joint: dict, margin: float = 0.5) -> list:
    """Groups that run off the end of the joint instead of stopping at it.

    Planes are placed as oversized prisms, so a group with nothing bounding it
    does not stop -- it sweeps the whole member. For a splice that only matters
    below y = 0, because the axial trim closes the far end. A PATCH has no trim:
    it is let into the middle of the timber and every group must be bounded at
    BOTH ends, or the "patch" quietly amputates the beam.
    """
    aspect = float(joint.get("aspect", 3.0))
    patch = str(joint.get("kind", "splice")).lower() == "patch"
    ys = np.linspace(-3.0 * aspect, (4.0 if patch else 1.0) * aspect, 220)
    xs = zs = np.linspace(-0.48, 0.48, 5)
    probe = np.array([[x, y, z] for y in ys for x in xs for z in zs])
    planes = {str(p["id"]): p for p in joint["planes"]}
    bad = []
    for position, group in enumerate(joint["groups"]):
        inside = np.ones(len(probe), bool)
        for name in group:
            plane = planes[str(name)]
            normal = np.asarray(plane["normal"], float)
            normal = normal / np.linalg.norm(normal)
            inside &= (probe @ normal) >= float(plane["d"]) / np.linalg.norm(
                np.asarray(plane["normal"], float)
            )
        if inside.any():
            reach = -float(probe[inside][:, 1].min())
            if reach > margin:
                bad.append({"group": position, "planes": list(group),
                            "side": "kept", "reaches": round(reach, 2)})
            if patch:
                over = float(probe[inside][:, 1].max()) - aspect
                if over > margin:
                    bad.append({"group": position, "planes": list(group),
                                "side": "far", "reaches": round(over, 2)})
    return bad


def fit_station(joint: dict, mem: dict, threshold: float, station: float,
                side: int, around=None, steps: int = 24, within=None):
    """Push the joint toward the end it replaces until decay would survive.

    `anchor` returns a rule: the deepest rot plus the whole joint window. But the
    window was never required to clear the rot -- it only has to hold the joint's
    shape, and the joint's own faces already reach through part of it. How much
    depends on the shape, so it cannot be known before the shape exists, and the
    rule has to assume none of it. That assumption is worth tens of millimetres
    of sound oak on every repair.

    So stop assuming. Step the joint one cell at a time toward the end, measure,
    and keep the furthest position that still takes every rotten cell and does
    not start cutting a connection that was clear before. Returns (station, note).
    """
    aspect = float(joint.get("aspect", 3.0))
    half = 0.5 * aspect * mem["section"]
    low, high = half, mem["length"] - half
    base = measure(joint, mem, station, side, threshold, around=around,
                   within=within)
    floor = int(base["rotLeft"])
    cutting = set(base.get("cutsConnections") or [])
    span = mem["length"] / mem["grid"][1]
    patch = str(joint.get("kind", "splice")).lower() == "patch"
    ways = (+1.0, -1.0) if patch else ((1.0 if int(side) > 0 else -1.0),)

    best, chosen = float(station), base
    for way in ways:
        for index in range(1, int(steps) + 1):
            trial = float(station) + span * way * index
            if not (low - 1e-9 <= trial <= high + 1e-9):
                break
            result = measure(joint, mem, trial, side, threshold, around=around,
                             within=within)
            if int(result["rotLeft"]) > floor:
                break
            if set(result.get("cutsConnections") or []) - cutting:
                break
            if result["soundTaken"] < chosen["soundTaken"]:
                best, chosen = trial, result

    moved = abs(best - float(station))
    if moved < 1e-6:
        return best, ("the rule's station is already as far toward the end as it "
                      "can go without leaving decay behind")
    return best, ("the rule put the joint at %.3f m; measured, it sits %.0f mm "
                  "closer to the end at %.3f m and still takes every rotten cell "
                  "-- %d fewer cells of sound timber"
                  % (station, 1000 * moved, best,
                     base["soundTaken"] - chosen["soundTaken"]))


def _window(points: np.ndarray, mem: dict, station: float, side: int,
            aspect: float) -> np.ndarray:
    """World points -> joint window coordinates, exactly as the kernel places it."""
    frame = mem["frame"]
    axes = [np.asarray(frame[k], float) for k in ("u", "v", "w")]
    basis = np.column_stack(axes)
    scale = min(float(frame["width"]), float(frame["height"]))
    reach = float(aspect) * scale
    half = reach / 2.0
    position = max(half, min(float(frame["length"]) - half, float(station)))

    matrix = basis * scale
    centre = (np.asarray(frame["origin"], float)
              + 0.5 * float(frame["width"]) * axes[0]
              + 0.5 * float(frame["height"]) * axes[2])
    shift = centre + (position - half) * axes[1]
    if int(side) < 0:
        shift = shift + matrix @ np.array([0.0, float(aspect), 0.0])
        matrix = matrix @ np.diag([-1.0, -1.0, 1.0])
    return (np.asarray(points, float) - shift) @ np.linalg.inv(matrix).T


def to_window(mem: dict, station: float, side: int, aspect: float) -> np.ndarray:
    """Cell centres in the joint's own coordinates.

    x, z across the section in -0.5..0.5; y along the member from 0 at the end
    that stays. The same numbers the designer draws in, which is the point: told
    "the rot reaches y = 0.8 on this column", a model can act on it.
    """
    return _window(mem["points"], mem, station, side, aspect)


def nearby(mem: dict, around, station: float, side: int, aspect: float) -> dict:
    """The parts bearing on this member, in the coordinates the designer draws in.

    A neighbour's contact is a stretch of the member's length, and until now that
    stretch was known only in metres from the beam's corner while the designer
    worked in the window. So a joint could put a shoulder straight through a
    rail's seat and nothing said a word until `cutsConnections` reported it
    afterwards -- by which time the geometry was already drawn.

    Reported as `y` spans, the same coordinate the decay uses. A span overlapping
    0..aspect is inside the joint; anything outside it the joint never reaches.
    """
    frame = mem["frame"]
    axes = [np.asarray(frame[k], float) for k in ("u", "v", "w")]
    centre = (np.asarray(frame["origin"], float)
              + 0.5 * float(frame["width"]) * axes[0]
              + 0.5 * float(frame["height"]) * axes[2])
    out = []
    for item in around or []:
        lo, hi = (float(v) for v in item.get("vRange") or (0.0, 0.0))
        pair = np.array([centre + lo * axes[1], centre + hi * axes[1]])
        ys = sorted(float(v) for v in _window(pair, mem, station, side, aspect)[:, 1])
        out.append({
            "id": item.get("id"),
            "label": item.get("label"),
            "bearsOn": item.get("againstFaces") or [],
            "y": [round(ys[0], 2), round(ys[1], 2)],
            "insideTheJoint": bool(ys[1] > 0.0 and ys[0] < float(aspect)),
            "declared": bool(item.get("declared")),
        })
    out.sort(key=lambda n: n["y"][0])
    return {
        "parts": out,
        "meaning": ("each part bears on this member over the y span given. A span "
                    "with insideTheJoint true sits within the joint window, so a "
                    "face placed there cuts through that part's seat and turns "
                    "one repair into several."),
    }


def cut(joint: dict, mem: dict, station: float, side: int) -> dict:
    """Place the joint on the member. No search: station and side are given."""
    return kernel.build_repair(
        to_template(joint), mem["frame"],
        position=float(station), rotate_deg=0.0,
        side=int(side), interface_scale=1.0,
    )


# ------------------------------------------------------------- measurement


def _locks(mem: dict, removed: np.ndarray) -> list:
    """Directions the new piece cannot be slid out along, at cell resolution."""
    nu, nv, nw = mem["grid"]
    block = removed.reshape(nu, nv, nw)
    kept = ~block
    out = []
    for name, (axis, sign) in DIRECTIONS.items():
        free = True
        for step in range(1, block.shape[axis]):
            moved = np.roll(block, sign * step, axis=axis)
            # cells that rolled around the far edge have left the member
            cut_slice = [slice(None)] * 3
            cut_slice[axis] = slice(0, step) if sign > 0 else slice(-step, None)
            moved[tuple(cut_slice)] = False
            if (moved & kept).any():
                free = False
                break
        if not free:
            out.append(name)
    return out


def measure(joint: dict, mem: dict, station: float, side: int, threshold: float,
            boxes=None, around=None, within=None) -> dict:
    """Everything we know about one placed joint, counted on the cells.

    `boxes` and `around` are the surrounding parts, from `neighbours`. Give them
    and the joint is also judged on whether the new piece can be got into place
    and whether it cuts through a working connection -- the two things that make
    a joint wrong for reasons the damage field cannot see.

    Sound timber is counted on cell centres, which is what `soundTaken` has
    always been and why `spent()` exists to say the same thing by volume. Decay
    is counted on whole cells: a rotten cell is taken only when all eight of its
    corners are inside the removal. Half a cell of rot left standing is rot left
    standing, and asking the centre alone would not see it.
    """
    repair = cut(joint, mem, station, side)
    rot = damaged(mem, threshold)
    if within is not None:
        # Which decay is THIS repair answerable for. Without it a patch drawn
        # for a pocket at mid-height is free to slide down onto the foot rot,
        # because sliding there lowers `soundTaken` and never raises `rotLeft`
        # -- the search is measuring the member's whole damage against one
        # repair that was only ever meant to take part of it.
        axis = mem["local"][:, 1]
        rot = rot & (axis >= float(within[0]) - 1e-9) & (axis <= float(within[1]) + 1e-9)
    count = len(mem["points"])
    corners = mem["corners"][rot].reshape(-1, 3)
    flags = np.asarray(scoring.removed_mask(
        np.vstack([mem["points"], corners]) if len(corners) else mem["points"],
        repair), bool)
    removed = flags[:count]
    taken = flags[count:].reshape(-1, 8)
    rot_left = int((~taken.all(axis=1)).sum())
    rot_wholly = int((~taken.any(axis=1)).sum())
    v = mem["local"][:, 1]
    inside = v[removed] if removed.any() else np.array([0.0])
    extra = {}
    if boxes:
        extra["insertionBlocked"] = neighbours.blocked(
            mem["points"][removed], boxes, mem["frame"])
    if around and removed.any():
        lo, hi = float(inside.min()), float(inside.max())
        seats = [item for item in around
                 if item.get("againstFaces")
                 and not set(item["againstFaces"]) & {"-v", "+v"}]
        extra["cutsConnections"] = [
            item["id"] for item in seats
            if item["vRange"][0] <= hi and item["vRange"][1] >= lo
        ]
        # How near the cut comes to a seat it does NOT cut. Running through one
        # is caught above; stopping two millimetres short of one is not, and it
        # is barely a better repair -- the timber under the seat is left as a
        # wafer. Reported so it can be seen and ranked on.
        clear = [max(item["vRange"][0] - hi, lo - item["vRange"][1])
                 for item in seats if item["id"] not in extra["cutsConnections"]]
        extra["nearestSeatMm"] = (round(1000.0 * min(clear), 1) if clear else None)
    return dict(extra, **{
        "repair": repair,
        "removed": removed,
        "rotLeft": rot_left,                    # not taken WHOLE -- ranks
        "rotWhollyLeft": rot_wholly,            # not touched at all
        "rotPartly": rot_left - rot_wholly,     # clipped by a face, some rot stays
        "rotTaken": taken,                      # (rotten cells, 8) corner flags
        "rotTotal": int(rot.sum()),
        "soundTaken": int((~rot & removed).sum()),
        "soundTotal": int((~rot).sum()),
        "extent": [round(float(inside.min()), 3), round(float(inside.max()), 3)],
        "fractionOfMember": round(float(removed.mean()), 3),
        "locks": _locks(mem, removed),
        "station": round(float(station), 3),
        "side": int(side),
    })


def spent(joint: dict, mem: dict, station: float, side: int,
          threshold: float) -> float:
    """Sound timber this placement destroys, as a fraction of the member.

    Measured on the dense probe, not on the cells. Two placements that differ
    by a millimetre differ here by a millimetre's worth; on the cells they
    differ by nothing at all, or by a whole row.
    """
    removed = np.asarray(
        scoring.removed_mask(mem["probe"], cut(joint, mem, station, side)), bool)
    return float((removed & (mem["probeDamage"] < float(threshold))).mean())


def merge_kept(repairs) -> dict:
    """One `kept` part with every repair's removal taken out of it. -> part

    Each repair carries its own copy of the beam and names its cutters `lhf_0`,
    `lhf_1`, ... so two of them cannot simply be put in one list: the names
    collide and the second repair's cutters silently replace the first's.
    Renaming them apart and unioning the removals keeps the whole thing one CSG
    expression, which is what `evaluator` already knows how to build.
    """
    head = "Difference(lhf_0, "
    cuts, removals = [], []
    for index, repair in enumerate(repairs):
        part = repair["repair"]["parts"][0]              # the "kept" part
        text = str(part["expression"])
        if not text.startswith(head) or not text.endswith(")"):
            raise ValueError("unexpected kept expression: %s" % text[:60])
        removal = text[len(head):-1]
        # every cut, not just the numbered ones: a splice also carries an axial
        # trim of its own. Longest name first so `lhf_1` cannot eat `lhf_10`.
        for name in sorted({str(c["name"]) for c in part["cuts"]},
                           key=len, reverse=True):
            removal = re.sub(r"\b%s\b" % re.escape(name),
                             "r%d_%s" % (index, name), removal)
        removals.append(removal)
        cuts.extend(dict(c, name="r%d_%s" % (index, c["name"]))
                    for c in part["cuts"])
    whole = removals[0] if len(removals) == 1 else "Union(%s)" % ", ".join(removals)
    return {"name": "kept", "expression": "Difference(r0_lhf_0, %s)" % whole,
            "cuts": cuts}


def scheme(repairs, mem: dict, threshold: float, boxes=None, around=None) -> dict:
    """Several placed repairs on one member, measured as ONE answer.

    `repairs` is a list of {"joint", "station", "side"} -- whatever `place`
    returned for each cluster. Every number here is asked of the scheme, not of
    the repairs separately, because that is the only level at which the
    questions mean anything: two repairs that each take all of "their" decay can
    still leave rot between them, and two that each keep clear of the rails can
    still both cut the same one.

    `betweenMm` is the new measure. Two repairs in one member interact -- a
    patch let in two hundred millimetres above a splice shoulder leaves a short
    band of original timber carrying both, and neither repair can see that from
    inside itself. Negative means they overlap and it is one repair, badly drawn.
    """
    rot = damaged(mem, threshold)
    parts = []
    union = np.zeros(len(mem["points"]), bool)
    union_probe = np.zeros(len(mem["probe"]), bool)
    taken = np.zeros((int(rot.sum()), 8), bool)
    cuts, spans = [], []

    for item in repairs:
        one = measure(item["joint"], mem, item["station"], item["side"],
                      threshold, boxes=boxes, around=around)
        union |= one["removed"]
        taken |= one["rotTaken"]
        union_probe |= np.asarray(
            scoring.removed_mask(mem["probe"], one["repair"]), bool)
        cuts.extend(one.get("cutsConnections") or [])
        spans.append(one["extent"])
        parts.append({k: one[k] for k in
                      ("rotLeft", "soundTaken", "locks", "extent", "station",
                       "side")})
        parts[-1]["cutsConnections"] = one.get("cutsConnections") or []
        parts[-1]["kind"] = str(item["joint"].get("kind", "splice"))

    # closest approach between any two repairs, along the member
    between = None
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            clear = max(spans[j][0] - spans[i][1], spans[i][0] - spans[j][1])
            between = clear if between is None else min(between, clear)

    left = int((~taken.all(axis=1)).sum())
    return {
        "repairs": parts,
        "removed": union,
        "rotLeft": left,
        "rotWhollyLeft": int((~taken.any(axis=1)).sum()),
        "rotPartly": left - int((~taken.any(axis=1)).sum()),
        "rotTotal": int(rot.sum()),
        "soundTaken": int((~rot & union).sum()),
        "soundTotal": int((~rot).sum()),
        "fractionOfMember": round(float(union.mean()), 3),
        "spent": float((union_probe
                        & (mem["probeDamage"] < float(threshold))).mean()),
        "cutsConnections": sorted(set(cuts)),
        "betweenMm": None if between is None else round(1000.0 * between, 1),
        "overlap": bool(between is not None and between < 0),
        "locks": _locks(mem, union),
    }


def place(joint: dict, mem: dict, threshold: float, station: float, side: int,
          around=None, degrees: int = 15, shortlist: int = 6,
          within=None) -> dict:
    """The best way to sit this joint on this beam: turn it, then slide it.

    Two nested sweeps. The joint is turned right around the member -- the whole
    circle, not a few degrees either side -- and at every angle it is slid
    toward the end until a decayed cell would survive. The angle decides which
    way the joint's rake points; the slide decides how far in it reaches. A
    joint whose rake follows the decay front reaches much further than its own
    mirror, and no rule can know which way the front runs. Only the sweep can.

    The sweep runs on the cells because they are cheap, and the handful of best
    placements are then re-measured on the dense probe, because a cell count is
    too coarse to rank them: it moves in steps of a quarter of a percent of the
    member, and spikes when a cut face lands parallel to the grid.
    """
    import copy

    axis = np.array([0.0, 1.0, 0.0])
    tried, best_rot = [], None
    for turn in range(0, 360, max(1, int(degrees))):
        candidate = copy.deepcopy(joint)
        candidate["twist"] = float(turn)
        if turn:
            for plane in candidate["planes"]:
                plane["normal"] = list(_rotate(plane["normal"], axis, turn))
        if open_at_kept_side(candidate):
            continue
        try:
            where, _ = fit_station(candidate, mem, threshold, station, side,
                                   around=around, steps=40, within=within)
            result = measure(candidate, mem, where, side, threshold,
                             around=around, within=within)
        except Exception:
            continue
        tried.append({"twist": float(turn), "station": where, "joint": candidate,
                      "rotLeft": result["rotLeft"], "cells": result["soundTaken"],
                      "cuts": len(result.get("cutsConnections") or []),
                      "clear": result.get("nearestSeatMm"),
                      "locks": result["locks"]})

    # Order matters and it is not negotiable: decay left standing first, then a
    # working bearing cut through, and only then oak. A joint that runs through
    # a seat has turned one repair into several -- it is not a cheaper joint.
    clean = [t for t in tried if t["rotLeft"] == 0] or tried
    clean.sort(key=lambda t: (t["rotLeft"], t["cuts"], t["cells"]))
    for item in clean[:max(1, int(shortlist))]:
        item["spent"] = spent(item["joint"], mem, item["station"], side, threshold)
    ranked = sorted(clean[:max(1, int(shortlist))],
                    key=lambda t: (t["rotLeft"], t["cuts"], t["spent"]))
    if not ranked:
        raise ValueError("no placement of this joint takes the decay")

    best = ranked[0]
    best["tried"] = len(tried)
    best["note"] = ("turned %+.0f deg and slid to %.3f m -- reaches %.0f mm "
                    "further than the rule and spends %.1f%% of the member in "
                    "sound timber; the worst angle spends %.1f%%"
                    % (best["twist"], best["station"],
                       1000 * abs(best["station"] - station),
                       100 * best["spent"], 100 * max(t["spent"] for t in ranked)))
    return best


# ------------------------------------------------------------ rigid moves


def differences(base: dict, variation: dict, tilt: float = 1.0,
                shift: float = 1e-3) -> dict:
    """What actually changed between a joint and a variation of it.

    The model says what it altered; this says what it altered. A variation
    claiming "the stops deepened" that quietly introduced two new directions is
    not a variation, it is another joint -- and the comparison that follows,
    base against variation, then measures nothing in particular.

    Faces are matched by direction, not by id: the closest base normal to each
    variation normal, counting a normal and its negation as the same face
    inverted. Anything with no close match is a new direction.
    """
    def unit(plane):
        vector = np.asarray(plane["normal"], float)
        length = max(float(np.linalg.norm(vector)), 1e-12)
        return vector / length, float(plane.get("d", 0.0)) / length

    was = {str(p["id"]): unit(p) for p in base.get("planes") or []}
    now = {str(p["id"]): unit(p) for p in variation.get("planes") or []}
    near = np.cos(np.radians(35.0))

    tilted, inverted, moved = [], 0, []
    for name in set(was) & set(now):
        (before, at), (after, to) = was[name], now[name]
        dot = float(before @ after)
        if dot < 0:
            inverted += 1
        else:
            angle = float(np.degrees(np.arccos(min(1.0, abs(dot)))))
            if angle > float(tilt):
                tilted.append(round(angle, 1))
        if abs(to - at) > float(shift):
            moved.append(round(to - at, 3))

    # a plane added on a direction the base already uses is a step; one on a
    # direction it does not is a different joint wearing the same name
    fresh = sum(1 for name in set(now) - set(was)
                if max([abs(float(now[name][0] @ other)) for other, _ in was.values()]
                       or [0.0]) < near)
    added, gone = len(set(now) - set(was)), len(set(was) - set(now))

    said = []
    if fresh:
        said.append("%d new direction(s)" % fresh)
    if inverted:
        said.append("%d plane(s) inverted" % inverted)
    if tilted:
        said.append("%d tilted (up to %.0f deg)" % (len(tilted), max(tilted)))
    if moved:
        said.append("%d offset(s) moved (up to %.3f)" % (len(moved), max(map(abs, moved))))
    if added:
        said.append("%d plane(s) added" % added)
    if gone:
        said.append("%d removed" % gone)
    if [sorted(g) for g in base.get("groups") or []] != \
            [sorted(g) for g in variation.get("groups") or []]:
        said.append("regrouped")
    if abs(float(variation.get("aspect", 0)) - float(base.get("aspect", 0))) > 1e-6:
        said.append("aspect %.1f -> %.1f" % (float(base.get("aspect", 0)),
                                             float(variation.get("aspect", 0))))
    return {"newDirections": fresh, "inverted": inverted, "tilted": tilted,
            "moved": moved, "added": added, "removed": gone,
            "did": ", ".join(said) or "nothing measurable"}


_AXIS = {"x": 0, "y": 1, "z": 2}


def claimed_locks(tokens, twist: float, side: int) -> list:
    """Direction claims written in joint coordinates, read in member axes.

    The designer writes "+y" meaning along the member away from the end that
    stays. By the time the joint is measured it has been turned about the member
    axis and, when the low end is being replaced, mirrored -- the same two moves
    the plane normals go through in `place` and in `build_repair`. Put the claim
    through them too, or it is being compared against a differently oriented
    beam and the comparison is noise.

    Order matters and follows `build_repair`: M = B . (s Rq) . Rf, so a joint
    vector is flipped and then rotated. `place` has already baked the rotation
    into the normals, so the flip is applied after it here for the same reason.
    """
    out = []
    for token in tokens or []:
        text = str(token).strip().lower().replace(" ", "")
        if len(text) != 2 or text[0] not in "+-" or text[1] not in _AXIS:
            continue
        vector = np.zeros(3)
        vector[_AXIS[text[1]]] = 1.0 if text[0] == "+" else -1.0
        vector = _rotate(vector, np.array([0.0, 1.0, 0.0]), float(twist))
        if int(side) < 0:
            vector = vector * np.array([-1.0, -1.0, 1.0])
        axis = int(np.argmax(np.abs(vector)))
        out.append("%s%s" % ("+" if vector[axis] >= 0 else "-", "uvw"[axis]))
    return sorted(set(out))


def _rotate(normal, axis, degrees):
    normal = np.asarray(normal, float)
    axis = np.asarray(axis, float)
    axis = axis / np.linalg.norm(axis)
    angle = np.radians(float(degrees))
    return (normal * np.cos(angle)
            + np.cross(axis, normal) * np.sin(angle)
            + axis * float(axis @ normal) * (1.0 - np.cos(angle)))


# How far the joint may be turned about the member axis. This is the only
# variation there is, and it is deliberate rather than sampled. Random tilts of
# single faces and random slides of offsets were tried and thrown out: they
# produce planes that are slightly wrong rather than joints that are usefully
# different, and there is nothing to learn from a shoulder that has moved four
# millimetres for no reason.
