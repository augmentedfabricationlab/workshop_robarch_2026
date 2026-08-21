"""Agent-facing JointProgram contract and focused AnyJoint fitting.

The LLM reads the selected member, neighbours, repair strategy and evidence,
then authors six oriented planes, a Boolean removal rule and a construction
contract. This module validates that program and turns it into a focused
placement search with damage, engagement, interface, ligament, plane-angle
and assembly gates.

The distinction is intentional:

* the LLM authors construction behaviour and the six-plane program;
* AnyJoint validates geometry and solves placement;
* Rhino/Grasshopper builds the exact Breps.

The contract is JSON-only and Rhino-free so both the Repair Workspace server
and Grasshopper can exchange it without sharing implementation details.
"""

from __future__ import annotations

import copy
import json
import math
import os
from typing import Any, Optional, Sequence

from . import anyjoint


PROGRAM_SCHEMA = "joinery-program@1"
VALID_TOPOLOGIES = ("any_joint", "lap", "scarf", "lapped_bowtie")
LOCK_WORDS = ("positive", "lock", "locked", "dovetail", "bowtie", "tension")
CARDINAL_DIRECTIONS = {"+X", "-X", "+Y", "-Y", "+Z", "-Z"}


class JointProgramError(ValueError):
    """Raised when a JointProgram cannot be made safe enough to execute."""


def _cardinal_direction(value: Any) -> Optional[Any]:
    """Return a canonical local direction while preserving valid vectors."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            vector = [float(component) for component in value]
        except (TypeError, ValueError):
            return None
        if all(math.isfinite(component) for component in vector) and any(
            abs(component) > 1e-8 for component in vector
        ):
            return vector
        return None
    if not isinstance(value, str):
        return None
    key = value.strip().upper().replace("LOCAL_", "")
    return key if key in CARDINAL_DIRECTIONS else None


def _signed_direction_from_words(value: Any) -> Optional[str]:
    """Resolve common access phrases to the beam-local extraction direction."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "+X": ("positive_x", "x_positive", "from_right", "extract_right"),
        "-X": ("negative_x", "x_negative", "from_left", "extract_left"),
        "+Y": ("positive_y", "y_positive", "toward_far_end", "extract_forward"),
        "-Y": ("negative_y", "y_negative", "toward_near_end", "extract_backward"),
        "+Z": ("positive_z", "z_positive", "from_above", "from_top", "extract_up"),
        "-Z": ("negative_z", "z_negative", "from_below", "from_bottom", "extract_down"),
    }
    for direction, words in aliases.items():
        if any(word in text for word in words):
            return direction
    return None


def _semantic_lock_directions(value: Any) -> list[str]:
    """Expand behavioural language into beam-local directions that must lock."""
    if not isinstance(value, str):
        return []
    text = value.strip().lower().replace("-", "_").replace(" ", "_")
    if any(word in text for word in ("axial", "longitudinal", "beam_axis")):
        return ["+Y", "-Y"]
    if any(word in text for word in ("vertical", "uplift", "gravity")):
        return ["+Z", "-Z"]
    if any(word in text for word in ("lateral", "transverse", "sideways")):
        return ["+X", "-X"]
    return []


def _normalise_construction_directions(constraints: dict, warnings: list[str]) -> None:
    """Keep Gemini's rationale but give the geometry kernel executable axes."""
    notes = constraints.setdefault("directionNotes", [])
    assembly = constraints.get("assemblyDirection")
    if assembly is not None:
        canonical = _cardinal_direction(assembly)
        if canonical is None:
            canonical = _signed_direction_from_words(assembly)
        if canonical is None:
            raise JointProgramError(
                "assemblyDirection %r cannot be resolved; use +X, -X, +Y, -Y, +Z, -Z or null"
                % assembly
            )
        else:
            if canonical != assembly:
                notes.append("assemblyDirection authored as: %s" % assembly)
                warnings.append(
                    "assemblyDirection %r resolved to local %s" % (assembly, canonical)
                )
            constraints["assemblyDirection"] = canonical

    raw_locks = constraints.get("geometricLockDirections") or []
    if isinstance(raw_locks, (str, dict)):
        raw_locks = [raw_locks]
    locks = []
    for value in raw_locks:
        canonical = _cardinal_direction(value)
        expanded = [canonical] if canonical is not None else _semantic_lock_directions(value)
        if not expanded:
            raise JointProgramError(
                "geometric lock direction %r cannot be resolved; use local axis labels"
                % value
            )
        if canonical is None:
            notes.append("geometricLockDirections authored as: %s" % value)
            warnings.append(
                "geometric lock %r expanded to local %s"
                % (value, ", ".join(expanded))
            )
        for direction in expanded:
            if direction not in locks:
                locks.append(direction)
    constraints["geometricLockDirections"] = locks
    if not notes:
        constraints.pop("directionNotes", None)


