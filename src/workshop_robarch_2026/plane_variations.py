"""Fast LLM-authored, deterministic plane-joinery variations."""

from __future__ import annotations

import copy
import json
import os
from typing import Any

from . import candidate_runtime, evaluator, joinery_program, llm_candidate, repair_candidate


SCHEMA = "repair-plane-program-set@1"


def program_quality(program: dict) -> dict:
    """Sample the canonical DNF and expose redundant groups/planes."""
    import numpy as np

    geometry = program.get("geometry") or {}
    aspect = float(geometry.get("aspect", 3.0))
    planes = geometry.get("planes") or []
    by_id = {str(item.get("id")): item for item in planes}
    xs = np.linspace(-0.49, 0.49, 9)
    ys = np.linspace(0.01, max(0.02, aspect - 0.01), 31)
    zs = np.linspace(-0.49, 0.49, 9)
    points = np.asarray([[x, y, z] for x in xs for y in ys for z in zs], float)
    predicates, strict_predicates = {}, {}
    for plane_id, item in by_id.items():
        normal = np.asarray(item.get("normal") or [], float)
        length = float(np.linalg.norm(normal))
        if normal.shape != (3,) or length <= 1e-9:
            raise ValueError("{} has an invalid normal".format(plane_id))
        signed = points @ (normal / length) - float(item.get("d", 0.0)) / length
        predicates[plane_id] = signed >= -1e-9
        strict_predicates[plane_id] = signed >= 0.01
    groups = [[str(value) for value in group] for group in (geometry.get("removalGroups") or [])]
    group_masks, strict_group_masks = [], []
    for group in groups:
        if not group or any(value not in predicates for value in group):
            raise ValueError("removalGroups contain an empty or unknown plane reference")
        mask = np.ones(len(points), dtype=bool)
        strict_mask = np.ones(len(points), dtype=bool)
        for plane_id in group:
            mask &= predicates[plane_id]
            strict_mask &= strict_predicates[plane_id]
        group_masks.append(mask)
        strict_group_masks.append(strict_mask)
    removal = np.zeros(len(points), dtype=bool)
    for mask in group_masks:
        removal |= mask
    active_groups, active_planes, group_stats = [], set(), []
    for group_index, (group, mask) in enumerate(zip(groups, group_masks)):
        others = np.zeros(len(points), dtype=bool)
        for index, other in enumerate(group_masks):
            if index != group_index:
                others |= other
        unique_count = int(np.count_nonzero(mask & ~others))
        if unique_count:
            active_groups.append(group_index)
        group_stats.append(
            {
                "index": group_index,
                "planeIds": group,
                "sampledCells": int(np.count_nonzero(mask)),
                "uniqueSampledCells": unique_count,
            }
        )
        for plane_id in group:
            without = np.ones(len(points), dtype=bool)
            for other_id in group:
                if other_id != plane_id:
                    without &= predicates[other_id]
            if np.any(without & ~mask & ~others):
                active_planes.add(plane_id)
    text = json.dumps(
        {"behaviour": program.get("jointBehaviour"), "geometry": geometry.get("roles")},
        ensure_ascii=False,
    ).lower()
    complex_claim = str((program.get("jointBehaviour") or {}).get("retention", "")).lower() == "positive_lock"
    complex_claim = complex_claim or any(word in text for word in ("bridle", "tenon", "mortise", "step", "lock", "dovetail"))
    auxiliaries = geometry.get("auxiliaryGeometry") or []
    fastening_count = int(((program.get("fabricationPlan") or {}).get("fastening") or {}).get("count") or 0)
    issues, quality_warnings = [], []
    if not groups or not 0.005 < float(removal.mean()) < 0.995:
        issues.append("plane Boolean is empty or consumes the whole joint window")
    redundant = [item["index"] for item in group_stats if not item["uniqueSampledCells"]]
    nonempty = [index for index, mask in enumerate(strict_group_masks) if np.any(mask)]
    remaining, connected_components = set(nonempty), 0
    while remaining:
        connected_components += 1
        stack = [remaining.pop()]
        while stack:
            current = stack.pop()
            linked = [
                index for index in remaining
                if np.any(strict_group_masks[current] & strict_group_masks[index])
            ]
            for index in linked:
                remaining.remove(index)
                stack.append(index)
    if redundant:
        quality_warnings.append(
            "removal group(s) {} are redundant: they add no unique sampled volume".format(
                ", ".join("G{}".format(value) for value in redundant)
            )
        )
    if complex_claim and len(active_planes) < 3:
        issues.append(
            "claimed interlock uses only {} active cutting plane(s): {}".format(
                len(active_planes), ", ".join(sorted(active_planes)) or "none"
            )
        )
    if fastening_count and len(auxiliaries) < fastening_count:
        issues.append("fasteners are described but missing from auxiliaryGeometry")
    return {
        "activeGroupCount": len(active_groups), "groupCount": len(groups),
        "activeGroupIndices": active_groups, "redundantGroupIndices": redundant,
        "volumeConnectedGroupComponents": connected_components,
        "groupStats": group_stats,
        "activePlaneIds": sorted(active_planes), "removalFractionSampled": float(removal.mean()),
        "auxiliaryCount": len(auxiliaries), "warnings": quality_warnings, "issues": issues,
    }


