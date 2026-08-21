"""One LLM-authored plane joint, explored through local geometric variations."""

from __future__ import annotations

import copy
import json
import math
import os
from typing import Any, Optional

import numpy as np

from . import joinery_program, llm_candidate, plane_variations, repair_candidate, scoring


SCHEMA = "repair-variation-source@1"


def _corpus(repo: str) -> list[dict]:
    folder = os.path.join(repo, "data", "corpus", "joints")
    result = []
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not name.lower().endswith(".json"):
            continue
        with open(os.path.join(folder, name), "r", encoding="utf-8") as handle:
            item = json.load(handle)
        result.append(
            {
                "key": item.get("key") or os.path.splitext(name)[0],
                "aspect": item.get("aspect"),
                "removalGroups": item.get("removal_groups") or [],
                "planes": [
                    {
                        "normal": cut.get("normal"),
                        "d": cut.get("offset"),
                        "role": cut.get("name"),
                    }
                    for cut in (item.get("cuts") or [])
                ],
            }
        )
    return result


def damage_context(box: Any, centers: list[Any], damage: list[float], threshold: float) -> dict:
    """Concise local damage information for the authoring request."""
    frame = plane_variations.box_frame(box)
    points = np.asarray(
        [[float(point.X), float(point.Y), float(point.Z)] for point in centers], float
    )
    values = np.asarray(damage, float)
    local = scoring.to_local(points, frame)
    dimensions = np.asarray([frame["width"], frame["length"], frame["height"]], float)
    selected = values >= float(threshold)
    result = {
        "threshold": float(threshold),
        "cellCount": int(len(points)),
        "requiredDamageCellCount": int(selected.sum()),
        "memberDimensions": dimensions.tolist(),
        "localAxes": ["section_x", "member_axis_y", "section_z"],
    }
    if selected.any():
        damaged = local[selected]
        result.update(
            {
                "requiredLocalBounds": {
                    "min": damaged.min(axis=0).tolist(),
                    "max": damaged.max(axis=0).tolist(),
                },
                "requiredNormalisedBounds": {
                    "min": (damaged.min(axis=0) / dimensions).tolist(),
                    "max": (damaged.max(axis=0) / dimensions).tolist(),
                },
                "requiredCellIndices": np.flatnonzero(selected).astype(int).tolist(),
            }
        )
    return result


def _validate_program(raw: Any, session: dict) -> tuple[dict, dict, list[str]]:
    if not isinstance(raw, dict):
        raise ValueError("Gemini returned no jointProgram object")
    program, warnings = joinery_program.normalise_program(raw, beam_id=session.get("beamId"))
    geometry = program.get("geometry") or {}
    if geometry.get("topology") != "any_joint" or len(geometry.get("planes") or []) != 6:
        raise ValueError("the joint must contain exactly six authored plane slots")
    actions = {str(value) for value in (session.get("actionIds") or [])}
    if program.get("repairStepRef") and str(program["repairStepRef"]) not in actions:
        raise ValueError("repairStepRef is outside the reviewed Workspace plan")
    quality = plane_variations.program_quality(program)
    issues = [
        value for value in quality["issues"]
        if "auxiliaryGeometry" not in value
    ]
    if issues:
        raise ValueError("; ".join(issues))
    intent = geometry.get("prosthesisIntent") or {}
    if bool(intent.get("connected", True)) and quality.get("volumeConnectedGroupComponents", 0) > 1:
        raise ValueError(
            "the Boolean contains {} volume-disconnected removal regions; one connected prosthesis was declared"
            .format(quality["volumeConnectedGroupComponents"])
        )
    warnings.extend(quality.get("warnings") or [])
    return program, quality, warnings


def _replacement_sides(frame: dict, points: np.ndarray, values: np.ndarray, threshold: float):
    """Infer the repaired member end when the damage is clearly end-localised."""
    selected = values >= float(threshold)
    if not selected.any():
        return None
    axial = scoring.to_local(points[selected], frame)[:, 1]
    length = float(frame["length"])
    low_gap = max(0.0, float(axial.min()))
    high_gap = max(0.0, length - float(axial.max()))
    if low_gap * 2.0 < high_gap:
        return [-1]
    if high_gap * 2.0 < low_gap:
        return [1]
    return None


