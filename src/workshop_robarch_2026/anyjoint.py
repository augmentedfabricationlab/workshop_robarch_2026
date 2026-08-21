"""Adaptive six-plane repair-joint generation and fitting.

This is the deterministic core used by the Grasshopper ``AnyJoint``
component.  It compiles an LLM-authored six-plane Boolean program, places it
around a cellular damage field, applies construction feasibility gates, and
ranks only the survivors.  Sound timber loss remains a reported conservation
metric; it is deliberately a low-weight tie breaker after feasibility.

The named SJ joints remain regression precedents and backwards-compatible
helpers. The Gemini path compiles direct planes and never selects those names.
Legacy local generation can still vary:

* lap depth;
* plan chevron;
* parallel or opposed rake;
* scarf slope;
* position, rotation and replaced beam end.

The module is Rhino-free so its geometry decisions can be unit tested.
Rhino/Grasshopper evaluates only the shortlisted repairs as Breps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import numpy as np

from . import kernel, scoring
from .six_plane_grammar import (
    ASPECT,
    COMMON_GROUPS,
    COMMON_ROLES,
    COMMON_RULE,
    SJ7_GROUPS,
    SJ7_ROLES,
    SJ7_RULE,
    JointTemplate,
    Plane,
    H,
    simplify,
    support_groups,
)


@dataclass(frozen=True)
class GrammarCandidate:
    """One generated topology/parameter combination before placement."""

    candidate_id: str
    family: str
    template: JointTemplate
    parameters: dict


def plane_program_candidate(
    planes,
    removal_groups,
    aspect: float = ASPECT,
    roles=None,
    candidate_id: str = "AJ-ANY",
) -> GrammarCandidate:
    """Create one name-free AnyJoint directly from six half-space planes.

    Canonical coordinates use ``x,z = -0.5..0.5`` across the section and
    ``y = 0..aspect`` along the beam. A predicate is ``normal dot p >= d``.
    Predicates within a removal group are intersected; groups are unioned.
    """
    a = float(aspect)
    if not np.isfinite(a) or not 0.5 <= a <= 8.0:
        raise ValueError("AnyJoint aspect must lie between 0.5 and 8.0 sections")
    if not isinstance(planes, (list, tuple)) or len(planes) != 6:
        raise ValueError("AnyJoint requires exactly six plane slots")

    parsed = []
    parsed_roles = []
    for index, value in enumerate(planes):
        if isinstance(value, Plane):
            plane = value
            role = "plane P%d" % index
        elif isinstance(value, dict):
            normal = value.get("normal")
            if not isinstance(normal, (list, tuple)) or len(normal) != 3:
                raise ValueError("plane P%d needs normal [nx, ny, nz]" % index)
            numbers = np.asarray(normal, float)
            d = float(value.get("d", value.get("offset", 0.0)))
            if not np.isfinite(numbers).all() or not np.isfinite(d):
                raise ValueError("plane P%d contains a non-finite value" % index)
            length = float(np.linalg.norm(numbers))
            if length <= 1e-8:
                raise ValueError("plane P%d has a zero-length normal" % index)
            numbers /= length
            plane = H(numbers[0], numbers[1], numbers[2], d / length)
            role = str(value.get("role") or "plane P%d" % index)
        else:
            raise ValueError("plane P%d must be an object" % index)
        parsed.append(plane)
        parsed_roles.append(role)

    groups = []
    for group_index, raw_group in enumerate(removal_groups or []):
        if not isinstance(raw_group, (list, tuple)) or not raw_group:
            raise ValueError("removal group %d must contain plane ids" % group_index)
        group = []
        for value in raw_group:
            if isinstance(value, str) and value.strip().upper().startswith("P"):
                value = value.strip()[1:]
            try:
                slot = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid plane reference %r" % value) from exc
            if slot < 0 or slot >= 6:
                raise ValueError("plane reference P%d is outside P0..P5" % slot)
            if slot not in group:
                group.append(slot)
        groups.append(tuple(group))
    if not groups:
        raise ValueError("AnyJoint needs at least one removal group")

    if roles is not None:
        if not isinstance(roles, (list, tuple)) or len(roles) != 6:
            raise ValueError("roles must contain six entries")
        parsed_roles = [str(value) for value in roles]
    rule = " | ".join(
        "(" + " & ".join("P%d" % slot for slot in group) + ")"
        for group in groups
    )
    template = JointTemplate(
        str(candidate_id or "AJ-ANY"),
        tuple(parsed),
        tuple(groups),
        rule,
        tuple(parsed_roles),
        "Gemini-authored AnyJoint six-plane program",
    )
    return GrammarCandidate(
        str(candidate_id or "AJ-ANY"),
        "any_joint",
        template,
        {
            "aspect": a,
            "plane_program": True,
            "removal_groups": [list(group) for group in groups],
        },
    )


def _slug(value: float) -> str:
    sign = "m" if value < 0 else "p"
    return "%s%03d" % (sign, int(round(abs(float(value)) * 100.0)))


def lap_candidate(
    chevron: float = 0.0,
    rake_left: float = 0.0,
    rake_right: float = 0.0,
    lap_fraction: float = 0.5,
    aspect: float = ASPECT,
) -> GrammarCandidate:
    """Create a six-slot lap-family candidate.

    Canonical section coordinates are x,z = -0.5..0.5 and the beam runs in
    +y.  ``chevron`` opens the paired shoulder/tip planes in plan.
    ``rake_left`` and ``rake_right`` tilt the two abutments in elevation.
    Equal rakes make an undercut family; opposing rakes make a miter family.
    """
    c = max(0.0, float(chevron))
    rl, rr = float(rake_left), float(rake_right)
    lf = float(lap_fraction)
    if not 0.2 <= lf <= 0.8:
        raise ValueError("lap_fraction must lie between 0.2 and 0.8")
    a = float(aspect)
    if a <= 0:
        raise ValueError("aspect must be positive")

    z0 = lf - 0.5
    left, right = a / 3.0, 2.0 * a / 3.0
    planes = (
        H(0, 0, 1, z0),
        H(-c, 1, -rl, left),
        H(c, 1, -rl, left),
        H(0, 0, -1, -z0),
        H(c, 1, -rr, right),
        H(-c, 1, -rr, right),
    )
    mode = "parallel" if abs(rl - rr) < 1e-9 else "opposed"
    cid = "AJ-LAP-c%s-rl%s-rr%s-d%02d" % (
        _slug(c), _slug(rl), _slug(rr), int(round(100.0 * lf))
    )
    template = JointTemplate(
        cid,
        planes,
        COMMON_GROUPS,
        COMMON_RULE,
        COMMON_ROLES,
        "adaptive %s-rake six-plane lap" % mode,
    )
    return GrammarCandidate(
        cid,
        "lap",
        template,
        {
            "chevron": c,
            "rake_left": rl,
            "rake_right": rr,
            "lap_fraction": lf,
            "rake_mode": mode,
            "aspect": a,
        },
    )


def scarf_candidate(slope: float = 3.0, aspect: float = ASPECT) -> GrammarCandidate:
    """Create a single-surface scarf as a six-slot coplanar degeneration."""
    s = float(slope)
    a = float(aspect)
    if abs(s) < 1e-6:
        raise ValueError("scarf slope must be non-zero")
    plane = H(0, 1, s, 0.5 * a)
    cid = "AJ-SCARF-s%s" % _slug(s)
    template = JointTemplate(
        cid,
        (plane,) * 6,
        COMMON_GROUPS,
        COMMON_RULE,
        COMMON_ROLES,
        "adaptive single-plane scarf",
    )
    return GrammarCandidate(cid, "scarf", template, {"slope": s, "aspect": a})


def lapped_bowtie_candidate(
    lap_fraction: float = 0.5,
    root_fraction: float = 0.34,
    shoulder_fraction: float = 0.50,
    seat_fraction: float = 0.64,
    tip_fraction: float = 0.84,
    lock_half_width: float = 0.24,
    aspect: float = ASPECT,
) -> GrammarCandidate:
    """Create a lapped splice with an intersected positive-lock feature.

    This is the compositional topology demonstrated by SJ7: a short lap is
    combined with a centred bow-tie/dovetail removal region.  The six plane
    slots use a different Boolean program from the common SJ1--SJ6 family,
    which lets a JointProgram request genuinely different topology instead
    of receiving another angle variation of the same lap.

    Fractions are measured along the canonical interface length.  The lock
    widens from its tip towards ``root_fraction``; ``lock_half_width`` is the
    half-width at that root in canonical section units.
    """
    a = float(aspect)
    lf = float(lap_fraction)
    root = float(root_fraction)
    shoulder = float(shoulder_fraction)
    seat = float(seat_fraction)
    tip = float(tip_fraction)
    width = float(lock_half_width)
    if a <= 0:
        raise ValueError("aspect must be positive")
    if not 0.2 <= lf <= 0.8:
        raise ValueError("lap_fraction must lie between 0.2 and 0.8")
    if not (0.05 <= root < shoulder < seat < tip <= 0.98):
        raise ValueError(
            "bowtie fractions must satisfy 0.05 <= root < shoulder < seat < tip <= 0.98"
        )
    if not 0.06 <= width <= 0.48:
        raise ValueError("lock_half_width must lie between 0.06 and 0.48")

    root_y = root * a
    shoulder_y = shoulder * a
    seat_y = seat * a
    tip_y = tip * a
    lock_slope = (tip_y - root_y) / width
    z0 = lf - 0.5
    planes = (
        H(0, 1, 0, seat_y),
        H(0, 0, 1, z0),
        H(0, 1, 0, shoulder_y),
        H(0, 1, 0, root_y),
        H(-lock_slope, -1, 0, -tip_y),
        H(lock_slope, -1, 0, -tip_y),
    )
    cid = "AJ-LOCK-d%02d-r%02d-w%02d" % (
        int(round(100.0 * lf)),
        int(round(100.0 * root)),
        int(round(100.0 * width)),
    )
    template = JointTemplate(
        cid,
        planes,
        SJ7_GROUPS,
        SJ7_RULE,
        SJ7_ROLES,
        "adaptive lap plus intersected bow-tie positive lock",
    )
    return GrammarCandidate(
        cid,
        "lapped_bowtie",
        template,
        {
            "lap_fraction": lf,
            "root_fraction": root,
            "shoulder_fraction": shoulder,
            "seat_fraction": seat,
            "tip_fraction": tip,
            "lock_half_width": width,
            "lock_slope": lock_slope,
            "aspect": a,
        },
    )


def default_grammar(
    allow_chevron: bool = True,
    allow_undercut: bool = True,
    allow_scarf: bool = True,
) -> list[GrammarCandidate]:
    """A compact search bank with both precedents and novel intermediates."""
    chevrons = (0.0, 0.35, 0.685) if allow_chevron else (0.0,)
    lap_depths = (0.40, 0.50, 0.60)
    rakes = [(0.0, 0.0)]
    if allow_undercut:
        # Parallel rakes span square -> undercut. Opposed rakes cover the
        # mitered family. Combining an opposed rake with a chevron is omitted
        # from v1 because it produces fragile compound tips.
        rakes += [(0.35, 0.35), (0.70, 0.70), (-0.50, 0.50), (-1.0, 1.0)]

    out = []
    for depth in lap_depths:
        for chevron in chevrons:
            for rl, rr in rakes:
                if abs(rl - rr) > 1e-9 and chevron > 1e-9:
                    continue
                out.append(lap_candidate(chevron, rl, rr, depth))
    if allow_scarf:
        out.extend([scarf_candidate(1.5), scarf_candidate(3.0)])
    return out


def compile_candidate(candidate: GrammarCandidate) -> dict:
    """Compile semantic planes to the repair kernel, merging duplicates."""
    aspect = float(candidate.parameters.get("aspect", ASPECT))
    planes, groups, _ = simplify(candidate.template)
    cuts = [
        kernel.half_space_cut(
            "lhf_%d" % (i + 1), plane.normal, plane.point(), aspect
        )
        for i, plane in enumerate(planes)
    ]
    return {
        "schema": kernel.SCHEMA,
        "key": candidate.candidate_id,
        "kind": "splice",
        "aspect": aspect,
        "section": 1.0,
        "cuts": [cut.to_json() for cut in cuts],
        "removal_groups": [list(group) for group in groups],
    }


_CARDINAL_DIRECTIONS = {
    "+X": np.array([1.0, 0.0, 0.0]),
    "-X": np.array([-1.0, 0.0, 0.0]),
    "+Y": np.array([0.0, 1.0, 0.0]),
    "-Y": np.array([0.0, -1.0, 0.0]),
    "+Z": np.array([0.0, 0.0, 1.0]),
    "-Z": np.array([0.0, 0.0, -1.0]),
}


def _direction(value):
    if isinstance(value, str):
        key = value.strip().upper().replace("LOCAL_", "")
        if key in _CARDINAL_DIRECTIONS:
            return key, _CARDINAL_DIRECTIONS[key]
        raise ValueError("direction %r must be +X, -X, +Y, -Y, +Z or -Z" % value)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("direction must be a local-axis label or [x, y, z]")
    vector = np.asarray(value, float)
    length = float(np.linalg.norm(vector))
    if not np.isfinite(vector).all() or length <= 1e-8:
        raise ValueError("direction vector must be finite and non-zero")
    vector /= length
    key = "[%s]" % ",".join("%.4f" % component for component in vector)
    return key, vector


def _template_removal_mask(template: JointTemplate, points) -> np.ndarray:
    points = np.asarray(points, float)
    predicates = []
    for plane in template.planes:
        normal = np.asarray(plane.normal, float)
        predicates.append(points @ normal >= float(plane.d) - 1e-9)
    removed = np.zeros(len(points), bool)
    for group in template.groups:
        inside = np.ones(len(points), bool)
        for slot in group:
            inside &= predicates[int(slot)]
        removed |= inside
    return removed


def _extraction_clear(template: JointTemplate, aspect: float, points, removed, direction) -> bool:
    prosthesis = np.asarray(points, float)[np.asarray(removed, bool)]
    if not len(prosthesis):
        return False
    if len(prosthesis) > 3500:
        prosthesis = prosthesis[:: max(1, len(prosthesis) // 3500)]
    vector = np.asarray(direction, float)
    max_travel = float(aspect) + 1.5
    for distance in np.linspace(0.04, max_travel, 28):
        moved = prosthesis + distance * vector
        inside_stock = (
            (moved[:, 0] >= -0.5)
            & (moved[:, 0] <= 0.5)
            & (moved[:, 1] >= 0.0)
            & (moved[:, 1] <= float(aspect))
            & (moved[:, 2] >= -0.5)
            & (moved[:, 2] <= 0.5)
        )
        if inside_stock.any():
            still_removed = _template_removal_mask(template, moved[inside_stock])
            if (~still_removed).any():
                return False
    return True


def candidate_geometry_metrics(candidate: GrammarCandidate, directions=()) -> dict:
    """Fast voxel diagnostics for constructionally meaningful geometry.

    These metrics are topology-independent. They describe the actual Boolean
    region produced by the planes rather than comparing it with a named joint.
    """
    template = candidate.template
    aspect = float(candidate.parameters.get("aspect", ASPECT))
    nx = nz = 19
    # Construction gates need finer axial resolution than the display/search
    # cells. At 45 samples per section the engagement quantisation is about
    # 0.022 section depths rather than the former 0.056.
    ny = max(45, int(round(45.0 * aspect)))
    dx, dy, dz = 1.0 / nx, aspect / ny, 1.0 / nz
    xs = np.linspace(-0.5 + 0.5 * dx, 0.5 - 0.5 * dx, nx)
    ys = np.linspace(0.5 * dy, aspect - 0.5 * dy, ny)
    zs = np.linspace(-0.5 + 0.5 * dz, 0.5 - 0.5 * dz, nz)
    yy, xx, zz = np.meshgrid(ys, xs, zs, indexing="ij")
    points = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()])
    removed = _template_removal_mask(template, points).reshape(ny, nx, nz)
    section_fraction = removed.mean(axis=(1, 2))
    mixed = (section_fraction > 0.01) & (section_fraction < 0.99)
    engagement = float(mixed.sum()) * dy
    engagement_span = (
        float(ys[np.flatnonzero(mixed)[-1]] - ys[np.flatnonzero(mixed)[0]] + dy)
        if mixed.any()
        else 0.0
    )
    ligament = np.minimum(section_fraction[mixed], 1.0 - section_fraction[mixed])

    # Count all kept/replacement voxel-face transitions. Dividing by the
    # section area yields an interface-area ratio in section-area units.
    area = (
        float(np.count_nonzero(removed[:, 1:, :] != removed[:, :-1, :])) * dy * dz
        + float(np.count_nonzero(removed[1:, :, :] != removed[:-1, :, :])) * dx * dz
        + float(np.count_nonzero(removed[:, :, 1:] != removed[:, :, :-1])) * dx * dy
    )

    supports = support_groups(template)
    support_normals = []
    for slots in supports:
        normal = np.asarray(template.planes[slots[0]].normal, float)
        support_normals.append(normal / np.linalg.norm(normal))
    angles = []
    for index, normal_a in enumerate(support_normals):
        for normal_b in support_normals[index + 1 :]:
            cosine = float(np.clip(abs(normal_a @ normal_b), 0.0, 1.0))
            angle = float(np.degrees(np.arccos(cosine)))
            if angle > 1e-5:  # parallel supports are valid opposing/offset faces
                angles.append(angle)

    requested = list(_CARDINAL_DIRECTIONS.items())
    for value in directions or []:
        key, vector = _direction(value)
        if key not in [item[0] for item in requested]:
            requested.append((key, vector))
    flat_removed = removed.ravel()
    clear = [
        key
        for key, vector in requested
        if _extraction_clear(template, aspect, points, flat_removed, vector)
    ]
    return {
        "engagementSections": engagement,
        "engagementSpanSections": engagement_span,
        "interfaceAreaRatio": area,
        "medianLigamentRatio": float(np.median(ligament)) if len(ligament) else 0.0,
        "replacementVolumeFraction": float(removed.mean()),
        "supportPlaneCount": len(supports),
        "minimumPlaneAngleDeg": min(angles) if angles else 180.0,
        "clearExtractionDirections": clear,
    }


def construction_failures(metrics: dict, constraints: Optional[dict] = None) -> list[str]:
    """Return deterministic hard-gate failures for one AnyJoint geometry."""
    constraints = constraints or {}
    checks = (
        ("engagementSections", "minimumEngagementSections", 1.0, "engagement"),
        ("interfaceAreaRatio", "minimumInterfaceAreaRatio", 1.0, "interface area"),
        ("medianLigamentRatio", "minimumLigamentRatio", 0.08, "ligament"),
        ("minimumPlaneAngleDeg", "minimumPlaneAngleDeg", 10.0, "plane angle"),
    )
    failures = []
    for metric_name, constraint_name, default, label in checks:
        required = float(constraints.get(constraint_name, default))
        actual = float(metrics.get(metric_name, 0.0))
        if actual + 1e-9 < required:
            failures.append("%s %.3f < required %.3f" % (label, actual, required))
    max_supports = int(constraints.get("maximumSupportPlanes", 6))
    if int(metrics.get("supportPlaneCount", 0)) > max_supports:
        failures.append(
            "support planes %d > allowed %d"
            % (int(metrics.get("supportPlaneCount", 0)), max_supports)
        )
    volume = float(metrics.get("replacementVolumeFraction", 0.0))
    min_volume = float(constraints.get("minimumReplacementVolumeFraction", 0.02))
    max_volume = float(constraints.get("maximumReplacementVolumeFraction", 0.90))
    if not min_volume <= volume <= max_volume:
        failures.append(
            "replacement volume fraction %.3f outside %.3f..%.3f"
            % (volume, min_volume, max_volume)
        )
    clear = set(metrics.get("clearExtractionDirections") or [])
    assembly = constraints.get("assemblyDirection")
    if assembly is not None:
        key, _ = _direction(assembly)
        if key not in clear:
            failures.append("assembly direction %s is collision-blocked" % key)
    for value in constraints.get("geometricLockDirections") or []:
        key, _ = _direction(value)
        if key in clear:
            failures.append("required geometric lock is open in direction %s" % key)
    return failures


def damage_extent(cells, damage, frame, threshold: float):
    local = scoring.to_local(cells, frame)
    mandatory = np.asarray(damage, float) >= float(threshold)
    if not mandatory.any():
        return None, None, mandatory
    return (
        float(local[mandatory, 1].min()),
        float(local[mandatory, 1].max()),
        mandatory,
    )


def _placements(
    frame,
    v0: float,
    v1: float,
    scale: float,
    n_positions: int,
    window: float,
    rotations: Sequence[float],
    sides: Sequence[int],
):
    length = float(frame["length"])
    section = min(float(frame["width"]), float(frame["height"]))
    lo = max(0.02, (v0 - float(window) * section) / length)
    hi = min(0.98, (v1 + float(window) * section) / length)
    if hi <= lo:
        lo, hi = 0.15, 0.85
    for position in np.linspace(lo, hi, max(2, int(n_positions))):
        for rotation in rotations:
            for side in sides:
                yield {
                    "position": float(position),
                    "rotate_deg": float(rotation),
                    "side": +1 if int(side) >= 0 else -1,
                    "interface_scale": float(scale),
                }


def _complexity(candidate: GrammarCandidate) -> float:
    supports = len(support_groups(candidate.template))
    p = candidate.parameters
    angles = (
        abs(float(p.get("chevron", 0.0)))
        + 0.5 * abs(float(p.get("rake_left", 0.0)))
        + 0.5 * abs(float(p.get("rake_right", 0.0)))
    )
    return float(supports) + angles


def search(
    frame: dict,
    cells,
    damage,
    threshold: float = 0.5,
    grammar: Optional[Iterable[GrammarCandidate]] = None,
    n_positions: int = 7,
    window: float = 1.5,
    margin: float = 1.0,
    rotations: Sequence[float] = (0.0, 90.0, 180.0, 270.0),
    sides: Sequence[int] = (+1, -1),
    complexity_weight: float = 0.002,
    construction_constraints: Optional[dict] = None,
    verify: bool = True,
    allow_partial: bool = False,
):
    """Fit plane joints after damage and construction feasibility gates.

    ``threshold`` creates the initial mandatory-removal mask. The construction
    contract may add an uncertainty threshold and a physical buffer. Geometry
    engagement, interface, ligament, plane angle and assembly are candidate
    gates. Conservation loss participates only after those gates pass.

    Returns ``(results, report)``.  Every result contains the exact repair dict
    under ``repair`` for downstream Rhino evaluation.
    """
    points = np.asarray(cells, float)
    values = np.asarray(damage, float)
    report = []
    if points.ndim != 2 or points.shape[1] != 3:
        return [], ["cell centres must be an N x 3 array"]
    if len(points) != len(values):
        return [], ["centres/damage length mismatch: %d vs %d" % (len(points), len(values))]
    if not len(points):
        return [], ["no cells supplied"]

    if verify:
        ok, message, _ = scoring.check_cells(points, frame)
        report.append(message)
        if not ok:
            return [], report

    v0, v1, mandatory = damage_extent(points, values, frame, threshold)
    if v0 is None:
        report.append("no cell reaches threshold %.2f" % float(threshold))
        return [], report

    constraints = construction_constraints
    section = min(float(frame["width"]), float(frame["height"]))
    required = mandatory.copy()
    buffer_sections = 0.0
    if constraints is not None:
        suspect_threshold = constraints.get("damageUncertaintyThreshold")
        if suspect_threshold is not None:
            required |= values >= float(suspect_threshold)
        buffer_sections = max(0.0, float(constraints.get("damageBufferSections", 0.0)))
        if buffer_sections > 0.0:
            radius = buffer_sections * section
            damaged_points = points[mandatory]
            distances2 = ((points[:, None, :] - damaged_points[None, :, :]) ** 2).sum(axis=2)
            required |= (distances2 <= radius * radius + 1e-12).any(axis=1)
    need = (v1 - v0) + 2.0 * float(margin) * section
    candidates = list(grammar) if grammar is not None else default_grammar()
    report.append(
        "required removal: %d damage + %d uncertainty/buffer cells of %d; "
        "axial %.3f..%.3f m; %d bounded AnyJoint plane variant(s)"
        % (
            int(mandatory.sum()),
            int(required.sum() - mandatory.sum()),
            len(points),
            v0,
            v1,
            len(candidates),
        )
    )

    results = []
    built = covered = failed = 0
    rejected_geometry = 0
    for candidate in candidates:
        direction_values = []
        if constraints is not None:
            if constraints.get("assemblyDirection") is not None:
                direction_values.append(constraints["assemblyDirection"])
            direction_values.extend(constraints.get("geometricLockDirections") or [])
        quality = candidate_geometry_metrics(candidate, direction_values)
        quality_failures = (
            construction_failures(quality, constraints)
            if constraints is not None
            else []
        )
        if quality_failures:
            rejected_geometry += 1
            report.append(
                "%s rejected before placement: %s"
                % (candidate.candidate_id, "; ".join(quality_failures))
            )
            continue
        joint = compile_candidate(candidate)
        scale = max(1.0, need / (float(joint["aspect"]) * section))
        predicate_count = len(joint["cuts"])
        support_count = len(support_groups(candidate.template))
        complexity = _complexity(candidate)
        for placement in _placements(
            frame, v0, v1, scale, n_positions, window, rotations, sides
        ):
            built += 1
            try:
                repair = kernel.build_repair(
                    joint,
                    frame,
                    position=placement["position"] * float(frame["length"]),
                    rotate_deg=placement["rotate_deg"],
                    side=placement["side"],
                    interface_scale=placement["interface_scale"],
                )
                removed = scoring.removed_mask(points, repair)
            except Exception:
                failed += 1
                continue
            required_left = int((required & ~removed).sum())
            if required_left and not allow_partial:
                continue
            if required_left == 0:
                covered += 1
            metrics = scoring.score(points, values, repair, threshold, verify=False)
            sound_denominator = max(1, metrics["n_sound"])
            loss = metrics["sound_sacrificed_weighted"] / sound_denominator
            kept_points = points[~removed]
            if len(kept_points):
                distances = np.sqrt(
                    ((points[mandatory, None, :] - kept_points[None, :, :]) ** 2).sum(axis=2)
                )
                clearance = float(distances.min()) / section
            else:
                clearance = 0.0

            if constraints is None:
                rank_score = loss + float(complexity_weight) * complexity
            else:
                target_engagement = max(
                    1e-6,
                    float(
                        constraints.get(
                            "targetEngagementSections",
                            constraints.get("minimumEngagementSections", 1.0),
                        )
                    ),
                )
                target_interface = max(
                    1e-6,
                    float(
                        constraints.get(
                            "targetInterfaceAreaRatio",
                            constraints.get("minimumInterfaceAreaRatio", 1.0),
                        )
                    ),
                )
                target_clearance = max(
                    1e-6,
                    float(
                        constraints.get(
                            "targetDamageClearanceSections",
                            max(buffer_sections, 0.25),
                        )
                    ),
                )
                engagement_penalty = abs(
                    float(quality["engagementSections"]) - target_engagement
                ) / target_engagement
                interface_penalty = abs(
                    float(quality["interfaceAreaRatio"]) - target_interface
                ) / target_interface
                clearance_penalty = max(0.0, target_clearance - clearance) / target_clearance
                fabrication_penalty = max(0.0, support_count - 1.0) / 5.0
                weights = constraints.get("rankingWeights") or {}
                rank_score = (
                    float(weights.get("damageRobustness", 0.35)) * clearance_penalty
                    + float(weights.get("engagement", 0.25)) * engagement_penalty
                    + float(weights.get("interface", 0.15)) * interface_penalty
                    + float(weights.get("fabrication", 0.15)) * fabrication_penalty
                    + float(weights.get("conservation", 0.10)) * loss
                )
            metrics.update(
                candidate_id=candidate.candidate_id,
                family=candidate.family,
                parameters=dict(candidate.parameters),
                position=placement["position"],
                rotate_deg=placement["rotate_deg"],
                side=placement["side"],
                interface_scale=placement["interface_scale"],
                support_plane_count=support_count,
                predicate_count=predicate_count,
                complexity=complexity,
                rank_score=rank_score,
                construction_metrics=dict(quality),
                construction_failures=[],
                required_removal_count=int(required.sum()),
                required_left=required_left,
                damage_clearance_sections=clearance,
                repair=repair,
                joint=joint,
            )
            results.append(metrics)

    results.sort(
        key=lambda item: (
            item["required_left"],
            item["rank_score"],
            item["sound_sacrificed_weighted"],
            item["support_plane_count"],
            item["candidate_id"],
        )
    )
    report.append(
        "tested %d placements after %d plane-program rejection(s); %d satisfy "
        "damage, buffer and construction gates; %d geometric failures"
        % (built, rejected_geometry, covered, failed)
    )
    if results:
        best = results[0]
        report.append(
            "best %s %s at %.2f, rot %.0f, side %+d: clearance %.2f sections; "
            "conservation loss %d/%d sound cells"
            % (
                "full-coverage" if best["required_left"] == 0 else "partial",
                best["candidate_id"],
                best["position"],
                best["rotate_deg"],
                best["side"],
                best["damage_clearance_sections"],
                best["sound_sacrificed"],
                best["n_sound"],
            )
        )
    else:
        report.append("no plane program satisfies the current construction contract")
    return results, report


def shortlist(results, count: int = 4):
    """Return a compact, geometrically diverse shortlist."""
    selected = []
    seen_candidates = set()
    for result in results:
        # First show the best placement of each generated shape.  Otherwise a
        # dense axial search can fill the viewport with almost identical
        # copies of one candidate and hide the actual grammar alternatives.
        candidate_id = result["candidate_id"]
        if candidate_id in seen_candidates:
            continue
        seen_candidates.add(candidate_id)
        selected.append(result)
        if len(selected) >= max(1, int(count)):
            break
    return selected


def result_summary(result: dict) -> str:
    p = result["parameters"]
    if result["family"] == "any_joint":
        construction = result.get("construction_metrics") or {}
        shape = "AnyJoint engagement %.2f, interface %.2f, ligament %.2f" % (
            float(construction.get("engagementSections", 0.0)),
            float(construction.get("interfaceAreaRatio", 0.0)),
            float(construction.get("medianLigamentRatio", 0.0)),
        )
    elif result["family"] == "scarf":
        shape = "scarf slope %.2f" % float(p["slope"])
    elif result["family"] == "lapped_bowtie":
        shape = "positive lock depth %.2f, root %.2f, half-width %.2f" % (
            float(p["lap_fraction"]),
            float(p["root_fraction"]),
            float(p["lock_half_width"]),
        )
    else:
        shape = "depth %.2f, chevron %.3f, rake %.2f/%.2f" % (
            float(p["lap_fraction"]),
            float(p["chevron"]),
            float(p["rake_left"]),
            float(p["rake_right"]),
        )
    return (
        "%s | %s | pos %.3f rot %.0f side %+d | construction PASS | "
        "clearance %.2f sections | sound %d/%d (weighted %.2f) | %d support planes"
        % (
            result["candidate_id"],
            shape,
            float(result["position"]),
            float(result["rotate_deg"]),
            int(result["side"]),
            float(result.get("damage_clearance_sections", 0.0)),
            int(result["sound_sacrificed"]),
            int(result["n_sound"]),
            float(result["sound_sacrificed_weighted"]),
            int(result["support_plane_count"]),
        )
    )
