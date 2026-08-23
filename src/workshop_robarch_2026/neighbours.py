"""The parts around the one being repaired, from the Workspace alone.

Every part in the exported Workspace carries a centre, local extents and a
rotation, so the whole frame can be rebuilt without touching Rhino. Verified
against the corner post: the eight parts that geometrically touch it are exactly
the eight listed in its `connections`.

This is the first piece of context that is not the damage. It changes geometry
because the measures below consume it: a joint cannot be slid in through a sill,
and it should not run through a working connection.
"""
from __future__ import annotations

import numpy as np

FACES = {
    "-u": (0, -1), "+u": (0, +1),
    "-v": (1, -1), "+v": (1, +1),
    "-w": (2, -1), "+w": (2, +1),
}

# The Workspace exports with Y as up -- in the corner-frame example the sills
# sit at y = 0.05, the top plates at y = 1.99, and every post's axis runs along
# Y. Rhino is Z-up. Mixing the two puts the post on its side and, worse, puts
# every neighbour a metre from where the member thinks it is, so nothing is
# found to be touching and the joint is designed as though the member stood
# alone. A workspace may override this by carrying "upAxis".
WORLD_UP = "y"

_UP_TO_Z = {
    "z": np.eye(3),
    "y": np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]),
    "x": np.array([[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]]),
}


def world_matrix(workspace=None) -> np.ndarray:
    """Workspace coordinates -> Rhino coordinates. A rotation, nothing else."""
    up = str((workspace or {}).get("upAxis") or WORLD_UP).strip().lower()
    return _UP_TO_Z.get(up, np.eye(3))


# The order the Workspace's Euler angles compose in. Y-up scenes are written
# YXZ -- yaw about the up axis first -- and on the corner frame that is not a
# preference but a measurement: of the 88 declared connections, YXZ confirms 88
# geometrically and XYZ confirms 80. The eight it misses all belong to one part,
# `diagonal_brace_right`, because it is the only member whose rotation is about
# more than the two axes every other part shares -- and where only one angle
# turns, every order gives the same matrix and the mistake is invisible.
EULER_ORDER = "YXZ"


def _rotation(rot: dict, order=None) -> np.ndarray:
    """local -> world, from Euler angles in x, y, z."""
    angles = {k: float(rot.get(k, 0.0)) for k in ("x", "y", "z")}
    turn = {
        "x": lambda a: np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)],
                                 [0, np.sin(a), np.cos(a)]], float),
        "y": lambda a: np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0],
                                 [-np.sin(a), 0, np.cos(a)]], float),
        "z": lambda a: np.array([[np.cos(a), -np.sin(a), 0],
                                 [np.sin(a), np.cos(a), 0], [0, 0, 1]], float),
    }
    out = np.eye(3)
    for axis in str(order or EULER_ORDER).strip().lower():
        if axis in turn:
            out = out @ turn[axis](angles[axis])
    return out


def confirm_connections(workspace) -> tuple:
    """(confirmed, missed) over every declared connection in the Workspace.

    The whole coordinate convention -- up axis, Euler order, which dimension is
    which -- is checkable against one fact the Workspace already states: parts
    that say they are connected should be touching. When they are not, something
    upstream of every measurement is wrong, and it is worth finding out in one
    line rather than from a render that looks nearly right.
    """
    parts = list((workspace.get("instance") or {}).get("parts") or [])
    world = world_matrix(workspace)
    boxes = {}
    for part in parts:
        box = part_box(part, world)
        if box is not None:
            boxes[str(part.get("id"))] = box

    confirmed, missed = 0, []
    for part in parts:
        here = boxes.get(str(part.get("id")))
        if here is None:
            continue
        for other_id in (part.get("connections") or []):
            there = boxes.get(str(other_id))
            if there is None:
                continue
            worst = -9.0
            for (centre, axes, half), other in ((here, there), (there, here)):
                local = (corners(other) - centre) @ axes
                worst = max(worst, float(np.maximum(local.min(axis=0) - half,
                                                    -half - local.max(axis=0)).max()))
            if worst <= 0.012:
                confirmed += 1
            else:
                missed.append("%s-%s" % (part.get("id"), other_id))
    return confirmed, missed


