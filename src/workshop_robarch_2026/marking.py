"""What the mill needs: the joint's planes in world coordinates, and an order.

The joint is designed in its own window and placed on the member by the kernel.
By the time anyone cuts it, none of that matters -- what matters is where each
face sits in space, which side of it is waste, and in what order the faces can
actually be reached with the piece clamped down.

This file does the arithmetic. The curves themselves are drawn in Rhino, in
05 MARK, because that is the only place the solid actually exists.
"""
from __future__ import annotations

import numpy as np

VERSION = 1

FACES = {"+u": (0, +1), "-u": (0, -1),
         "+v": (1, +1), "-v": (1, -1),
         "+w": (2, +1), "-w": (2, -1)}


def face_vector(name: str, frame: dict) -> np.ndarray:
    axis, sign = FACES[str(name).strip()]
    key = ("u", "v", "w")[axis]
    return float(sign) * np.asarray(frame[key], float)


def placed_planes(joint: dict, repair: dict) -> list:
    """The joint's planes after placement, in world coordinates.

    `to_template` builds one cutter per plane in order and `build_repair` keeps
    that order, so cut i + 1 is plane i -- the stock cut is first and the axial
    trim, when there is one, is last. The normal points INTO the waste.
    """
    cuts = repair["parts"][0]["cuts"]
    planes = joint["planes"]
    groups = joint.get("groups") or []
    out = []
    for index, plane in enumerate(planes):
        cut = cuts[index + 1]
        normal = np.asarray(cut["normal"], float)
        normal = normal / np.linalg.norm(normal)
        out.append({
            "id": str(plane.get("id", "P%d" % index)),
            "cut": cut["name"],
            "role": str(plane.get("role") or "face %d" % index),
            "groups": [i for i, group in enumerate(groups)
                       if str(plane.get("id")) in [str(n) for n in group]],
            "normal": normal.tolist(),          # points into the waste
            "offset": float(cut["offset"]),     # normal . p = offset
        })
    return out


def _nearest_face(direction: np.ndarray) -> str:
    return max(FACES, key=lambda name: float(direction[FACES[name][0]]) * FACES[name][1])


def in_member(placed: list, frame: dict) -> list:
    """The same planes in the member's own axes, said in words a machinist reads.

    The sign of the normal means opposite things on the two bodies and this is
    the easiest thing in the whole file to get backwards. The normal points into
    the material that leaves the historic member -- which is the material the
    replacement piece is made of. So on the blank:

        n . p >= offset   is the PIECE
        n . p <  offset   is the WASTE, and that is the only side the cutter
                          can approach this face from.

    `toolFrom` is therefore the face the cutter comes from, stated outright, so
    nobody downstream has to rederive it from a sign.
    """
    basis = np.column_stack([np.asarray(frame[k], float) for k in ("u", "v", "w")])
    origin = np.asarray(frame["origin"], float)
    centre = origin + (0.5 * float(frame["width"]) * basis[:, 0]
                       + 0.5 * float(frame["height"]) * basis[:, 2])
    axis = basis[:, 1]

    out = []
    for item in placed:
        world = np.asarray(item["normal"], float)
        local = world @ basis
        piece, tool = _nearest_face(local), _nearest_face(-local)

        # where the face meets the member's centre line, which is the one point
        # on it anybody can picture
        along = float(world @ axis)
        crossing = None
        if abs(along) > 1e-6:
            crossing = round(1000.0 * (float(item["offset"]) - float(world @ centre))
                             / along, 1)
        # only meaningful for a face that runs along the member and so never
        # crosses the centre line; for an oblique face it is a big useless number
        off_centre = (None if crossing is not None else
                      round(1000.0 * abs(float(world @ centre) - float(item["offset"])), 1))

        out.append(dict(item, **{
            "inMember": [round(float(v), 3) for v in local],
            "pieceToward": piece,
            "toolFrom": tool,
            "tiltFromFaceDeg": round(float(np.degrees(np.arccos(
                np.clip(abs(local[FACES[tool][0]]), -1.0, 1.0)))), 1),
            "crossesCentreLineAtMm": crossing,
            "offCentreLineMm": off_centre,
        }))
    return out


def sequence_payload(joint: dict, placed: list, frame: dict, built: dict,
                     tool_mm: float, stepover_mm: float) -> dict:
    """Everything the cutting-order agent is shown. No world vectors."""
    return {
        "joint": {"id": joint.get("id"), "groups": joint.get("groups")},
        "faces": [{k: item[k] for k in
                   ("id", "role", "groups", "toolFrom", "pieceToward",
                    "tiltFromFaceDeg", "crossesCentreLineAtMm", "offCentreLineMm")}
                  for item in in_member(placed, frame)],
        "member": {"widthMm": round(1000 * frame["width"], 1),
                   "heightMm": round(1000 * frame["height"], 1)},
        "fabrication": built,
        "tool": {"diameterMm": tool_mm, "stepoverMm": stepover_mm,
                 "axes": 3, "heldOn": "a bench beside the frame"},
        "faceNames": sorted(FACES),
    }


def check_sequence(order: list, placed: list, frame: dict) -> list:
    """Where the returned order does not survive contact with the geometry.

    Never refuses -- the point of showing a participant a bad sequence is that
    they can see why it is bad.
    """
    problems = []
    wanted = [item["id"] for item in placed]
    got = [str(step.get("face")) for step in order]
    for name in wanted:
        count = got.count(name)
        if count == 0:
            problems.append("%s is never cut" % name)
        elif count > 1:
            problems.append("%s is cut %d times" % (name, count))
    for name in got:
        if name not in wanted:
            problems.append("%s is not a face of this joint" % name)

    by_id = {item["id"]: item for item in placed}
    flips = 0
    previous = None
    for position, step in enumerate(order):
        up = str(step.get("toolFrom") or "").strip()
        if up not in FACES:
            problems.append("step %d does not say which face the tool comes "
                            "from" % (position + 1))
            continue
        if previous is not None and up != previous:
            flips += 1
        previous = up
        item = by_id.get(str(step.get("face")))
        if item is None:
            continue
        # the waste lies on the far side of the face from the piece, so the tool
        # can only come from a direction the face's outward normal looks toward
        outward = -np.asarray(item["normal"], float)
        if float(outward @ face_vector(up, frame)) <= 0.10:
            problems.append(
                "%s (%s) is cut from %s, but its waste lies toward %s -- from %s "
                "the cutter is on the wrong side of the face, and a 3-axis mill "
                "will not turn the corner"
                % (item["id"], item["role"], up,
                   _nearest_face(outward @ np.column_stack(
                       [np.asarray(frame[k], float) for k in ("u", "v", "w")])), up))
    if flips:
        problems.append("the piece is turned over %d time(s); each flip is a "
                        "re-registration and a new chance to be out by a "
                        "millimetre" % flips)
    return problems


def roughing(offset: float, depth: float, step: float) -> list:
    """Plane constants from the far side of the waste in to the finished face.

    The face is flat, so roughing it is a stack of slices parallel to it. The
    last entry is the finish pass and sits exactly on the design plane. `depth`
    and `step` are in whatever units the offset is in -- the caller converts,
    because only the caller knows whether the model is in metres or millimetres.
    """
    step, depth = float(step), float(depth)
    if step <= 0 or depth <= 0:
        return [float(offset)]
    passes = int(np.ceil(depth / step))
    return [float(offset) + depth * (1.0 - k / passes) for k in range(1, passes + 1)]