def _corpus_examples(repo: str) -> list[dict]:
    folder = os.path.join(repo, "data", "corpus", "joints")
    examples = []
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not name.lower().endswith(".json"):
            continue
        with open(os.path.join(folder, name), "r", encoding="utf-8") as handle:
            joint = json.load(handle)
        examples.append(
            {
                "key": joint.get("key") or os.path.splitext(name)[0],
                "aspect": joint.get("aspect"),
                "removalGroups": joint.get("removal_groups") or [],
                "planes": [
                    {
                        "normal": cut.get("normal"),
                        "d": cut.get("offset"),
                        "role": cut.get("name"),
                    }
                    for cut in (joint.get("cuts") or [])
                ],
            }
        )
    return examples


def _validated_entries(raw: Any, requested: int, session: dict, repo: str) -> tuple[list[dict], list[str]]:
    if not isinstance(raw, list):
        return [], ["response has no variations list"]
    action_ids = {str(value) for value in (session.get("actionIds") or [])}
    seen, variations, rejected = set(), [], []
    for index, entry in enumerate(raw[:requested]):
        try:
            if not isinstance(entry, dict) or not isinstance(entry.get("jointProgram"), dict):
                raise ValueError("missing jointProgram")
            program, warnings = joinery_program.normalise_program(
                entry["jointProgram"], beam_id=session.get("beamId")
            )
            geometry = program.get("geometry") or {}
            if geometry.get("topology") != "any_joint" or len(geometry.get("planes") or []) != 6:
                raise ValueError("needs six plane slots")
            if program.get("repairStepRef") and str(program["repairStepRef"]) not in action_ids:
                raise ValueError("references an unknown Workspace repair step")
            quality = program_quality(program)
            if quality["issues"]:
                raise ValueError("; ".join(quality["issues"]))
            warnings.extend(quality["warnings"])
            base_id = str(program.get("id") or "plane_variation_{}".format(index + 1))
            program["id"] = base_id
            suffix = 2
            while program["id"] in seen:
                program["id"] = "{}_v{}".format(base_id, suffix)
                suffix += 1
            seen.add(program["id"])
            variations.append(
                {
                    "id": program["id"], "summary": str(entry.get("summary") or ""),
                    "program": program, "quality": quality, "warnings": warnings,
                }
            )
        except Exception as exc:
            rejected.append("variation {}: {}".format(index + 1, exc))
    return variations, rejected