def _load_json(value: Any, label: str) -> dict:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise JointProgramError("%s is empty" % label)
        if os.path.isfile(text):
            with open(text, "r", encoding="utf-8-sig") as handle:
                text = handle.read()
        try:
            parsed = json.loads(text)
        except Exception as exc:
            raise JointProgramError("%s is not valid JSON: %s" % (label, exc)) from exc
        if not isinstance(parsed, dict):
            raise JointProgramError("%s must contain a JSON object" % label)
        return parsed
    raise JointProgramError("%s must be a dict, JSON string or JSON file path" % label)


def _step_relevance(step: dict) -> tuple[int, list[str]]:
    """Give the LLM a transparent shortlist of likely joinery actions.

    The score only narrows a plan to a few plausible actions.  The LLM still
    chooses the step after reading the assembly, dependencies and evidence.
    """
    text = " ".join(
        str(step.get(key) or "").lower() for key in ("title", "description", "action")
    )
    weights = {
        "splice": 12,
        "dutchman": 10,
        "joinery": 7,
        "joint": 6,
        "scarf": 5,
        "dovetail": 5,
        "bowtie": 5,
        "half lap": 5,
        "bladed lap": 5,
        "repair": 3,
        "fabricate": 3,
        "shape": 2,
        "cut": 1,
        "saw": 1,
        "chisel": 1,
    }
    penalties = {
        "document": -7,
        "source": -6,
        "protective finish": -6,
        "remove": -4,
        "dry fit": -3,
        "shore": -3,
        "reinstall": -2,
    }
    score = 0
    reasons = []
    for word, weight in weights.items():
        if word in text:
            score += weight
            reasons.append(word)
    for word, weight in penalties.items():
        if word in text:
            score += weight
    return score, reasons


