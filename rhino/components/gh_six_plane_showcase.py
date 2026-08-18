"""GH Python 3 -- SIX-PLANE JOINT GRAMMAR SHOWCASE.

Generates SJ1--SJ7 side by side from one six-slot plane vocabulary.  SJ1--SJ6
share one Boolean rule; plane angles, offsets, and coplanarity constraints make
the different joints.  SJ7 composes a shortened lap with an intersected
bow-tie/dovetail feature.

Inputs:
    run          (bool)   build the showcase
    spacing      (float)  distance between joints; default 1.75
    show_planes  (bool)   show support planes and removal-side arrows
    plane_joint  (int)    1..7 selects plane graphics; 0 shows every joint
                           (default 4)
    samples      (int)    live corpus-comparison probes per joint; default
                           25000, maximum 250000

Outputs:
    stock          canonical stock, one branch per joint
    kept           kept solid, one branch per joint
    prosthesis     replacement solid, one branch per joint
    plane_traces   intersections of every unique support plane with the stock
    plane_patches  support-plane surfaces for the selected joint(s)
    plane_arrows   arrow curves showing each oriented removal half-space
    labels         joint and selected-plane TextDots
    report         rules, coplanarity, and live corpus agreement

Preview suggestion: give `kept` and `prosthesis` two colours.  Give
`plane_patches` a transparent material.  The geometry is rotated upright for
display: canonical +Y becomes world +Z.
"""

import math
import os
import sys


def _repo_from_component():
    """Find the repository containing this component."""
    def find_upwards(start):
        if not start:
            return None
        current = os.path.abspath(os.path.expanduser(str(start)))
        if os.path.isfile(current) or os.path.splitext(current)[1]:
            current = os.path.dirname(current)
        while True:
            package = os.path.join(current, "src", "workshop_robarch_2026")
            if os.path.isdir(package):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                return None
            current = parent

    override = os.environ.get("ROBARCH_REPO")
    component_file = globals().get("_p") or globals().get("__file__")
    if override:
        repo = find_upwards(override)
        if repo:
            return repo
        raise RuntimeError("ROBARCH_REPO does not contain the repository: {}".format(override))

    candidates = [component_file]
    try:
        document = ghenv.Component.OnPingDocument()
        candidates.append(document.FilePath if document else None)
    except Exception:
        pass
    candidates.append(os.getcwd())
    for candidate in candidates:
        repo = find_upwards(candidate)
        if repo:
            return repo
    raise RuntimeError("Cannot locate repo; set the ROBARCH_REPO environment variable")


REPO = _repo_from_component()
SRC = os.path.join(REPO, "src")
if SRC not in sys.path:
    sys.path.append(SRC)
for module_name in list(sys.modules):
    if module_name.startswith("workshop_robarch_2026"):
        sys.modules.pop(module_name)

from workshop_robarch_2026 import evaluator, kernel
from workshop_robarch_2026 import six_plane_grammar as grammar
from workshop_robarch_2026.version import VERSION

import numpy as np
import Rhino.Geometry as rg
from Grasshopper import DataTree
from Grasshopper.Kernel.Data import GH_Path


stock = DataTree[object]()
kept = DataTree[object]()
prosthesis = DataTree[object]()
plane_traces = DataTree[object]()
plane_patches = DataTree[object]()
plane_arrows = DataTree[object]()
labels = []
report = ["six-plane showcase | version {}".format(VERSION)]


_BOX_VERTICES = [
    np.array([x, y, z], float)
    for x in (-0.5, 0.5)
    for y in (0.0, grammar.ASPECT)
    for z in (-0.5, 0.5)
]
_BOX_EDGES = (
    (0, 1), (0, 2), (0, 4),
    (1, 3), (1, 5),
    (2, 3), (2, 6),
    (3, 7),
    (4, 5), (4, 6),
    (5, 7), (6, 7),
)
_UPRIGHT = rg.Transform.Rotation(
    math.pi / 2.0, rg.Vector3d.XAxis, rg.Point3d.Origin
)


def _input(name, default):
    value = globals().get(name)
    return default if value is None else value


def _display_point(point, joint_index, gap):
    result = rg.Point3d(float(point[0]), float(point[1]), float(point[2]))
    result.Transform(_UPRIGHT)
    result.Transform(rg.Transform.Translation(float(joint_index) * gap, 0.0, 0.0))
    return result


def _display_geometry(geometry, joint_index, gap):
    if isinstance(geometry, rg.Brep):
        result = geometry.DuplicateBrep()
    elif isinstance(geometry, rg.Curve):
        result = geometry.DuplicateCurve()
    else:
        result = geometry.Duplicate()
    result.Transform(_UPRIGHT)
    result.Transform(rg.Transform.Translation(float(joint_index) * gap, 0.0, 0.0))
    return result


