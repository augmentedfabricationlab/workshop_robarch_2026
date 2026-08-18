"""The shared six-slot plane grammar behind catalogue joints SJ1--SJ7.

Each :class:`Plane` is an oriented half-space ``normal dot point >= d``.
SJ1--SJ6 share six semantic slots and one removal rule::

    R = (P0 & P1 & P2) | (P3 & P4) | (P3 & P5)

Repeated or opposing slots may lie on the same geometric support plane.  This
is how the rule degenerates cleanly to the simpler joints.  Folder joint SJ7
uses the same six-slot vocabulary compositionally: an SJ1-like lap plus an
intersected dovetail/bow-tie feature.

The module is Rhino-free.  Grasshopper can use :func:`build_joint` to compile
the templates into the existing repair-joint kernel, while tests and command
line tools can compare the result with the stored corpus geometrically.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import joints, kernel


ASPECT = 3.0
SECTION = 1.0
SLOT_IDS = ("P0", "P1", "P2", "P3", "P4", "P5")
COMMON_GROUPS = ((0, 1, 2), (3, 4), (3, 5))
SJ7_GROUPS = ((0,), (1, 2), (1, 3, 4, 5))
COMMON_RULE = "(P0 & P1 & P2) | (P3 & P4) | (P3 & P5)"
SJ7_RULE = "base = P0 | (P1 & P2); bowtie = P1 & P3 & P4 & P5; R = base | bowtie"

COMMON_ROLES = (
    "lap positive",
    "shoulder flank A",
    "shoulder flank B",
    "lap opposite",
    "tip flank A",
    "tip flank B",
)
SJ7_ROLES = (
    "seat end",
    "lap positive",
    "shoulder",
    "bowtie root",
    "bowtie flank A",
    "bowtie flank B",
)


@dataclass(frozen=True)
class Plane:
    """An oriented half-space ``normal dot x >= d``."""

    normal: tuple[float, float, float]
    d: float

    def point(self) -> np.ndarray:
        """Return the point on the plane closest to the world origin."""
        n = np.asarray(self.normal, float)
        return (self.d / float(n @ n)) * n


@dataclass(frozen=True)
class JointTemplate:
    key: str
    planes: tuple[Plane, ...]
    groups: tuple[tuple[int, ...], ...]
    rule: str
    roles: tuple[str, ...]
    description: str


def H(nx: float, ny: float, nz: float, d: float) -> Plane:
    return Plane((float(nx), float(ny), float(nz)), float(d))


_SJ4_NX = 0.4795101517671503
_SJ4_NY = 0.7189057747633437
_SJ4_NZ = -0.5032340423343405


TEMPLATES = {
    "SJ1": JointTemplate(
        "SJ1",
        (
            H(0, 0, 1, 0), H(0, 1, 0, 1), H(0, 1, 0, 1),
            H(0, 0, -1, 0), H(0, 1, 0, 2), H(0, 1, 0, 2),
        ),
        COMMON_GROUPS,
        COMMON_RULE,
        COMMON_ROLES,
        "square stepped lap",
    ),
    "SJ2": JointTemplate(
        "SJ2",
        (
            H(0, 0, 1, 0), H(0, 1, -0.5, 0.64), H(0, 1, -0.5, 0.64),
            H(0, 0, -1, 0), H(0, 1, -0.5, 2.36), H(0, 1, -0.5, 2.36),
        ),
        COMMON_GROUPS,
        COMMON_RULE,
        COMMON_ROLES,
        "raked stepped lap",
    ),
    "SJ3": JointTemplate(
        "SJ3",
        (
            H(0, 0, 1, 0), H(-0.685, 1, 0, 0.415), H(0.685, 1, 0, 0.415),
            H(0, 0, -1, 0), H(0.685, 1, 0, 2.585), H(-0.685, 1, 0, 2.585),
        ),
        COMMON_GROUPS,
        COMMON_RULE,
        COMMON_ROLES,
        "vertical chevron lap",
    ),
    "SJ4": JointTemplate(
        "SJ4",
        (
            H(0, 0, 1, 0),
            H(-_SJ4_NX, _SJ4_NY, _SJ4_NZ, 0.3120051062472912),
            H(_SJ4_NX, _SJ4_NY, _SJ4_NZ, 0.3120051062472912),
            H(0, 0, -1, 0),
            H(_SJ4_NX, _SJ4_NY, _SJ4_NZ, 1.8447122180427398),
            H(-_SJ4_NX, _SJ4_NY, _SJ4_NZ, 1.8447122180427398),
        ),
        COMMON_GROUPS,
        COMMON_RULE,
        COMMON_ROLES,
        "compound undercut chevron lap",
    ),
    "SJ5": JointTemplate(
        "SJ5",
        (H(0, 0.31622776601683794, 0.9486832980505138, 0.4743416490252569),) * 6,
        COMMON_GROUPS,
        COMMON_RULE,
        COMMON_ROLES,
        "single scarf plane",
    ),
    "SJ6": JointTemplate(
        "SJ6",
        (
            H(1, 0, 0, 0), H(0, 1, 1, 0.5), H(0, 1, 1, 0.5),
            H(-1, 0, 0, 0), H(0, 1, -1, 2.5), H(0, 1, -1, 2.5),
        ),
        COMMON_GROUPS,
        COMMON_RULE,
        COMMON_ROLES,
        "paired 45-degree mitres",
    ),
    "SJ7": JointTemplate(
        "SJ7",
        (
            H(0, 1, 0, 1.8037974683544304),
            H(0, 0, 1, 0),
            H(0, 1, 0, 1.5),
            H(0, 1, 0, 1.0158227848101267),
            H(-0.9879195263882261, -0.15496776884521196, 0, -0.39658385306333016),
            H(0.9879195263882261, -0.15496776884521196, 0, -0.39658385306333016),
        ),
        SJ7_GROUPS,
        SJ7_RULE,
        SJ7_ROLES,
        "shortened lap plus intersected bow-tie feature",
    ),
}


def list_keys() -> tuple[str, ...]:
    return tuple("SJ%d" % i for i in range(1, 8))


def get_template(key) -> JointTemplate:
    if isinstance(key, int):
        key = "SJ%d" % key
    clean = str(key).strip().upper()
    try:
        return TEMPLATES[clean]
    except KeyError as exc:
        raise KeyError("unknown six-plane template %r; choose SJ1--SJ7" % key) from exc


def _normalised(plane: Plane):
    n = np.asarray(plane.normal, float)
    length = float(np.linalg.norm(n))
    if length <= 1e-12:
        raise ValueError("plane normal has zero length")
    return n / length, plane.d / length


def oriented_key(plane: Plane, digits: int = 10) -> tuple[float, ...]:
    """Canonical key for one oriented half-space; polarity is preserved."""
    n, d = _normalised(plane)
    return tuple(np.round(np.r_[n, d], digits))


def support_key(plane: Plane, digits: int = 10) -> tuple[float, ...]:
    """Canonical key for an unoriented geometric support plane."""
    n, d = _normalised(plane)
    for value in n:
        if abs(value) > 1e-12:
            if value < 0:
                n, d = -n, -d
            break
    return tuple(np.round(np.r_[n, d], digits))


def support_groups(template_or_key) -> tuple[tuple[int, ...], ...]:
    """Group six slot indices by coincident geometric support plane."""
    template = (get_template(template_or_key)
                if not isinstance(template_or_key, JointTemplate) else template_or_key)
    ordered = []
    by_key = {}
    for slot, plane in enumerate(template.planes):
        key = support_key(plane)
        if key not in by_key:
            by_key[key] = len(ordered)
            ordered.append([])
        ordered[by_key[key]].append(slot)
    return tuple(tuple(group) for group in ordered)


def simplify(template_or_key):
    """Deduplicate coincident *oriented* predicates for robust Rhino Booleans.

    All six semantic slots remain present in the template and its graphics.
    The returned cuts/groups contain only the predicates needed by the CSG
    evaluator.  Opposite sides of the same support plane remain distinct.
    """
    template = (get_template(template_or_key)
                if not isinstance(template_or_key, JointTemplate) else template_or_key)
    unique = []
    by_key = {}
    remap = []
    for plane in template.planes:
        key = oriented_key(plane)
        if key not in by_key:
            by_key[key] = len(unique)
            unique.append(plane)
        remap.append(by_key[key])

    clean_groups = []
    seen_groups = set()
    for group in template.groups:
        mapped = []
        for slot in group:
            predicate = remap[slot]
            if predicate not in mapped:
                mapped.append(predicate)
        group_key = tuple(sorted(mapped))
        if group_key not in seen_groups:
            seen_groups.add(group_key)
            clean_groups.append(tuple(mapped))
    return tuple(unique), tuple(clean_groups), tuple(remap)


def build_joint(key, simplify_predicates: bool = False) -> dict:
    """Compile a six-slot template into the current repair-joint schema."""
    template = get_template(key)
    if simplify_predicates:
        planes, groups, _ = simplify(template)
    else:
        planes, groups = template.planes, template.groups
    cuts = [
        kernel.half_space_cut("lhf_%d" % (i + 1), plane.normal, plane.point(), ASPECT)
        for i, plane in enumerate(planes)
    ]
    return {
        "schema": kernel.SCHEMA,
        "key": template.key,
        "aspect": ASPECT,
        "section": SECTION,
        "cuts": [cut.to_json() for cut in cuts],
        "removal_groups": [list(group) for group in groups],
    }


def load_stored_joint(repo_root, key) -> dict:
    path = Path(repo_root) / "data" / "corpus" / "joints" / (get_template(key).key + ".json")
    return json.loads(path.read_text(encoding="utf-8"))


def removal_mask(joint: dict, points: np.ndarray) -> np.ndarray:
    cuts = [kernel.Cut.from_json(cut) for cut in joint["cuts"]]
    for i, cut in enumerate(cuts):
        cut.name = "lhf_%d" % (i + 1)
    expression = kernel.removal_expression(joint, [cut.name for cut in cuts])
    return kernel.points_in_part(points, cuts, expression)


def _near_plane_points(planes, rng, per_plane: int = 1000) -> np.ndarray:
    out = []
    unique = {}
    for plane in planes:
        unique.setdefault(support_key(plane), plane)
    lo = np.array([-0.5, 0.0, -0.5])
    hi = np.array([0.5, ASPECT, 0.5])
    for plane in unique.values():
        n = np.asarray(plane.normal, float)
        unit = n / np.linalg.norm(n)
        base = rng.uniform(lo, hi, size=(per_plane, 3))
        signed = (base @ n - plane.d) / float(n @ n)
        on_plane = base - signed[:, None] * n
        for side in (-1.0, 1.0):
            points = on_plane + side * 1e-6 * unit
            inside = ((points > lo + 1e-9) & (points < hi - 1e-9)).all(axis=1)
            out.append(points[inside])
    return np.vstack(out) if out else np.empty((0, 3), float)


def compare_to_stored(repo_root, key, n_random: int = 50000, seed: int = 20260817) -> dict:
    """Probe corpus and grammar solids at random and near-plane locations."""
    template = get_template(key)
    rng = np.random.default_rng(int(seed))
    lo = np.array([-0.5, 0.0, -0.5])
    hi = np.array([0.5, ASPECT, 0.5])
    random_points = rng.uniform(lo, hi, size=(max(0, int(n_random)), 3))
    near_points = _near_plane_points(template.planes, rng)
    points = np.vstack([random_points, near_points])

    stored = load_stored_joint(repo_root, key)
    candidate = build_joint(key)
    expected = removal_mask(stored, points)
    actual = removal_mask(candidate, points)
    false_positive = int((actual & ~expected).sum())
    false_negative = int((expected & ~actual).sum())
    check = joints.check_joint(candidate, n=max(20000, min(120000, int(n_random))))
    unique_predicates, _, _ = simplify(template)
    return {
        "key": template.key,
        "samples": int(len(points)),
        "mismatch": false_positive + false_negative,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "agreement": float((actual == expected).mean()),
        "accepted": bool(check["accepted"]),
        "slot_count": len(template.planes),
        "predicate_count": len(unique_predicates),
        "support_plane_count": len(support_groups(template)),
        "rule": template.rule,
    }