def _fit_probe(program: dict, probe: dict, rotations: Optional[list[float]] = None) -> dict:
    frame = probe["frame"]
    points = np.asarray(probe["centers"], float)
    values = np.asarray(probe["damage"], float)
    threshold = float(probe["threshold"])
    candidate = copy.deepcopy(program)
    objective = candidate.setdefault("fitObjective", {})
    objective["parameterSamples"] = 3
    objective["positionSamples"] = 25
    if rotations is not None:
        objective["rotationsDeg"] = [float(value) for value in rotations]
    sides = _replacement_sides(frame, points, values, threshold)
    if sides:
        objective["replacementSides"] = sides
    result, _, report = joinery_program.fit_program(
        candidate, frame, points, values,
        beam_id=candidate.get("targetPartRef"), threshold=threshold,
        verify=True, enforce_construction=False, allow_partial=True,
    )
    if result is None:
        side_note = " inferred replacement side {}".format(sides) if sides else ""
        raise ValueError("the authored geometry has no damage-covering fit on{}: {}".format(side_note, "; ".join(report[-2:])))
    return {
        "replacementSides": sides or list((candidate.get("fitObjective") or {}).get("replacementSides") or []),
        "rotationDeg": float(result["rotate_deg"]),
        "soundCellsRemoved": int(result["sound_sacrificed"]),
        "soundCellCount": int(result["n_sound"]),
        "requiredDamageRemoved": int(result.get("required_removal_count", 0) - result.get("required_left", 0)),
    }


def author_joint(
    repo: str,
    session: dict,
    context: dict,
    brief: dict,
    cellular_damage: dict,
    fit_probe: Optional[dict] = None,
    instruction: str = "",
    model: Optional[str] = None,
    count: int = 5,
) -> dict:
    """Author one base concept and an LLM-directed incremental angle study."""
    requested = max(2, min(8, int(count or 5)))
    payload = {
        "session": {key: value for key, value in session.items() if key != "workspaceSource"},
        "workspaceContext": context,
        "reviewedRepairBrief": brief,
        "participantInstruction": str(instruction or ""),
        "cellularDamage": copy.deepcopy(cellular_damage),
        "corpusPlaneReferences": _corpus(repo),
        "requestedVariationCount": requested,
    }
    attachments = llm_candidate.evidence_attachments(session, context)
    errors, previous = [], None
    for attempt in range(3):
        if previous is not None:
            payload["previousProgram"] = previous
            payload["fitFeedback"] = errors[-1]
        response = llm_candidate.request_json(
            repo,
            "design_joinery_standalone.md",
            payload,
            model=model,
            attachments=attachments,
            temperature=0.72 if attempt == 0 else 0.45,
        )
        previous = response.get("jointProgram")
        try:
            program, quality, warnings = _validate_program(previous, session)
            studies = _study_programs(program, response, requested)
            baseline = (
                _fit_probe(program, fit_probe, [0.0, 90.0, 180.0, 270.0])
                if fit_probe else {"rotationDeg": 0.0}
            )
            baseline_angle = float(baseline["rotationDeg"])
            valid, fit_errors = [], []
            for study in studies:
                try:
                    relative = float(study.get("rotationDeg") or 0.0)
                    actual = (baseline_angle + relative) % 360.0
                    objective = study["program"].setdefault("fitObjective", {})
                    objective["rotationsDeg"] = [actual]
                    transform = study["program"]["geometry"].setdefault("variationTransform", {})
                    transform["fitOrientationBaselineDeg"] = baseline_angle
                    study["fitProbe"] = _fit_probe(study["program"], fit_probe) if fit_probe else None
                    valid.append(study)
                except Exception as exc:
                    fit_errors.append("{}: {}".format(study["id"], exc))
            minimum = min(requested, 3)
            if len(valid) < minimum:
                raise ValueError(
                    "only {} of {} LLM angle studies fit; {}".format(
                        len(valid), requested, " | ".join(fit_errors[:4])
                    )
                )
            record = {
                "schema": SCHEMA,
                "summary": str(response.get("summary") or ""),
                "program": program,
                "variations": valid[:requested],
                "quality": quality,
                "warnings": warnings,
                "authoringAttempts": attempt + 1,
                "requestedCount": requested,
                "fitOrientationBaselineDeg": baseline_angle,
                "rejectedStudies": fit_errors,
                "briefHash": repair_candidate.stable_json_hash(brief),
            }
            record["sourceHash"] = repair_candidate.stable_json_hash(record)
            return record
        except Exception as exc:
            errors.append(str(exc))
    raise ValueError("Gemini plane program remained invalid: " + " | ".join(errors))


def validate_source(value: Any) -> dict:
    record = value if isinstance(value, dict) else json.loads(str(value or ""))
    if record.get("schema") != SCHEMA:
        raise ValueError("variation source must use {}".format(SCHEMA))
    check = copy.deepcopy(record)
    saved = check.pop("sourceHash", None)
    if saved != repair_candidate.stable_json_hash(check):
        raise ValueError("variation source changed after authoring")
    return record