def workspace_context(
    workspace: Any, beam_id: str, repair_step_id: Optional[str] = None
) -> dict:
    """Extract the construction context around one selected member.

    Raw image data is deliberately excluded.  Evidence ids and metadata stay
    in the context; multimodal callers send the corresponding images as model
    attachments.
    """
    ws = _load_json(workspace, "workspace")
    parts = list((ws.get("instance") or {}).get("parts") or [])
    by_id = {str(part.get("id")): part for part in parts if part.get("id")}
    target = by_id.get(str(beam_id))
    if target is None:
        raise JointProgramError(
            "beam_id %r does not match a workspace part; available ids: %s"
            % (beam_id, ", ".join(sorted(by_id)) or "(none)")
        )

    neighbour_ids = [str(value) for value in (target.get("connections") or [])]
    neighbours = [by_id[value] for value in neighbour_ids if value in by_id]
    conditions = [
        item
        for item in (ws.get("conditions") or [])
        if str(item.get("partRef")) == str(beam_id)
    ]
    evidence_ids = set()
    for condition in conditions:
        evidence_ids.update(str(value) for value in (condition.get("evidenceRefs") or []))
    evidence = []
    for item in ws.get("evidence") or []:
        attached = item.get("attachedTo")
        attached_id = attached.get("id") if isinstance(attached, dict) else attached
        if str(item.get("id")) in evidence_ids or str(attached_id) in {
            str(beam_id),
            *[str(condition.get("id")) for condition in conditions],
        }:
            evidence.append(
                {
                    key: item.get(key)
                    for key in (
                        "id",
                        "kind",
                        "attachedTo",
                        "capturedAt",
                        "text",
                        "measurement",
                        "fileName",
                        "mimeType",
                    )
                    if item.get(key) is not None
                }
            )

    plans = list(ws.get("plans") or [])
    current_plan = next(
        (plan for plan in plans if str(plan.get("id")) == str(ws.get("currentPlanId"))),
        None,
    )
    if current_plan is None and len(plans) == 1:
        current_plan = plans[0]
    relevant_steps = []
    sequence_steps = []
    relevant_edges = []
    joinery_candidates = []
    selected_step = None
    if current_plan:
        all_steps = list(current_plan.get("steps") or [])
        by_step_id = {
            str(step.get("id")): step for step in all_steps if step.get("id")
        }
        condition_ids = {str(item.get("id")) for item in conditions if item.get("id")}
        relevant_steps = [
            step
            for step in all_steps
            if (
                str(beam_id)
                in [str(value) for value in (step.get("affectedPartRefs") or [])]
                or bool(
                    condition_ids.intersection(
                        str(value) for value in (step.get("addressesConditionRefs") or [])
                    )
                )
            )
        ]
        relevant_ids = {str(step.get("id")) for step in relevant_steps}
        all_edges = list(current_plan.get("edges") or [])
        adjacent_ids = set(relevant_ids)
        for edge in all_edges:
            source = str(edge.get("source"))
            target_id = str(edge.get("target"))
            if source in relevant_ids or target_id in relevant_ids:
                adjacent_ids.update((source, target_id))
        sequence_steps = [
            step for step in all_steps if str(step.get("id")) in adjacent_ids
        ]
        relevant_edges = [
            edge
            for edge in all_edges
            if str(edge.get("source")) in adjacent_ids
            and str(edge.get("target")) in adjacent_ids
        ]
        ranked = []
        for step in relevant_steps:
            score, reasons = _step_relevance(step)
            if score > 0:
                candidate = copy.deepcopy(step)
                candidate["semanticRelevance"] = score
                candidate["matchedTerms"] = reasons
                ranked.append(candidate)
        joinery_candidates = sorted(
            ranked,
            key=lambda item: (-int(item.get("semanticRelevance", 0)), str(item.get("id"))),
        )
        if repair_step_id:
            selected_step = by_step_id.get(str(repair_step_id))
            if selected_step is None:
                raise JointProgramError(
                    "repair_step_id %r does not exist in current plan %r"
                    % (repair_step_id, current_plan.get("id"))
                )

    def part_summary(part: dict) -> dict:
        return {
            key: copy.deepcopy(part.get(key))
            for key in (
                "id",
                "label",
                "origin",
                "dimensions",
                "rotation",
                "connections",
                "material",
                "status",
                "notes",
                "function",
            )
            if part.get(key) is not None
        }

    return {
        "schemaVersion": ws.get("schemaVersion"),
        "instance": {
            "id": (ws.get("instance") or {}).get("id"),
            "name": (ws.get("instance") or {}).get("name"),
            "provenance": (ws.get("instance") or {}).get("provenance"),
            "notes": (ws.get("instance") or {}).get("notes"),
        },
        "targetPart": part_summary(target),
        "connectedParts": [part_summary(part) for part in neighbours],
        "conditions": copy.deepcopy(conditions),
        "evidence": evidence,
        "strategy": (
            {
                "id": current_plan.get("id"),
                "label": current_plan.get("label"),
                "intent": copy.deepcopy(current_plan.get("intent")),
                "constraints": copy.deepcopy(current_plan.get("constraints")),
                "relevantSteps": copy.deepcopy(relevant_steps),
                "joineryStepCandidates": copy.deepcopy(joinery_candidates),
                "sequenceSteps": copy.deepcopy(sequence_steps),
                "sequenceEdges": copy.deepcopy(relevant_edges),
                "humanSelectedStep": copy.deepcopy(selected_step),
                "stepSelectionRule": (
                    "Choose exactly one existing step id whose action creates the repair "
                    "joinery. Treat preparation, shoring, dry fitting, installation and "
                    "finishing as sequence context. Use humanSelectedStep when supplied."
                ),
            }
            if current_plan
            else None
        ),
    }


def _operation_parameters(program: dict) -> dict:
    out = copy.deepcopy((program.get("geometry") or {}).get("parameters") or {})
    for operation in program.get("geometryProgram") or []:
        if isinstance(operation, dict):
            out.update(operation.get("parameters") or {})
    return out