def author_program_set(
    repo: str,
    session: dict,
    context: dict,
    brief: dict,
    instruction: str = "",
    model: str | None = None,
    count: int = 3,
) -> dict:
    """Ask once for several plane programs under the exact same repair brief."""
    requested = max(2, min(5, int(count or 3)))
    payload = {
        "session": {key: value for key, value in session.items() if key != "workspaceSource"},
        "workspaceContext": context,
        "reviewedRepairBrief": brief,
        "participantInstruction": str(instruction or ""),
        "requestedVariationCount": requested,
        "corpusPlaneReferences": _corpus_examples(repo),
    }
    result = llm_candidate.request_json(
        repo,
        "design_joinery_variations.md",
        payload,
        model=model,
        attachments=llm_candidate.evidence_attachments(session, context),
        temperature=0.85,
    )
    raw = result.get("variations") or []
    variations, rejected = _validated_entries(raw, requested, session, repo)
    if len(variations) < 2:
        payload["generationFeedback"] = rejected
        payload["previousInvalidVariations"] = raw
        retry = llm_candidate.request_json(
            repo, "design_joinery_variations.md", payload, model=model,
            attachments=llm_candidate.evidence_attachments(session, context),
            temperature=0.7,
        )
        retry_variations, retry_rejected = _validated_entries(
            retry.get("variations") or [], requested, session, repo
        )
        known = {item["id"] for item in variations}
        for item in retry_variations:
            base_id, suffix = item["id"], 2
            while item["id"] in known:
                item["id"] = "{}_v{}".format(base_id, suffix)
                item["program"]["id"] = item["id"]
                suffix += 1
            known.add(item["id"])
            variations.append(item)
            if len(variations) >= requested:
                break
        rejected.extend(retry_rejected)
    if not variations:
        raise ValueError(
            "Gemini returned no meaningful plane variation. "
            + " | ".join(rejected)
        )
    record = {
        "schema": SCHEMA,
        "requestedCount": requested,
        "briefHash": repair_candidate.stable_json_hash(brief),
        "variations": variations,
        "rejected": rejected,
    }
    record["setHash"] = repair_candidate.stable_json_hash(record)
    return record


def revise_program(
    repo: str, session: dict, context: dict, brief: dict, program: dict,
    feedback: str, model: str | None = None,
) -> dict:
    """Revise one selected plane program while preserving the reviewed repair idea."""
    if not str(feedback or "").strip():
        raise ValueError("write focused feedback before pressing revise")
    payload = {
        "session": {key: value for key, value in session.items() if key != "workspaceSource"},
        "workspaceContext": context, "reviewedRepairBrief": brief,
        "requestedVariationCount": 1, "previousProgram": program,
        "revisionFeedback": str(feedback), "corpusPlaneReferences": _corpus_examples(repo),
    }
    response = llm_candidate.request_json(
        repo, "design_joinery_variations.md", payload, model=model,
        attachments=llm_candidate.evidence_attachments(session, context), temperature=0.65,
    )
    entries = response.get("variations") or []
    if not entries or not isinstance(entries[0].get("jointProgram"), dict):
        raise ValueError("Gemini returned no revised plane program")
    revised, warnings = joinery_program.normalise_program(
        entries[0]["jointProgram"], beam_id=session.get("beamId")
    )
    if len((revised.get("geometry") or {}).get("planes") or []) != 6:
        raise ValueError("revised program needs six plane slots")
    quality = program_quality(revised)
    if quality["issues"]:
        raise ValueError("revised geometry is not meaningful: " + "; ".join(quality["issues"]))
    warnings.extend(quality["warnings"])
    revised["id"] = "{}_revision".format(program.get("id") or revised["id"])
    return {
        "program": revised, "summary": str(entries[0].get("summary") or ""),
        "warnings": warnings,
    }


def validate_set(value: Any) -> dict:
    record = value if isinstance(value, dict) else json.loads(str(value or ""))
    if record.get("schema") != SCHEMA:
        raise ValueError("candidate set must use {}".format(SCHEMA))
    check = copy.deepcopy(record)
    saved = check.pop("setHash", None)
    if saved != repair_candidate.stable_json_hash(check):
        raise ValueError("plane variation set changed after authoring")
    for item in record.get("variations") or []:
        quality = program_quality(item.get("program") or {})
        if quality["issues"]:
            raise ValueError(
                "{} needs regeneration: {}".format(
                    item.get("id"), "; ".join(quality["issues"])
                )
            )
    return record