ORIGIN_KEYS = ("origin", "position", "centre", "center", "location", "translation")


def _xyz(value):
    """A point written any of the ways a Workspace might write one, or None.

    {"x":..,"y":..,"z":..} and [x, y, z] both occur, and the key the part
    carries it under is not fixed either. Guessing wrong used to cost nothing
    visible: `part_box` returned None, `around` skipped the part in silence, and
    a post with eight neighbours reported none touching it.
    """
    if isinstance(value, dict):
        if not any(k in value for k in ("x", "y", "z")):
            return None
        return np.array([float(value.get(k, 0.0)) for k in ("x", "y", "z")])
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return np.array([float(v) for v in value[:3]])
        except (TypeError, ValueError):
            return None
    return None


point_of = _xyz          # public name: a point written whichever way it is written


def part_origin(part: dict):
    for key in ORIGIN_KEYS:
        point = _xyz(part.get(key))
        if point is not None:
            return point, key
    return None, None


def part_box(part: dict, world=None):
    """(centre, axes 3x3, half extents) in Rhino coordinates, or None.

    `world` is the workspace-to-Rhino rotation from `world_matrix`. Pass it
    wherever the result is compared against Rhino geometry, which is everywhere.
    """
    centre, _ = part_origin(part)
    dims = part.get("dimensions") or {}
    if centre is None or not dims:
        return None
    half = np.array([float(dims.get(k, 0.0)) for k in ("width", "height", "depth")]) / 2.0
    if not np.all(half > 0):
        return None
    axes = _rotation(part.get("rotation") or {})
    if world is None:
        return centre, axes, half
    world = np.asarray(world, float)
    return world @ centre, world @ axes, half


def member_frame(part: dict, world=None):
    """The member's own frame, from its part record. -> frame dict, or None.

    Origin at a corner, `v` along the longest side, right-handed. The same
    three fields (`origin`, `dimensions`, `rotation`) every other part is read
    from, so the member and its neighbours cannot end up in different
    coordinates.
    """
    packed = part_box(part, world)
    if packed is None:
        return None
    centre, axes, half = packed
    size = 2.0 * half
    v_axis = int(np.argmax(size))
    # The other two IN ORDER, never rotated by (v+1, v+2). The box built from
    # this frame carries u, v, w as its X, Y, Z, so anything re-deriving a frame
    # from that box finds v in the middle and recovers u and w by taking the
    # remaining axes in order. Rotating them here and not there swaps u for w --
    # invisible on a square section, and on a rectangular one it reshapes the
    # cells against the wrong grid and the damage comes back in stripes.
    u_axis, w_axis = [i for i in (0, 1, 2) if i != v_axis]
    u, v = axes[:, u_axis], axes[:, v_axis]
    if float(np.cross(u, v) @ axes[:, w_axis]) < 0:
        u = -u
    w = np.cross(u, v)
    extents = np.array([size[u_axis], size[v_axis], size[w_axis]], float)
    origin = centre - np.column_stack([u, v, w]) @ (extents / 2.0)
    return {"origin": origin.tolist(), "u": u.tolist(), "v": v.tolist(),
            "w": w.tolist(), "width": float(extents[0]),
            "length": float(extents[1]), "height": float(extents[2])}


def basis_of(frame: dict) -> np.ndarray:
    """The frame's axes as columns: world = origin + basis @ (u, v, w)."""
    return np.column_stack([np.asarray(frame[k], float) for k in ("u", "v", "w")])


