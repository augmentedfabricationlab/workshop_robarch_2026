"""GH Python 3 -- EDGES: brep edges lifted off the solid, catalogue-style.

Extracts every edge of the pieces and offsets it outward along the bisector
of its adjacent faces' normals, orientation verified EMPIRICALLY per edge
(probe point + Brep.IsPointInside), same as the grain component. Feed the
result into Custom Preview Lineweights (black, thickness 3-4) for the thick
book edges -- fully visible, never swallowed by the white shading.

Inputs:
    pieces  (Brep, list)
    lift    (float) [auto = 0.0008 x bbox diagonal, matching the grain]
    run     (bool)
Outputs:
    edges   lifted edge curves, one branch per piece
    report
"""
import Rhino.Geometry as rg
from Grasshopper import DataTree
from Grasshopper.Kernel.Data import GH_Path

EDGES_V = "1"
edges = DataTree[object]()
report = []

if run and pieces:
    try:
        breps = pieces if isinstance(pieces, list) else [pieces]
        breps = [b for b in breps if b is not None]
        bb = rg.BoundingBox.Empty
        for b in breps:
            bb.Union(b.GetBoundingBox(True))
        diag = bb.Diagonal.Length
        lift_v = float(lift) if lift else diag * 0.0008
        probe_d = max(lift_v * 4.0, diag * 0.002)

        n_edges = 0
        n_flip = 0
        for bi, b in enumerate(breps):
            for e in b.Edges:
                # outward-ish direction: bisector of adjacent face normals
                d = rg.Vector3d(0, 0, 0)
                for fidx in e.AdjacentFaces():
                    f = b.Faces[fidx]
                    um = f.Domain(0).Mid
                    vm = f.Domain(1).Mid
                    n = f.NormalAt(um, vm)
                    if f.OrientationIsReversed:
                        n.Reverse()
                    d += n
                if d.Length < 1e-9:
                    d = rg.Vector3d(0, 0, 1)
                d.Unitize()
                # empirical check at the edge midpoint
                tmid = e.Domain.Mid
                m = e.PointAt(tmid)
                try:
                    if b.IsPointInside(m + d * probe_d, 1e-6, True):
                        d = -d
                        n_flip += 1
                except Exception:
                    pass
                c = e.DuplicateCurve()
                c.Translate(d * lift_v)
                edges.Add(c, GH_Path(bi))
                n_edges += 1
        report.append("edges v{}: {} edge(s) on {} piece(s)  lift {:.4f}  "
                      "({} empirically flipped)".format(
                          EDGES_V, n_edges, len(breps), lift_v, n_flip))
    except Exception as exc:
        report.append("ERROR: {}".format(exc))
else:
    report.append("wire kept + prosthesis into `pieces`, set run")
