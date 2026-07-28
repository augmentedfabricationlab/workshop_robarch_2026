"""Rhino twin of the kernel: evaluate cut-list parts as exact NURBS Breps.

Import-safe off-Rhino (Rhino.Geometry is imported lazily); the pure math
mirrors kernel.frame_from_normal exactly.

Every robustness measure from the bridge era is baked in:
  * profiles arrive CCW from the kernel (inside-out solids invert Rhino's
    boolean semantics) -- and any solid that still reports Inward is flipped;
  * Difference(x, Union(cutters)) is evaluated union-first (cutters overlap
    volumetrically by construction), with a sequential fallback that names
    the cutter on any empty result;
  * Intersection(x, Union(cutters)) is evaluated per-cutter (base x cutter_i,
    then union of pieces), with named failures -- cutters are never
    boolean'd against each other on a tangent plane.
"""
from __future__ import annotations

import math
import re

from .version import VERSION as EVALUATOR_VERSION

TOL = 0.001


# ----------------------------------------------------------------- pure math

def frame_from_normal(normal):
    nx, ny, nz = (float(c) for c in normal)
    ln = math.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / ln, ny / ln, nz / ln
    ax, ay, az = (0.0, 0.0, 1.0) if abs(nz) < 0.999 else (1.0, 0.0, 0.0)
    ux, uy, uz = ay * nz - az * ny, az * nx - ax * nz, ax * ny - ay * nx
    lu = math.sqrt(ux * ux + uy * uy + uz * uz)
    ux, uy, uz = ux / lu, uy / lu, uz / lu
    vx, vy, vz = ny * uz - nz * uy, nz * ux - nx * uz, nx * uy - ny * ux
    return (ux, uy, uz), (vx, vy, vz), (nx, ny, nz)


def cut_origin(cut: dict):
    u, v, n = frame_from_normal(cut["normal"])
    o, (su, sv) = float(cut["offset"]), cut["in_plane"]
    return tuple(o * n[i] + su * u[i] + sv * v[i] for i in range(3))


def profile_world_points(cut: dict, polyset):
    u, v, _ = frame_from_normal(cut["normal"])
    ox, oy, oz = cut_origin(cut)
    return [(ox + x * u[0] + y * v[0],
             oy + x * u[1] + y * v[1],
             oz + x * u[2] + y * v[2]) for x, y in polyset]


# ------------------------------------------------------------ expression AST

_TOKEN = re.compile(r"\s*(Union|Difference|Intersection|\(|\)|,|[A-Za-z_]\w*)")


def parse_expression(expr: str):
    tokens = _TOKEN.findall(expr)
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def eat(expected=None):
        t = tokens[pos[0]]
        if expected and t != expected:
            raise ValueError("expected %r, got %r in %r" % (expected, t, expr))
        pos[0] += 1
        return t

    def node():
        t = eat()
        if t in ("Union", "Difference", "Intersection"):
            eat("(")
            children = [node()]
            while peek() == ",":
                eat(",")
                children.append(node())
            eat(")")
            if t == "Difference" and len(children) != 2:
                raise ValueError("Difference expects 2 args in %r" % expr)
            return (t, children)
        return t

    tree = node()
    if pos[0] != len(tokens):
        raise ValueError("trailing tokens in %r" % expr)
    return tree


# ------------------------------------------------------------- rhino breps

def _rg():
    import Rhino.Geometry as rg  # lazy: keeps the module importable off-Rhino
    return rg


def _polyline_curve(points3d):
    rg = _rg()
    pts = [rg.Point3d(*p) for p in points3d]
    if pts[0].DistanceTo(pts[-1]) > 1e-12:
        pts.append(pts[0])
    return rg.PolylineCurve(pts)


def lhf_breps(cut: dict, tol: float = TOL) -> list:
    """One cut -> list of capped extrusion Breps, outward-oriented."""
    rg = _rg()
    curves = [_polyline_curve(profile_world_points(cut, ps))
              for ps in cut["polysets"]]
    faces = rg.Brep.CreatePlanarBreps(curves, tol)
    if not faces:
        raise ValueError("cut %s: planar face creation failed" % cut["name"])
    amount = float(cut["amount"])
    _, _, n = frame_from_normal(cut["normal"])
    solids = []
    for f in faces:
        face = f.Faces[0]
        fn = face.NormalAt(face.Domain(0).Mid, face.Domain(1).Mid)
        sign = 1.0 if (fn.X * n[0] + fn.Y * n[1] + fn.Z * n[2]) > 0 else -1.0
        s = rg.Brep.CreateFromOffsetFace(face, sign * amount, tol, False, True)
        if s is None or not s.IsSolid:
            raise ValueError("cut %s: offset-face solid failed" % cut["name"])
        # inside-out solids invert Rhino's boolean semantics -- fix orientation
        if s.SolidOrientation == rg.BrepSolidOrientation.Inward:
            s.Flip()
        solids.append(s)
    return solids