def resolve_topology(program: dict) -> str:
    """Resolve an explicit or behavioural topology choice."""
    geometry = program.get("geometry") or {}
    if geometry.get("planes"):
        return "any_joint"
    explicit = str(geometry.get("topology") or "").strip().lower()
    aliases = {
        "bowtie": "lapped_bowtie",
        "dovetail": "lapped_bowtie",
        "positive_lock": "lapped_bowtie",
        "lap_plus_bowtie": "lapped_bowtie",
        "simple_scarf": "scarf",
        "half_lap": "lap",
    }
    explicit = aliases.get(explicit, explicit)
    if explicit in VALID_TOPOLOGIES:
        return explicit

    words = []
    for operation in program.get("geometryProgram") or []:
        if isinstance(operation, dict):
            words.extend(
                str(operation.get(key) or "").lower()
                for key in ("operation", "feature", "type", "grammar")
            )
    behaviour = program.get("jointBehaviour") or {}
    words.extend(
        str(behaviour.get(key) or "").lower()
        for key in ("tensionRetention", "retention", "loadTransfer")
    )
    joined = " ".join(words)
    if any(word in joined for word in LOCK_WORDS):
        return "lapped_bowtie"
    if "scarf" in joined:
        return "scarf"
    return "lap"


def normalise_program(program: Any, beam_id: Optional[str] = None) -> tuple[dict, list[str]]:
    raw = _load_json(program, "JointProgram")
    warnings = []
    schema = raw.get("schema") or PROGRAM_SCHEMA
    if schema != PROGRAM_SCHEMA:
        warnings.append("program schema %r normalised to %s" % (schema, PROGRAM_SCHEMA))

    target = raw.get("targetPartRef") or beam_id
    if not target:
        raise JointProgramError("JointProgram needs targetPartRef or a beam_id input")
    if beam_id and str(target) != str(beam_id):
        raise JointProgramError(
            "JointProgram targets %r while Grasshopper selected %r" % (target, beam_id)
        )

    confidence = float(raw.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))
    normalised = {
        "schema": PROGRAM_SCHEMA,
        "id": str(raw.get("id") or "joinery_%s" % target),
        "targetPartRef": str(target),
        "repairStepRef": raw.get("repairStepRef"),
        "addressesConditionRefs": [
            str(value) for value in (raw.get("addressesConditionRefs") or [])
        ],
        "contextAssessment": copy.deepcopy(raw.get("contextAssessment") or {}),
        "jointBehaviour": copy.deepcopy(raw.get("jointBehaviour") or {}),
        "geometry": copy.deepcopy(raw.get("geometry") or {}),
        "geometryProgram": copy.deepcopy(raw.get("geometryProgram") or []),
        "constructionConstraints": copy.deepcopy(
            raw.get("constructionConstraints") or {}
        ),
        "fitObjective": copy.deepcopy(raw.get("fitObjective") or {}),
        "assemblyPlan": copy.deepcopy(raw.get("assemblyPlan") or {}),
        "fabricationPlan": copy.deepcopy(raw.get("fabricationPlan") or {}),
        "affectedPartRefs": [str(value) for value in (raw.get("affectedPartRefs") or [])],
        "evidence": copy.deepcopy(raw.get("evidence") or []),
        "confidence": confidence,
        "openQuestions": [str(value) for value in (raw.get("openQuestions") or [])],
    }
    topology = resolve_topology(normalised)
    normalised["geometry"]["topology"] = topology
    constraints = normalised["constructionConstraints"]
    defaults = {
        "damageBufferSections": 0.25,
        "minimumEngagementSections": 1.5,
        "targetEngagementSections": 2.5,
        "minimumInterfaceAreaRatio": 1.2,
        "targetInterfaceAreaRatio": 2.0,
        "minimumLigamentRatio": 0.08,
        "minimumPlaneAngleDeg": 10.0,
        "maximumSupportPlanes": 6,
        "targetDamageClearanceSections": 0.35,
        "rankingWeights": {
            "damageRobustness": 0.35,
            "engagement": 0.25,
            "interface": 0.15,
            "fabrication": 0.15,
            "conservation": 0.10,
        },
    }
    if topology == "any_joint":
        for key, value in defaults.items():
            constraints.setdefault(key, copy.deepcopy(value))
        _normalise_construction_directions(constraints, warnings)
    if not normalised["geometryProgram"]:
        if topology == "any_joint":
            normalised["geometryProgram"] = [
                {"operation": "plane_boolean", "grammar": "six_plane_dnf"}
            ]
        elif topology == "lapped_bowtie":
            normalised["geometryProgram"] = [
                {"operation": "base_splice", "grammar": "six_plane"},
                {"operation": "intersect_feature", "feature": "bowtie_lock"},
            ]
        else:
            normalised["geometryProgram"] = [
                {"operation": "base_splice", "grammar": topology}
            ]
    return normalised, warnings