def _rotate(vector: np.ndarray, axis: np.ndarray, angle_deg: float) -> np.ndarray:
    axis = axis / np.linalg.norm(axis)
    angle = math.radians(float(angle_deg))
    return (
        vector * math.cos(angle)
        + np.cross(axis, vector) * math.sin(angle)
        + axis * float(axis @ vector) * (1.0 - math.cos(angle))
    )


def _nudge_plane(item: dict, angle_deg: float) -> dict:
    result = copy.deepcopy(item)
    normal = np.asarray(item.get("normal"), float)
    length = float(np.linalg.norm(normal))
    normal /= length
    offset = float(item.get("d", 0.0)) / length
    member_axis = np.asarray([0.0, 1.0, 0.0], float)
    axis = np.cross(normal, member_axis)
    if float(np.linalg.norm(axis)) <= 1e-8:
        axis = np.asarray([1.0, 0.0, 0.0], float)
    moved = _rotate(normal, axis, angle_deg)
    anchor = offset * normal
    result["normal"] = moved.tolist()
    result["d"] = float(moved @ anchor)
    return result


def _study_programs(program: dict, response: dict, requested: int) -> list[dict]:
    """Compile LLM-selected plane-role studies into executable programs."""
    base_id = str(program.get("id") or "joint")
    known = {str(item.get("id")) for item in ((program.get("geometry") or {}).get("planes") or [])}

    def prepared(source: dict, study_id: str, summary: str, reason: str, rotation: float,
                 changes: list[dict], kind: str) -> dict:
        changed = copy.deepcopy(source)
        geometry = changed.setdefault("geometry", {})
        planes = list(geometry.get("planes") or [])
        adjusted, deltas = [], []
        for change in changes:
            ids = [str(value) for value in (change.get("planeIds") or [])]
            delta = float(change.get("angleDeltaDeg", 0.0))
            if not ids or any(value not in known for value in ids):
                raise ValueError("study {} references an unknown plane".format(study_id))
            if not 0.25 <= abs(delta) <= 8.0:
                raise ValueError("study {} plane movement must stay between 0.25° and 8°".format(study_id))
            selected = set(ids)
            planes = [
                _nudge_plane(item, delta) if str(item.get("id")) in selected else item
                for item in planes
            ]
            adjusted.extend(value for value in ids if value not in adjusted)
            deltas.append(delta)
        if abs(float(rotation)) > 10.0:
            raise ValueError("study {} whole-joint movement exceeds 10°".format(study_id))
        if not changes and abs(float(rotation)) < 0.25 and kind != "LLM reference":
            raise ValueError("study {} makes no geometric change".format(study_id))
        geometry["planes"] = planes
        geometry["variationTransform"] = {
            "kind": kind,
            "summary": summary,
            "reason": reason,
            "rotationAroundMemberAxisDeg": float(rotation),
            "adjustedPlaneIds": adjusted,
            "planeAngleDeltaDeg": deltas[0] if len(set(deltas)) == 1 else 0.0,
            "changes": copy.deepcopy(changes),
        }
        changed["id"] = "{}__{}".format(base_id, study_id)
        objective = changed.setdefault("fitObjective", {})
        objective["rotationsDeg"] = [float(rotation)]
        objective["parameterSamples"] = 3
        objective["positionSamples"] = 25
        quality = plane_variations.program_quality(changed)
        issues = [value for value in quality["issues"] if "auxiliaryGeometry" not in value]
        intent = geometry.get("prosthesisIntent") or {}
        if bool(intent.get("connected", True)) and quality.get("volumeConnectedGroupComponents", 0) > 1:
            issues.append("angle study creates volume-disconnected prosthesis regions")
        if issues:
            raise ValueError("study {}: {}".format(study_id, "; ".join(issues)))
        return {
            "id": changed["id"], "summary": summary, "reason": reason,
            "program": changed, "kind": kind, "rotationDeg": float(rotation),
            "planeIds": adjusted, "angleDeltaDeg": deltas[0] if len(set(deltas)) == 1 else 0.0,
        }

    base = prepared(
        program, "reference", str(response.get("summary") or "Authored reference"),
        "reference geometry authored from the reviewed repair", 0.0, [], "LLM reference",
    )
    raw = response.get("variationStudies") or []
    if not isinstance(raw, list) or len(raw) < requested - 1:
        raise ValueError("Gemini returned too few incremental angle studies")
    result, signatures, rejected = [base], set(), []
    for index, item in enumerate(raw):
        if len(result) >= requested:
            break
        if not isinstance(item, dict):
            rejected.append("study {} is not an object".format(index + 1))
            continue
        name = str(item.get("id") or "study_{}".format(index + 1))
        slug = "".join(char.lower() if char.isalnum() else "_" for char in name).strip("_")
        try:
            study = prepared(
                program, slug or "study_{}".format(index + 1),
                str(item.get("summary") or name), str(item.get("reason") or ""),
                float(item.get("wholeRotationDeg") or 0.0), list(item.get("changes") or []),
                "LLM angle study",
            )
            signature = repair_candidate.stable_json_hash(
                (study["program"].get("geometry") or {}).get("variationTransform")
            )
            if signature in signatures:
                raise ValueError("duplicate geometry")
            signatures.add(signature)
            result.append(study)
        except Exception as exc:
            rejected.append("{}: {}".format(name, exc))
    if len(result) < min(requested, 3):
        raise ValueError(
            "Gemini returned fewer than three usable incremental studies: "
            + " | ".join(rejected[:4])
        )
    return result