def _boolean_union(breps, tol):
    rg = _rg()
    if len(breps) == 1:
        return breps
    out = rg.Brep.CreateBooleanUnion(breps, tol)
    return list(out) if out else breps


def _boolean_difference(a, b, tol):
    rg = _rg()
    out = rg.Brep.CreateBooleanDifference(a, b, tol)
    if out is None:
        raise ValueError("boolean difference failed (tolerance?)")
    return list(out)


def evaluate_part(part: dict, tol: float = TOL) -> list:
    """One part -> list of Breps (usually one solid)."""
    rg = _rg()
    solids = {c["name"]: lhf_breps(c, tol) for c in part["cuts"]}

    def walk(node):
        if isinstance(node, str):
            return list(solids[node])
        op, children = node

        # Difference(x, Union(cutters)): union-first (cutters overlap
        # volumetrically), sequential fallback with named empty detection.
        if op == "Difference" and isinstance(children[1], tuple) \
                and children[1][0] == "Union":
            base = walk(children[0])
            cutters = []
            for ch in children[1][1]:
                cutters += walk(ch)
            merged = rg.Brep.CreateBooleanUnion(cutters, tol)
            if merged:
                out = rg.Brep.CreateBooleanDifference(base, list(merged), tol)
                if out and len(list(out)) > 0:
                    return list(out)
            acc = list(base)
            for ch in children[1][1]:
                nm = ch if isinstance(ch, str) else "?"
                out = rg.Brep.CreateBooleanDifference(acc, walk(ch), tol)
                if out is None or len(list(out)) == 0:
                    raise ValueError(
                        "difference - %s produced EMPTY (tangency?)" % nm)
                acc = list(out)
            return acc

        # Intersection(x, Union(cutters)): merge cutters FIRST (they overlap
        # volumetrically by the authoring rules), then ONE intersection --
        # a single boundary, no internal seams. Per-cutter fallback with
        # named failures only if the merge or the intersection declines.
        if op == "Intersection" and isinstance(children[1], tuple) \
                and children[1][0] == "Union":
            base = walk(children[0])
            cutters = []
            for ch in children[1][1]:
                cutters += walk(ch)
            merged = rg.Brep.CreateBooleanUnion(cutters, tol) \
                if len(cutters) > 1 else cutters
            if merged:
                out = rg.Brep.CreateBooleanIntersection(base, list(merged), tol)
                if out and len(list(out)) > 0:
                    return list(out)
            pieces = []
            for ch in children[1][1]:
                nm = ch if isinstance(ch, str) else "?"
                out = rg.Brep.CreateBooleanIntersection(base, walk(ch), tol)
                if out is None:
                    raise ValueError("intersection stock x %s FAILED" % nm)
                pieces += list(out)
            if not pieces:
                raise ValueError("intersection produced nothing")
            return _boolean_union(pieces, tol)

        if op == "Union":
            acc = []
            for ch in children:
                acc += walk(ch)
            return _boolean_union(acc, tol)
        if op == "Intersection":
            acc = walk(children[0])
            for ch in children[1:]:
                out = rg.Brep.CreateBooleanIntersection(acc, walk(ch), tol)
                if not out:
                    raise ValueError("boolean intersection failed (tolerance?)")
                acc = list(out)
            return acc
        return _boolean_difference(walk(children[0]), walk(children[1]), tol)

    return walk(parse_expression(part["expression"]))


def diagnose_cuts(part: dict, tol: float = TOL) -> list:
    """Per-cutter report lines: does each cut build as a valid solid, and
    what is its Rhino volume. Useful in any component's info panel."""
    rg = _rg()
    lines = []
    for c in part["cuts"]:
        try:
            bs = lhf_breps(c, tol)
            vol = 0.0
            ok = True
            for b in bs:
                vp = rg.VolumeMassProperties.Compute(b)
                if vp:
                    vol += vp.Volume
                ok = ok and b.IsSolid
            lines.append("  %-9s rhino=%8.2fL  solid=%s" % (c["name"], vol * 1000, ok))
        except Exception as exc:
            lines.append("  %-9s FAILED: %s" % (c["name"], exc))
    return lines
