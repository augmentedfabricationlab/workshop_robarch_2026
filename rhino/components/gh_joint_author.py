"""GH Python 3 -- JOINT AUTHOR: turn Rhino curves into a catalogue joint.

Model your joint in CANONICAL SPACE at the world origin:
    stock section 1 x 1:  x, z in [-0.5, +0.5]
    stock length along y: 0 to `aspect` (e.g. 3.0)
Draw one CLOSED PLANAR curve per cutter. Cutters remove the PROSTHESIS-side
material of the interface. Every cutter must overshoot the stock in x/z --
never share a face with the stock sides (the tangency lesson).

Inputs:
    cutters (Curve, list)  closed planar curves, one per cutter
    depths  (float, list)  extrusion depth per cutter, along each curve's
                           plane normal; negative = extrude the other way
    key     (str)          e.g. "SW2"
    aspect  (float)        interface length in section units (e.g. 3.0)
    save    (bool)         write data/corpus/joints/<key>.json (+ .md skeleton)
Outputs:
    kept, prosthesis       canonical partition preview (validation by eye)
    report                 acceptance test + per-cutter diagnostics
"""
import sys, os

REPO = r"C:\Users\tizian\workspace\projects\workshop_robarch_2026"
SRC = os.path.join(REPO, "src")
if SRC not in sys.path:
    sys.path.append(SRC)
for m in list(sys.modules):
    if m.startswith("workshop_robarch_2026"):
        sys.modules.pop(m)

from workshop_robarch_2026 import kernel, joints, evaluator
from workshop_robarch_2026.version import VERSION

import numpy as np
import Rhino.Geometry as rg
from Grasshopper import DataTree
from Grasshopper.Kernel.Data import GH_Path

kept = DataTree[object]()
prosthesis = DataTree[object]()
report = ["version: {}".format(VERSION)]

def _curve_to_cut(crv, depth, name):
    ok, plane = crv.TryGetPlane(0.001)
    if not ok:
        raise ValueError("{}: curve is not planar".format(name))
    n = np.array([plane.Normal.X, plane.Normal.Y, plane.Normal.Z], float)
    amount = float(depth)
    if amount < 0:
        n, amount = -n, -amount
    ok, poly = crv.TryGetPolyline()
    if not ok:
        params = crv.DivideByCount(64, True)
        pts = [crv.PointAt(t) for t in params]
    else:
        pts = list(poly)
        if len(pts) > 1 and pts[0].DistanceTo(pts[-1]) < 1e-9:
            pts = pts[:-1]
    P = np.array([[p.X, p.Y, p.Z] for p in pts], float)
    u2, v2, nn = kernel.frame_from_normal(n)
    P0 = P.mean(axis=0)
    offset = float(P0 @ nn)
    in_plane = [float(P0 @ u2), float(P0 @ v2)]
    prof = np.array([[(p - P0) @ u2, (p - P0) @ v2] for p in P])
    return kernel.Cut(name, False, nn, offset, in_plane, amount, [prof])

if cutters and key:
    try:
        crvs = cutters if isinstance(cutters, list) else [cutters]
        dpts = depths if isinstance(depths, list) else [depths]
        if len(dpts) == 1 and len(crvs) > 1:
            dpts = dpts * len(crvs)
        cuts = [_curve_to_cut(c, d, "lhf_%d" % (i + 1))
                for i, (c, d) in enumerate(zip(crvs, dpts))]

        asp = float(aspect) if aspect else 3.0
        j = {"schema": kernel.SCHEMA, "key": str(key), "aspect": asp,
             "section": 1.0, "cuts": [c.to_json() for c in cuts]}

        # headless acceptance
        chk = joints.check_joint(j)
        report.append("partition_ok: {}   kept {:.0%} / prosthesis {:.0%}".format(
            chk["partition_ok"], chk["kept_fraction"], chk["prosthesis_fraction"]))
        if chk["overlap_points"] or chk["uncovered_points"]:
            report.append("PROBLEM: overlap {} pts, uncovered {} pts".format(
                chk["overlap_points"], chk["uncovered_points"]))
        if not chk["has_both_sides"]:
            report.append("PROBLEM: cutters produce no interface (one side empty)")
        if not chk.get("end_overshoot_ok", True):
            report.append("PROBLEM: a cutter ends exactly at y=aspect -- extend it")
            report.append("  past the prosthesis end (overshoot along y too)")
        if not chk.get("orientation_ok", True):
            report.append("PROBLEM: orientation inverted -- cutters must remove")
            report.append("  more material toward y=aspect (the prosthesis end)")

        # canonical partition preview via the real evaluator
        stock = kernel.canonical_stock(asp)
        named = [kernel.Cut.from_json(c) for c in j["cuts"]]
        for i, c in enumerate(named):
            c.name = "lhf_%d" % (i + 1)
        all_cuts = [stock] + named
        names = ", ".join(c.name for c in named)
        cj = [c.to_json() for c in all_cuts]
        for pname, expr, tree in (
            ("kept", "Difference(lhf_0, Union(%s))" % names, kept),
            ("prosthesis", "Intersection(lhf_0, Union(%s))" % names, prosthesis)):
            try:
                bs = evaluator.evaluate_part({"cuts": cj, "expression": expr})
                tree.AddRange(bs, GH_Path(0))
                report.append("{}: {} brep(s)".format(pname, len(bs)))
            except Exception as exc:
                report.append("{} FAILED: {}".format(pname, exc))
        report += evaluator.diagnose_cuts({"cuts": cj, "expression": ""})

        if save and chk["partition_ok"] and chk["has_both_sides"] and chk.get("orientation_ok", True) and chk.get("end_overshoot_ok", True):
            path = joints.save_joint(REPO, str(key), asp, cuts)
            report.append("SAVED: {}".format(path))
            report.append("datasheet: fill in {}.md next to it".format(key))
        elif save:
            report.append("NOT saved -- fix the problems above first")
    except Exception as exc:
        report.append("ERROR: {}".format(exc))
else:
    report.append("connect cutter curves + key, set save when the preview is right")
    report.append("catalogue: {}".format(", ".join(joints.list_keys(REPO)) or "(empty)"))