def why_empty(workspace, beam_id: str, frame=None) -> list:
    """Why `around` found what it found. Never let a zero be silent.

    Reports how many parts carry a usable box, under which key, and -- for the
    declared connections specifically -- what happened to each. A part listed as
    a connection but skipped for want of coordinates is a data problem, not a
    geometry problem, and the two look identical from the outside.
    """
    parts = list((workspace.get("instance") or {}).get("parts") or [])
    target = next((p for p in parts if str(p.get("id")) == str(beam_id)), None)
    if target is None:
        return ["no part %r in the Workspace" % beam_id]

    declared = [str(v) for v in (target.get("connections") or [])]
    keys, boxed, no_point, no_dims = {}, 0, [], []
    for part in parts:
        if str(part.get("id")) == str(beam_id):
            continue
        point, key = part_origin(part)
        if point is None:
            no_point.append(str(part.get("id")))
            continue
        keys[key] = keys.get(key, 0) + 1
        if part_box(part) is None:
            no_dims.append(str(part.get("id")))
            continue
        boxed += 1

    lines = ["%d of %d other part(s) carry a usable box%s"
             % (boxed, len(parts) - 1,
                " (position read from %s)" % ", ".join(sorted(keys)) if keys else "")]
    if no_point:
        lines.append("no position on %d part(s) -- looked for %s -- e.g. %s"
                     % (len(no_point), "/".join(ORIGIN_KEYS), ", ".join(no_point[:4])))
    if no_dims:
        lines.append("no usable dimensions on %d part(s): %s"
                     % (len(no_dims), ", ".join(no_dims[:4])))
    if declared:
        lines.append("%d connection(s) declared on this part: %s"
                     % (len(declared), ", ".join(declared)))

    # The decisive number when the data is fine but nothing touches: how far
    # the geometry wired in sits from where the Workspace says this part is. A
    # metre of it means the Rhino model and the export are not in the same
    # coordinates, and no neighbour will ever be found.
    box = part_box(target, world_matrix(workspace))
    if frame is not None and box is not None:
        size = np.array([frame["width"], frame["length"], frame["height"]], float)
        basis = np.column_stack([np.asarray(frame[k], float) for k in ("u", "v", "w")])
        here = np.asarray(frame["origin"], float) + basis @ (size / 2.0)
        drift = float(np.linalg.norm(box[0] - here))
        lines.append("the box wired in sits %.0f mm from where the Workspace puts "
                     "this part%s" % (1000 * drift,
                                      "" if drift < 0.05 else
                                      " -- they are not in the same coordinates, "
                                      "which is why nothing touches"))
    return lines


def corners(box) -> np.ndarray:
    centre, axes, half = box
    signs = np.array([[a, b, c] for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)], float)
    return (signs * half) @ axes.T + centre


def to_local(points: np.ndarray, frame: dict) -> np.ndarray:
    """World points -> member coordinates, metres from the member's corner."""
    origin = np.asarray(frame["origin"], float)
    basis = np.column_stack([np.asarray(frame[k], float) for k in ("u", "v", "w")])
    return (np.asarray(points, float) - origin) @ basis


def inside(box, points: np.ndarray, slack: float = 0.0) -> np.ndarray:
    """Which world points fall inside this oriented box."""
    centre, axes, half = box
    local = (np.asarray(points, float) - centre) @ axes
    return np.all(np.abs(local) <= (half + float(slack)), axis=1)