def _numbers(value: Any, default: Sequence[float]) -> list[float]:
    raw = value if isinstance(value, (list, tuple)) else default
    out = []
    for item in raw:
        try:
            number = float(item)
        except (TypeError, ValueError):
            continue
        if number not in out:
            out.append(number)
    return out or [float(value) for value in default]


def _finite_float(value: Any, default: float) -> float:
    """Read a model-authored number without allowing NaN or infinity through."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _ordered_bowtie_fractions(params: dict) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Project four bowtie stations into the compiler's valid ordered domain.

    Gemini occasionally returns the right topology with stations listed out of
    order.  Sorting retains its proposed station set; a small deterministic gap
    separates coincident stations so the resulting planes remain buildable.
    """
    defaults = (0.34, 0.50, 0.64, 0.84)
    names = (
        "root_fraction",
        "shoulder_fraction",
        "seat_fraction",
        "tip_fraction",
    )
    raw = tuple(
        _finite_float(params.get(name, default), default)
        for name, default in zip(names, defaults)
    )
    clipped = [max(0.05, min(0.98, value)) for value in raw]
    if all(clipped[index] < clipped[index + 1] for index in range(3)):
        return raw, tuple(clipped)

    ordered = sorted(clipped)
    minimum_gap = 0.04
    for index in range(1, len(ordered)):
        ordered[index] = max(ordered[index], ordered[index - 1] + minimum_gap)
    if ordered[-1] > 0.98:
        ordered[-1] = 0.98
        for index in range(len(ordered) - 2, -1, -1):
            ordered[index] = min(ordered[index], ordered[index + 1] - minimum_gap)
    return raw, tuple(ordered)


def _record_resolved_parameters(program: dict, values: dict) -> None:
    """Make resolved_json describe the values the compiler actually used."""
    geometry = program.setdefault("geometry", {})
    geometry.setdefault("parameters", {}).update(values)
    for operation in program.get("geometryProgram") or []:
        if not isinstance(operation, dict) or not isinstance(operation.get("parameters"), dict):
            continue
        for name, value in values.items():
            if name in operation["parameters"]:
                operation["parameters"][name] = value


def _axially_scaled_plane_candidate(
    candidate: anyjoint.GrammarCandidate, factor: float
) -> anyjoint.GrammarCandidate:
    """Lengthen one authored AnyJoint along local Y without changing topology."""
    scale = float(factor)
    aspect = float(candidate.parameters["aspect"])
    centre = 0.5 * aspect
    planes = []
    for index, plane in enumerate(candidate.template.planes):
        nx, ny, nz = (float(value) for value in plane.normal)
        planes.append(
            {
                "id": "P%d" % index,
                "normal": [scale * nx, ny, scale * nz],
                "d": scale * float(plane.d) - ny * centre * (scale - 1.0),
                "role": candidate.template.roles[index],
            }
        )
    scaled = anyjoint.plane_program_candidate(
        planes,
        [list(group) for group in candidate.template.groups],
        aspect=aspect,
        candidate_id="%s-S%03d" % (candidate.candidate_id, round(100.0 * scale)),
    )
    scaled.parameters["axial_scale"] = scale
    return scaled


def _focused_anyjoint_variants(
    candidate: anyjoint.GrammarCandidate, constraints: dict, count: int
) -> list[anyjoint.GrammarCandidate]:
    """Create at most two solver refinements around Gemini's authored planes."""
    candidate.parameters["axial_scale"] = 1.0
    if count <= 1:
        return [candidate]
    metrics = anyjoint.candidate_geometry_metrics(candidate)
    actual = float(metrics.get("engagementSections", 0.0))
    if actual <= 1e-6:
        factors = [1.0, 1.15, 1.30]
    else:
        minimum = float(constraints.get("minimumEngagementSections", 1.5))
        target = max(minimum, float(constraints.get("targetEngagementSections", minimum)))
        # A small margin keeps the refined geometry clear of the sampled hard
        # boundary. The target remains authored by Gemini/the Workspace plan.
        minimum_factor = max(1.0, (minimum + 0.10) / actual)
        target_factor = max(1.0, target / actual)
        factors = [
            1.0,
            min(1.35, max(1.03, minimum_factor)),
            min(1.35, max(1.08, target_factor)),
        ]
    unique = []
    for factor in factors[:count]:
        rounded = round(float(factor), 4)
        if rounded not in unique:
            unique.append(rounded)
    return [
        candidate if abs(factor - 1.0) <= 1e-8 else _axially_scaled_plane_candidate(candidate, factor)
        for factor in unique
    ]


