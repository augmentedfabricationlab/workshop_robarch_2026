"""GH Python 3 -- GRAIN v3: 3D wood-grain curves for the catalogue look.

Live companion to the "Katalog" display mode (white shading, black edges,
dashed hidden lines): generates real 3D grain curves ON the faces of the
pieces, lifted a hair off the surface. Solids occlude their own back-face
grain naturally -- nothing is view-dependent.

Grain by face type (angle between face normal and beam axis):
    |dot| >  end_threshold [0.15] -> growth-ring CYLINDERS around the piece's
        pith line, intersected with the face plane: circles on square cuts,
        stretched ovals on miters and scarf cheeks (anatomically continuous)
    |dot| <= end_threshold        -> longitudinal flowing lines

v3: clipping fully vectorized in 2D face space (numpy) -- no per-point
RhinoCommon calls; expect ~5-10x faster than v2.

Inputs:
    pieces        (Brep, list)
    axis          (Vector)  beam axis; empty -> longest bbox direction
    density       (float) [1.0]
    lift          (float) [auto = 0.0004 x bbox diagonal]
    end_threshold (float) [0.85] faces with |dot| above this get rings
    run           (bool)
Outputs:
    grain, report
"""
import sys, os, math, random

def _repo_from_component():
    """Find the repository containing the component loaded by Grasshopper."""
    override = os.environ.get("ROBARCH_REPO")
    component_file = globals().get("_p") or globals().get("__file__")
    if override:
        repo = os.path.abspath(os.path.expanduser(override))
    elif component_file:
        repo = os.path.abspath(
            os.path.join(os.path.dirname(component_file), "..", "..")
        )
    else:
        raise RuntimeError("Cannot locate repo; set the ROBARCH_REPO environment variable")
    package = os.path.join(repo, "src", "workshop_robarch_2026")
    if not os.path.isdir(package):
        raise RuntimeError("Repository package not found at: {}".format(package))
    return repo


REPO = _repo_from_component()
SRC = os.path.join(REPO, "src")
if SRC not in sys.path:
    sys.path.append(SRC)

import numpy as np
import Rhino
import Rhino.Geometry as rg
from Grasshopper import DataTree
from Grasshopper.Kernel.Data import GH_Path

GRAIN_V = "14"
grain = DataTree[object]()
report = []


def _face_plane(face):
    ok, plane = face.TryGetPlane(0.001)
    return plane if ok else None


def _loops_2d(face, plane):
    loops = []
    for lp in face.Loops:
        c = lp.To3dCurve()
        if c is None:
            continue
        div = c.DivideByCount(96, True)
        if not div:
            continue
        pts = [c.PointAt(t) for t in div]
        arr = np.array([[(p - plane.Origin) * plane.XAxis,
                         (p - plane.Origin) * plane.YAxis] for p in pts])
        loops.append(arr)
    return loops


def _segments(loops):
    a, b = [], []
    for L in loops:
        a.append(L)
        b.append(np.roll(L, -1, axis=0))
    return np.vstack(a), np.vstack(b)


def _mask_points(P, seg_a, seg_b):
    if len(P) == 0:
        return np.zeros(0, bool)
    px, py = P[:, 0:1], P[:, 1:2]
    x1, y1 = seg_a[:, 0][None, :], seg_a[:, 1][None, :]
    x2, y2 = seg_b[:, 0][None, :], seg_b[:, 1][None, :]
    cond = (y1 > py) != (y2 > py)
    xin = x1 + (py - y1) * (x2 - x1) / (y2 - y1 + 1e-30)
    return (np.sum(cond & (px < xin), axis=1) % 2) == 1


