"""Adaptive six-plane repair-joint generation and fitting.

This is the deterministic core used by the Grasshopper ``AnyJoint``
component.  It generates a bounded family of planar splice joints, places
them around a cellular damage field, rejects every placement that leaves a
mandatory-damage cell behind, and ranks the survivors by sound timber loss
and geometric complexity.

The named SJ joints remain useful precedents.  Search here is over grammar
parameters rather than catalogue keys:

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
    verify: bool = True,
):
    """Fit generated plane joints around mandatory damaged cells.

    ``threshold`` creates the hard mandatory-removal mask.  All damage values
    still participate in ``sound_sacrificed_weighted`` so the ranking remains
    continuous around the threshold.

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

    section = min(float(frame["width"]), float(frame["height"]))
    need = (v1 - v0) + 2.0 * float(margin) * section
    candidates = list(grammar) if grammar is not None else default_grammar()
    report.append(
        "mandatory damage: %d/%d cells, axial %.3f..%.3f m; %d grammar variants"
        % (int(mandatory.sum()), len(points), v0, v1, len(candidates))
    )

    results = []
    built = covered = failed = 0
    for candidate in candidates:
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
            if (mandatory & ~removed).any():
                continue
            covered += 1
            metrics = scoring.score(points, values, repair, threshold, verify=False)
            sound_denominator = max(1, metrics["n_sound"])
            loss = metrics["sound_sacrificed_weighted"] / sound_denominator
            rank_score = loss + float(complexity_weight) * complexity
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
                repair=repair,
                joint=joint,
            )
            results.append(metrics)

    results.sort(
        key=lambda item: (
            item["rank_score"],
            item["sound_sacrificed_weighted"],
            item["support_plane_count"],
            item["candidate_id"],
        )
    )
    report.append(
        "tested %d placements; %d cover mandatory damage; %d geometric failures"
        % (built, covered, failed)
    )
    if results:
        best = results[0]
        report.append(
            "best %s at %.2f, rot %.0f, side %+d: removes %d/%d sound cells"
            % (
                best["candidate_id"],
                best["position"],
                best["rotate_deg"],
                best["side"],
                best["sound_sacrificed"],
                best["n_sound"],
            )
        )
    else:
        report.append("no feasible candidate in the current grammar/search window")
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
    if result["family"] == "scarf":
        shape = "scarf slope %.2f" % float(p["slope"])
    else:
        shape = "depth %.2f, chevron %.3f, rake %.2f/%.2f" % (
            float(p["lap_fraction"]),
            float(p["chevron"]),
            float(p["rake_left"]),
            float(p["rake_right"]),
        )
    return (
        "%s | %s | pos %.3f rot %.0f side %+d | damage PASS | "
        "sound %d/%d (weighted %.2f) | %d support planes"
        % (
            result["candidate_id"],
            shape,
            float(result["position"]),
            float(result["rotate_deg"]),
            int(result["side"]),
            int(result["sound_sacrificed"]),
            int(result["n_sound"]),
            float(result["sound_sacrificed_weighted"]),
            int(result["support_plane_count"]),
        )
    )