def program_candidates(program: Any, beam_id: Optional[str] = None):
    """Compile a semantic program into a small, focused grammar bank.

    The default produces one requested topology with at most three nearby
    parameterisations.  This keeps the LLM responsible for the design idea
    while giving the deterministic fitter enough freedom to fit the damage.
    """
    resolved, warnings = normalise_program(program, beam_id=beam_id)
    topology = resolved["geometry"]["topology"]
    params = _operation_parameters(resolved)
    objective = resolved.get("fitObjective") or {}
    refinement = max(1, min(3, int(objective.get("parameterSamples", 3))))

    if topology == "any_joint":
        geometry = resolved.get("geometry") or {}
        candidate = anyjoint.plane_program_candidate(
            geometry.get("planes"),
            geometry.get("removalGroups"),
            aspect=_finite_float(geometry.get("aspect", 3.0), 3.0),
            roles=geometry.get("roles"),
            candidate_id="AJ-ANY-%s" % str(resolved["id"]).replace(" ", "-")[:40],
        )
        resolved["geometry"]["aspect"] = float(candidate.parameters["aspect"])
        resolved["geometry"]["planes"] = [
            {
                "id": "P%d" % index,
                "normal": [float(value) for value in plane.normal],
                "d": float(plane.d),
                "role": candidate.template.roles[index],
            }
            for index, plane in enumerate(candidate.template.planes)
        ]
        resolved["geometry"]["removalGroups"] = [
            ["P%d" % slot for slot in group]
            for group in candidate.template.groups
        ]
        candidates = _focused_anyjoint_variants(
            candidate,
            resolved.get("constructionConstraints") or {},
            refinement,
        )
    elif topology == "scarf":
        slope = _finite_float(params.get("slope", 2.25), 2.25)
        multipliers = (1.0,) if refinement == 1 else (0.85, 1.0, 1.15)
        candidates = [anyjoint.scarf_candidate(max(0.2, slope * value)) for value in multipliers]
    elif topology == "lapped_bowtie":
        raw_depth = _finite_float(params.get("lap_fraction", 0.5), 0.5)
        raw_width = _finite_float(params.get("lock_half_width", 0.24), 0.24)
        depth = max(0.2, min(0.8, raw_depth))
        width = max(0.06, min(0.48, raw_width))
        raw_stations, stations = _ordered_bowtie_fractions(params)
        root, shoulder, seat, tip = stations
        resolved_values = {
            "lap_fraction": depth,
            "root_fraction": root,
            "shoulder_fraction": shoulder,
            "seat_fraction": seat,
            "tip_fraction": tip,
            "lock_half_width": width,
        }
        _record_resolved_parameters(resolved, resolved_values)
        if raw_stations != stations:
            warnings.append(
                "bowtie fractions adjusted from %s to %s so "
                "root < shoulder < seat < tip"
                % (
                    [round(value, 4) for value in raw_stations],
                    [round(value, 4) for value in stations],
                )
            )
        if raw_depth != depth:
            warnings.append("lap_fraction adjusted from %.4g to %.4g" % (raw_depth, depth))
        if raw_width != width:
            warnings.append(
                "lock_half_width adjusted from %.4g to %.4g" % (raw_width, width)
            )
        multipliers = (1.0,) if refinement == 1 else (0.82, 1.0, 1.18)
        candidates = [
            anyjoint.lapped_bowtie_candidate(
                lap_fraction=depth,
                root_fraction=root,
                shoulder_fraction=shoulder,
                seat_fraction=seat,
                tip_fraction=tip,
                lock_half_width=max(0.06, min(0.48, width * value)),
            )
            for value in multipliers
        ]
    else:
        depth = float(params.get("lap_fraction", 0.5))
        chevron = float(params.get("chevron", 0.0))
        rake_left = float(params.get("rake_left", params.get("rake", 0.0)))
        rake_right = float(params.get("rake_right", params.get("rake", 0.0)))
        offsets = (0.0,) if refinement == 1 else (-0.08, 0.0, 0.08)
        candidates = [
            anyjoint.lap_candidate(
                chevron=max(0.0, chevron + offset),
                rake_left=rake_left,
                rake_right=rake_right,
                lap_fraction=max(0.2, min(0.8, depth)),
            )
            for offset in offsets
        ]
    return candidates[:3], resolved, warnings


