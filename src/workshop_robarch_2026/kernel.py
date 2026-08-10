"""Pure-python repair kernel: cut-list joints placed onto a beam.

No Rhino, no external deps beyond numpy. All lessons from the bridge era are
baked in: CCW profiles, oversized cutters, similarity placement that survives
reflections, trim overlap so cutters never meet on a tangent plane.

Canonical joint space (the authoring contract):
    stock section: x, z in [-0.5, +0.5]   (section = 1.0)
    stock length:  y in [0, aspect]        (aspect = length / section)
    the PRIMARY part's cutters remove the prosthesis side material;
    kept   = Difference(stock, REMOVAL)
    prosthesis = Intersection(stock, REMOVAL)
    where REMOVAL is Union(cutters), or a union of intersect
    groups when the joint declares "removal_groups".
    cutters MUST overshoot the stock sideways (never share a face with it).
"""
from __future__ import annotations
import json
import math
import re as _re
import numpy as np

SCHEMA = "repair-joint@1"


# ------------------------------------------------------------------ frames
def frame_from_normal(normal):
    """(u, v, n) plane frame for a cut normal -- the single convention used by
    authoring, kernel and evaluator alike."""
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    arbitrary = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.999 else np.array([1.0, 0.0, 0.0])
    u = np.cross(arbitrary, n)
    u = u / np.linalg.norm(u)
    v = np.cross(n, u)
    return u, v, n


def ccw(polysets):
    """Force counter-clockwise winding (positive area) on every polyset.
    Inside-out solids invert Rhino's boolean semantics -- never skip this."""
    out = []
    for ps in polysets:
        p = np.asarray(ps, float)
        x, y = p[:, 0], p[:, 1]
        area2 = float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
        out.append(p[::-1].copy() if area2 < 0 else p)
    return out


# ------------------------------------------------------------------ cuts
class Cut:
    def __init__(self, name, is_stock, normal, offset, in_plane, amount,
                 polysets, poly_signs=None):
        self.name = name
        self.is_stock = bool(is_stock)
        self.normal = np.asarray(normal, float)
        self.offset = float(offset)
        self.in_plane = np.asarray(in_plane, float)
        self.amount = float(amount)
        self.polysets = ccw([np.asarray(ps, float) for ps in polysets])
        self.poly_signs = list(poly_signs) if poly_signs else [1] * len(self.polysets)

    def to_json(self):
        return {"name": self.name, "is_stock": self.is_stock,
                "normal": self.normal.tolist(), "offset": self.offset,
                "in_plane": self.in_plane.tolist(), "amount": self.amount,
                "polysets": [p.tolist() for p in self.polysets],
                "poly_signs": self.poly_signs}

    @staticmethod
    def from_json(d):
        return Cut(d["name"], d.get("is_stock", False), d["normal"], d["offset"],
                   d["in_plane"], d["amount"], d["polysets"], d.get("poly_signs"))

    def origin(self):
        u, v, n = frame_from_normal(self.normal)
        return self.offset * n + self.in_plane[0] * u + self.in_plane[1] * v


# ------------------------------------------------------- canonical helpers
def canonical_stock(aspect, section=1.0):
    """The canonical joint stock as a Cut (normal +y)."""
    h = 0.5 * section
    n = np.array([0.0, 1.0, 0.0])
    u2, v2, _ = frame_from_normal(n)
    corners = [np.array([-h, 0, -h]), np.array([h, 0, -h]),
               np.array([h, 0, h]), np.array([-h, 0, h])]
    prof = np.array([[c @ u2, c @ v2] for c in corners])
    return Cut("lhf_0", True, n, 0.0, [0.0, 0.0], aspect * section, [prof])




def half_space_cut(name, normal, point, aspect, section=1.0, slack=6.0):
    """A cutter that removes everything on ONE SIDE of a plane.

    The normal points INTO the material to be removed. The prism is sized
    from the canonical stock so it overshoots in every direction, which
    keeps its cap faces well clear of the stock and out of the design.

    Half-spaces are only useful in combination: a lone one cuts the stock
    in two. Intersect them via "removal_groups" and any convex solid is
    reachable; union those groups and any polyhedron is.
    """
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    o = np.asarray(point, float)
    u, v, _ = frame_from_normal(n)

    centre = np.array([0.0, 0.5 * aspect * section, 0.0])
    q = centre - float((centre - o) @ n) * n          # centre projected on plane
    span = slack * (aspect * section + 2.0 * section)
    a = 0.5 * span
    prof = np.array([[-a, -a], [a, -a], [a, a], [-a, a]], float)
    return Cut(name, False, n, float(q @ n),
               [float(q @ u), float(q @ v)], span, [prof])


