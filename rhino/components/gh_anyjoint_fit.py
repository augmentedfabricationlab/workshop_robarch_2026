"""GH Python 3 -- ANYJOINT: fit generated six-plane joints to damaged cells.

Connect this directly after the BEAM CELLS component supplied with the
workshop.  The deterministic search generates plane-joint variants, fits them
to the cellular damage field, rejects every candidate that leaves a mandatory
damage cell, and evaluates only the shortlisted results as Rhino Breps.

Inputs:
    box              (Box)          oriented box from BEAM CELLS
    centers          (list Point3d) world cell centroids from BEAM CELLS
    damage           (list float)   one 0..1 score per centre
    threshold        (float)        mandatory-removal threshold [0.50]
    top_n            (int)          candidates to build as Breps [4]
    n_positions      (int)          axial samples per grammar variant [7]
    margin           (float)        axial margin in beam sections [1.0]
    allow_chevron    (bool)         search square through chevron [True]
    allow_undercut   (bool)         search parallel/opposed rakes [True]
    allow_scarf      (bool)         include generated scarf slopes [True]
    complexity_weight (float)       effort penalty in ranking [0.002]
    repo             (str)          optional repository override
    run              (bool)

Outputs:
    kept             tree {candidate}: retained beam Breps
    prosthesis       tree {candidate}: replacement Breps
    plane_rectangles tree {candidate}: active cutting-plane graphics
    plane_arrows     tree {candidate}: half-space polarity arrows
    removed_cells    tree {candidate}: cell centres removed by the candidate
    candidate_ids    ordered candidate identifiers
    metrics          one readable line per candidate
    report           validation and search diagnostics

Scope of this first component:
    * splice/lap, chevron, undercut/miter and scarf plane grammars;
    * hard coverage and continuous sound-loss ranking;
    * exact final Rhino Breps for the shortlist.

Tool reach, neighbour collisions, full-cell coverage, cutting sequences and
the LLM/Workspace constraint compiler are later validation layers.  Their
absence is reported rather than silently treated as a pass.
"""

import os
import sys


def _repo_from_component():
    override = globals().get("repo") or os.environ.get("ROBARCH_REPO")
    component_file = globals().get("_p") or globals().get("__file__")
    candidates = []
    if override:
        candidates.append(os.path.abspath(os.path.expanduser(str(override))))
    if component_file:
        candidates.append(
            os.path.abspath(os.path.join(os.path.dirname(component_file), "..", ".."))
        )
    candidates.append(r"C:\Users\tizian\workspace\projects\workshop_robarch_2026")
    for root in candidates:
        package = os.path.join(root, "src", "workshop_robarch_2026")
        if os.path.isdir(package):
            return root
    raise RuntimeError(
        "repository package not found; connect the repo input or set ROBARCH_REPO"
    )


REPO = _repo_from_component()
SRC = os.path.join(REPO, "src")
if SRC not in sys.path:
    sys.path.append(SRC)
for module_name in list(sys.modules):
    if module_name.startswith("workshop_robarch_2026"):
        sys.modules.pop(module_name)

import numpy as np
import Rhino.Geometry as rg
from Grasshopper import DataTree
from Grasshopper.Kernel.Data import GH_Path

from workshop_robarch_2026 import anyjoint, evaluator, kernel, scoring


kept = DataTree[object]()
prosthesis = DataTree[object]()
plane_rectangles = DataTree[object]()
plane_arrows = DataTree[object]()
removed_cells = DataTree[object]()
candidate_ids = []
metrics = []
report = ["AnyJoint deterministic fitter"]


def _box_frame(b):
    """Centred Rhino Box -> corner-origin frame used by the repair kernel."""
    pl = b.Plane
    axes = [
        np.array([pl.XAxis.X, pl.XAxis.Y, pl.XAxis.Z], float),
        np.array([pl.YAxis.X, pl.YAxis.Y, pl.YAxis.Z], float),
        np.array([pl.ZAxis.X, pl.ZAxis.Y, pl.ZAxis.Z], float),
    ]
    extents = [float(b.X.Length), float(b.Y.Length), float(b.Z.Length)]
    iv = int(np.argmax(extents))
    iu, iw = (iv + 1) % 3, (iv + 2) % 3
    U, V = axes[iu], axes[iv]
    W = np.cross(U, V)
    W /= np.linalg.norm(W)
    intervals = [b.X, b.Y, b.Z]
    origin = (
        np.array([pl.Origin.X, pl.Origin.Y, pl.Origin.Z], float)
        + intervals[0].Min * axes[0]
        + intervals[1].Min * axes[1]
        + intervals[2].Min * axes[2]
    )
    if float(W @ axes[iw]) < 0:
        origin = origin + extents[iw] * axes[iw]
    return {
        "origin": origin.tolist(),
        "u": U.tolist(),
        "v": V.tolist(),
        "w": W.tolist(),
        "width": extents[iu],
        "length": extents[iv],
        "height": extents[iw],
    }