def _rhino_plane(plane):
    normal = np.asarray(plane.normal, float)
    length = float(np.linalg.norm(normal))
    normal = normal / length
    d = plane.d / length
    centre = np.array([0.0, 0.5 * grammar.ASPECT, 0.0])
    origin = centre - (float(centre @ normal) - d) * normal
    axis_u, axis_v, _ = kernel.frame_from_normal(normal)
    rh_plane = rg.Plane(
        rg.Point3d(*origin), rg.Vector3d(*axis_u), rg.Vector3d(*axis_v)
    )
    return rh_plane, origin, normal, axis_u, axis_v


def _plane_box_polygon(plane, tolerance=1e-9):
    """Convex section polygon between an infinite plane and canonical stock."""
    normal = np.asarray(plane.normal, float)
    points = []

    def add_unique(point):
        if not any(float(np.linalg.norm(point - other)) <= 1e-8 for other in points):
            points.append(point)

    for ia, ib in _BOX_EDGES:
        a, b = _BOX_VERTICES[ia], _BOX_VERTICES[ib]
        da = float(a @ normal - plane.d)
        db = float(b @ normal - plane.d)
        if abs(da) <= tolerance:
            add_unique(a)
        if abs(db) <= tolerance:
            add_unique(b)
        if da * db < -(tolerance * tolerance):
            t = da / (da - db)
            add_unique(a + t * (b - a))

    if len(points) < 3:
        return None, None
    centre = np.mean(points, axis=0)
    axis_u, axis_v, _ = kernel.frame_from_normal(normal)
    points.sort(key=lambda p: math.atan2(float((p - centre) @ axis_v),
                                         float((p - centre) @ axis_u)))
    rhino_points = [rg.Point3d(*point) for point in points]
    rhino_points.append(rhino_points[0])
    return rg.PolylineCurve(rhino_points), centre


def _support_patch(plane, margin=0.08):
    rh_plane, origin, _, axis_u, axis_v = _rhino_plane(plane)
    coordinates = []
    for vertex in _BOX_VERTICES:
        delta = vertex - origin
        coordinates.append((float(delta @ axis_u), float(delta @ axis_v)))
    u_values = [pair[0] for pair in coordinates]
    v_values = [pair[1] for pair in coordinates]
    surface = rg.PlaneSurface(
        rh_plane,
        rg.Interval(min(u_values) - margin, max(u_values) + margin),
        rg.Interval(min(v_values) - margin, max(v_values) + margin),
    )
    return surface.ToBrep()


def _arrow_curves(plane, slot_text):
    _, origin, normal, axis_u, _ = _rhino_plane(plane)
    start = origin
    end = origin + 0.30 * normal
    left = end - 0.075 * normal + 0.035 * axis_u
    right = end - 0.075 * normal - 0.035 * axis_u
    shaft = rg.LineCurve(rg.Point3d(*start), rg.Point3d(*end))
    head = rg.PolylineCurve([rg.Point3d(*left), rg.Point3d(*end), rg.Point3d(*right)])
    label_point = end + 0.045 * axis_u
    return shaft, head, label_point, slot_text


def _slot_group_text(template, slots):
    names = [grammar.SLOT_IDS[index] for index in slots]
    orientations = {grammar.oriented_key(template.planes[index]) for index in slots}
    separator = "=" if len(orientations) == 1 else "/"
    return separator.join(names)


def _merged_slot_summary(template):
    merged = []
    for slots in grammar.support_groups(template):
        if len(slots) > 1:
            merged.append(_slot_group_text(template, slots))
    return ", ".join(merged) if merged else "all six supports distinct"


def _evaluate_partition(joint):
    stock_cut = kernel.canonical_stock(grammar.ASPECT, grammar.SECTION)
    cuts = [kernel.Cut.from_json(data) for data in joint["cuts"]]
    for i, cut in enumerate(cuts):
        cut.name = "lhf_%d" % (i + 1)
    names = [cut.name for cut in cuts]
    removal = kernel.removal_expression(joint, names)
    all_cut_json = [stock_cut.to_json()] + [cut.to_json() for cut in cuts]
    stock_breps = evaluator.lhf_breps(stock_cut.to_json())
    kept_breps = evaluator.evaluate_part({
        "cuts": all_cut_json,
        "expression": "Difference(lhf_0, {})".format(removal),
    })
    prosthesis_breps = evaluator.evaluate_part({
        "cuts": all_cut_json,
        "expression": "Intersection(lhf_0, {})".format(removal),
    })
    return stock_breps, kept_breps, prosthesis_breps


def _plane_selector(value):
    if isinstance(value, str):
        text = value.strip().upper()
        if text.startswith("SJ"):
            text = text[2:]
        value = int(text)
    selected = int(value)
    if selected < 0 or selected > 7:
        raise ValueError("plane_joint must be 0 or a number from 1 to 7")
    return selected