def around(workspace, beam_id: str, frame: dict, touching: float = 0.01) -> list:
    """Every other part, described in the repaired member's own coordinates.

    Returns, per part: where it sits along the member (`vRange`, metres from the
    member's corner), which face of the member it lies against, and the gap.
    """
    parts = list((workspace.get("instance") or {}).get("parts") or [])
    target = next((p for p in parts if str(p.get("id")) == str(beam_id)), None)
    if target is None:
        return []
    declared = {str(v) for v in (target.get("connections") or [])}
    size = np.array([frame["width"], frame["length"], frame["height"]], float)
    world = world_matrix(workspace)

    out = []
    for part in parts:
        part_id = str(part.get("id"))
        if part_id == str(beam_id):
            continue
        box = part_box(part, world)
        if box is None:
            continue
        local = to_local(corners(box), frame)
        lo, hi = local.min(axis=0), local.max(axis=0)

        # gap to the member on each axis; negative means it overlaps that span
        gaps = np.maximum(lo - size, -hi)
        gap = float(gaps.max())
        if gap > touching:
            continue

        faces = []
        for name, (axis, sign) in FACES.items():
            if sign < 0 and abs(hi[axis]) <= touching:
                faces.append(name)
            elif sign > 0 and abs(lo[axis] - size[axis]) <= touching:
                faces.append(name)
        out.append({
            "id": part_id,
            "label": part.get("label") or part_id,
            "declared": part_id in declared,
            "againstFaces": faces,
            "vRange": [round(float(max(0.0, lo[1])), 3),
                       round(float(min(size[1], hi[1])), 3)],
            "gap": round(gap, 4),
        })
    return out


def stack(boxes: list):
    """All the boxes as arrays, so a collision test is one numpy expression."""
    if not boxes:
        return None
    return (np.array([b[0] for b in boxes], float),        # centres  (B, 3)
            np.array([b[1] for b in boxes], float),        # axes     (B, 3, 3)
            np.array([b[2] for b in boxes], float))        # halves   (B, 3)


def any_hit(packed, points: np.ndarray) -> bool:
    if packed is None or not len(points):
        return False
    centres, axes, halves = packed
    diff = np.asarray(points, float)[None, :, :] - centres[:, None, :]   # (B, N, 3)
    local = np.einsum("bnp,bpq->bnq", diff, axes)                        # (B, N, 3)
    return bool(np.any(np.all(np.abs(local) <= halves[:, None, :], axis=2)))


def blocked(prosthesis_points: np.ndarray, boxes: list, frame: dict,
            steps: int = 20) -> list:
    """Directions the replacement piece cannot travel without hitting a part.

    Slides the piece and looks for a collision -- the honest version of "can we
    actually get it in". Without this, a joint that can only be assembled by
    lifting a standing post looks perfectly fine.
    """
    if not len(prosthesis_points) or not boxes:
        return []
    packed = stack(boxes)
    basis = np.column_stack([np.asarray(frame[k], float) for k in ("u", "v", "w")])
    points = np.asarray(prosthesis_points, float)
    if len(points) > 800:
        points = points[:: max(1, len(points) // 800)]

    # How far the piece has to travel to be free.
    #
    # Across the section it must clear the member, so the member's own width and
    # height are right. ALONG the member it only has to travel its own length to
    # come off its seat -- asking whether it can slide the whole length of a 3 m
    # rail answers "no" for every joint ever drawn, because there is a post at
    # each end. That made every joint report the same two blocked directions.
    local = (points - np.asarray(frame["origin"], float)) @ basis
    along = float(local[:, 1].max() - local[:, 1].min())
    reach = [float(frame["width"]), max(along, 0.05), float(frame["height"])]
    out = []
    for name, (axis, sign) in FACES.items():
        vector = basis[:, axis] * float(sign)
        distance = reach[axis] * 1.2
        for step in range(1, int(steps) + 1):
            if any_hit(packed, points + vector * (distance * step / steps)):
                out.append(name)
                break
    return out


def summary(neighbours: list) -> list:
    """Lines for the report and for the brief -- plain language, no vectors."""
    lines = []
    for item in neighbours:
        where = ", ".join(item["againstFaces"]) or "alongside"
        lines.append(
            "%s (%s) lies against %s over %.2f..%.2f m along the member%s"
            % (item["label"], item["id"], where,
               item["vRange"][0], item["vRange"][1],
               "" if item["declared"] else " -- touching but NOT listed as a connection"))
    return lines