def _xyz(point):
    if hasattr(point, "X"):
        return [float(point.X), float(point.Y), float(point.Z)]
    return [float(point[0]), float(point[1]), float(point[2])]


def _plane_graphics(cut_json, size):
    cut = kernel.Cut.from_json(cut_json)
    origin = cut.origin()
    normal = np.asarray(cut.normal, float)
    normal /= np.linalg.norm(normal)
    point = rg.Point3d(float(origin[0]), float(origin[1]), float(origin[2]))
    vector = rg.Vector3d(float(normal[0]), float(normal[1]), float(normal[2]))
    plane = rg.Plane(point, vector)
    rectangle = rg.Rectangle3d(
        plane, rg.Interval(-size, size), rg.Interval(-size, size)
    ).ToNurbsCurve()
    arrow = rg.LineCurve(point, point + float(0.35 * size) * vector)
    return rectangle, arrow


if bool(globals().get("run")):
    try:
        if globals().get("box") is None:
            raise ValueError("connect the oriented box from BEAM CELLS")
        raw_centres = list(globals().get("centers") or [])
        raw_damage = list(globals().get("damage") or [])
        if not raw_centres:
            raise ValueError("connect cell centres from BEAM CELLS")
        if len(raw_centres) != len(raw_damage):
            raise ValueError(
                "centres/damage length mismatch: {} vs {}".format(
                    len(raw_centres), len(raw_damage)
                )
            )

        frame = _box_frame(box)
        points = np.asarray([_xyz(point) for point in raw_centres], float)
        values = np.asarray([float(value) for value in raw_damage], float)
        grammar = anyjoint.default_grammar(
            allow_chevron=True if globals().get("allow_chevron") is None else bool(allow_chevron),
            allow_undercut=True if globals().get("allow_undercut") is None else bool(allow_undercut),
            allow_scarf=True if globals().get("allow_scarf") is None else bool(allow_scarf),
        )
        results, search_report = anyjoint.search(
            frame,
            points,
            values,
            threshold=0.5 if globals().get("threshold") is None else float(threshold),
            grammar=grammar,
            n_positions=7 if globals().get("n_positions") is None else int(n_positions),
            margin=1.0 if globals().get("margin") is None else float(margin),
            complexity_weight=(
                0.002
                if globals().get("complexity_weight") is None
                else float(complexity_weight)
            ),
        )
        report.extend(search_report)
        chosen = anyjoint.shortlist(
            results, 4 if globals().get("top_n") is None else int(top_n)
        )

        for rank, result in enumerate(chosen):
            path = GH_Path(rank)
            candidate_ids.append(result["candidate_id"])
            metrics.append(anyjoint.result_summary(result))
            repair = result["repair"]

            for part in repair["parts"]:
                try:
                    breps = evaluator.evaluate_part(part)
                except Exception as exc:
                    report.append(
                        "candidate {} {} BRep failed: {}".format(
                            rank + 1, part["name"], exc
                        )
                    )
                    breps = []
                target = kept if part["name"] == "kept" else prosthesis
                target.AddRange(breps, path)

            predicate_count = int(result["predicate_count"])
            # Every repair part carries the same cut list: stock first,
            # generated predicates next, axial trim last.
            cut_jsons = repair["parts"][0]["cuts"][1 : 1 + predicate_count]
            display_size = 0.75 * max(frame["width"], frame["height"])
            for cut_json in cut_jsons:
                rectangle, arrow = _plane_graphics(cut_json, display_size)
                plane_rectangles.Add(rectangle, path)
                plane_arrows.Add(arrow, path)

            removed = scoring.removed_mask(points, repair)
            removed_cells.AddRange(
                [raw_centres[i] for i in np.flatnonzero(removed)], path
            )

        if not chosen:
            report.append("Try a lower threshold, larger margin/search window, or inspect frame alignment.")
        report.append(
            "prototype validation: damage-centroid coverage PASS for every shown candidate"
        )
        report.append(
            "pending validators: whole-cell coverage, structure, tool reach, collisions, assembly"
        )
    except Exception as exc:
        report.append("ERROR: {}".format(exc))
else:
    report.append("connect box + centers + damage, then set run=True")