run_flag = bool(_input("run", True))
gap = max(1.05, float(_input("spacing", 1.75)))
show_plane_graphics = bool(_input("show_planes", True))
sample_count = max(0, min(250000, int(_input("samples", 25000))))

try:
    selected_joint = _plane_selector(_input("plane_joint", 4))
except Exception as exc:
    selected_joint = 4
    report.append("plane selector error: {}; showing SJ4".format(exc))


if run_flag:
    report.append("SJ1--SJ6 rule: {}".format(grammar.COMMON_RULE))
    report.append("SJ1--SJ6 slots: P0/P3 lap sides; P1/P2 shoulders; P4/P5 tips")
    report.append("SJ7 rule: {}".format(grammar.SJ7_RULE))
    report.append("SJ7 slots: P0 seat; P1 lap; P2 shoulder; P3 root; P4/P5 bow-tie flanks")
    report.append("plane graphics: {}".format(
        "all joints" if selected_joint == 0 else "SJ%d" % selected_joint
    ))

    for joint_index, key in enumerate(grammar.list_keys()):
        path = GH_Path(joint_index)
        template = grammar.get_template(key)
        selected = selected_joint == 0 or selected_joint == joint_index + 1

        try:
            # Coincident predicates are simplified only for Rhino's Boolean
            # evaluator.  The template and all explanatory graphics retain the
            # complete six-slot definition.
            rhino_joint = grammar.build_joint(key, simplify_predicates=True)
            stock_breps, kept_breps, prosthesis_breps = _evaluate_partition(rhino_joint)
            for brep in stock_breps:
                stock.Add(_display_geometry(brep, joint_index, gap), path)
            for brep in kept_breps:
                kept.Add(_display_geometry(brep, joint_index, gap), path)
            for brep in prosthesis_breps:
                prosthesis.Add(_display_geometry(brep, joint_index, gap), path)
        except Exception as exc:
            report.append("{} Rhino evaluation FAILED: {}".format(key, exc))

        labels.append(rg.TextDot(
            "{}\n{}".format(key, template.description),
            _display_point((0.0, -0.20, -0.62), joint_index, gap),
        ))

        if show_plane_graphics:
            for support_index, slots in enumerate(grammar.support_groups(template)):
                representative = template.planes[slots[0]]
                trace, centre = _plane_box_polygon(representative)
                support_path = GH_Path(joint_index, support_index)
                if trace is not None:
                    plane_traces.Add(
                        _display_geometry(trace, joint_index, gap), support_path
                    )
                if selected:
                    plane_patches.Add(
                        _display_geometry(_support_patch(representative), joint_index, gap),
                        support_path,
                    )
                    support_text = _slot_group_text(template, slots)
                    label_origin = centre if centre is not None else representative.point()
                    label_normal = np.asarray(representative.normal, float)
                    label_origin = label_origin + 0.04 * label_normal / np.linalg.norm(label_normal)
                    labels.append(rg.TextDot(
                        support_text,
                        _display_point(label_origin, joint_index, gap),
                    ))

            if selected:
                oriented_groups = {}
                for slot, plane in enumerate(template.planes):
                    oriented_groups.setdefault(grammar.oriented_key(plane), []).append(slot)
                for arrow_index, slots in enumerate(oriented_groups.values()):
                    arrow_path = GH_Path(joint_index, arrow_index)
                    text = "=".join(grammar.SLOT_IDS[slot] for slot in slots)
                    shaft, head, label_point, arrow_text = _arrow_curves(
                        template.planes[slots[0]], text
                    )
                    plane_arrows.Add(
                        _display_geometry(shaft, joint_index, gap), arrow_path
                    )
                    plane_arrows.Add(
                        _display_geometry(head, joint_index, gap), arrow_path
                    )
                    labels.append(rg.TextDot(
                        arrow_text,
                        _display_point(label_point, joint_index, gap),
                    ))

        try:
            comparison = grammar.compare_to_stored(
                REPO, key, n_random=sample_count, seed=20260817
            )
            status = "PASS" if comparison["mismatch"] == 0 and comparison["accepted"] else "CHECK"
            report.append(
                "{} {} | 6 slots -> {} predicates -> {} supports | "
                "{} probes, {} mismatches | {}".format(
                    key,
                    status,
                    comparison["predicate_count"],
                    comparison["support_plane_count"],
                    comparison["samples"],
                    comparison["mismatch"],
                    _merged_slot_summary(template),
                )
            )
        except Exception as exc:
            report.append("{} corpus comparison FAILED: {}".format(key, exc))

    report.append("catalogue note: data/corpus/joints/SJ7.json currently declares its key as SJ8")
    report.append("comparison target: nominal JSON geometry; SJ2/SJ4 fabrication relief is documented separately")
else:
    report.append("set run to True")