def box_frame(box: Any) -> dict:
    import numpy as np

    plane = box.Plane
    axes = [
        np.array([axis.X, axis.Y, axis.Z], float)
        for axis in (plane.XAxis, plane.YAxis, plane.ZAxis)
    ]
    extents = [float(interval.Length) for interval in (box.X, box.Y, box.Z)]
    axis_v = int(np.argmax(extents))
    axis_u, axis_w = (axis_v + 1) % 3, (axis_v + 2) % 3
    u, v = axes[axis_u], axes[axis_v]
    w = np.cross(u, v)
    w /= np.linalg.norm(w)
    intervals = [box.X, box.Y, box.Z]
    origin = np.array([plane.Origin.X, plane.Origin.Y, plane.Origin.Z], float)
    for interval, axis in zip(intervals, axes):
        origin += float(interval.Min) * axis
    if float(w @ axes[axis_w]) < 0:
        origin += extents[axis_w] * axes[axis_w]
    return {
        "origin": origin.tolist(), "u": u.tolist(), "v": v.tolist(), "w": w.tolist(),
        "width": extents[axis_u], "length": extents[axis_v], "height": extents[axis_w],
    }


def _plane_graphics(cuts: list[dict], size: float) -> list[Any]:
    import Rhino.Geometry as rg

    result = []
    for cut in cuts:
        origin = evaluator.cut_origin(cut)
        normal = cut["normal"]
        plane = rg.Plane(rg.Point3d(*origin), rg.Vector3d(*normal))
        result.append(
            rg.Rectangle3d(
                plane, rg.Interval(-size, size), rg.Interval(-size, size)
            ).ToNurbsCurve()
        )
        result.append(
            rg.LineCurve(
                rg.Point3d(*origin),
                rg.Point3d(*origin) + 0.35 * size * rg.Vector3d(*normal),
            )
        )
    return result


def _auxiliary_geometry(program: dict, fit: dict, frame: dict) -> list[tuple[Any, dict]]:
    """Build physical pegs/keys declared in canonical joint coordinates."""
    import math
    import numpy as np
    import Rhino.Geometry as rg

    definitions = ((program.get("geometry") or {}).get("auxiliaryGeometry") or [])
    if not definitions:
        return []
    joint = fit["joint"]
    section = float(joint.get("section", 1.0))
    aspect = float(joint["aspect"])
    scale = min(float(frame["width"]), float(frame["height"])) / section
    scale *= float(fit.get("interface_scale", 1.0))
    position = float(fit["position"]) * float(frame["length"])
    half = 0.5 * aspect * section * scale
    position = max(half, min(float(frame["length"]) - half, position))
    basis = np.column_stack(
        [np.asarray(frame[key], float) for key in ("u", "v", "w")]
    )
    angle = math.radians(float(fit.get("rotate_deg", 0.0)))
    rotation = np.array(
        [[math.cos(angle), 0, -math.sin(angle)], [0, 1, 0], [math.sin(angle), 0, math.cos(angle)]],
        float,
    )
    matrix = basis @ (scale * rotation)
    origin = np.asarray(frame["origin"], float)
    origin += 0.5 * float(frame["width"]) * np.asarray(frame["u"], float)
    origin += 0.5 * float(frame["height"]) * np.asarray(frame["w"], float)
    origin += (position - half) * np.asarray(frame["v"], float)
    if int(fit.get("side", 1)) < 0:
        origin += matrix @ np.array([0.0, aspect * section, 0.0])
        matrix = matrix @ np.array([[-1, 0, 0], [0, -1, 0], [0, 0, 1]], float)

    result = []
    for item in definitions:
        kind = str(item.get("kind") or "").lower()
        center = np.asarray(item.get("center") or [], float)
        if center.shape != (3,):
            raise ValueError("auxiliary geometry needs center [x,y,z]")
        world_center = origin + matrix @ center
        if kind == "cylinder":
            axis = np.asarray(item.get("axis") or [], float)
            length = float(np.linalg.norm(axis))
            if axis.shape != (3,) or length <= 1e-9:
                raise ValueError("auxiliary cylinder needs a nonzero axis")
            direction = matrix @ (axis / length)
            direction /= np.linalg.norm(direction)
            height = float(item.get("length")) * scale
            radius = float(item.get("radius")) * scale
            start = world_center - 0.5 * height * direction
            cylinder = rg.Cylinder(
                rg.Circle(rg.Plane(rg.Point3d(*start), rg.Vector3d(*direction)), radius),
                height,
            ).ToBrep(True, True)
            if cylinder is None:
                raise ValueError("auxiliary cylinder creation failed")
            result.append((cylinder, item))
        elif kind == "box":
            size = [float(value) * scale for value in (item.get("size") or [])]
            if len(size) != 3 or min(size) <= 0:
                raise ValueError("auxiliary box needs positive size [x,y,z]")
            xaxis, yaxis = matrix[:, 0], matrix[:, 1]
            plane = rg.Plane(rg.Point3d(*world_center), rg.Vector3d(*xaxis), rg.Vector3d(*yaxis))
            result.append((
                rg.Box(
                    plane,
                    rg.Interval(-0.5 * size[0], 0.5 * size[0]),
                    rg.Interval(-0.5 * size[1], 0.5 * size[1]),
                    rg.Interval(-0.5 * size[2], 0.5 * size[2]),
                ).ToBrep(), item
            ))
        else:
            raise ValueError("unsupported auxiliary kind {!r}; use cylinder or box".format(kind))
    return result