# --------------------------------------------------------- removal semantics
def removal_groups(joint, n_cuts):
    """Index groups making up the removal solid.

    Default is one group per cut, i.e. the plain union every catalogue
    entry has used so far. A joint may declare "removal_groups": lists of
    0-based indices into "cuts", whose members are INTERSECTED before the
    groups are unioned.

    Grouping is needed exactly when a design vertex is convex on the
    REMOVAL side. Cap faces must clear the stock under the overshoot rule,
    so every design face is a side face of its prism, so all design faces
    of one cut share a common perpendicular -- and planes sharing a common
    perpendicular meet in a LINE, never a point. No union of prisms can
    reach such a vertex; an intersection can.
    """
    g = joint.get("removal_groups")
    if not g:
        return [[i] for i in range(n_cuts)]
    return [list(x) for x in g]


def parse_groups(text, n_cuts):
    """Parse an intersect-group string.

    "0,1,4; 0,1,5" -> [[0,1,4],[0,1,5]]. Blank -> None, meaning the
    plain union. Every cut must appear in some group, so that a
    typo drops a cut loudly instead of silently changing the solid.
    """
    if not text or not str(text).strip():
        return None
    out = []
    for chunk in str(text).replace("\n", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        idx = []
        for tok in chunk.replace(" ", ",").split(","):
            if not tok:
                continue
            i = int(tok)
            if not (0 <= i < n_cuts):
                raise ValueError("group index %d out of range 0..%d"
                                 % (i, n_cuts - 1))
            idx.append(i)
        if idx:
            out.append(idx)
    used = set(i for g in out for i in g)
    missing = [i for i in range(n_cuts) if i not in used]
    if missing:
        raise ValueError("cut(s) %s appear in no group -- every cut must be "
                         "used, or leave groups blank" % missing)
    return out


def removal_expression(joint, names, extra=()):
    """CSG expression for the removal solid, over the given cut names."""
    terms = []
    for g in removal_groups(joint, len(names)):
        mem = [names[i] for i in g]
        terms.append(mem[0] if len(mem) == 1
                     else "Intersection(%s)" % ", ".join(mem))
    return "Union(%s)" % ", ".join(terms + list(extra))


# ------------------------------------------------------------- placement
def place_cut(cut: Cut, M: np.ndarray, t: np.ndarray) -> Cut:
    """Map a cut through p -> M p + t exactly, staying in the representation.
    Valid for any similarity (uniform scale x rotation, proper or improper)."""
    u, v, n = frame_from_normal(cut.normal)
    origin = cut.origin()
    eu, ev = M @ u, M @ v
    n2 = np.cross(eu, ev)
    n2 = n2 / np.linalg.norm(n2)
    d = (M @ n) * cut.amount
    depth = float(d @ n2)
    O = M @ origin + t + (d if depth < 0 else 0.0)
    u2, v2, _ = frame_from_normal(n2)
    M2 = np.array([[eu @ u2, ev @ u2], [eu @ v2, ev @ v2]])
    return Cut(cut.name, cut.is_stock, n2, float(O @ n2),
               [float(O @ u2), float(O @ v2)], abs(depth),
               [ps @ M2.T for ps in cut.polysets], cut.poly_signs)


def beam_stock_cut(frame: dict) -> Cut:
    o = np.asarray(frame["origin"], float)
    U = np.asarray(frame["u"], float)
    W = np.asarray(frame["w"], float)
    V = np.asarray(frame["v"], float)
    u2, v2, n = frame_from_normal(V)
    w_, h_ = frame["width"], frame["height"]
    corners = [np.zeros(3), w_ * U, w_ * U + h_ * W, h_ * W]
    prof = np.array([[c @ u2, c @ v2] for c in corners])
    return Cut("lhf_0", True, V, float(o @ n), [float(o @ u2), float(o @ v2)],
               float(frame["length"]), [prof])


def axial_trim(frame: dict, v_start: float, extent: float, direction: int,
               oversize: float) -> Cut:
    o = np.asarray(frame["origin"], float)
    U = np.asarray(frame["u"], float)
    V = np.asarray(frame["v"], float)
    W = np.asarray(frame["w"], float)
    sec_c = o + 0.5 * frame["width"] * U + 0.5 * frame["height"] * W
    n = float(direction) * V
    p0 = sec_c + v_start * V
    u2, v2, _ = frame_from_normal(n)
    h = oversize
    prof = np.array([[-h, -h], [h, -h], [h, h], [-h, h]], float)
    return Cut("lhf_trim", False, n, float(p0 @ n),
               [float(p0 @ u2), float(p0 @ v2)], float(extent), [prof])


def _full_coverage_v(placed_cuts, frame, start_v, direction, max_extra,
                     grid=16, step_frac=0.02):
    """Smallest |v| beyond start_v (in `direction`) where the union of the
    placed cutters covers the FULL beam section. Falls back to the farthest
    probed v if coverage never completes (degenerate joints)."""
    o = np.asarray(frame["origin"], float)
    U = np.asarray(frame["u"], float)
    V = np.asarray(frame["v"], float)
    W = np.asarray(frame["w"], float)
    w_, h_ = frame["width"], frame["height"]
    eps = 1e-6 * max(w_, h_)
    xs = np.linspace(eps, w_ - eps, grid)
    zs = np.linspace(eps, h_ - eps, grid)
    X, Z = np.meshgrid(xs, zs)
    sec = X.reshape(-1, 1) * U + Z.reshape(-1, 1) * W
    step = step_frac * max_extra
    v = start_v
    travelled = 0.0
    while travelled <= max_extra:
        pts = o + sec + v * V
        acc = np.zeros(len(pts), bool)
        for c in placed_cuts:
            acc |= _points_in_cut(pts, c)
            if acc.all():
                break
        if acc.all():
            return float(v)
        v += direction * step
        travelled += step
    return float(v)


def build_repair(joint: dict, frame: dict, position: float,
                 rotate_deg: float = 0.0, side: int = +1,
                 interface_scale: float = 1.0, blunt: float = 0.0) -> dict:
    """Place a catalogue joint on the beam. Human-driven v1: `side` says which
    end becomes the prosthesis (+1 = far end / high v, -1 = near end / low v)."""
    section = float(joint.get("section", 1.0))
    aspect = float(joint["aspect"])
    thickness = min(frame["width"], frame["height"])
    s = (thickness / section) * float(interface_scale)
    iface = aspect * section * s
    length = float(frame["length"])

    half = iface / 2.0
    position = max(half, min(length - half, float(position)))
    band_lo, band_hi = position - half, position + half

    o = np.asarray(frame["origin"], float)
    U = np.asarray(frame["u"], float)
    V = np.asarray(frame["v"], float)
    W = np.asarray(frame["w"], float)
    B = np.column_stack([U, V, W])
    a = math.radians(float(rotate_deg))
    Rq = np.array([[math.cos(a), 0, -math.sin(a)],
                   [0, 1, 0],
                   [math.sin(a), 0, math.cos(a)]], float)
    M = B @ (s * Rq)
    sec_c = o + 0.5 * frame["width"] * U + 0.5 * frame["height"] * W
    t = sec_c + band_lo * V
    flip = int(side) < 0
    if flip:
        Rf = np.array([[-1, 0, 0], [0, -1, 0], [0, 0, 1]], float)
        t = t + M @ np.array([0.0, aspect * section, 0.0])
        M = M @ Rf

    placed = [place_cut(Cut.from_json(c), M, t) for c in joint["cuts"]]
    for i, c in enumerate(placed):
        c.name = "lhf_%d" % (i + 1)

    # The trim sits EXACTLY where the placed cutters complete full-section
    # coverage. At rotation 0 that is the nominal band edge; under rotation
    # the beam's corners exit the rotated joint section, so coverage
    # completes further out -- a twisted scarf genuinely runs longer. We
    # probe numerically (any joint, any angle, any section), so the scarf
    # face always reaches the trim with nothing chopped.
    # `blunt` > 0 then deliberately truncates the interface tip inward.
    kind = str(joint.get("kind", "splice")).lower()
    if kind == "patch":
        # A patch is bounded by its own cutters. The coverage-probed trim
        # exists to close a splice against the beam end; applied to a pocket
        # it would remove everything beyond it, which is the opposite of the
        # point.
        stock = beam_stock_cut(frame)
        all_cuts = [stock] + placed
        removal = removal_expression(joint, [c.name for c in placed])
        return {
            "schema": "repair@1",
            "parts": [
                {"name": "kept", "cuts": [c.to_json() for c in all_cuts],
                 "expression": "Difference(lhf_0, %s)" % removal},
                {"name": "prosthesis", "cuts": [c.to_json() for c in all_cuts],
                 "expression": "Intersection(lhf_0, %s)" % removal},
            ],
            "interface_length": iface,
            "band": [band_lo, band_hi],
            "position_used": position,
            "flipped": bool(flip),
            "scale": interface_scale,
            "frame": frame,
            "kind": "patch",
        }

    oversize = 2.0 * max(frame["width"], frame["height"])
    edge = band_lo if flip else band_hi
    direction = -1 if flip else +1
    edge = _full_coverage_v(placed, frame, edge, direction,
                            max_extra=1.5 * iface)
    dlt = max(0.0, float(blunt))
    if flip:
        trim = axial_trim(frame, edge + dlt, edge + dlt + iface + 1.0, -1, oversize)
        band_lo = edge
    else:
        trim = axial_trim(frame, edge - dlt, (length - edge) + dlt + iface + 1.0,
                          +1, oversize)
        band_hi = edge

    stock = beam_stock_cut(frame)
    all_cuts = [stock] + placed + [trim]
    removal = removal_expression(joint, [c.name for c in placed],
                                 extra=[trim.name])
    kept_expr = "Difference(lhf_0, %s)" % removal
    pros_expr = "Intersection(lhf_0, %s)" % removal
    cuts_json = [c.to_json() for c in all_cuts]
    return {
        "schema": "repair@1",
        "parts": [{"name": "kept", "expression": kept_expr, "cuts": cuts_json},
                  {"name": "prosthesis", "expression": pros_expr, "cuts": cuts_json}],
        "interface_length": iface, "band": [band_lo, band_hi],
        "position_used": position, "flipped": bool(flip), "scale": s,
        "frame": frame,
    }


# ------------------------------------------------ analytic point classifier
_TOKEN = _re.compile(r"\s*(Union|Difference|Intersection|\(|\)|,|[A-Za-z_]\w*)")


def parse_expression(expr: str):
    tokens = _TOKEN.findall(expr)
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def node():
        t = tokens[pos[0]]; pos[0] += 1
        if t in ("Union", "Difference", "Intersection"):
            assert tokens[pos[0]] == "("; pos[0] += 1
            ch = [node()]
            while peek() == ",":
                pos[0] += 1
                ch.append(node())
            assert tokens[pos[0]] == ")"; pos[0] += 1
            return (t, ch)
        return t

    return node()


def _points_in_cut(points: np.ndarray, cut: Cut) -> np.ndarray:
    u, v, n = frame_from_normal(cut.normal)
    rel = points - cut.origin()
    z = rel @ n
    inside_z = (z >= -1e-9) & (z <= cut.amount + 1e-9)
    px, py = rel @ u, rel @ v
    acc = np.zeros(len(points), bool)
    for ps, sign in zip(cut.polysets, cut.poly_signs):
        xs, ys = ps[:, 0], ps[:, 1]
        m = np.zeros(len(points), bool)
        j = len(xs) - 1
        for i in range(len(xs)):
            cond = ((ys[i] > py) != (ys[j] > py)) & \
                   (px < (xs[j] - xs[i]) * (py - ys[i]) / (ys[j] - ys[i] + 1e-30) + xs[i])
            m ^= cond
            j = i
        acc = (acc | m) if sign > 0 else (acc & ~m)
    return acc & inside_z


def points_in_part(points: np.ndarray, cuts, expression: str) -> np.ndarray:
    solids = {c.name: c for c in cuts}

    def walk(node):
        if isinstance(node, str):
            return _points_in_cut(points, solids[node])
        op, ch = node
        if op == "Union":
            r = walk(ch[0]).copy()
            for x in ch[1:]:
                r |= walk(x)
            return r
        if op == "Intersection":
            r = walk(ch[0]).copy()
            for x in ch[1:]:
                r &= walk(x)
            return r
        return walk(ch[0]) & ~walk(ch[1])

    return walk(parse_expression(expression))
