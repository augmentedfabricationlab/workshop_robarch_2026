"""GH Python 3, 00 CELLS: divide the selected beam into cells and read its damage.

  1. Divide the member into cells. Box mode is always used, so a cell is a plain
     box and a point belongs to whichever cell its centroid falls in. The
     oriented box is taken from the Workspace part record rather than from Rhino
     geometry, so the cells, the member frame and the neighbouring parts all end
     up in the same coordinate system.
  2. Read the survey. One model call turns the recorded conditions, their
     evidence and the photographs into regions expressed in the member's own
     coordinates. Those regions are then painted onto the cells.

Needs
-----
assembly_information_model (CellularizedPart), compas and compas_rhino,
installed in Rhino's Python. The component cannot run without them.
src/workshop_robarch_2026: context, neighbours, damagemap, agents. Found from
the .gh file's folder, or wire `repo`.
data/prompts/damage.md, and a Gemini key in gemini_api_key.txt or GEMINI_API_KEY.

Inputs
------
workspace_json str    the exported Workspace ZIP (path) or JSON
picker         str    wire a Grasshopper Value List here
refresh        bool   fill that Value List from the Workspace parts
nu, nv, nw     int    subdivisions across width, along length, across height
resolution     str    "low", "medium" or "fine"; overrides nu/nv/nw
threshold      float  damage at or above this counts as decayed [0.50]
model          str    optional Gemini model
temperature    float  optional [0.2]
run            bool   press to read the damage; press again to ask afresh
repo           str    the repository folder, if it cannot be found on its own

Outputs
-------
timber       CellularizedPart, with damage set from the survey
plane        the member frame; Y is the beam axis
box          the oriented beam box, used by 01 and 03
centers      cell centroids in world coordinates
damage       one 0..1 value per cell, in the same order
boxes        every other part as an oriented box, in the same coordinates
labels       a TextDot per other part, at its centre, carrying its name
conditions   a TextDot per condition on this part, where the surveyor marked it
regions_json the model's regions and its reading, for a panel
report       str[]

TextDots carry their own text and position, so `labels` and `conditions` draw
themselves. Leave them previewing: a box in an implausible place, or a marker
floating off the timber, means the coordinates are wrong.
"""

import json
import os
import sys

PRESETS = {"low": 2, "medium": 3, "fine": 4}


def _repo_root():
    """The repository folder: the `repo` input, the .gh file, cwd, or sys.path."""
    tries = [globals().get("repo"), os.environ.get("ROBARCH_REPO")]
    try:
        tries.append(str(ghenv.Component.OnPingDocument().FilePath or ""))
    except Exception:
        pass
    for start in tries + [os.getcwd()] + list(sys.path):
        if not start or "://" in str(start):
            continue
        folder = os.path.abspath(os.path.expanduser(str(start)))
        for _ in range(9):
            if os.path.isdir(os.path.join(folder, "src", "workshop_robarch_2026")):
                return folder
            folder, before = os.path.dirname(folder), folder
            if folder == before:
                break
    raise RuntimeError("workshop repository not found; connect its folder to repo")


def _grid(extents, asked):
    """Roughly cubic cells, from a word or a number.

    Cubic matters: a fixed subdivision along a 1.84 m post makes 150 mm cells
    against 33 mm across it, and counting cells that shape measures the
    member's length far more than its section.
    """
    number = PRESETS.get(str(asked).strip().lower())
    if number is None:
        number = int(asked)
    size = min(extents) / float(number)
    return tuple(max(1, int(round(e / size))) for e in extents)


def _dot(text, point, height, rg):
    dot = rg.TextDot(str(text), rg.Point3d(*[float(c) for c in point]))
    try:
        dot.FontHeight = height
    except Exception:
        pass
    return dot


timber = plane = box = None
centers, damage, boxes, labels, conditions = [], [], [], [], []
regions_json = ""
report = ["00 Cells"]