def _snap_to_boundary(p_out, p_in, seg_a, seg_b):
    """Exact crossing of segment p_out->p_in with the face boundary (2D).
    Returns the crossing closest to the inside point, or None."""
    dd = p_in - p_out
    e = seg_b - seg_a
    w = seg_a - p_out[None, :]
    denom = dd[0] * (-e[:, 1]) - dd[1] * (-e[:, 0])
    denom = np.where(np.abs(denom) < 1e-30, 1e-30, denom)
    t = (w[:, 0] * (-e[:, 1]) - w[:, 1] * (-e[:, 0])) / denom
    u = (dd[0] * w[:, 1] - dd[1] * w[:, 0]) / denom
    ok = (t >= -1e-9) & (t <= 1 + 1e-9) & (u >= -1e-9) & (u <= 1 + 1e-9)
    if not ok.any():
        return None
    tbest = t[ok].max()
    return p_out + np.clip(tbest, 0, 1) * dd


def _face_inner_point(seg_a, seg_b, u0, u1, v0, v1, rnd):
    """A 2D point strictly inside the face polygon (centroid, then random)."""
    cands = [np.array([[(u0 + u1) / 2.0, (v0 + v1) / 2.0]])]
    for _ in range(24):
        cands.append(np.array([[u0 + rnd.random() * (u1 - u0),
                                v0 + rnd.random() * (v1 - v0)]]))
    for c in cands:
        if _mask_points(c, seg_a, seg_b)[0]:
            return c[0]
    return None


def _runs_to_curves(pts2d, mask, plane, lift_vec, seg_a, seg_b, min_pts=2):
    out = []
    if len(pts2d) == 0:
        return out
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return out
    breaks = np.where(np.diff(idx) > 1)[0]
    starts = np.r_[0, breaks + 1]
    ends = np.r_[breaks, len(idx) - 1]
    for s, e in zip(starts, ends):
        run = idx[s:e + 1]
        if len(run) < min_pts:
            continue
        chain = [pts2d[i] for i in run]
        i0, i1 = run[0], run[-1]
        if i0 > 0:  # entered the face: snap start onto the boundary
            q = _snap_to_boundary(pts2d[i0 - 1], pts2d[i0], seg_a, seg_b)
            if q is not None:
                chain.insert(0, q)
        if i1 < len(pts2d) - 1:  # left the face: snap end onto the boundary
            q = _snap_to_boundary(pts2d[i1 + 1], pts2d[i1], seg_a, seg_b)
            if q is not None:
                chain.append(q)
        pts = [plane.Origin + plane.XAxis * float(u) + plane.YAxis * float(v)
               + lift_vec for u, v in chain]
        out.append(rg.PolylineCurve(pts))
    return out