def _fit_one(spec: dict, frame: dict, points: np.ndarray, values: np.ndarray, threshold: float):
    program = copy.deepcopy(spec["program"])
    sides = _replacement_sides(frame, points, values, threshold)
    if sides:
        program.setdefault("fitObjective", {})["replacementSides"] = sides
    result, resolved, report = joinery_program.fit_program(
        program, frame, points, values,
        beam_id=program.get("targetPartRef"), threshold=threshold,
        verify=True, enforce_construction=False, allow_partial=True,
    )
    if result is None:
        raise ValueError("; ".join(report[-2:]))
    removed = scoring.removed_mask(points, result["repair"])
    return {**spec, "result": result, "resolvedProgram": resolved, "removed": removed, "fitReport": report}


def _ordered_fits(fits: list[dict]) -> list[dict]:
    """Rank feasible LLM studies by measured conservation performance."""
    return sorted(
        fits,
        key=lambda item: (
            int(item["result"].get("required_left", 0)),
            float(item["result"]["sound_sacrificed_weighted"]),
            int(item["result"]["sound_sacrificed"]),
            float(item["result"].get("rank_score", 0.0)),
            item["id"],
        ),
    )


def _indices(mask: np.ndarray, limit: int = 8) -> str:
    values = np.flatnonzero(mask).astype(int).tolist()
    if not values:
        return "none"
    text = ", ".join("#{}".format(value) for value in values[:limit])
    return text + (" …" if len(values) > limit else "")


def comparison(item: dict, base: dict, values: np.ndarray, threshold: float) -> dict:
    removed, reference = item["removed"], base["removed"]
    required = values >= float(threshold)
    sound = ~required
    affected = values > 0.0
    sound_removed = int(np.count_nonzero(removed & sound))
    base_sound = int(np.count_nonzero(reference & sound))
    newly_affected = removed & ~reference & affected
    missed_required = required & ~removed
    preserved_sound = reference & ~removed & sound
    extra_sound = removed & ~reference & sound
    transform = item["resolvedProgram"]["geometry"].get("variationTransform") or {}
    move = str(item.get("summary") or transform.get("summary") or "Authored angle study")
    changes = transform.get("changes") or []
    movements = [
        "{} {:+g}°".format("/".join(change.get("planeIds") or []), float(change.get("angleDeltaDeg", 0.0)))
        for change in changes if change.get("planeIds")
    ]
    rotation = float(transform.get("rotationAroundMemberAxisDeg", 0.0))
    if abs(rotation) >= 0.25:
        movements.append("whole joint {:+g}°".format(rotation))
    if movements:
        move += " (" + ", ".join(movements) + ")"
    reason = str(item.get("reason") or transform.get("reason") or "").strip()
    delta = sound_removed - base_sound
    if item is base:
        contrast = "reference candidate"
    elif delta < 0:
        contrast = "keeps {} more sound cell(s) than the reference".format(-delta)
    elif delta > 0:
        contrast = "removes {} more sound cell(s) than the reference".format(delta)
    else:
        contrast = "removes the same number of sound cells as the reference"
    text = (
        "{}; removes {}/{} required damaged cells and {}/{} sound cells; {}.{} "
        "UNREMOVED required cells: {}. "
        "New affected cells removed: {}. Sound cells recovered: {}; extra sound cells removed: {}."
    ).format(
        move,
        int(np.count_nonzero(removed & required)), int(np.count_nonzero(required)),
        sound_removed, int(np.count_nonzero(sound)), contrast,
        " Study purpose: {}.".format(reason) if reason else "",
        _indices(missed_required),
        _indices(newly_affected), _indices(preserved_sound), _indices(extra_sound),
    )
    return {
        "text": text,
        "transform": transform,
        "requiredDamageRemoved": int(np.count_nonzero(removed & required)),
        "requiredDamageCount": int(np.count_nonzero(required)),
        "requiredCoverageComplete": not bool(np.any(missed_required)),
        "unremovedRequiredCellIndices": np.flatnonzero(missed_required).astype(int).tolist(),
        "soundRemoved": sound_removed,
        "soundCount": int(np.count_nonzero(sound)),
        "soundDeltaFromReference": delta,
        "newAffectedCellIndices": np.flatnonzero(newly_affected).astype(int).tolist(),
        "recoveredSoundCellIndices": np.flatnonzero(preserved_sound).astype(int).tolist(),
        "extraSoundCellIndices": np.flatnonzero(extra_sound).astype(int).tolist(),
    }


