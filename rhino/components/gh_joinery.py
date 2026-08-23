"""GH Python 3 -- JOINERY: fit a joint to the damage cells and browse five variants.

No LLM yet. Pick a corpus joint (SJ1..SJ7) or paste one as JSON, and this shows
the five variations spanning the trade-off between healthy timber taken and
directions locked -- as real Breps.

Inputs
------
box         Box         oriented beam box from BEAM CELLS
centers     Point3d[]   cell centroids from BEAM CELLS
damage      float[]     one 0..1 value per cell, same order
nu, nv, nw  int         the grid BEAM CELLS was built with
threshold   float       damage at or above this must be removed [0.50]
joint_key   str         SJ1..SJ7, or leave empty and use joint_json
joint_json  str         a joint as JSON: {aspect, planes[], groups[]}
variant     int         which of the five to show [0 = as authored]
count       int         how many variations to sample [150]
tilt        float       how far a plane may lean, degrees [6.0]
shift       float       how far a plane may move, section depths [0.25]
seed        int         same seed, same five variations [7]
repo        str         optional repository override
run         bool

Outputs
-------
kept            Brep[]      the historic timber that stays
prosthesis      Brep[]      the replacement piece
planes          Curve[]     the cutting planes of the shown variant
removed_cells   Point3d[]   cell centres inside the replacement
summary         str[]       the five variants, one line each
variant_json    str         the shown variant's planes and measurements
report          str[]
"""

import json
import os
import sys


def _repo_root():
    starts = [globals().get("repo"), os.environ.get("ROBARCH_REPO")]
    try:
        starts.append(os.path.dirname(str(ghenv.Component.OnPingDocument().FilePath or "")))
    except Exception:
        pass
    starts.extend([os.getcwd(), *sys.path])
    for start in starts:
        if not start or "://" in str(start):
            continue
        folder = os.path.abspath(os.path.expanduser(str(start)))
        if os.path.isfile(folder):
            folder = os.path.dirname(folder)
        for _ in range(9):
            if os.path.isdir(os.path.join(folder, "src", "workshop_robarch_2026")):
                return folder
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
    raise RuntimeError("workshop repository not found; connect its folder to repo")


kept = []
prosthesis = []
planes = []
removed_cells = []
summary = []
variant_json = ""
report = ["Joinery"]


def _corner_frame(box, np):
    """Member frame with the origin at a CORNER -- what scoring.to_local expects.

    BEAM CELLS hands over a box centred on the frame origin. Everything
    downstream measures from a corner. Converting in exactly one place is the
    only way that stays true.
    """
    plane = box.Plane
    axes = [np.array([plane.XAxis.X, plane.XAxis.Y, plane.XAxis.Z], float),
            np.array([plane.YAxis.X, plane.YAxis.Y, plane.YAxis.Z], float),
            np.array([plane.ZAxis.X, plane.ZAxis.Y, plane.ZAxis.Z], float)]
    extents = [float(box.X.Length), float(box.Y.Length), float(box.Z.Length)]
    intervals = [box.X, box.Y, box.Z]
    axis_v = int(np.argmax(extents))
    axis_u, axis_w = (axis_v + 1) % 3, (axis_v + 2) % 3
    u, v = axes[axis_u], axes[axis_v]
    w = np.cross(u, v)
    w /= np.linalg.norm(w)
    origin = (np.array([plane.Origin.X, plane.Origin.Y, plane.Origin.Z], float)
              + intervals[0].Min * axes[0]
              + intervals[1].Min * axes[1]
              + intervals[2].Min * axes[2])
    if float(w @ axes[axis_w]) < 0:
        origin = origin + extents[axis_w] * axes[axis_w]
    return {"origin": origin.tolist(), "u": u.tolist(), "v": v.tolist(), "w": w.tolist(),
            "width": extents[axis_u], "length": extents[axis_v], "height": extents[axis_w]}


def _from_corpus(key, grammar):
    template = grammar.TEMPLATES[str(key).strip().upper()]
    return {
        "id": str(key).strip().upper(),
        "aspect": 3.0,
        "planes": [{"id": "P%d" % i, "normal": list(p.normal), "d": float(p.d), "role": r}
                   for i, (p, r) in enumerate(zip(template.planes, template.roles))],
        "groups": [["P%d" % i for i in group] for group in template.groups],
    }