def _subtract_auxiliaries(parts: list[Any], auxiliaries: list[Any], tolerance: float) -> list[Any]:
    import Rhino.Geometry as rg

    current = list(parts)
    for cutter in auxiliaries:
        next_parts = []
        for part in current:
            if candidate_runtime._boxes_disjoint(part, cutter, tolerance):
                next_parts.append(part)
                continue
            result = None
            for value in (tolerance, evaluator.TOL, 0.5 * tolerance, 2.0 * tolerance):
                result = list(rg.Brep.CreateBooleanDifference(part, cutter, float(value)) or [])
                if result:
                    break
            if not result:
                raise ValueError("auxiliary socket Boolean failed")
            next_parts.extend(result)
        current = next_parts
    return current


def _evaluate_part_robust(part: dict, tolerance: float) -> list[Any]:
    attempts, errors = [], []
    for value in (tolerance, evaluator.TOL, 0.5 * tolerance, 2.0 * tolerance):
        value = float(value)
        if value > 0 and value not in attempts:
            attempts.append(value)
    for value in attempts:
        try:
            return evaluator.evaluate_part(part, value)
        except Exception as exc:
            errors.append("{}: {}".format(value, exc))
    raise ValueError("exact Boolean failed at tested tolerances: " + " | ".join(errors))


def evaluate(program: dict, box: Any, centers: list[Any], damage: list[float], threshold: float) -> dict:
    """Fit one plane program, then build exact Kept/Prosthesis/Other geometry."""
    import numpy as np

    points = np.asarray([[p.X, p.Y, p.Z] for p in centers], dtype=float)
    values = np.asarray(damage, dtype=float)
    fit, resolved, report = joinery_program.fit_program(
        program, box_frame(box), points, values,
        beam_id=program.get("targetPartRef"), threshold=threshold, verify=True,
        enforce_construction=False,
    )
    if fit is None:
        raise ValueError("no damage-covering plane placement found: " + "; ".join(report[-3:]))
    kept, prosthesis = [], []
    repair = fit["repair"]
    tolerance = getattr(__import__("Rhino").RhinoDoc.ActiveDoc, "ModelAbsoluteTolerance", 0.001)
    for part in repair["parts"]:
        geometry = _evaluate_part_robust(part, float(tolerance))
        (kept if part["name"] == "kept" else prosthesis).extend(geometry)
    auxiliary_records = _auxiliary_geometry(resolved, fit, box_frame(box))
    other = [geometry for geometry, _ in auxiliary_records]
    kept_cutters = [geometry for geometry, item in auxiliary_records if "kept" in (item.get("cuts") or [])]
    prosthesis_cutters = [geometry for geometry, item in auxiliary_records if "prosthesis" in (item.get("cuts") or [])]
    if kept_cutters:
        kept = _subtract_auxiliaries(kept, kept_cutters, float(tolerance))
    if prosthesis_cutters:
        prosthesis = _subtract_auxiliaries(prosthesis, prosthesis_cutters, float(tolerance))
    report.append("physical auxiliary geometry: {} item(s)".format(len(other)))
    proposal = joinery_program.proposal_record(resolved, fit, report)
    proposal["fit"]["status"] = "damage_coverage_pass"
    proposal["fit"]["advisoryConstructionConstraints"] = copy.deepcopy(
        resolved.get("constructionConstraints") or {}
    )
    return {
        "kept": kept,
        "prosthesis": prosthesis,
        "other": other,
        "resolvedProgram": resolved,
        "fit": proposal,
        "report": report,
    }