def _render(item: dict, frame: dict) -> dict:
    result = item["result"]
    repair = result["repair"]
    import Rhino

    tolerance = float(getattr(Rhino.RhinoDoc.ActiveDoc, "ModelAbsoluteTolerance", 0.001))
    kept, prosthesis = [], []
    for part in repair["parts"]:
        geometry = plane_variations._evaluate_part_robust(part, tolerance)
        (kept if part["name"] == "kept" else prosthesis).extend(geometry)
    intent = (item["resolvedProgram"].get("geometry") or {}).get("prosthesisIntent") or {}
    if bool(intent.get("connected", True)) and len(prosthesis) != 1:
        raise ValueError(
            "declared connected prosthesis evaluated as {} separate Breps".format(len(prosthesis))
        )
    cut_count = int(result.get("predicate_count", 0))
    cuts = repair["parts"][0]["cuts"][1 : 1 + cut_count]
    frames = plane_variations._plane_graphics(cuts, 0.75 * max(frame["width"], frame["height"]))
    report = list(item["fitReport"])
    proposal = joinery_program.proposal_record(item["resolvedProgram"], result, report)
    proposal["fit"]["status"] = (
        "damage_coverage_pass" if int(result.get("required_left", 0)) == 0
        else "partial_damage_coverage"
    )
    return {**item, "kept": kept, "prosthesis": prosthesis, "frames": frames, "fit": proposal, "report": report}


def explore(
    source: dict,
    box: Any,
    centers: list[Any],
    damage: list[float],
    threshold: float,
    count: int = 5,
) -> dict:
    """Fit a local bank, skip failed Booleans, and return selectable results."""
    record = validate_source(source)
    requested = max(2, min(8, int(count or 5)))
    frame = plane_variations.box_frame(box)
    points = np.asarray([[point.X, point.Y, point.Z] for point in centers], float)
    values = np.asarray(damage, float)
    fitted, failures = [], []
    bank = list(record.get("variations") or [])
    if not bank:
        raise ValueError("the authored source contains no LLM angle studies")
    for spec in bank:
        try:
            fitted.append(_fit_one(spec, frame, points, values, float(threshold)))
        except Exception as exc:
            failures.append({"id": spec["id"], "stage": "fit", "error": str(exc)})
    ordered = _ordered_fits(fitted)
    if not ordered:
        raise ValueError("no variation covers the required damage; " + (failures[0]["error"] if failures else "no fit"))
    rendered = []
    for item in ordered:
        if len(rendered) >= requested:
            break
        try:
            rendered.append(_render(item, frame))
        except Exception as exc:
            failures.append({"id": item["id"], "stage": "Rhino Boolean", "error": str(exc)})
    if not rendered:
        boolean_failures = [item for item in failures if item["stage"] == "Rhino Boolean"]
        detail = boolean_failures[0]["error"] if boolean_failures else "unknown Boolean failure"
        raise ValueError("all fitted variations failed exact Rhino geometry; first failure: " + detail)
    reference = rendered[0]
    for item in rendered:
        item["analysis"] = comparison(item, reference, values, float(threshold))
    for rank, item in enumerate(rendered, 1):
        item["rank"] = rank
    return {
        "schema": "repair-variation-results@1",
        "requestedCount": requested,
        "bankCount": len(bank),
        "fitCount": len(fitted),
        "fullCoverageCount": sum(
            1 for item in fitted if int(item["result"].get("required_left", 0)) == 0
        ),
        "variations": rendered,
        "failures": failures,
    }


__all__ = [
    "SCHEMA", "author_joint", "comparison", "damage_context", "explore",
    "validate_source",
]