if bool(globals().get("run")):
    try:
        root = _repo_root()
        source = os.path.join(root, "src")
        if source not in sys.path:
            sys.path.append(source)
        for name in list(sys.modules):
            if name.startswith("workshop_robarch_2026"):
                sys.modules.pop(name)

        import numpy as np
        import Rhino
        import Rhino.Geometry as rg
        from workshop_robarch_2026 import evaluator, joinery, kernel, six_plane_grammar

        if globals().get("box") is None:
            raise ValueError("connect the oriented box from BEAM CELLS")
        centres = list(globals().get("centers") or [])
        values = [float(v) for v in (globals().get("damage") or [])]
        if not centres:
            raise ValueError("connect cell centres from BEAM CELLS")
        grid = (int(globals().get("nu") or 3),
                int(globals().get("nv") or 12),
                int(globals().get("nw") or 3))

        frame = _corner_frame(box, np)
        points = [[float(p.X), float(p.Y), float(p.Z)] for p in centres]
        mem = joinery.member(frame, points, values, grid)
        gate = 0.5 if globals().get("threshold") is None else float(threshold)
        rot = int(joinery.damaged(mem, gate).sum())
        report.append("member %.2f x %.2f x %.2f m, grid %dx%dx%d = %d cells"
                      % (frame["width"], frame["length"], frame["height"],
                         grid[0], grid[1], grid[2], len(points)))
        report.append("%d cells at or above %.2f" % (rot, gate))
        if not rot:
            raise ValueError("no cell reaches the threshold -- nothing to repair")

        raw = str(globals().get("joint_json") or "").strip()
        if raw:
            joint = json.loads(raw)
            joint.setdefault("id", "authored")
        else:
            joint = _from_corpus(globals().get("joint_key") or "SJ3", six_plane_grammar)
        report.append("joint: %s, %d planes, %d group(s)"
                      % (joint["id"], len(joint["planes"]), len(joint["groups"])))

        open_groups = joinery.open_at_kept_side(joint)
        for item in open_groups:
            report.append(
                "WARNING group %d (%s) is open on the kept side and reaches %.1f "
                "section depths past the joint -- it will sweep the member"
                % (item["group"], ", ".join(item["planes"]), item["reachesBelowZero"]))

        station, side, note = joinery.anchor(mem, gate, float(joint.get("aspect", 3.0)))
        report.append("%s -> joint at %.3f m, replacing the %s end"
                      % (note, station, "low" if side < 0 else "high"))

        candidates = joinery.vary(
            joint,
            count=int(globals().get("count") or 150),
            tilt=float(globals().get("tilt") or 6.0),
            shift=float(globals().get("shift") or 0.25),
            seed=int(globals().get("seed") or 7),
        )
        results = []
        for candidate in candidates:
            if joinery.open_at_kept_side(candidate):
                results.append(None)
                continue
            try:
                measured = joinery.measure(candidate, mem, station, side, gate)
                measured["id"] = candidate["id"]
                measured["moves"] = candidate["moves"]
                measured["joint"] = candidate
                results.append(measured)
            except Exception:
                results.append(None)
        usable = [r for r in results if r]
        report.append("%d of %d variations valid" % (len(usable), len(candidates)))

        five = joinery.span(results, keep=5)
        for index, item in enumerate(five):
            summary.append(
                "%d  %-10s rot left %-3d healthy %-4d locks %-18s %s"
                % (index, item["id"], item["rotLeft"], item["soundTaken"],
                   " ".join(item["locks"]) or "none",
                   "; ".join(item["moves"][:2]) or "as authored"))

        pick = max(0, min(len(five) - 1, int(globals().get("variant") or 0)))
        shown = five[pick]
        report.append("showing %d of %d: %s" % (pick, len(five), shown["id"]))

        tolerance = float(Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance)
        for part in shown["repair"]["parts"]:
            breps = evaluator.evaluate_part(part, tolerance)
            (kept if part["name"] == "kept" else prosthesis).extend(breps)

        size = 0.75 * max(frame["width"], frame["height"])
        for cut_json in shown["repair"]["parts"][0]["cuts"][1:-1]:
            item = kernel.Cut.from_json(cut_json)
            origin = item.origin()
            normal = np.asarray(item.normal, float)
            normal /= np.linalg.norm(normal)
            point = rg.Point3d(*[float(c) for c in origin])
            frame_plane = rg.Plane(point, rg.Vector3d(*[float(c) for c in normal]))
            planes.append(rg.Rectangle3d(
                frame_plane, rg.Interval(-size, size), rg.Interval(-size, size)
            ).ToNurbsCurve())

        removed_cells = [centres[i] for i, flag in enumerate(shown["removed"]) if flag]
        variant_json = json.dumps(
            {"id": shown["id"], "moves": shown["moves"], "joint": shown["joint"],
             "rotLeft": shown["rotLeft"], "soundTaken": shown["soundTaken"],
             "locks": shown["locks"], "extent": shown["extent"],
             "station": shown["station"], "side": shown["side"]},
            indent=2, ensure_ascii=False)
        report.append("kept %d Brep(s), prosthesis %d Brep(s)" % (len(kept), len(prosthesis)))
    except Exception as exc:
        report.append("ERROR: {}".format(exc))
else:
    report.append("set run to true")
