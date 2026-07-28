"""GH Python 3 -- ILLUSTRATE: render repair pieces like the joint catalogue.

Snapshot illustrator (view-dependent, like Make2D): computes visible edges,
dashed hidden edges and procedural wood grain for the ACTIVE viewport's
camera. Re-toggle `run` after changing the view.

Grain by face type (relative to the beam axis):
    end grain  (face ~perpendicular to axis) -> off-center growth rings
    long grain (face ~parallel to axis)      -> flowing lines, light waviness
    cut faces  (oblique, e.g. scarf cheeks)  -> dense straight hatch
Grain is occlusion-tested in a second pass, so it disappears behind solids.

Inputs:
    pieces   (Brep, list)   kept + prosthesis (and anything else)
    axis     (Vector)       beam axis; empty -> longest bbox direction
    density  (float) [1.0]  grain line density multiplier
    flatten  (bool)  [False] project the drawing onto the camera plane
    run      (bool)
Outputs:
    visible  solid edge curves        (preview black, print width ~0.4)
    hidden   dashed hidden-edge bits  (preview black/gray, width ~0.15)
    grain    grain curves             (preview black, width ~0.1)
    report
"""
import sys, os, math, random

REPO = r"C:\Users\tizian\workspace\projects\workshop_robarch_2026"
SRC = os.path.join(REPO, "src")
if SRC not in sys.path:
    sys.path.append(SRC)

import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc
from Grasshopper import DataTree
from Grasshopper.Kernel.Data import GH_Path

visible = DataTree[object]()
hidden = DataTree[object]()
grain = DataTree[object]()
report = []

VIS = rg.HiddenLineDrawingSegment.Visibility


def _dash(crv, dash, gap):
    out = []
    L = crv.GetLength()
    s = 0.0
    while s < L:
        e = min(s + dash, L)
        ok0, t0 = crv.LengthParameter(s)
        ok1, t1 = crv.LengthParameter(e)
        if ok0 and ok1:
            piece = crv.Trim(t0, t1)
            if piece:
                out.append(piece)
        s = e + gap
    return out


def _face_plane(face):
    ok, plane = face.TryGetPlane(0.001)
    return plane if ok else None


def _face_boundary_pts(face):
    pts = []
    for loop in face.Loops:
        c = loop.To3dCurve()
        if c:
            div = c.DivideByCount(64, True)
            if div:
                pts += [c.PointAt(t) for t in div]
    return pts


def _clip_to_face(face, pts3d, step_keep=2):
    """Sample a polyline; keep maximal runs whose points lie on the face."""
    runs, cur = [], []
    for p in pts3d:
        ok, u, v = face.ClosestPoint(p)
        on = False
        if ok:
            q = face.PointAt(u, v)
            if q.DistanceTo(p) < 1e-6 + 1e-4:
                rel = face.IsPointOnFace(u, v)
                on = rel != rg.PointFaceRelation.Exterior
        if on:
            cur.append(p)
        else:
            if len(cur) > step_keep:
                runs.append(cur)
            cur = []
    if len(cur) > step_keep:
        runs.append(cur)
    return [rg.PolylineCurve(r) for r in runs]


def _grain_for_face(face, axis, dens, seed, diag):
    plane = _face_plane(face)
    if plane is None:
        return []
    n = plane.Normal
    n.Unitize()
    a = rg.Vector3d(axis)
    a.Unitize()
    dot = abs(n * a)

    bpts = _face_boundary_pts(face)
    if not bpts:
        return []
    us = [(p - plane.Origin) * plane.XAxis for p in bpts]
    vs = [(p - plane.Origin) * plane.YAxis for p in bpts]
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    w, h = u1 - u0, v1 - v0
    if w < 1e-9 or h < 1e-9:
        return []
    rnd = random.Random(seed)
    P = lambda uu, vv: plane.Origin + plane.XAxis * uu + plane.YAxis * vv
    curves = []
    step = max(w, h) / 160.0

    if dot > 0.85:
        # END GRAIN: off-center growth rings
        cu = u0 + w * (0.25 + 0.5 * rnd.random())
        cv = v0 + h * (0.25 + 0.5 * rnd.random())
        squash = 0.75 + 0.2 * rnd.random()
        rmax = math.hypot(max(u1 - cu, cu - u0), max(v1 - cv, cv - v0)) * 1.2
        ring = min(w, h) / (7.0 * dens)
        r = ring * (0.4 + 0.4 * rnd.random())
        while r < rmax:
            pts = []
            for i in range(97):
                t = 2 * math.pi * i / 96.0
                rr = r * (1.0 + 0.05 * math.sin(3 * t + rnd.random() * 6))
                pts.append(P(cu + rr * math.cos(t),
                             cv + rr * math.sin(t) * squash))
            curves += _clip_to_face(face, pts)
            r += ring * (0.75 + 0.5 * rnd.random())
    else:
        # direction of grain on the face: projected beam axis
        d = a - n * (a * n)
        if d.Length < 1e-9:
            d = plane.XAxis
        d.Unitize()
        du = d * plane.XAxis
        dv = d * plane.YAxis
        # perpendicular in-plane
        pu, pv = -dv, du
        span = abs(du) * w + abs(dv) * h
        if dot < 0.15:
            n_lines = max(3, int(min(w, h) / max(w, h) * 10 * dens) + 4)
            n_lines = max(3, int(4 + 4 * dens))
            amp = min(w, h) * 0.03
            wav = 2.0
        else:
            # OBLIQUE CUT FACE: dense, straight hatch
            n_lines = int(10 * dens) + 8
            amp = min(w, h) * 0.006
            wav = 1.0
        for i in range(n_lines):
            f = (i + 0.5) / n_lines + rnd.uniform(-0.25, 0.25) / n_lines
            # anchor point across the perpendicular
            au = (u0 + u1) / 2 + pu * (f - 0.5) * (abs(pu) * w + abs(pv) * h)
            av = (v0 + v1) / 2 + pv * (f - 0.5) * (abs(pu) * w + abs(pv) * h)
            phase = rnd.random() * 6.28
            pts = []
            m = int(span / step) + 2
            for k in range(-m, m + 1):
                s = k * step
                off = amp * math.sin(wav * 6.28 * s / max(span, 1e-9) + phase)
                pts.append(P(au + du * s + pu * off, av + dv * s + pv * off))
            curves += _clip_to_face(face, pts)
    return curves