def selection_payload(
    result: dict, brief: dict, session: dict, box: Any,
    centers: list[Any], damage: list[float], threshold: float,
    neighbour_geometry: list[Any] | None = None,
    neighbour_ids: list[str] | None = None,
) -> tuple[dict, str, list[Any], list[dict], dict]:
    """Wrap resolved plane geometry in the common Review/Save record contract."""
    program = result["resolvedProgram"]
    step = str(program.get("repairStepRef") or "")
    refs = [step] if step else []
    claims = [
        {
            "id": item.get("id"), "text": item.get("text"),
            "source": item.get("source", "llm"), "requirement": True,
            "confirmed": bool(item.get("confirmedByHuman")), "test": item.get("test"),
        }
        for item in ((brief.get("repairIdea") or {}).get("requirements") or [])
        if item.get("id") and item.get("text")
    ]
    outputs = [
        {"id": "kept", "role": "Kept historic timber", "effect": "retained material", "materialEffect": "retain"},
        {"id": "prosthesis", "role": "Replacement prosthesis", "effect": "added material", "materialEffect": "add"},
        {"id": "other", "role": "Physical auxiliary joinery", "effect": "added pegs, keys or wedges", "materialEffect": "add"},
    ]
    for output in outputs:
        output["partRefs"], output["actionRefs"] = [session["beamId"]], refs
    manifest = {
        "schema": "repair-candidate@2", "id": program["id"],
        "title": "Plane joinery: " + program["id"],
        "partRefs": [session["beamId"]], "actionRefs": refs,
        "outputs": outputs, "assumptions": [], "claims": claims, "openQuestions": program.get("openQuestions") or [],
        "planeProgram": program, "fitRecord": result["fit"],
    }
    manifest = repair_candidate.apply_brief_authority(manifest, brief)
    code = "def build_candidate(ctx, emit):\n    \"\"\"Geometry is replayed by the stored planeProgram.\"\"\"\n    pass"
    geometry = result["kept"] + result["prosthesis"] + result["other"]
    entities, index = [], 0
    for declaration, items in zip(outputs, (result["kept"], result["prosthesis"], result["other"])):
        for offset, item in enumerate(items):
            entities.append(
                {
                    "id": declaration["id"] if len(items) == 1 else "{}:{}".format(declaration["id"], offset + 1),
                    "groupId": declaration["id"], "geometryIndex": index,
                    "geometryType": type(item).__name__, "role": declaration["role"],
                    "effect": declaration["effect"], "materialEffect": declaration["materialEffect"],
                    "purpose": "", "relatesTo": [], "partRefs": declaration["partRefs"],
                    "actionRefs": declaration["actionRefs"], "metadata": {},
                }
            )
            index += 1
    ctx = candidate_runtime.CandidateContext(
        session, box, centers, damage, threshold,
        neighbours=neighbour_geometry or [], neighbour_ids=neighbour_ids or [],
    )
    public_session = {key: value for key, value in session.items() if key != "workspaceSource"}
    execution = {
        "schema": "repair-candidate-execution@1", "candidateId": manifest["id"],
        "manifestHash": repair_candidate.stable_json_hash(manifest),
        "codeHash": repair_candidate.stable_json_hash(code),
        "sessionHash": repair_candidate.stable_json_hash(public_session),
        "geometryHash": candidate_runtime.runtime_signature(geometry),
        "entitiesHash": repair_candidate.stable_json_hash(entities),
        "analysisInputHash": ctx.analysis_input_hash, "beamId": session["beamId"],
        "workspaceHash": session.get("workspaceHash"), "contextHash": session.get("contextHash"),
        "cellDataHash": session.get("cellDataHash"), "status": "complete",
        "geometryCount": len(geometry), "entityCount": len(entities), "tolerance": ctx.tolerance,
        "messages": list(result.get("report") or []),
    }
    return manifest, code, geometry, entities, execution
