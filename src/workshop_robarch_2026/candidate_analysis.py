"""Neutral Rhino measurements for an authored repair candidate.

The analyser describes geometry and available construction evidence.  It only
evaluates a requirement when the candidate carries a sourced, machine-readable
test; free-text requirements remain visibly unresolved for human review.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from typing import Any, Iterable

from . import repair_candidate
from .candidate_runtime import CandidateContext, coerce_geometry, runtime_signature


MATERIAL_EFFECTS = ("add", "remove", "retain", "reference")


def _object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("expected one JSON object")
    return result


def _records(value: Any) -> list[dict]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    obj = _object(value)
    raw = obj.get("entities") or obj.get("items") or []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _fact(fact_id: str, status: str, value: Any = None, **details: Any) -> dict:
    if status == "measured":
        return repair_candidate.fact_record(fact_id, status, value, **details)
    return repair_candidate.fact_record(fact_id, status, **details)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_") or "unspecified"


def _effect_kind(record: dict) -> str:
    value = str(record.get("materialEffect") or "").strip().lower()
    return value if value in MATERIAL_EFFECTS else "other"


def _bbox(geometry: Any) -> Any:
    return geometry.GetBoundingBox(True)


def _bbox_values(box: Any) -> dict:
    return {
        "min": [float(box.Min.X), float(box.Min.Y), float(box.Min.Z)],
        "max": [float(box.Max.X), float(box.Max.Y), float(box.Max.Z)],
        "extent": [float(box.Diagonal.X), float(box.Diagonal.Y), float(box.Diagonal.Z)],
    }


def _volume(geometry: Any) -> float | None:
    import Rhino.Geometry as rg

    try:
        props = rg.VolumeMassProperties.Compute(geometry)
        return float(props.Volume) if props else None
    except Exception:
        return None


def _is_solid(geometry: Any) -> bool:
    import Rhino.Geometry as rg

    if isinstance(geometry, rg.Brep):
        return bool(geometry.IsSolid)
    if isinstance(geometry, rg.Mesh):
        return bool(geometry.IsClosed)
    for type_name in ("Extrusion", "SubD"):
        kind = getattr(rg, type_name, None)
        if kind is not None and isinstance(geometry, kind):
            return bool(getattr(geometry, "IsSolid", False))
    return False


def _solid_for_containment(geometry: Any) -> Any:
    """Return a Brep/Mesh with point-containment support when possible."""
    if not _is_solid(geometry):
        return None
    if hasattr(geometry, "IsPointInside"):
        return geometry
    method = getattr(geometry, "ToBrep", None)
    if callable(method):
        try:
            converted = method()
            if converted is not None and getattr(converted, "IsSolid", False):
                return converted
        except Exception:
            pass
    return None


def _bbox_disjoint(first: Any, second: Any, tolerance: float) -> bool:
    a, b = _bbox(first), _bbox(second)
    return (
        a.Max.X < b.Min.X - tolerance or b.Max.X < a.Min.X - tolerance
        or a.Max.Y < b.Min.Y - tolerance or b.Max.Y < a.Min.Y - tolerance
        or a.Max.Z < b.Min.Z - tolerance or b.Max.Z < a.Min.Z - tolerance
    )


def _point_inside(geometry: Any, point: Any, tolerance: float) -> bool | None:
    testable = _solid_for_containment(geometry)
    if testable is None:
        return None
    try:
        return bool(testable.IsPointInside(point, tolerance, False))
    except Exception:
        return None
    return None


def _intersection_brep(geometry: Any) -> Any:
    """Return a Brep for exact pair tests, without changing the source object."""
    import Rhino.Geometry as rg

    if isinstance(geometry, rg.Brep):
        return geometry
    method = getattr(geometry, "ToBrep", None)
    if callable(method):
        try:
            value = method()
            return value if isinstance(value, rg.Brep) else None
        except Exception:
            return None
    return None


def _touches(first: Any, second: Any, tolerance: float) -> tuple[bool | None, str]:
    """Return exact contact when available; ``None`` means unresolved."""
    if _bbox_disjoint(first, second, tolerance):
        return False, "bounding_boxes"
    try:
        import Rhino.Geometry as rg

        first_brep = _intersection_brep(first)
        second_brep = _intersection_brep(second)
        if first_brep is not None and second_brep is not None:
            success, curves, points = rg.Intersect.Intersection.BrepBrep(
                first_brep, second_brep, tolerance
            )
            if curves or points:
                return True, "brep_intersection"
            for vertex in list(first_brep.Vertices)[:8]:
                if _point_inside(second_brep, vertex.Location, tolerance):
                    return True, "containment"
            for vertex in list(second_brep.Vertices)[:8]:
                if _point_inside(first_brep, vertex.Location, tolerance):
                    return True, "containment"
            if success:
                return False, "brep_intersection"
            return None, "brep_intersection_failed"
    except Exception:
        return None, "intersection_failed"
    return None, "bounding_box_overlap_only"


def _pair_interaction(
    first: Any, second: Any, tolerance: float
) -> tuple[str, float | None, str, list[Any]]:
    """Classify one pair as clear, contact, overlap, or unknown."""
    touching, method = _touches(first, second, tolerance)
    if touching is False:
        return "clear", 0.0, method, []
    first_brep = _intersection_brep(first)
    second_brep = _intersection_brep(second)
    if first_brep is not None and second_brep is not None:
        try:
            import Rhino.Geometry as rg

            overlaps = list(
                rg.Brep.CreateBooleanIntersection(first_brep, second_brep, tolerance) or []
            )
            volumes = [_volume(item) for item in overlaps]
            if any(value is None for value in volumes):
                return "unknown", None, "boolean_intersection_volume_failed", overlaps
            volume = sum(volumes)
            if volume > tolerance ** 3:
                return "overlap", volume, "boolean_intersection", overlaps
            if touching is True:
                return "contact", 0.0, method, overlaps
            if touching is False:
                return "clear", 0.0, method, overlaps
            return "unknown", None, method, overlaps
        except Exception:
            return "unknown", None, "boolean_intersection_failed", []
    if touching is True:
        return "contact", 0.0, method, []
    return "unknown", None, method, []


def _components(
    indices: list[int], geometry: list[Any], tolerance: float
) -> tuple[list[list[int]], list[list[int]]]:
    neighbours = {index: set() for index in indices}
    unresolved = []
    for offset, first in enumerate(indices):
        for second in indices[offset + 1 :]:
            touching, _ = _touches(geometry[first], geometry[second], tolerance)
            if touching is True:
                neighbours[first].add(second)
                neighbours[second].add(first)
            elif touching is None:
                unresolved.append([first, second])
    groups, unseen = [], set(indices)
    while unseen:
        stack, group = [unseen.pop()], []
        while stack:
            item = stack.pop()
            group.append(item)
            linked = neighbours[item] & unseen
            unseen.difference_update(linked)
            stack.extend(linked)
        groups.append(sorted(group))
    return groups, unresolved


def _inventory(geometry: list[Any], entities: list[dict], tolerance: float) -> tuple[list[dict], dict]:
    import Rhino.Geometry as rg

    facts = []
    details = []
    union_box = rg.BoundingBox.Empty
    volumes = []
    missing_solid_volume = False
    valid_count = solid_count = 0
    for index, item in enumerate(geometry):
        record = entities[index] if index < len(entities) else {}
        valid = bool(getattr(item, "IsValid", True))
        solid = _is_solid(item)
        volume = _volume(item)
        box = _bbox(item)
        union_box.Union(box)
        valid_count += int(valid)
        solid_count += int(solid)
        if solid and volume is not None:
            volumes.append(volume)
        elif solid:
            missing_solid_volume = True
        details.append(
            {
                "id": record.get("id", "geometry_{}".format(index + 1)),
                "geometryIndex": index,
                "type": type(item).__name__,
                "valid": valid,
                "closedSolid": solid,
                "volume": volume,
                "boundingBox": _bbox_values(box),
                "role": record.get("role", "unspecified"),
                "effect": record.get("effect", "unspecified"),
                "materialEffect": record.get("materialEffect"),
                "effectKind": _effect_kind(record),
            }
        )
    indices = list(range(len(geometry)))
    components, unresolved_pairs = (
        _components(indices, geometry, tolerance) if indices else ([], [])
    )
    facts.extend(
        [
            _fact("geometry.output_count", "measured", len(geometry), unit="geometry items"),
            _fact("geometry.valid_count", "measured", valid_count, unit="geometry items"),
            _fact("geometry.closed_solid_count", "measured", solid_count, unit="geometry items"),
            _fact(
                "geometry.known_contact_component_count",
                "measured",
                len(components),
                method="exact-contact graph; unresolved pairs are kept separate",
            ),
        ]
    )
    if unresolved_pairs:
        facts.append(
            _fact(
                "geometry.connected_component_count",
                "unknown",
                note="{} geometry pair(s) could not be tested exactly".format(
                    len(unresolved_pairs)
                ),
            )
        )
    else:
        facts.append(
            _fact(
                "geometry.connected_component_count",
                "measured",
                len(components),
                method="exact-contact graph",
            )
        )
    if missing_solid_volume:
        facts.append(
            _fact(
                "geometry.total_closed_volume",
                "failed_to_compute",
                reason="volume properties failed for at least one closed solid",
            )
        )
    else:
        facts.append(
            _fact(
                "geometry.total_closed_volume",
                "measured",
                sum(volumes),
                unit="model_units^3",
            )
        )
    if geometry:
        facts.append(_fact("geometry.world_bounds", "measured", _bbox_values(union_box)))
    else:
        facts.append(_fact("geometry.world_bounds", "not_applicable", note="candidate emitted no geometry"))

    grouped = defaultdict(list)
    for index, record in enumerate(entities[: len(geometry)]):
        grouped[_effect_kind(record)].append(index)
    effect_components = {}
    for kind, group in sorted(grouped.items()):
        comps, unresolved = _components(group, geometry, tolerance)
        effect_components[kind] = comps
        facts.append(_fact("geometry.{}.output_count".format(kind), "measured", len(group)))
        if unresolved:
            facts.append(
                _fact(
                    "geometry.{}.component_count".format(kind),
                    "unknown",
                    note="{} pair(s) could not be tested exactly".format(len(unresolved)),
                )
            )
        else:
            facts.append(
                _fact("geometry.{}.component_count".format(kind), "measured", len(comps))
            )
        solid_indices = [index for index in group if _is_solid(geometry[index])]
        group_volumes = [_volume(geometry[index]) for index in solid_indices]
        if any(value is None for value in group_volumes):
            facts.append(
                _fact(
                    "geometry.{}.closed_volume".format(kind),
                    "failed_to_compute",
                    reason="volume properties failed for at least one closed solid",
                )
            )
        else:
            facts.append(
                _fact(
                    "geometry.{}.closed_volume".format(kind),
                    "measured",
                    sum(group_volumes),
                    unit="model_units^3",
                )
            )
    return facts, {
        "items": details,
        "components": components,
        "unresolvedComponentPairs": unresolved_pairs,
        "effectComponents": effect_components,
    }


def _condition_facts(
    candidate: dict, geometry: list[Any], entities: list[dict], ctx: CandidateContext
) -> tuple[list[dict], dict]:
    kinds = [_effect_kind(record) for record in entities]
    retain_all = [index for index, kind in enumerate(kinds) if kind == "retain"]
    remove_all = [index for index, kind in enumerate(kinds) if kind == "remove"]
    added_all = [index for index, kind in enumerate(kinds) if kind == "add"]
    retain = [index for index in retain_all if _is_solid(geometry[index])]
    remove = [index for index in remove_all if _is_solid(geometry[index])]
    added = [index for index in added_all if _is_solid(geometry[index])]
    material_config = _analysis_config(candidate, "material") or {}
    exhaustive_retain = bool(material_config.get("retainedSetIsComplete"))
    facts = [
        _fact("condition.cell_count", "measured", len(ctx.centers), unit="cells"),
        _fact("condition.damaged_cell_count", "measured", len(ctx.damaged_points), unit="cells"),
        _fact("condition.sound_cell_count", "measured", len(ctx.sound_points), unit="cells"),
    ]
    if not ctx.centers:
        facts.append(_fact("condition.removal", "not_applicable", note="centers and damage are empty"))
        return facts, {}
    can_measure_removal = bool(remove) or (exhaustive_retain and bool(retain))
    if not can_measure_removal:
        if (retain_all or remove_all) and not retain and not remove:
            note = "identified retain/remove effects are not measurable closed solids"
        elif retain and not exhaustive_retain:
            note = "retained outputs were not declared as the complete post-cut remainder"
        else:
            note = "no explicit removal volume or complete retained set was supplied"
        facts.extend(
            [
                _fact("condition.damaged_cells_removed", "unknown", note=note),
                _fact("condition.sound_cells_removed", "unknown", note=note),
            ]
        )
        removed_flags = [None] * len(ctx.centers)
    else:
        removed_flags = []

        def inside_any(indices, point):
            values = [_point_inside(geometry[index], point, ctx.tolerance) for index in indices]
            if any(value is True for value in values):
                return True
            if any(value is None for value in values):
                return None
            return False

        for point in ctx.centers:
            in_remove = inside_any(remove, point) if remove else False
            outside_retain = False
            if exhaustive_retain:
                in_retain = inside_any(retain, point)
                outside_retain = None if in_retain is None else not in_retain
            if in_remove is True or outside_retain is True:
                removed_flags.append(True)
            elif in_remove is None or outside_retain is None:
                removed_flags.append(None)
            else:
                removed_flags.append(False)

        damaged_count, sound_count = len(ctx.damaged_points), len(ctx.sound_points)
        for label, selected, count in (
            ("damaged", [flag for flag, value in zip(removed_flags, ctx.damage) if value >= ctx.threshold], damaged_count),
            ("sound", [flag for flag, value in zip(removed_flags, ctx.damage) if value < ctx.threshold], sound_count),
        ):
            known_removed = sum(flag is True for flag in selected)
            unknown = sum(flag is None for flag in selected)
            facts.append(
                _fact("condition.{}_cells_removal_unknown".format(label), "measured", unknown, unit="cells")
            )
            if unknown:
                facts.append(
                    _fact(
                        "condition.{}_cells_removed".format(label),
                        "unknown",
                        note="{} cell containment result(s) could not be computed".format(unknown),
                    )
                )
                if label == "damaged":
                    facts.append(
                        _fact("condition.damaged_cells_remaining", "unknown", note="removal classification is incomplete")
                    )
                facts.append(
                    _fact("condition.{}_removal_fraction".format(label), "unknown", note="removal classification is incomplete")
                )
            else:
                facts.append(
                    _fact("condition.{}_cells_removed".format(label), "measured", known_removed, unit="cells")
                )
                if label == "damaged":
                    facts.append(
                        _fact("condition.damaged_cells_remaining", "measured", count - known_removed, unit="cells")
                    )
                facts.append(
                    _fact("condition.{}_removal_fraction".format(label), "measured", known_removed / count)
                    if count
                    else _fact(
                        "condition.{}_removal_fraction".format(label),
                        "not_applicable",
                        note="there are no {} cells at this threshold".format(label),
                    )
                )

    if added:
        occupied = []
        component_sets = []
        add_components, _ = _components(added, geometry, ctx.tolerance)
        component_of = {
            index: component_index
            for component_index, component in enumerate(add_components)
            for index in component
        }
        for point, value in zip(ctx.centers, ctx.damage):
            if value < ctx.threshold:
                continue
            memberships = {
                index: _point_inside(geometry[index], point, ctx.tolerance)
                for index in added
            }
            hits = [index for index, state in memberships.items() if state is True]
            occupied.append(True if hits else (None if any(state is None for state in memberships.values()) else False))
            component_sets.extend(component_of[index] for index in hits)
        unknown = sum(value is None for value in occupied)
        if unknown:
            facts.append(
                _fact(
                    "condition.damaged_cells_occupied_by_added_geometry",
                    "unknown",
                    note="{} point-containment result(s) could not be computed".format(unknown),
                )
            )
        else:
            facts.append(
                _fact(
                    "condition.damaged_cells_occupied_by_added_geometry",
                    "measured",
                    sum(value is True for value in occupied),
                    unit="cells",
                )
            )
        facts.append(
            _fact(
                "condition.added_components_at_damaged_cells",
                "measured",
                sorted(set(component_sets)),
                method="point-in-solid and contact graph",
            )
        )
    elif added_all:
        facts.append(
            _fact("condition.damaged_cells_occupied_by_added_geometry", "unknown", note="identified added effects are not closed solids")
        )
    else:
        facts.append(
            _fact("condition.damaged_cells_occupied_by_added_geometry", "not_applicable", note="no added effect was identified")
        )
    return facts, {"removedFlags": removed_flags, "retainedSetIsComplete": exhaustive_retain}


def _end_contact_facts(
    geometry: list[Any], entities: list[dict], ctx: CandidateContext
) -> list[dict]:
    import Rhino.Geometry as rg

    half_u, half_v = 0.5 * ctx.section_size[0], 0.5 * ctx.section_size[1]
    end_faces = {}
    try:
        for name, point in (("start", ctx.start), ("end", ctx.end)):
            plane = rg.Plane(point, ctx.section_u, ctx.section_v)
            boundary = rg.Rectangle3d(
                plane,
                rg.Interval(-half_u, half_u),
                rg.Interval(-half_v, half_v),
            ).ToNurbsCurve()
            faces = list(rg.Brep.CreatePlanarBreps(boundary, ctx.tolerance) or [])
            if not faces:
                raise ValueError("could not create target {} end face".format(name))
            end_faces[name] = faces[0]
    except Exception as exc:
        facts = [_fact("interfaces.target_end_contacts", "failed_to_compute", reason=str(exc))]
        for name in ("start", "end"):
            for label in ("", "added_", "retained_"):
                facts.append(
                    _fact(
                        "interfaces.target_{}_{}contact_count".format(name, label),
                        "failed_to_compute",
                        reason=str(exc),
                    )
                )
        return facts

    contacts = {"start": [], "end": []}
    unresolved = {"start": [], "end": []}
    for index, item in enumerate(geometry):
        record = entities[index] if index < len(entities) else {}
        if not _is_solid(item) or _effect_kind(record) not in ("retain", "add"):
            continue
        for name, face in end_faces.items():
            touching, method = _touches(item, face, ctx.tolerance)
            if touching:
                contacts[name].append(
                    {
                        "entityId": record.get("id", "geometry_{}".format(index + 1)),
                        "role": record.get("role", "unspecified"),
                        "effect": record.get("effect", "unspecified"),
                        "materialEffect": _effect_kind(record),
                        "method": method,
                    }
                )
            elif touching is None:
                unresolved[name].append(
                    {
                        "entityId": record.get("id", "geometry_{}".format(index + 1)),
                        "materialEffect": _effect_kind(record),
                        "method": method,
                    }
                )
    facts = [
        _fact(
            "interfaces.target_end_contacts",
            "measured",
            {"contacts": contacts, "unresolved": unresolved},
            method="member-end face intersection on added and retained solids",
        )
    ]
    for name in ("start", "end"):
        for effect, label in ((None, ""), ("add", "added_"), ("retain", "retained_")):
            selected_contacts = [
                item for item in contacts[name]
                if effect is None or item.get("materialEffect") == effect
            ]
            selected_unknown = [
                item for item in unresolved[name]
                if effect is None or item.get("materialEffect") == effect
            ]
            fact_id = "interfaces.target_{}_{}contact_count".format(name, label)
            if selected_unknown:
                facts.append(
                    _fact(
                        fact_id,
                        "unknown",
                        note="{} candidate/end-face pair(s) could not be tested exactly".format(
                            len(selected_unknown)
                        ),
                    )
                )
            else:
                facts.append(
                    _fact(
                        fact_id,
                        "measured",
                        len(selected_contacts),
                        unit="geometry items",
                    )
                )
            facts.append(
                _fact(
                    "interfaces.target_{}_{}contact_unknown_count".format(name, label),
                    "measured",
                    len(selected_unknown),
                    unit="geometry items",
                )
            )
    return facts


def _neighbour_facts(
    geometry: list[Any], entities: list[dict], ctx: CandidateContext
) -> tuple[list[dict], list[Any]]:
    if not ctx.neighbours:
        return [
            _fact("interfaces.neighbour_interactions", "unknown", note="neighbour_geometry is not connected"),
            _fact("interfaces.neighbour_overlap_count", "unknown", note="neighbour_geometry is not connected"),
            _fact("interfaces.neighbour_contact_count", "unknown", note="neighbour_geometry is not connected"),
            _fact("interfaces.neighbour_added_overlap_count", "unknown", note="neighbour_geometry is not connected"),
            _fact("interfaces.neighbour_added_contact_count", "unknown", note="neighbour_geometry is not connected"),
        ], []

    rows, diagnostics = [], []
    for index, item in enumerate(geometry):
        entity = entities[index] if index < len(entities) else {}
        material_effect = _effect_kind(entity)
        if material_effect not in ("add", "retain"):
            continue
        for neighbour_id, neighbour in ctx._neighbours.items():
            interaction, volume, method, overlaps = _pair_interaction(
                item, neighbour, ctx.tolerance
            )
            diagnostics.extend(overlaps)
            rows.append(
                {
                    "entityId": entity.get("id", "geometry_{}".format(index + 1)),
                    "neighbourId": neighbour_id,
                    "materialEffect": material_effect,
                    "interaction": interaction,
                    "overlapVolume": volume,
                    "declaredRelation": neighbour_id in (entity.get("relatesTo") or []),
                    "method": method,
                }
            )
    counts = {
        name: sum(row["interaction"] == name for row in rows)
        for name in ("overlap", "contact", "clear", "unknown")
    }
    facts = [_fact("interfaces.neighbour_interactions", "measured", rows)]
    for name, count in counts.items():
        fact_id = "interfaces.neighbour_{}_count".format(name)
        if name != "unknown" and counts["unknown"]:
            facts.append(
                _fact(fact_id, "unknown", note="some candidate-neighbour pairs were unresolved")
            )
        else:
            facts.append(
                _fact(fact_id, "measured", count, unit="candidate-neighbour pairs")
            )
    for effect, label in (("add", "added"), ("retain", "retained")):
        selected = [row for row in rows if row.get("materialEffect") == effect]
        selected_unknown = sum(row["interaction"] == "unknown" for row in selected)
        for name in ("overlap", "contact", "clear", "unknown"):
            fact_id = "interfaces.neighbour_{}_{}_count".format(label, name)
            if name != "unknown" and selected_unknown:
                facts.append(
                    _fact(fact_id, "unknown", note="some {}-neighbour pairs were unresolved".format(label))
                )
            else:
                facts.append(
                    _fact(
                        fact_id,
                        "measured",
                        sum(row["interaction"] == name for row in selected),
                        unit="candidate-neighbour pairs",
                    )
                )
    return facts, diagnostics


def _analysis_config(candidate: dict, name: str) -> dict | None:
    analysis = candidate.get("analysis") or candidate.get("checks") or {}
    value = analysis.get(name) if isinstance(analysis, dict) else None
    return value if isinstance(value, dict) else None


def _entity_indices(
    entities: list[dict], refs: Iterable[str]
) -> tuple[list[int], list[str]]:
    wanted = [str(value) for value in refs]
    wanted_set = set(wanted)
    indices = [
        index for index, item in enumerate(entities)
        if str(item.get("id")) in wanted_set or str(item.get("groupId")) in wanted_set
    ]
    missing = [
        ref for ref in wanted
        if not any(
            str(item.get("id")) == ref or str(item.get("groupId")) == ref
            for item in entities
        )
    ]
    return indices, missing


def _insertion_facts(
    candidate: dict, geometry: list[Any], entities: list[dict], ctx: CandidateContext
) -> tuple[list[dict], list[Any]]:
    fact_ids = (
        "assembly.insertion_sampled",
        "assembly.insertion_sampled_penetration_count",
        "assembly.insertion_sampled_target_penetration_count",
        "assembly.insertion_sampled_neighbour_penetration_count",
        "assembly.insertion_sampled_contact_count",
        "assembly.insertion_sampled_unknown_count",
    )

    def unavailable(status, note):
        return [_fact(fact_id, status, note=note) for fact_id in fact_ids], []

    config = _analysis_config(candidate, "insertion")
    if config is None:
        return unavailable("not_applicable", "candidate declares no insertion analysis")
    refs = config.get("movingOutputRefs") or []
    offset = config.get("startOffset")
    indices, missing_refs = _entity_indices(entities, refs)
    if missing_refs:
        return unavailable(
            "unknown",
            "movingOutputRefs did not resolve: {}".format(", ".join(missing_refs)),
        )
    if not indices or not isinstance(offset, (list, tuple)) or len(offset) != 3:
        return unavailable(
            "unknown", "movingOutputRefs and a three-number startOffset are required"
        )
    import Rhino.Geometry as rg
    from .candidate_runtime import duplicate_geometry

    try:
        samples = max(2, min(30, int(config.get("samples", 8))))
        vector = rg.Vector3d(float(offset[0]), float(offset[1]), float(offset[2]))
    except (TypeError, ValueError, OverflowError):
        return unavailable("unknown", "insertion samples/startOffset must be finite numbers")
    distance = float(vector.Length)
    if not math.isfinite(distance) or distance <= ctx.tolerance:
        return unavailable("unknown", "startOffset must describe a non-zero insertion movement")
    retain_indices = [
        index for index, record in enumerate(entities)
        if index not in indices
        and _effect_kind(record) == "retain"
        and _is_solid(geometry[index])
    ]
    remove_indices = [
        index for index, record in enumerate(entities)
        if _effect_kind(record) == "remove" and _is_solid(geometry[index])
    ]
    complete_retain = bool(
        (_analysis_config(candidate, "material") or {}).get("retainedSetIsComplete")
    )
    target_status = "unknown"
    target_note = "target remainder needs removal solids or a complete retained set"
    target_obstacles = []
    if complete_retain and retain_indices:
        target_obstacles = [
            ("target-retained:" + entities[index].get("id", str(index)), geometry[index], "target")
            for index in retain_indices
        ]
        target_status, target_note = "measured", "complete retained set"
    elif remove_indices:
        try:
            remainder = [ctx.target]
            for index in remove_indices:
                next_parts = []
                for part in remainder:
                    next_parts.extend(ctx.difference(part, [geometry[index]]))
                remainder = next_parts
            target_obstacles = [
                ("target-remainder:{}".format(index + 1), part, "target")
                for index, part in enumerate(remainder)
            ]
            target_status, target_note = "measured", "target box minus declared removal solids"
        except Exception as exc:
            target_note = "target remainder failed: {}".format(exc)
    obstacles = list(target_obstacles)
    obstacles.extend(
        ("neighbour:" + part_id, item, "neighbour")
        for part_id, item in ctx._neighbours.items()
    )
    if not obstacles:
        return unavailable("unknown", target_note + "; no neighbour geometry is available")
    interactions, diagnostics = [], []
    for step in range(samples):  # final, installed position is intentionally excluded
        factor = 1.0 - float(step) / samples
        transform = rg.Transform.Translation(factor * vector)
        for index in indices:
            moved = duplicate_geometry(geometry[index])
            moved.Transform(transform)
            for obstacle_id, obstacle, obstacle_scope in obstacles:
                interaction, volume, method, overlaps = _pair_interaction(
                    moved, obstacle, ctx.tolerance
                )
                if interaction == "clear":
                    continue
                if interaction == "overlap":
                    diagnostics.extend(overlaps)
                interactions.append(
                    {
                        "sample": step,
                        "fractionFromStart": round(float(step) / samples, 4),
                        "movingEntityId": entities[index].get("id"),
                        "obstacleId": obstacle_id,
                        "obstacleScope": obstacle_scope,
                        "interaction": interaction,
                        "overlapVolume": volume,
                        "method": method,
                    }
                )
    counts = {
        name: sum(row["interaction"] == name for row in interactions)
        for name in ("overlap", "contact", "unknown")
    }
    value = {
        "sampleCount": samples,
        "sampleSpacing": distance / samples,
        "startOffset": [float(value) for value in offset],
        "installedPositionExcluded": True,
        "targetObstacleStatus": target_status,
        "targetObstacleBasis": target_note,
        "checkedObstacleIds": [item[0] for item in obstacles],
        "interactions": interactions,
    }
    facts = [
        _fact(
            "assembly.insertion_sampled",
            "measured",
            value,
            method="discrete poses; this is not a continuous swept-volume proof",
        ),
        _fact(
            "assembly.insertion_sampled_unknown_count",
            "measured",
            counts["unknown"],
            unit="sampled moving-obstacle pairs",
        ),
    ]
    if counts["unknown"] or target_status != "measured":
        facts.append(
            _fact(
                "assembly.insertion_sampled_contact_count",
                "unknown",
                note="target completeness or pair classification remains unknown",
            )
        )
    else:
        facts.append(
            _fact(
                "assembly.insertion_sampled_contact_count",
                "measured",
                counts["contact"],
                unit="sampled moving-obstacle pairs",
            )
        )
    for scope in ("target", "neighbour"):
        selected = [row for row in interactions if row.get("obstacleScope") == scope]
        unknown = sum(row["interaction"] == "unknown" for row in selected)
        status = "unknown" if unknown or (scope == "target" and target_status != "measured") else "measured"
        fact_id = "assembly.insertion_sampled_{}_penetration_count".format(scope)
        if status == "unknown":
            facts.append(
                _fact(fact_id, "unknown", note=target_note if scope == "target" else "some neighbour pairs were unresolved")
            )
        else:
            facts.append(
                _fact(
                    fact_id,
                    "measured",
                    sum(row["interaction"] == "overlap" for row in selected),
                    unit="sampled moving-obstacle pairs",
                )
            )
    if counts["unknown"] or target_status != "measured":
        facts.append(
            _fact(
                "assembly.insertion_sampled_penetration_count",
                "unknown",
                note="target completeness or {} sampled pair classification(s) remain unknown".format(counts["unknown"]),
            )
        )
    else:
        facts.append(
            _fact(
                "assembly.insertion_sampled_penetration_count",
                "measured",
                counts["overlap"],
                unit="sampled moving-obstacle pairs",
            )
        )
    return facts, diagnostics


def _tool_facts(
    candidate: dict, geometry: list[Any], entities: list[dict], ctx: CandidateContext
) -> tuple[list[dict], list[Any]]:
    config = _analysis_config(candidate, "tool")
    if config is None:
        return [
            _fact("fabrication.tool_radius", "not_applicable", note="candidate declares no tool analysis"),
            _fact("fabrication.tool_uncovered_cut_volume", "not_applicable", note="candidate declares no tool analysis"),
            _fact("fabrication.tool_excess_obstacle_overlap_volume", "not_applicable", note="candidate declares no tool analysis"),
        ], []
    try:
        radius = float(config.get("radius"))
        if not math.isfinite(radius) or radius <= 0:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        note = "tool radius must be a positive finite number"
        return [
            _fact("fabrication.tool_radius", "unknown", note=note),
            _fact("fabrication.tool_uncovered_cut_volume", "unknown", note=note),
            _fact("fabrication.tool_excess_obstacle_overlap_volume", "unknown", note=note),
        ], []
    path_indices, missing_paths = _entity_indices(
        entities, config.get("pathOutputRefs") or []
    )
    cut_indices, missing_cuts = _entity_indices(
        entities, config.get("cutOutputRefs") or []
    )
    if missing_paths or missing_cuts:
        missing = missing_paths + missing_cuts
        note = "configured tool output refs did not resolve: {}".format(", ".join(missing))
        return [
            _fact("fabrication.tool_radius", "measured", radius, unit="model_units"),
            _fact("fabrication.tool_sweep_count", "unknown", note=note),
            _fact("fabrication.tool_sweep_coverage", "unknown", note=note),
            _fact("fabrication.tool_uncovered_cut_volume", "unknown", note=note),
            _fact("fabrication.tool_excess_obstacle_overlap_volume", "unknown", note=note),
        ], []
    if not path_indices:
        return [
            _fact("fabrication.tool_radius", "measured", radius, unit="model_units"),
            _fact("fabrication.tool_sweep_coverage", "unknown", note="no pathOutputRefs geometry is available"),
            _fact("fabrication.tool_uncovered_cut_volume", "unknown", note="no pathOutputRefs geometry is available"),
            _fact("fabrication.tool_excess_obstacle_overlap_volume", "unknown", note="no pathOutputRefs geometry is available"),
        ], []
    import Rhino
    import Rhino.Geometry as rg

    sweeps = []
    angle = float(getattr(Rhino.RhinoDoc.ActiveDoc, "ModelAngleToleranceRadians", math.radians(1.0)))
    for index in path_indices:
        curve = geometry[index]
        if not isinstance(curve, rg.Curve):
            continue
        try:
            sweeps.extend(
                list(rg.Brep.CreatePipe(curve, radius, False, rg.PipeCapMode.Flat, True, ctx.tolerance, angle) or [])
            )
        except Exception:
            pass
    facts = [
        _fact("fabrication.tool_radius", "measured", radius, unit="model_units"),
        _fact("fabrication.tool_sweep_count", "measured", len(sweeps), unit="closed sweeps"),
    ]
    if not sweeps or not cut_indices:
        facts.append(_fact("fabrication.tool_sweep_coverage", "unknown", note="both tool-path curves and cutOutputRefs are needed"))
        facts.append(_fact("fabrication.tool_uncovered_cut_volume", "unknown", note="both tool paths and cut volumes are needed"))
        facts.append(_fact("fabrication.tool_excess_obstacle_overlap_volume", "unknown", note="a valid tool sweep is needed"))
        return facts, []

    diagnostics = []
    try:
        cut_breps = []
        for index in cut_indices:
            brep = _intersection_brep(geometry[index])
            if brep is None or not _is_solid(geometry[index]):
                raise ValueError("cutOutputRefs must identify measurable closed solids")
            cut_breps.append((entities[index].get("id"), brep))

        def union_parts(parts, label):
            if len(parts) <= 1:
                return list(parts)
            result = list(rg.Brep.CreateBooleanUnion(parts, ctx.tolerance) or [])
            if not result:
                raise ValueError("{} boolean union failed".format(label))
            return result

        sweep_union = union_parts(sweeps, "tool sweep")
        cut_union = union_parts([item for _, item in cut_breps], "declared cut")

        def intersection_parts(base, cutters):
            parts = []
            for cutter in cutters:
                if _bbox_disjoint(base, cutter, ctx.tolerance):
                    continue
                result = list(
                    rg.Brep.CreateBooleanIntersection(base, cutter, ctx.tolerance) or []
                )
                if not result:
                    touching, _ = _touches(base, cutter, ctx.tolerance)
                    if touching is None:
                        raise ValueError("tool intersection could not be resolved")
                parts.extend(result)
            return parts

        def measured_volume(parts):
            values = [_volume(part) for part in parts]
            if any(value is None for value in values):
                raise ValueError("volume properties failed during tool analysis")
            return sum(values)

        def difference_parts(base, cutters):
            current = [base]
            for cutter in cutters:
                next_parts = []
                for part in current:
                    if _bbox_disjoint(part, cutter, ctx.tolerance):
                        next_parts.append(part)
                        continue
                    result = list(
                        rg.Brep.CreateBooleanDifference(part, cutter, ctx.tolerance) or []
                    )
                    if result:
                        next_parts.extend(result)
                        continue
                    intersection = intersection_parts(part, [cutter])
                    overlap = measured_volume(intersection)
                    part_volume = _volume(part)
                    if part_volume is None:
                        raise ValueError("difference volume properties failed")
                    if overlap >= part_volume - ctx.tolerance ** 3:
                        continue
                    if overlap <= ctx.tolerance ** 3:
                        next_parts.append(part)
                        continue
                    raise ValueError("tool boolean difference failed")
                current = next_parts
            return current

        coverage_rows = []
        uncovered_volume = 0.0
        for entity_id, cut in cut_breps:
            cut_volume = _volume(cut)
            if cut_volume is None:
                raise ValueError("cut volume properties could not be computed")
            uncovered_parts = difference_parts(cut, sweep_union)
            diagnostics.extend(uncovered_parts)
            uncovered = measured_volume(uncovered_parts)
            covered_volume = max(0.0, cut_volume - uncovered)
            uncovered_volume += uncovered
            coverage_rows.append(
                {
                    "cutEntityId": entity_id,
                    "cutVolume": cut_volume,
                    "coveredVolume": covered_volume,
                    "uncoveredVolume": uncovered,
                }
            )

        obstacle_rows = []
        excess_volume = 0.0
        obstacle_items = [("target:" + ctx.beam_id, ctx.target)]
        obstacle_items.extend(
            ("neighbour:" + part_id, item)
            for part_id, item in ctx._neighbours.items()
        )
        for obstacle_id, obstacle in obstacle_items:
            obstacle_brep = _intersection_brep(obstacle)
            if obstacle_brep is None:
                obstacle_rows.append(
                    {"obstacleId": obstacle_id, "status": "unknown", "reason": "not convertible to Brep"}
                )
                continue
            remaining_obstacle = difference_parts(obstacle_brep, cut_union)
            excess_parts = []
            for part in remaining_obstacle:
                excess_parts.extend(intersection_parts(part, sweep_union))
            excess = measured_volume(excess_parts)
            excess_volume += excess
            diagnostics.extend(excess_parts)
            obstacle_rows.append(
                {
                    "obstacleId": obstacle_id,
                    "status": "measured",
                    "excessOverlapVolume": excess,
                }
            )

        facts.extend(
            [
                _fact(
                    "fabrication.tool_sweep_coverage",
                    "measured",
                    {"cuts": coverage_rows, "obstacles": obstacle_rows},
                    method="declared path pipes compared with declared cuts, target box, and available neighbours",
                ),
                _fact(
                    "fabrication.tool_uncovered_cut_volume",
                    "measured",
                    uncovered_volume,
                    unit="model_units^3",
                ),
            ]
        )
        if any(row.get("status") == "unknown" for row in obstacle_rows):
            facts.append(
                _fact(
                    "fabrication.tool_excess_obstacle_overlap_volume",
                    "unknown",
                    note="one or more obstacle intersections could not be computed",
                )
            )
        elif obstacle_rows:
            facts.append(
                _fact(
                    "fabrication.tool_excess_obstacle_overlap_volume",
                    "measured",
                    excess_volume,
                    unit="model_units^3",
                )
            )
        else:
            facts.append(
                _fact(
                    "fabrication.tool_excess_obstacle_overlap_volume",
                    "not_applicable",
                    note="no target or neighbour obstacle geometry is available",
                )
            )
    except Exception as exc:
        facts.append(_fact("fabrication.tool_sweep_coverage", "failed_to_compute", reason=str(exc)))
        facts.append(_fact("fabrication.tool_uncovered_cut_volume", "failed_to_compute", reason=str(exc)))
        facts.append(_fact("fabrication.tool_excess_obstacle_overlap_volume", "failed_to_compute", reason=str(exc)))
    return facts, diagnostics


def _compare(observed: Any, operator: str, expected: Any) -> bool:
    scalar = (str, int, float, bool, type(None))
    if operator in ("eq", "ne", "lt", "lte", "gt", "gte"):
        if not isinstance(observed, scalar) or not isinstance(expected, scalar):
            raise ValueError("machine tests must reference a scalar fact")
        operations = {
            "eq": lambda a, b: a == b,
            "ne": lambda a, b: a != b,
            "lt": lambda a, b: a < b,
            "lte": lambda a, b: a <= b,
            "gt": lambda a, b: a > b,
            "gte": lambda a, b: a >= b,
        }
        return bool(operations[operator](observed, expected))
    if operator == "contains":
        if isinstance(observed, str) and isinstance(expected, str):
            return expected in observed
        if isinstance(observed, list) and all(isinstance(item, scalar) for item in observed):
            return expected in observed
        raise ValueError("contains supports only text or a flat scalar list")
    if operator == "in":
        if isinstance(expected, str) and isinstance(observed, str):
            return observed in expected
        if isinstance(expected, list) and all(isinstance(item, scalar) for item in expected):
            return observed in expected
        raise ValueError("in supports only text or a flat scalar list")
    raise ValueError("unsupported operator {}".format(operator))


def requirement_results(
    candidate: dict, facts: list[dict], identity: dict | None = None
) -> dict:
    resolved = repair_candidate.resolve_requirements(candidate, require_authority=True)
    by_id = {fact["id"]: fact for fact in facts}
    for category in ("compliance", "advisory"):
        for claim in resolved[category]:
            test = claim.get("test") or {}
            fact_id = test.get("factId") or claim.get("factId")
            operator = test.get("operator") or claim.get("operator")
            expected = test.get("expected", claim.get("expected"))
            fact = by_id.get(str(fact_id)) if fact_id else None
            if not fact_id or not operator:
                evaluation = {"status": "unknown", "reason": "no machine-readable fact test was supplied"}
            elif not fact or fact.get("status") != "measured":
                evaluation = {"status": "unknown", "factId": fact_id, "reason": "referenced fact is unavailable"}
            else:
                try:
                    satisfied = _compare(fact.get("value"), str(operator), expected)
                    evaluation = {
                        "status": "satisfied" if satisfied else "not_satisfied",
                        "factId": fact_id,
                        "observed": fact.get("value"),
                        "operator": operator,
                        "expected": expected,
                    }
                except Exception as exc:
                    evaluation = {"status": "unknown", "factId": fact_id, "reason": str(exc)}
            claim["evaluation"] = evaluation
    return {
        "schema": "repair-requirements@1",
        **dict(identity or {}),
        "factsHash": repair_candidate.stable_json_hash(facts),
        **resolved,
    }


_requirement_results = requirement_results


def analyze_candidate(
    session: Any,
    candidate: Any,
    geometry: Iterable[Any],
    entity_json: Any,
    box: Any,
    centers: Iterable[Any],
    damage: Iterable[float],
    threshold: float,
    neighbour_geometry: Iterable[Any] = (),
    neighbour_ids: Iterable[str] = (),
) -> tuple[dict, dict, list[Any], list[str]]:
    """Measure an executed candidate without producing a global verdict."""
    session_obj = _object(session)
    selected_beam = str(session_obj.get("beamId") or session_obj.get("beam_id") or "")
    candidate_obj = repair_candidate.validate_scope(
        _object(candidate),
        beam_id=selected_beam,
        part_ids=session_obj.get("partIds"),
        action_ids=session_obj.get("actionIds"),
        workspace_hash=session_obj.get("workspaceHash"),
        context_hash=session_obj.get("contextHash"),
    )
    items = [coerce_geometry(item) for item in (geometry or [])]
    entity_envelope = _object(entity_json)
    entities_raw = _records(entity_envelope)
    if len(entities_raw) != len(items):
        raise ValueError(
            "entity_json/candidate_geometry length mismatch: {} vs {}".format(
                len(entities_raw), len(items)
            )
        )
    by_index = {}
    for record in entities_raw:
        index = int(record.get("geometryIndex", -1))
        if index < 0 or index >= len(items) or index in by_index:
            raise ValueError("entity_json contains a duplicate or invalid geometryIndex")
        by_index[index] = record
    entities = [by_index[index] for index in range(len(items))]
    declarations = {
        str(item.get("id")): item for item in candidate_obj.get("outputs") or []
    }
    for record in entities:
        group_id = str(record.get("groupId") or "")
        declaration = declarations.get(group_id)
        if declaration is None:
            raise ValueError("entity_json references an undeclared output group")
        for key in ("materialEffect", "partRefs", "actionRefs"):
            expected_value = declaration.get(key)
            if key in ("partRefs", "actionRefs"):
                expected_value = list(expected_value or [])
            if record.get(key) != expected_value:
                raise ValueError("entity_json {} differs from the manifest declaration".format(key))

    ctx = CandidateContext(
        session_obj, box, centers, damage, threshold,
        neighbours=neighbour_geometry, neighbour_ids=neighbour_ids,
    )
    manifest_hash = repair_candidate.stable_json_hash(candidate_obj)
    public_session = {key: value for key, value in session_obj.items() if key != "workspaceSource"}
    session_hash = repair_candidate.stable_json_hash(public_session)
    geometry_hash = runtime_signature(items)
    entities_hash = repair_candidate.stable_json_hash(entities_raw)
    expected = {
        "candidateId": candidate_obj["id"],
        "beamId": ctx.beam_id,
        "manifestHash": manifest_hash,
        "sessionHash": session_hash,
        "geometryHash": geometry_hash,
        "entitiesHash": entities_hash,
        "analysisInputHash": ctx.analysis_input_hash,
    }
    for key, value in expected.items():
        if entity_envelope.get(key) != value:
            raise ValueError("entity_json {} does not match the active analysis input".format(key))
    code_hash = str(entity_envelope.get("codeHash") or "")
    if not code_hash:
        raise ValueError("entity_json has no executed codeHash")
    facts, inventory = _inventory(items, entities, ctx.tolerance)
    condition, condition_details = _condition_facts(candidate_obj, items, entities, ctx)
    facts.extend(condition)
    facts.extend(_end_contact_facts(items, entities, ctx))
    neighbour_facts, neighbour_diag = _neighbour_facts(items, entities, ctx)
    insertion_facts, insertion_diag = _insertion_facts(candidate_obj, items, entities, ctx)
    tool_facts, tool_diag = _tool_facts(candidate_obj, items, entities, ctx)
    facts.extend(neighbour_facts + insertion_facts + tool_facts)
    facts = repair_candidate.normalise_facts(facts)
    identity = {
        "candidateId": candidate_obj["id"],
        "beamId": ctx.beam_id,
        "manifestHash": manifest_hash,
        "codeHash": code_hash,
        "sessionHash": session_hash,
        "geometryHash": geometry_hash,
        "entitiesHash": entities_hash,
        "analysisInputHash": ctx.analysis_input_hash,
    }
    requirements = requirement_results(candidate_obj, facts, identity)
    envelope = {
        "schema": "repair-candidate-facts@1",
        "candidateId": candidate_obj["id"],
        "beamId": ctx.beam_id,
        "session": {
            key: session_obj.get(key)
            for key in ("workspaceHash", "contextHash", "cellDataHash", "beamId", "threshold", "modelUnits")
            if session_obj.get(key) is not None
        },
        "tolerance": ctx.tolerance,
        "manifestHash": manifest_hash,
        "codeHash": code_hash,
        "sessionHash": session_hash,
        "geometryHash": geometry_hash,
        "entitiesHash": entities_hash,
        "analysisInputHash": ctx.analysis_input_hash,
        "facts": facts,
        "inventory": inventory,
    }
    report = [
        "measured {} neutral facts for {}".format(len(facts), candidate_obj["id"]),
        "{} binding and {} advisory claim(s)".format(
            len(requirements["compliance"]), len(requirements["advisory"])
        ),
        "no global approval was inferred",
    ]
    diagnostics = neighbour_diag + insertion_diag + tool_diag
    return envelope, requirements, diagnostics, report