def _rings_conic(plane, pith, a, allpts, du, dv, ring_base, dens, rnd, dot):
    """Growth-ring cylinders around the pith line intersected with the face
    plane. One system for ALL faces: circles (square cuts) -> stretched
    ovals (miters, scarfs) -> pairs of parallel lines (axis-parallel faces,
    i.e. tangential grain), all from the same ring radii."""
    A = np.array([a.X, a.Y, a.Z])
    X = np.array([plane.XAxis.X, plane.XAxis.Y, plane.XAxis.Z])
    Y = np.array([plane.YAxis.X, plane.YAxis.Y, plane.YAxis.Z])
    O = np.array([plane.Origin.X, plane.Origin.Y, plane.Origin.Z])
    P = np.array([pith.X, pith.Y, pith.Z])

    def rej(v):
        return v - (v @ A) * A

    e1, e2 = rej(X), rej(Y)
    c0p = rej(O - P)
    # radii ladder shared by every branch
    def radii():
        r = ring_base / dens * (0.4 + 0.4 * rnd.random())
        while True:
            yield r
            r += ring_base / dens * (0.75 + 0.5 * rnd.random())
    # farthest loop point from the pith line
    rmax = 0.0
    for uu, vv in allpts:
        wp = c0p + uu * e1 + vv * e2
        rmax = max(rmax, float(np.sqrt(wp @ wp)))

    polys = []
    if dot < 0.06:
        # Axis-parallel faces: COVERAGE-driven lines, evenly jittered across
        # the face's true perpendicular extent. (Radius-ladder line pairs
        # starve narrow faces far from the pith -- a tall beam's thin faces
        # got 0-2 lines. The book fills narrow faces evenly; so do we.)
        pu, pv = -dv, du
        proj_s = allpts[:, 0] * du + allpts[:, 1] * dv
        proj_p = allpts[:, 0] * pu + allpts[:, 1] * pv
        s0, s1 = float(proj_s.min()), float(proj_s.max())
        p0, p1 = float(proj_p.min()), float(proj_p.max())
        span, perp = s1 - s0, p1 - p0
        if span < 1e-9 or perp < 1e-9:
            return []
        step = span / 90.0
        svals = np.arange(s0 - step, s1 + 2 * step, step)
        n_lines = max(3, int(4 + 5 * dens))
        amp = min(perp * 0.06, ring_base * 0.2)
        for i in range(n_lines):
            f = (i + 0.5) / n_lines + rnd.uniform(-0.3, 0.3) / n_lines
            pval = p0 + f * perp
            off = amp * np.sin(2 * 6.28 * (svals - s0) / span
                               + rnd.random() * 6.28)
            pp = pval + off
            polys.append(np.column_stack([svals * du + pp * pu,
                                          svals * dv + pp * pv]))
        return polys

    # CONIC: well-conditioned quadratic -> (possibly very long) ellipses
    G = np.array([[e1 @ e1, e1 @ e2], [e1 @ e2, e2 @ e2]])
    bv = 2.0 * np.array([c0p @ e1, c0p @ e2])
    try:
        m = np.linalg.solve(G, -bv / 2.0)
    except Exception:
        return []
    lam, vec = np.linalg.eigh(G)
    if lam[0] < 1e-12:
        return []
    npts = int(np.clip(97 * (lam[1] / lam[0]) ** 0.25, 97, 641))
    t = np.linspace(0, 2 * math.pi, npts)
    cc = float(c0p @ c0p)
    for r in radii():
        if r > rmax + ring_base:
            break
        k = -(float(m @ (G @ m)) + float(bv @ m) + (cc - r * r))
        if k > 1e-14:
            r1, r2 = math.sqrt(k / lam[0]), math.sqrt(k / lam[1])
            wob = 1.0 + 0.04 * np.sin(3 * t + rnd.random() * 6)
            d = (np.outer(np.cos(t) * r1 * wob, vec[:, 0]) +
                 np.outer(np.sin(t) * r2 * wob, vec[:, 1]))
            polys.append(d + m[None, :])
    return polys


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
        a = rg.Vector3d(ax)
        a.Unitize()

        lift_v = float(lift) if lift else diag * 0.0008
        thr = float(end_threshold) if end_threshold else 0.15
        dens = float(density or 1.0)
        n_curves = 0
        face_lines = []

        # section frame perpendicular to the axis (shared by all pieces)
        e1 = rg.Vector3d.CrossProduct(a, rg.Vector3d(0, 0, 1))
        if e1.Length < 1e-6:
            e1 = rg.Vector3d.CrossProduct(a, rg.Vector3d(1, 0, 0))
        e1.Unitize()
        e2 = rg.Vector3d.CrossProduct(a, e1)
        e2.Unitize()

        for bi, b in enumerate(breps):
            # an Inward-oriented solid flips every face normal: lift must
            # respect the BREP's orientation, not only the face's
            ssign = -1.0 if b.SolidOrientation == \
                rg.BrepSolidOrientation.Inward else 1.0
            pbb = b.GetBoundingBox(True)
            pc = pbb.Center
            pd = pbb.Diagonal
            ext1 = abs(pd * e1) / 2.0
            ext2 = abs(pd * e2) / 2.0
            prnd = random.Random(0xA5 ^ (bi * 7919) ^
                                 (hash(round(pc.X + pc.Y + pc.Z, 3)) & 0xFFFF))
            pith = (rg.Point3d(pc) +
                    e1 * (ext1 * prnd.uniform(-0.45, 0.45)) +
                    e2 * (ext2 * prnd.uniform(-0.45, 0.45)))
            ring_base = min(ext1, ext2) / 4.0 * (0.9 + 0.2 * prnd.random())
            for fi, f in enumerate(b.Faces):
                plane = _face_plane(f)
                if plane is None:
                    continue
                n = rg.Vector3d(plane.Normal)
                n.Unitize()
                # OUTWARD normal from the surface itself -- TryGetPlane's
                # normal sign is arbitrary and must not be trusted for lift
                um = f.Domain(0).Mid
                vm = f.Domain(1).Mid
                out_n = f.NormalAt(um, vm)
                if f.OrientationIsReversed:
                    out_n.Reverse()
                out_n.Unitize()
                dot = abs(n * a)
                kind = "lines" if dot < 0.06 else "rings"

                loops = _loops_2d(f, plane)
                if not loops:
                    continue
                seg_a, seg_b = _segments(loops)
                allpts = np.vstack(loops)
                u0, u1 = allpts[:, 0].min(), allpts[:, 0].max()
                v0, v1 = allpts[:, 1].min(), allpts[:, 1].max()
                if (u1 - u0) < 1e-9 or (v1 - v0) < 1e-9:
                    continue

                dvec = a - n * (a * n)
                if dvec.Length < 1e-9:
                    dvec = plane.XAxis
                dvec.Unitize()
                du = dvec * plane.XAxis
                dv = dvec * plane.YAxis

                seed = hash((bi, fi, round(plane.Origin.X, 4),
                             round(plane.Origin.Y, 4),
                             round(plane.Origin.Z, 4))) & 0xFFFF
                rnd = random.Random(seed)
                # EMPIRICAL outward check: probe a point off the face along
                # the assumed normal; if it lands inside the solid, flip.
                # Immune to every orientation convention.
                flipped = ""
                ip = _face_inner_point(seg_a, seg_b, u0, u1, v0, v1, rnd)
                if ip is not None:
                    p3 = plane.Origin + plane.XAxis * float(ip[0]) \
                         + plane.YAxis * float(ip[1])
                    # two-sided probe, SMALL so it cannot pass through thin
                    # members; retry closer if both sides read outside
                    d0 = max(lift_v * 2.0, diag * 0.0003)
                    try:
                        for dp in (d0, d0 * 0.25):
                            fwd = b.IsPointInside(p3 + out_n * dp, 1e-9, True)
                            bwd = b.IsPointInside(p3 - out_n * dp, 1e-9, True)
                            if fwd and not bwd:
                                out_n = -out_n
                                flipped = " !"
                                break
                            if bwd and not fwd:
                                break            # normal confirmed outward
                            # both inside or both outside: probe again closer
                    except Exception:
                        pass
                lift_vec = out_n * lift_v

                polys2d = _rings_conic(plane, pith, a, allpts, du, dv,
                                       ring_base, dens, rnd, dot)
                n_face = 0
                for pts2d in polys2d:
                    mask = _mask_points(pts2d, seg_a, seg_b)
                    crvs = _runs_to_curves(pts2d, mask, plane, lift_vec,
                                           seg_a, seg_b)
                    for c in crvs:
                        grain.Add(c, GH_Path(bi))
                    n_face += len(crvs)
                n_curves += n_face
                face_lines.append("p{} f{}: dot={:.2f} -> {} ({} crv){}".format(
                    bi, fi, dot, kind, n_face, flipped))

        report.append("grain v{}: {} curves on {} piece(s)  lift {:.4f}  "
                      "end_threshold {:.2f}".format(
                          GRAIN_V, n_curves, len(breps), lift_v, thr))
        if len(face_lines) <= 28:
            report += face_lines
    except Exception as exc:
        report.append("ERROR: {}".format(exc))
else:
    report.append("wire kept + prosthesis into `pieces`, set run")
