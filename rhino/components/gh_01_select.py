"""GH Python 3, 01 SELECT: bind the chosen member to its cells and write the bundle.

There is no model call here. The component checks that the cells 00 produced
really belong to the member you picked, and then writes the single record that
02, 03 and 04 all read: the part, the repair plan, the recorded conditions, the
damage as numbers, and what touches the member.

Needs
-----
src/workshop_robarch_2026: context, neighbours, joinery. Found from the .gh
file's folder, or wire `repo`.
No prompt file and no API key, because this component never calls a model.

Inputs
------
workspace_json  str        the exported Workspace ZIP (path) or JSON
picker          str        wire the same Value List as 00
refresh         bool       fill that Value List from the Workspace parts
centers         Point3d[]  cell centroids, from 00
damage          float[]    one 0..1 value per cell, from 00
threshold       float      damage at or above this must be removed [0.50]
repo            str        the repository folder, if it cannot be found on its own

Outputs
-------
setup_json      str        the bundle, for 02 and 03
beam_id         str        the selected part id
damage_grid     str        the damage as numbers, for a panel
report          str[]
"""

import json
import os
import sys


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


setup_json = beam_id = damage_grid = ""
report = ["01 Select"]

try:
    root = _repo_root()
    if os.path.join(root, "src") not in sys.path:
        sys.path.append(os.path.join(root, "src"))
    for name in [n for n in sys.modules if n.startswith("workshop_robarch_2026")]:
        sys.modules.pop(name)

    import numpy as np
    import Rhino
    from workshop_robarch_2026 import context, joinery, neighbours

    # ---- the part ---------------------------------------------------------
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

    # The same call 00 makes, so the cells and the frame cannot disagree.
    frame = neighbours.member_frame(part, neighbours.world_matrix(workspace))
    if frame is None:
        raise ValueError("part %s carries no usable origin or dimensions. "
                         "It has: %s"
                         % (beam_id, ", ".join(sorted(part.keys()))))

    # ---- the cells, and whether they are this member's --------------------
    centres = list(globals().get("centers") or [])
    values = [float(v) for v in (globals().get("damage") or [])]
    if not centres:
        raise ValueError("connect `centers` and `damage` from 00")
    points = np.array([[float(p.X), float(p.Y), float(p.Z)] for p in centres], float)

    # Containment, not span: centroids reach only (n-1)/n of the member.
    size = np.array([frame["width"], frame["length"], frame["height"]], float)
    local = (points - np.asarray(frame["origin"], float)) @ neighbours.basis_of(frame)
    lo, hi = local.min(axis=0), local.max(axis=0)
    if (np.any(lo < -0.05 * size) or np.any(hi > 1.05 * size)
            or np.any(np.abs(0.5 * (lo + hi) - 0.5 * size) > 0.1 * size)):
        raise ValueError(
            "these cells do not belong to this member. %s measures "
            "%.2f x %.2f x %.2f m "
            "and the cells run %.2f..%.2f, %.2f..%.2f, %.2f..%.2f inside it. Check "
            "that 00 and 01 have the same part selected."
            % ((beam_id,) + tuple(size) + tuple(v for pair in zip(lo, hi) for v in pair)))

    mem = joinery.member(frame, points, values)
    gate = 0.5 if globals().get("threshold") is None else float(threshold)

    # ---- the bundle -------------------------------------------------------
    around = neighbours.around(workspace, beam_id, frame)
    document = Rhino.RhinoDoc.ActiveDoc
    bundle = context.setup(workspace, beam_id, mem, gate,
                           float(document.ModelAbsoluteTolerance),
                           str(document.ModelUnitSystem), around=around)
    bundle["member"]["frame"] = frame        # carried, so 03 need not re-derive it
    setup_json = json.dumps(bundle, indent=2, ensure_ascii=False)
    damage_grid = bundle["damage"]["grid"]

    steps = (bundle.get("plan") or {}).get("stepsForThisMember") or []
    report += [
        "selected: %s (%s)" % (part.get("label") or beam_id, beam_id),
        "grid %dx%dx%d = %d cells, %d at or above %.2f"
        % (mem["grid"] + (len(points), bundle["damage"]["cellsAtOrAbove"], gate)),
        "plan: %s, %d step(s) touch this member"
        % ((bundle.get("plan") or {}).get("label") or "none", len(steps)),
    ]
    report += ["   %s  %s" % (s.get("id"), s.get("title")) for s in steps]
    report.append("%d part(s) touch this member" % len(around))
    report += ["   " + line for line in neighbours.summary(around)]
    if not around:
        report += ["   " + line for line
                   in neighbours.why_empty(workspace, beam_id, frame)]
    undeclared = [item["id"] for item in around if not item["declared"]]
    if undeclared:
        report.append("WARNING: touching but not listed as connections: %s"
                      % ", ".join(undeclared))
except Exception as exc:
    report.append("ERROR: {}".format(exc))