def fit_program(
    program: Any,
    frame: dict,
    cells,
    damage,
    beam_id: Optional[str] = None,
    threshold: Optional[float] = None,
    verify: bool = True,
    enforce_construction: bool = True,
    allow_partial: bool = False,
) -> tuple[Optional[dict], dict, list[str]]:
    """Fit one LLM-authored program and return its single preferred result."""
    grammar, resolved, warnings = program_candidates(program, beam_id=beam_id)
    objective = resolved.get("fitObjective") or {}
    gate = float(
        threshold
        if threshold is not None
        else objective.get("damageThreshold", 0.5)
    )
    rotations = _numbers(objective.get("rotationsDeg"), (0.0, 90.0, 180.0, 270.0))
    sides = [int(value) for value in _numbers(objective.get("replacementSides"), (1, -1))]
    results, report = anyjoint.search(
        frame,
        cells,
        damage,
        threshold=gate,
        grammar=grammar,
        n_positions=max(2, min(25, int(objective.get("positionSamples", 7)))),
        window=float(objective.get("searchWindowSections", 1.5)),
        margin=float(objective.get("damageMarginSections", 1.0)),
        rotations=rotations,
        sides=sides,
        complexity_weight=float(objective.get("complexityWeight", 0.0)),
        construction_constraints=(
            resolved.get("constructionConstraints") or None
            if enforce_construction else None
        ),
        verify=verify,
        allow_partial=allow_partial,
    )
    report = [
        "JointProgram %s: %s; one Gemini design compiled into %d bounded plane variant(s)"
        % (resolved["id"], resolved["geometry"]["topology"], len(grammar))
    ] + warnings + report
    if not enforce_construction:
        report.insert(
            1,
            "LLM-authored construction constraints are advisory; damage coverage and exact geometry remain active",
        )
    if not results:
        return None, resolved, report
    chosen = results[0]
    report.append("resolved proposal: %s" % anyjoint.result_summary(chosen))
    return chosen, resolved, report


def proposal_record(program: dict, result: Optional[dict], report: Sequence[str]) -> dict:
    """Build the compact record stored on a Repair Workspace plan step."""
    geometry = None
    fit = {"status": "no_fit"}
    if result is not None:
        geometry = {
            "candidateId": result["candidate_id"],
            "topology": program["geometry"]["topology"],
            "parameters": copy.deepcopy(result["parameters"]),
            "position": float(result["position"]),
            "rotationDeg": float(result["rotate_deg"]),
            "replacementSide": int(result["side"]),
            "interfaceScale": float(result["interface_scale"]),
            "joint": copy.deepcopy(result["joint"]),
        }
        fit = {
            "status": (
                "construction_contract_pass"
                if result.get("family") == "any_joint"
                else "damage_coverage_pass"
            ),
            "damageLeft": int(result["damage_left"]),
            "soundCellsRemoved": int(result["sound_sacrificed"]),
            "soundCellCount": int(result["n_sound"]),
            "weightedSoundLoss": float(result["sound_sacrificed_weighted"]),
            "requiredRemovalCount": int(result.get("required_removal_count", 0)),
            "requiredLeft": int(result.get("required_left", 0)),
            "damageClearanceSections": float(
                result.get("damage_clearance_sections", 0.0)
            ),
            "constructionMetrics": copy.deepcopy(
                result.get("construction_metrics") or {}
            ),
        }
    return {
        "schema": "joinery-proposal@1",
        "id": program["id"],
        "targetPartRef": program["targetPartRef"],
        "repairStepRef": program.get("repairStepRef"),
        "program": copy.deepcopy(program),
        "resolvedGeometry": geometry,
        "fit": fit,
        "report": [str(value) for value in report],
    }