if run and pieces:
    try:
        breps = pieces if isinstance(pieces, list) else [pieces]
        breps = [b for b in breps if b is not None]
        bb = rg.BoundingBox.Empty
        for b in breps:
            bb.Union(b.GetBoundingBox(True))
        diag = bb.Diagonal.Length

        ax = axis
        if ax is None or (hasattr(ax, "Length") and ax.Length < 1e-9):
            d = bb.Diagonal
            ax = rg.Vector3d(1, 0, 0)
            best = -1
            for cand in (rg.Vector3d(1, 0, 0), rg.Vector3d(0, 1, 0),
                         rg.Vector3d(0, 0, 1)):
                ext = abs(d * cand)
                if ext > best:
                    best, ax = ext, cand

        view = Rhino.RhinoDoc.ActiveDoc.Views.ActiveView
        vp = view.ActiveViewport
        cam_dir = vp.CameraDirection

        # ---- pass 1: solid edges ----
        p1 = rg.HiddenLineDrawingParameters()
        p1.AbsoluteTolerance = 0.001
        p1.IncludeHiddenCurves = True
        p1.IncludeTangentEdges = False
        p1.SetViewport(vp)
        for b in breps:
            p1.AddGeometry(b, "S")
        hld1 = rg.HiddenLineDrawing.Compute(p1, True)
        if hld1 is None:
            raise ValueError("hidden line computation failed")
        dash = diag / 90.0
        vis_crvs, hid_crvs = [], []
        for seg in hld1.Segments:
            c = seg.CurveGeometry
            if c is None:
                continue
            c = c.DuplicateCurve()
            if seg.SegmentVisibility == VIS.Visible:
                vis_crvs.append(c)
            elif seg.SegmentVisibility == VIS.Hidden:
                hid_crvs += _dash(c, dash, dash * 0.7)

        # ---- grain on camera-facing planar faces ----
        grain_raw = []
        for bi, b in enumerate(breps):
            for fi, f in enumerate(b.Faces):
                plane = _face_plane(f)
                if plane is None:
                    continue
                nrm = plane.Normal
                if f.OrientationIsReversed:
                    nrm = -nrm
                if nrm * cam_dir > -0.05:   # not facing the camera
                    continue
                seed = hash((bi, fi, round(plane.Origin.X, 4),
                             round(plane.Origin.Y, 4))) & 0xFFFF
                grain_raw += _grain_for_face(f, ax, float(density or 1.0),
                                             seed, diag)

        # ---- pass 2: occlusion-test the grain ----
        grain_crvs = list(grain_raw)
        try:
            p2 = rg.HiddenLineDrawingParameters()
            p2.AbsoluteTolerance = 0.001
            p2.IncludeHiddenCurves = False
            p2.IncludeTangentEdges = False
            p2.SetViewport(vp)
            for b in breps:
                p2.AddGeometry(b, "S")
            for g in grain_raw:
                p2.AddGeometry(g, "G")
            hld2 = rg.HiddenLineDrawing.Compute(p2, True)
            if hld2:
                grain_crvs = []
                for seg in hld2.Segments:
                    if seg.SegmentVisibility != VIS.Visible:
                        continue
                    pc = seg.ParentCurve
                    src = pc.SourceObject if pc else None
                    if src is not None and src.Tag == "G":
                        c = seg.CurveGeometry
                        if c:
                            grain_crvs.append(c.DuplicateCurve())
        except Exception as exc:
            report.append("grain occlusion pass skipped: {}".format(exc))

        # ---- optional flatten onto the camera plane ----
        if flatten:
            ok, frame = vp.GetCameraFrame()
            proj = rg.Transform.PlanarProjection(frame if ok else rg.Plane.WorldXY)
            for lst in (vis_crvs, hid_crvs, grain_crvs):
                for c in lst:
                    c.Transform(proj)

        visible.AddRange(vis_crvs, GH_Path(0))
        hidden.AddRange(hid_crvs, GH_Path(0))
        grain.AddRange(grain_crvs, GH_Path(0))
        report.append("visible {} / hidden {} / grain {} curves".format(
            len(vis_crvs), len(hid_crvs), len(grain_crvs)))
        report.append("axis ({:.2f} {:.2f} {:.2f})  view: {}".format(
            ax.X, ax.Y, ax.Z, view.MainViewport.Name))
    except Exception as exc:
        report.append("ERROR: {}".format(exc))
else:
    report.append("wire kept + prosthesis into `pieces`, set run")
