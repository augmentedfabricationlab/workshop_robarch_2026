"""GH Python 3 -- REPAIR (human-driven v1): place a catalogue joint on a beam.

The beam is a Rhino BOX; its longest axis becomes the beam axis. The joint is
selected by key (Value List), positioned by slider, rotated in degrees, and
`side` says which end becomes the prosthesis.

Inputs:
    box             (Box)    the beam
    key             (str)    joint key from the catalogue (see info for keys)
    position        (float)  NORMALIZED cut position: 0.0 = beam start,
                             1.0 = beam end (independent of beam length)
    rotation        (float)  degrees about the beam axis
    side            (int)    +1 = far end is replaced, -1 = near end
    interface_scale (float)  [1.0]
    run             (bool)
Outputs:
    kept, prosthesis, cut_plane, info
    cut_lines   the scribe lines: where the cut surfaces meet the beam's
                skin -- the layout a carpenter (or marking robot) draws
"""
# r: roslibpy

import sys, os

REPO = r"C:\Users\avishek\workspace\projects\workshop_robarch_2026"
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
cut_lines = DataTree[object]()
cut_plane = None
info = ["version: {}".format(VERSION)]

def _box_frame(b):
    """Box -> frame dict; longest axis = beam axis v; right-handed."""
    pl = b.Plane
    ax = [np.array([pl.XAxis.X, pl.XAxis.Y, pl.XAxis.Z]),
          np.array([pl.YAxis.X, pl.YAxis.Y, pl.YAxis.Z]),
          np.array([pl.ZAxis.X, pl.ZAxis.Y, pl.ZAxis.Z])]
    ext = [b.X.Length, b.Y.Length, b.Z.Length]
    iv = int(np.argmax(ext))
    iu = (iv + 1) % 3
    iw = (iv + 2) % 3
    U, V = ax[iu], ax[iv]
    W = np.cross(U, V)
    W /= np.linalg.norm(W)
    # origin: the box corner from which +U, +V, +W all point inward
    corner = b.PointAt(0, 0, 0)
    o = np.array([corner.X, corner.Y, corner.Z])
    ivals = [b.X, b.Y, b.Z]
    o = (np.array([b.Plane.Origin.X, b.Plane.Origin.Y, b.Plane.Origin.Z])
         + ivals[0].Min * ax[0] + ivals[1].Min * ax[1] + ivals[2].Min * ax[2])
    # if W is opposite the box's own third axis, shift origin to the far
    # w-corner so the frame stays on the box with right-handed axes
    if float(W @ ax[iw]) < 0:
        o = o + ext[iw] * ax[iw]
    return {"origin": o.tolist(), "u": U.tolist(), "v": V.tolist(),
            "w": W.tolist(), "width": float(ext[iu]),
            "height": float(ext[iw]), "length": float(ext[iv])}

if run and box is not None and key:
    try:
        j = joints.load_joint(REPO, str(key))
        frame = _box_frame(box)
        pos_n = float(position) if position is not None else 0.5
        r = kernel.build_repair(
            j, frame,
            position=pos_n * frame["length"],
            rotate_deg=float(rotation) if rotation else 0.0,
            side=int(side) if side else +1,
            interface_scale=float(interface_scale) if interface_scale else 1.0)

        info.append("joint: {}   interface: {:.1f} cm".format(
            key, r["interface_length"] * 100))
        info.append("cut @ {:.2f} (= {:.3f} m)   band {:.2f}..{:.2f} m   "
                    "side: {}".format(
            r["position_used"] / frame["length"], r["position_used"],
            r["band"][0], r["band"][1],
            "near (flipped)" if r["flipped"] else "far"))

        o, V = frame["origin"], frame["v"]
        pos = r["position_used"]
        cpt = rg.Point3d(o[0] + pos * V[0], o[1] + pos * V[1], o[2] + pos * V[2])
        vn = rg.Vector3d(V[0], V[1], V[2]); vn.Unitize()
        cut_plane = rg.Plane(cpt, vn)

        for part in r["parts"]:
            try:
                bs = evaluator.evaluate_part(part)
            except Exception as exc:
                info.append("WARNING: {} failed: {}".format(part["name"], exc))
                bs = []
            (kept if part["name"] == "kept" else prosthesis).AddRange(bs, GH_Path(0))
            vol = 0.0
            for b in bs:
                vp = rg.VolumeMassProperties.Compute(b)
                if vp:
                    vol += vp.Volume
            info.append("{}: {} brep(s)  vol={:.2f} L".format(
                part["name"], len(bs), vol * 1000))

        # scribe lines: edges of the kept piece lying ON the beam skin,
        # excluding the beam's own arrises -- the in-situ marking layout
        try:
            skin = box.ToBrep()
            arris = [e.DuplicateCurve() for e in skin.Edges]
            def _on_skin(p):
                ok, cp, ci, u, v, mx = False, None, None, 0, 0, None
                cp = skin.ClosestPoint(p)
                return cp.DistanceTo(p) < 1e-6
            def _on_arris(p):
                for c in arris:
                    okc, t = c.ClosestPoint(p)
                    if okc and c.PointAt(t).DistanceTo(p) < 1e-6:
                        return True
                return False
            raw = []
            for b_ in kept.AllData():
                for e in b_.Edges:
                    pts = [e.PointAt(e.Domain.ParameterAt(f))
                           for f in (0.25, 0.5, 0.75)]
                    if all(_on_skin(p) for p in pts) and \
                       not all(_on_arris(p) for p in pts):
                        raw.append(e.DuplicateCurve())
            joined = rg.Curve.JoinCurves(raw, 1e-6) if raw else []
            cut_lines.AddRange(list(joined), GH_Path(0))
            info.append("scribe lines: {} curve(s)".format(len(list(joined))))
        except Exception as exc:
            info.append("scribe lines failed: {}".format(exc))
    except Exception as exc:
        info.append("ERROR: {}".format(exc))
else:
    info.append("catalogue: {}".format(", ".join(joints.list_keys(REPO)) or "(empty)"))
    info.append("connect box + key, set run")