try:
    root = _repo_root()
    if os.path.join(root, "src") not in sys.path:
        sys.path.append(os.path.join(root, "src"))
    import scriptcontext as sc

    # Do not reload the package while a background thread is still using it.
    live = sc.sticky.get("joinery.jobs.%s" % ghenv.Component.InstanceGuid, {})
    if not any(isinstance(j, dict) and not j.get("done") for j in live.values()):
        for name in [n for n in sys.modules if n.startswith("workshop_robarch_2026")]:
            sys.modules.pop(name)

    import numpy as np
    import Rhino.Geometry as rg
    from compas.geometry import Frame, Point, Transformation, transform_points
    from compas_rhino.conversions import frame_to_rhino, mesh_to_compas, point_to_rhino
    from assembly_information_model import CellularizedPart
    from workshop_robarch_2026 import agents, context, damagemap, neighbours

    # ---- the part, and its frame -----------------------------------------
    workspace = context.load_workspace(globals().get("workspace_json"))
    options = context.part_options(workspace)
    if not options:
        raise ValueError("the Workspace contains no parts")

    beam_id = str(globals().get("picker") or "").strip()
    if bool(globals().get("refresh")):
        beam_id, rewritten = context.fill_picker(ghenv.Component, options)
        report.append("dropdown filled" if rewritten else "dropdown already current")
    elif beam_id not in [item["id"] for item in options]:
        beam_id = options[0]["id"]
        report.append("press refresh once to fill the connected Value List")
    part = context.find_part(workspace, beam_id)

    to_rhino = neighbours.world_matrix(workspace)
    frame = neighbours.member_frame(part, to_rhino)
    if frame is None:
        raise ValueError("part %s carries no usable origin or dimensions. "
                         "It has: %s"
                         % (beam_id, ", ".join(sorted(part.keys()))))
    extents = (frame["width"], frame["length"], frame["height"])
    origin = np.asarray(frame["origin"], float)
    basis = neighbours.basis_of(frame)
    member = Frame(Point(*(origin + basis @ (np.array(extents) / 2.0))),
                   frame["u"], frame["v"])
    plane = frame_to_rhino(member)
    box = rg.Box(plane, *[rg.Interval(-e / 2.0, e / 2.0) for e in extents])

    # ---- the rest of the frame, before any early exit --------------------
    for other in context.parts(workspace):
        packed = (None if str(other.get("id")) == str(beam_id)
                  else neighbours.part_box(other, to_rhino))
        if packed is None:
            continue
        at, axes, half = packed
        where = rg.Plane(rg.Point3d(*[float(c) for c in at]),
                         rg.Vector3d(*[float(c) for c in axes[:, 0]]),
                         rg.Vector3d(*[float(c) for c in axes[:, 1]]))
        boxes.append(rg.Box(where, *[rg.Interval(-h, h) for h in half]))
        labels.append(_dot(other.get("label") or other.get("id"), at, 11, rg))

    # ---- cellularize, in the member's own frame --------------------------
    # build_cell_grid subdivides the world bounding box and ignores the frame,
    # so the member is moved into its own frame first and back afterwards.
    asked = str(globals().get("resolution") or "").strip()
    if not asked and any(globals().get(k) for k in ("nu", "nv", "nw")):
        grid = tuple(int(globals().get(k) or d)
                     for k, d in (("nu", 3), ("nv", 12), ("nw", 3)))
    else:
        grid = _grid(extents, asked or "medium")

    forward = Transformation.from_frame_to_frame(member, Frame.worldXY())
    backward = Transformation.from_frame_to_frame(Frame.worldXY(), member)
    timber = CellularizedPart.from_mesh(
        mesh_to_compas(rg.Mesh.CreateFromBox(box, 1, 1, 1)).transformed(forward),
        grid=grid, box_mode=True, name="timber", frame=Frame.worldXY())
    timber.mesh.transform(backward)
    network = timber.cell_network
    for vertex in network.vertices():          # CellNetwork.transform() raises
        network.vertex_attributes(vertex, "xyz", transform_points(
            [network.vertex_coordinates(vertex)], backward)[0])
    for cell in network.cells():
        piece = network.cell_attribute(cell, "cell_mesh")
        if piece is not None:
            piece.transform(backward)
    timber.frame = member

    world = np.array([list(network.cell_centroid(c)) for c in network.cells()], float)
    centers = [point_to_rhino(Point(*p)) for p in world]
    local = (world - origin) @ basis          # metres from the member's corner
    damage = [0.0] * len(world)
    timber.set_damage_scores(list(damage))
    gate = 0.5 if globals().get("threshold") is None else float(threshold)

    cell_mm = tuple(1000.0 * extents[i] / grid[i] for i in range(3))
    axis = frame["v"]
    confirmed, missed = neighbours.confirm_connections(workspace)
    around = neighbours.around(workspace, beam_id, frame)
    report += [
        "selected: %s (%s)" % (part.get("label") or beam_id, beam_id),
        "member %.3f x %.3f x %.3f m, axis = frame Y" % extents,
        "workspace is %s-up; the axis points (%+.2f, %+.2f, %+.2f) here, so the "
        "member is %s"
        % (neighbours.WORLD_UP.upper(), axis[0], axis[1], axis[2],
           "upright" if abs(axis[2]) > 0.9 else
           "horizontal" if abs(axis[2]) < 0.1 else "raking"),
        "grid %d x %d x %d = %d cells, %.0f x %.0f x %.0f mm"
        % (grid + (timber.num_cells,) + cell_mm),
        "%d other part(s) as boxes; %d touch this member" % (len(boxes), len(around)),
        "%d declared connection(s) confirmed geometrically%s"
        % (confirmed, "" if not missed else
           ", %d NOT touching: %s. The coordinate convention is wrong somewhere."
           % (len(missed), ", ".join(missed[:4]))),
    ]
    if max(cell_mm) / min(cell_mm) > 3.0:
        report.append("WARNING: cells are %.1f:1 elongated, so counting them "
                      "measures length far more than section"
                      % (max(cell_mm) / min(cell_mm)))

    # ---- what the survey says --------------------------------------------
    recorded = context.conditions_for(workspace, beam_id)
    evidence = context.evidence_for(workspace, recorded, beam_id)
    report.append("%d condition(s) recorded here, %d piece(s) of evidence"
                  % (len(recorded), len(evidence)))

    pinned = damagemap.marks(recorded, frame, to_rhino)
    for pin in pinned:
        conditions.append(_dot(pin["label"], pin["world"], 14, rg))
        report.append("   %s at u %.3f  v %.3f  w %.3f%s\n      %s"
                      % (pin["label"], pin["atMemberUVW"][0], pin["atMemberUVW"][1],
                         pin["atMemberUVW"][2],
                         "" if pin["insideTheMember"] else
                         "  (WARNING: outside this member)", pin["says"]))
    if not recorded:
        report.append("nothing recorded, so damage stays 0.0 on every cell. "
                      "Add a condition to the part in the Workspace, then "
                      "press run.")
        raise SystemExit

    model = str(globals().get("model") or agents.DEFAULT_MODEL).strip()
    warmth = globals().get("temperature")
    warmth = 0.2 if warmth is None else float(warmth)
    pictures, notes = context.evidence_images(globals().get("workspace_json"), evidence)
    report.extend(notes)

    payload = {
        "part": {k: part.get(k) for k in ("id", "label", "dimensions", "material",
                                          "function", "notes") if part.get(k)},
        "member": {"widthMm": round(1000 * extents[0], 1),
                   "lengthMm": round(1000 * extents[1], 1),
                   "heightMm": round(1000 * extents[2], 1),
                   "grid": list(grid), "cellCount": int(len(world)),
                   "cellMm": [round(c, 1) for c in cell_mm]},
        "orientation": damagemap.orientation(frame, around),
        "neighbours": around,
        "conditions": recorded,
        "conditionPoints": pinned,
        "evidence": evidence,
    }
    answer, lines = agents.ask(
        ghenv.Component, "damage",
        agents.signature("damage-v1", payload, len(pictures), model, warmth),
        lambda: agents.call(root, "damage.md", payload, model=model,
                            temperature=warmth, attachments=pictures),
        bool(globals().get("run")), "read the damage from the survey")
    report.extend(lines)
    if answer is None:
        raise SystemExit

    # ---- paint the regions onto the cells --------------------------------
    regions = answer.get("regions") or []
    values = damagemap.paint(regions, local, extents)
    damage = [float(v) for v in values]
    timber.set_damage_scores(list(damage))
    regions_json = json.dumps(dict(answer, memberExtentsM=list(extents)),
                              indent=2, ensure_ascii=False)
    report.extend(damagemap.read_report(answer, local, extents, gate, pinned,
                                        [p["id"] for p in pictures]))
    report.append(damagemap.summary(values, gate))
except SystemExit:
    pass
except Exception as exc:
    report.append("ERROR: {}".format(exc))
