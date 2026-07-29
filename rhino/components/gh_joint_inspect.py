"""GH Python 3 -- JOINT INSPECT: load a catalogue joint and look inside it.

Shows everything a joint entry contains, in canonical space at the origin:
the stock, each cutter as a transparent solid, the resulting kept/prosthesis
partition, the acceptance-test report, and the datasheet text.

Inputs:
    key     (str)   joint key, e.g. "SW1" (empty -> report lists the catalogue)
    run     (bool)
Outputs:
    stock       canonical stock Brep (preview as wireframe/ghosted)
    curves      the authoring curves: one branch per cutter, the closed planar
                profile(s) each cutter was defined with (round-trip exact, up
                to winding direction -- profiles are stored CCW)
    cutters     one branch per cutter -- preview transparent to see the planes
    kept        kept piece of the canonical partition
    prosthesis  prosthesis piece
    report      acceptance test, per-cut listing, datasheet
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

import Rhino.Geometry as rg
from Grasshopper import DataTree
from Grasshopper.Kernel.Data import GH_Path

stock = None
curves = DataTree[object]()
cutters = DataTree[object]()
kept = DataTree[object]()
prosthesis = DataTree[object]()
report = ["version: {}".format(VERSION)]

if run and key:
    try:
        j = joints.load_joint(REPO, str(key))
        aspect = float(j["aspect"])
        report.append("joint: {}   aspect: {}   cutters: {}".format(
            j["key"], aspect, len(j["cuts"])))

        # acceptance test
        chk = joints.check_joint(j)
        report.append("partition_ok: {}   orientation_ok: {}   end_overshoot_ok: {}".format(
            chk["partition_ok"], chk.get("orientation_ok"), chk.get("end_overshoot_ok")))
        report.append("kept {:.0%} / prosthesis {:.0%}".format(
            chk["kept_fraction"], chk["prosthesis_fraction"]))

        # per-cut listing
        for c in j["cuts"]:
            n = c["normal"]
            report.append("  {}: normal ({:+.2f} {:+.2f} {:+.2f})  depth {:.2f}  "
                          "{} polyset(s)".format(c["name"], n[0], n[1], n[2],
                                                 c["amount"], len(c["polysets"])))

        # geometry: stock, cutter solids, partition
        stock_cut = kernel.canonical_stock(aspect, float(j.get("section", 1.0)))
        stock_breps = evaluator.lhf_breps(stock_cut.to_json())
        stock = stock_breps[0] if stock_breps else None

        named = [kernel.Cut.from_json(c) for c in j["cuts"]]
        for i, c in enumerate(named):
            c.name = "lhf_%d" % (i + 1)
            cj_one = c.to_json()
            for ps in cj_one["polysets"]:
                pts = [rg.Point3d(*p) for p in
                       evaluator.profile_world_points(cj_one, ps)]
                if pts[0].DistanceTo(pts[-1]) > 1e-12:
                    pts.append(pts[0])
                curves.Add(rg.PolylineCurve(pts), GH_Path(i))
            try:
                for b in evaluator.lhf_breps(cj_one):
                    cutters.Add(b, GH_Path(i))
            except Exception as exc:
                report.append("  {} FAILED to build: {}".format(c.name, exc))

        all_cuts = [stock_cut] + named
        names = ", ".join(c.name for c in named)
        cj = [c.to_json() for c in all_cuts]
        for pname, expr, tree in (
                ("kept", "Difference(lhf_0, Union(%s))" % names, kept),
                ("prosthesis", "Intersection(lhf_0, Union(%s))" % names, prosthesis)):
            try:
                bs = evaluator.evaluate_part({"cuts": cj, "expression": expr})
                tree.AddRange(bs, GH_Path(0))
            except Exception as exc:
                report.append("{} evaluation FAILED: {}".format(pname, exc))

        # datasheet
        md = joints.load_datasheet(REPO, str(key))
        if md.strip():
            report.append("--- datasheet ---")
            report += md.strip().splitlines()
        else:
            report.append("(no datasheet -- create {}.md)".format(key))
    except Exception as exc:
        report.append("ERROR: {}".format(exc))
else:
    report.append("catalogue: {}".format(", ".join(joints.list_keys(REPO)) or "(empty)"))
    report.append("set key + run to inspect a joint")
