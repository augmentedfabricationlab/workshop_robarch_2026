"""GH Python 3 -- 05 MARK: the joint line round the blank, and the order to cut it.

The replacement piece is milled from a rectangular blank of the member's own
section. This component draws where the joint faces surface on that blank -- the
line that wraps round all four sides -- and two kinds of offset from it.

The first is the tally. Beside each face sits a group of strokes, and the number
of strokes is that face's place in the cutting order: four strokes means cut this
one fourth. It is the carpenter's own way of numbering work, it survives being
photographed and scribed 2 mm deep, and it needs no legend.

The count runs over the faces that are actually cut, contiguously. 03 measures
how much face each plane really contributes once the joint is on the timber and
drops the ones that contribute none, so a joint drawn with five planes and cut
with three is numbered one, two, three -- not one, two, five. Where a cut face
is internal and surfaces nowhere on the blank, its number cannot be written and
the report says which number is missing rather than leaving a silent gap.

The second offset is the roughing passes at the cutter's stepover, which say how
the waste comes off.

The order is not a rule of mine. A model is given the faces, their roles, which
way the waste lies and what the tool is, and it decides the sequence and where
the piece is turned over. What it returns is then checked against the geometry,
and anything it got wrong is printed rather than hidden.

Inputs
------
cuts_json    str      from 03 JOINT
prosthesis   Brep[]   from 03 JOINT -- the replacement piece
mark_side    str      piece | waste  [piece]  which side of the line the tally
                      sits on. `piece` keeps every mark on the material that
                      becomes the prosthesis and off the timber that stays.
stroke_mm    float    length of one tally stroke [12]
tally_gap_mm float    spacing between strokes in a group [6]
stepover_mm  float    roughing stepover; 0 turns the passes off [3]
model        str      optional Gemini model
temperature  float    optional [0.2]
run          bool     press to ask for the cutting order
repo         str      optional repository override

Outputs
-------
blank        Brep     the stock the piece is milled from
waste        Brep[]   what comes off it
outline      Curve[]  the joint line on the blank's four long faces, in cut order
strokes      Curve[]  the tally: N strokes beside the face that is cut Nth, so
                      the count on the timber is the step number
passes       Curve[]  roughing passes, deepest first, finish pass last
order        str[]    the sequence in words
sequence_json str
report       str[]
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


blank = None
waste = []
outline = []
strokes = []
passes = []
order = []
sequence_json = ""
report = ["05 Mark"]

try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    import scriptcontext as sc
    import time as _time

    _live = sc.sticky.get("joinery.jobs.%s" % ghenv.Component.InstanceGuid, {})
    for _job in _live.values():
        if (isinstance(_job, dict) and not _job.get("done")
                and _time.time() - float(_job.get("started") or 0) > 300.0):
            _job["done"] = True
    if not any(isinstance(j, dict) and not j.get("done") for j in _live.values()):
        for name in list(sys.modules):
            if name.startswith("workshop_robarch_2026"):
                sys.modules.pop(name)

    import numpy as np
    import Rhino
    import Rhino.Geometry as rg
    from workshop_robarch_2026 import agents, joinery, marking

    if int(getattr(marking, "VERSION", 0)) < 1:
        raise RuntimeError("marking.py is missing or old; loaded from %s"
                           % getattr(marking, "__file__", "nowhere"))

    doc = Rhino.RhinoDoc.ActiveDoc
    tol = float(doc.ModelAbsoluteTolerance)
    per_metre = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem)

    def mm(value):
        return float(value) / 1000.0 * per_metre

    raw = str(globals().get("cuts_json") or "").strip()
    if not raw:
        raise ValueError("connect cuts_json from 03 JOINT")
    data = json.loads(raw)
    frame = data["frame"]
    faces = data["faces"]
    solids = [b for b in (globals().get("prosthesis") or []) if b is not None]
    if not solids:
        raise ValueError("connect the prosthesis Breps from 03 JOINT")

    def vec(key):
        return rg.Vector3d(*[float(v) for v in frame[key]])

    u_axis, v_axis, w_axis = vec("u"), vec("v"), vec("w")
    corner = rg.Point3d(*[float(v) for v in frame["origin"]])
    basis = np.column_stack([np.asarray(frame[k], float) for k in ("u", "v", "w")])
    origin = np.asarray(frame["origin"], float)

    # ---- the blank ------------------------------------------------------
    # full section, and along the member exactly as far as the piece reaches
    section_plane = rg.Plane(corner, u_axis, v_axis)
    box = rg.BoundingBox.Empty
    for solid in solids:
        box = rg.BoundingBox.Union(box, solid.GetBoundingBox(section_plane))
    lo = section_plane.PointAt(box.Min.X, box.Min.Y, box.Min.Z)
    hi = section_plane.PointAt(box.Max.X, box.Max.Y, box.Max.Z)
    v_lo = float((np.asarray([lo.X, lo.Y, lo.Z]) - origin) @ basis[:, 1])
    v_hi = float((np.asarray([hi.X, hi.Y, hi.Z]) - origin) @ basis[:, 1])
    blank_box = rg.Box(rg.Plane(corner, u_axis, v_axis),
                       rg.Interval(0.0, float(frame["width"])),
                       rg.Interval(min(v_lo, v_hi), max(v_lo, v_hi)),
                       rg.Interval(0.0, float(frame["height"])))
    blank = blank_box.ToBrep()
    report.append("blank %.0f x %.0f x %.0f mm"
                  % (1000 * frame["width"] / per_metre,
                     1000 * abs(v_hi - v_lo) / per_metre,
                     1000 * frame["height"] / per_metre))

    joined = rg.Brep.CreateBooleanUnion(solids, tol) or solids
    cut_away = rg.Brep.CreateBooleanDifference(
        [blank], list(joined), tol) or []
    waste = list(cut_away)

    # ---- which cut plane each surfaced edge belongs to -------------------
    def plane_of(item):
        normal = rg.Vector3d(*[float(c) for c in item["normal"]])
        return normal, float(item["offset"])

    long_faces = {"+u": (basis[:, 0], float(frame["width"])),
                  "-u": (-basis[:, 0], 0.0),
                  "+w": (basis[:, 2], float(frame["height"])),
                  "-w": (-basis[:, 2], 0.0)}

    def on_blank_surface(point):
        """Which of the four long faces this point lies on, if any."""
        local = (np.array([point.X, point.Y, point.Z]) - origin) @ basis
        for name, (direction, _) in long_faces.items():
            axis = 0 if "u" in name else 2
            edge_value = float(frame["width"] if axis == 0 else frame["height"])
            want = edge_value if name[0] == "+" else 0.0
            if abs(local[axis] - want) <= 5.0 * tol:
                return name, direction
        return None, None

    edges_for_face = {item["id"]: [] for item in faces}
    for solid in joined:
        for edge in solid.Edges:
            curve = edge.EdgeCurve.Trim(edge.Domain) or edge.EdgeCurve
            middle = curve.PointAtNormalizedLength(0.5)
            face_name, _ = on_blank_surface(middle)
            if face_name is None:
                continue
            best, best_gap = None, 5.0 * tol
            for item in faces:
                normal, offset = plane_of(item)
                gap = abs(normal.X * middle.X + normal.Y * middle.Y
                          + normal.Z * middle.Z - offset)
                if gap < best_gap:
                    best, best_gap = item["id"], gap
            if best is not None:
                edges_for_face[best].append((face_name, curve.DuplicateCurve()))

    found = sum(len(v) for v in edges_for_face.values())
    report.append("%d edge(s) of the joint surface on the blank, across %d face(s)"
                  % (found, sum(1 for v in edges_for_face.values() if v)))
    report.append("%d face(s) are cut; the tally counts those and nothing else, "
                  "so the strokes beside a line are that line's step number"
                  % len(faces))

    # ---- the sequence ---------------------------------------------------
    stepover = float(globals().get("stepover_mm") or 3.0)
    built = (data.get("fabrication") or {}).get("built") or {}
    sequence = None
    payload = marking.sequence_payload(data["joint"], faces, frame, built,
                                       joinery.TOOL_MM, stepover)
    model = str(globals().get("model") or agents.DEFAULT_MODEL).strip()
    temperature = globals().get("temperature")
    temperature = 0.2 if temperature is None else float(temperature)
    state = agents.background(
        ghenv.Component, "sequence",
        agents.signature("sequence-v1", payload, model, temperature),
        lambda: agents.call(root, "sequence.md", payload, model=model,
                            temperature=temperature),
        bool(globals().get("run")))

    if state["status"] == "running":
        report.append("Gemini working on the order, %.0f s" % state["seconds"])
    elif state["status"] == "error":
        report.append("ERROR from the sequence agent: %s" % state["error"])
    elif state["status"] == "done":
        sequence, notes = state["result"]
        report.extend(notes)
        problems = marking.check_sequence(sequence.get("order") or [], faces, frame)
        for line in problems:
            report.append("SEQUENCE: %s" % line)
        if not problems:
            report.append("the sequence reaches every face from a direction it "
                          "actually looks toward")
    else:
        report.append("press run for the cutting order; the geometry below is in "
                      "the order the joint was authored")

    steps = (sequence or {}).get("order") or [{"face": item["id"]} for item in faces]
    ordered = []
    for position, step in enumerate(steps):
        item = next((f for f in faces if f["id"] == str(step.get("face"))), None)
        if item is not None:
            ordered.append((position, step, item))
    for item in faces:                       # anything the model forgot, at the end
        if not any(o[2]["id"] == item["id"] for o in ordered):
            ordered.append((len(ordered), {"face": item["id"]}, item))

    # ---- outline, strokes, passes, in that order -------------------------
    side = str(globals().get("mark_side") or "piece").strip().lower()
    if side not in ("waste", "piece"):
        side = "piece"
    stroke_len = mm(float(globals().get("stroke_mm") or 12.0))
    gap = mm(float(globals().get("tally_gap_mm") or 6.0))
    want_waste = side == "waste"
    report.append("tally marks on the %s side, %.0f mm strokes %.0f mm apart"
                  % (side, 1000 * stroke_len / per_metre, 1000 * gap / per_metre))

    corners = np.array([[a, b, c]
                        for a in (0.0, float(frame["width"]))
                        for b in (min(v_lo, v_hi), max(v_lo, v_hi))
                        for c in (0.0, float(frame["height"]))])
    world_corners = corners @ basis.T + origin

    for position, step, item in ordered:
        normal_v = rg.Vector3d(*[float(c) for c in item["normal"]])
        normal = np.asarray(item["normal"], float)
        offset = float(item["offset"])

        for face_name, curve in edges_for_face[item["id"]]:
            outline.append(curve)

        # The tally: as many strokes as this face's place in the sequence, so
        # the count on the timber IS the step number. Four strokes means cut
        # this one fourth. Written once per face, on its longest surfaced edge,
        # running off the line into the material -- never across the line.
        number = position + 1
        edges = edges_for_face[item["id"]]
        face_name, curve = (max(edges, key=lambda pair: pair[1].GetLength())
                            if edges else (None, None))
        length = curve.GetLength() if curve else 0.0
        span = (number - 1) * gap
        if curve is None:
            report.append("cut %d (%s, %s) is an internal face and surfaces "
                          "nowhere on the blank, so there is no %d to find on the "
                          "timber -- the tally skips from %d to %d"
                          % (number, item["id"], item["role"], number,
                             number - 1, number + 1))
        elif length <= span + gap:
            report.append("cut %d (%s): %d strokes need %.0f mm and its longest "
                          "line is %.0f mm -- reduce tally_gap_mm, or this cut is "
                          "too late in the order to be marked here"
                          % (number, item["id"], number,
                             1000 * (span + gap) / per_metre,
                             1000 * length / per_metre))
            curve = None

        face_normal = (rg.Vector3d(*[float(c) for c in long_faces[face_name][0]])
                       if curve is not None else None)
        for index in range(number if curve is not None else 0):
            ok, t = curve.NormalizedLengthParameter(
                min(1.0, max(0.0, (0.5 * (length - span) + index * gap) / length)))
            if not ok:
                continue
            point = curve.PointAt(t)
            tangent = curve.TangentAt(t)
            across = rg.Vector3d.CrossProduct(face_normal, tangent)
            if not across.Unitize():
                continue
            # n . p >= offset is the piece, below it is the waste
            probe = point + across * (0.25 * stroke_len)
            on_waste = (normal_v.X * probe.X + normal_v.Y * probe.Y
                        + normal_v.Z * probe.Z - offset) < 0
            if on_waste != want_waste:
                across = -across
            end = point + across * stroke_len
            local = (np.array([end.X, end.Y, end.Z]) - origin) @ basis
            inside = (-tol <= local[0] <= frame["width"] + tol
                      and -tol <= local[2] <= frame["height"] + tol
                      and min(v_lo, v_hi) - tol <= local[1] <= max(v_lo, v_hi) + tol)
            if inside:
                strokes.append(rg.LineCurve(point, end))

        if stepover > 0 and waste:
            reach = float((world_corners @ normal).max() - offset)
            for constant in marking.roughing(offset, max(0.0, reach), mm(stepover)):
                seed = constant * normal
                plane = rg.Plane(rg.Point3d(float(seed[0]), float(seed[1]),
                                            float(seed[2])), normal_v)
                for solid in waste:
                    ok, curves, _ = rg.Intersect.Intersection.BrepPlane(
                        solid, plane, tol)
                    if ok and curves:
                        passes.extend(curves)

        order.append("%d  %-4s %-28s tool from %-3s  %s"
                     % (position + 1, item["id"], item["role"][:28],
                        str(step.get("toolFrom") or "-"),
                        str(step.get("why") or "")))

    for setup in (sequence or {}).get("setups") or []:
        order.append("   setup: tool from %-3s  %s  -- %s"
                     % (setup.get("toolFrom"), " ".join(setup.get("faces") or []),
                        setup.get("note") or ""))
    for line in (sequence or {}).get("risk") or []:
        report.append("RISK: %s" % line)

    sequence_json = json.dumps({"variant": data.get("variant"),
                                "sequence": sequence,
                                "faces": data.get("inMember")},
                               indent=2, ensure_ascii=False)
    report.append("%d outline curve(s), %d stroke(s), %d roughing pass(es)"
                  % (len(outline), len(strokes), len(passes)))
except SystemExit:
    pass
except Exception as exc:
    report.append("ERROR: {}".format(exc))
