import os
import sys

import numpy as np


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from workshop_robarch_2026 import anyjoint, joints, joinery_program


def _beam_cells():
    frame = {
        "origin": [0.0, 0.0, 0.0],
        "u": [1.0, 0.0, 0.0],
        "v": [0.0, 1.0, 0.0],
        "w": [0.0, 0.0, 1.0],
        "width": 0.12,
        "length": 1.20,
        "height": 0.12,
    }
    u = np.linspace(0.015, 0.105, 4)
    v = np.linspace(0.015, 1.185, 40)
    w = np.linspace(0.015, 0.105, 4)
    cells = np.array(np.meshgrid(u, v, w, indexing="ij")).reshape(3, -1).T
    damage = np.zeros(len(cells))
    damage[(cells[:, 1] > 0.94) & (cells[:, 2] < 0.08)] = 0.9
    return frame, cells, damage


def test_generated_lap_variants_are_valid_joint_partitions():
    variants = [
        anyjoint.lap_candidate(),
        anyjoint.lap_candidate(0.35, 0.35, 0.35, 0.4),
        anyjoint.lap_candidate(0.685, 0.70, 0.70, 0.6),
        anyjoint.lap_candidate(0.0, -1.0, 1.0, 0.5),
        anyjoint.scarf_candidate(2.25),
    ]
    for candidate in variants:
        joint = anyjoint.compile_candidate(candidate)
        check = joints.check_joint(joint, n=30000)
        assert check["accepted"], (candidate.candidate_id, check)


def test_search_returns_only_damage_covering_generated_candidates():
    frame, cells, damage = _beam_cells()
    grammar = [
        anyjoint.lap_candidate(0.0, 0.0, 0.0, 0.5),
        anyjoint.lap_candidate(0.35, 0.35, 0.35, 0.5),
        anyjoint.scarf_candidate(2.0),
    ]
    results, report = anyjoint.search(
        frame,
        cells,
        damage,
        threshold=0.5,
        grammar=grammar,
        n_positions=5,
        rotations=(0.0, 90.0),
        sides=(+1, -1),
    )
    assert results, report
    assert all(result["valid"] for result in results)
    assert all(result["damage_left"] == 0 for result in results)
    assert all(result["candidate_id"].startswith("AJ-") for result in results)
    selected = anyjoint.shortlist(results, 3)
    assert len({item["candidate_id"] for item in selected}) == len(selected)


def test_default_grammar_contains_novel_and_degenerate_families():
    grammar = anyjoint.default_grammar()
    ids = {candidate.candidate_id for candidate in grammar}
    assert len(grammar) > 20
    assert any(candidate.family == "scarf" for candidate in grammar)
    assert any(
        candidate.parameters.get("chevron") == 0.35
        and candidate.parameters.get("rake_left") == 0.35
        for candidate in grammar
    )
    assert len(ids) == len(grammar)


def test_lapped_bowtie_uses_a_distinct_boolean_topology():
    candidate = anyjoint.lapped_bowtie_candidate()
    joint = anyjoint.compile_candidate(candidate)
    assert candidate.family == "lapped_bowtie"
    assert tuple(tuple(group) for group in joint["removal_groups"]) != (
        (0, 1, 2),
        (3, 4),
        (3, 5),
    )
    check = joints.check_joint(joint, n=30000)
    assert check["accepted"], check


def test_joint_program_uses_positive_lock_and_returns_one_resolved_fit():
    frame, cells, damage = _beam_cells()
    program = {
        "schema": "joinery-program@1",
        "id": "joinery_test_lock",
        "targetPartRef": "beam_test",
        "jointBehaviour": {"tensionRetention": "positive mechanical lock"},
        "geometryProgram": [
            {"operation": "base_splice", "grammar": "six_plane"},
            {"operation": "intersect_feature", "feature": "bowtie_lock"},
        ],
        "fitObjective": {
            "damageThreshold": 0.5,
            "parameterSamples": 1,
            "positionSamples": 5,
            "rotationsDeg": [0, 90],
            "replacementSides": [1, -1],
        },
    }
    result, resolved, report = joinery_program.fit_program(
        program,
        frame,
        cells,
        damage,
        beam_id="beam_test",
    )
    assert result is not None, report
    assert resolved["geometry"]["topology"] == "lapped_bowtie"
    assert result["family"] == "lapped_bowtie"
    assert result["damage_left"] == 0
    record = joinery_program.proposal_record(resolved, result, report)
    assert record["fit"]["status"] == "damage_coverage_pass"
    assert record["resolvedGeometry"]["topology"] == "lapped_bowtie"


def test_joint_program_repairs_unordered_bowtie_fractions():
    program = {
        "schema": "joinery-program@1",
        "targetPartRef": "beam_test",
        "geometry": {
            "topology": "lapped_bowtie",
            "parameters": {
                "lap_fraction": 1.2,
                "root_fraction": 0.78,
                "shoulder_fraction": 0.22,
                "seat_fraction": 0.62,
                "tip_fraction": 0.48,
                "lock_half_width": 0.9,
            },
        },
        "fitObjective": {"parameterSamples": 1},
    }
    candidates, resolved, warnings = joinery_program.program_candidates(
        program, beam_id="beam_test"
    )
    parameters = resolved["geometry"]["parameters"]
    stations = [
        parameters["root_fraction"],
        parameters["shoulder_fraction"],
        parameters["seat_fraction"],
        parameters["tip_fraction"],
    ]
    assert 0.05 <= stations[0] < stations[1] < stations[2] < stations[3] <= 0.98
    assert parameters["lap_fraction"] == 0.8
    assert parameters["lock_half_width"] == 0.48
    assert candidates[0].family == "lapped_bowtie"
    assert any("bowtie fractions adjusted" in warning for warning in warnings)


def test_joint_program_separates_coincident_bowtie_fractions():
    program = {
        "schema": "joinery-program@1",
        "targetPartRef": "beam_test",
        "geometry": {
            "topology": "lapped_bowtie",
            "parameters": {
                "root_fraction": 0.5,
                "shoulder_fraction": 0.5,
                "seat_fraction": 0.5,
                "tip_fraction": 0.5,
            },
        },
        "fitObjective": {"parameterSamples": 1},
    }
    candidates, resolved, warnings = joinery_program.program_candidates(
        program, beam_id="beam_test"
    )
    parameters = resolved["geometry"]["parameters"]
    stations = [parameters[name] for name in (
        "root_fraction", "shoulder_fraction", "seat_fraction", "tip_fraction"
    )]
    assert all(stations[index + 1] - stations[index] >= 0.039 for index in range(3))
    assert candidates
    assert warnings


def _anyjoint_plane_program(axial_run=3.5):
    return {
        "schema": "joinery-program@1",
        "id": "joinery_free_planes",
        "targetPartRef": "beam_test",
        "geometry": {
            "topology": "any_joint",
            "aspect": 3.5,
            "planes": [
                {
                    "id": "P%d" % index,
                    "normal": [0.0, 1.0, axial_run],
                    "d": 1.75,
                    "role": "long bearing face",
                }
                for index in range(6)
            ],
            "removalGroups": [["P0"]],
        },
        "geometryProgram": [
            {"operation": "plane_boolean", "grammar": "six_plane_dnf"}
        ],
        "constructionConstraints": {
            "damageBufferSections": 0.0,
            "minimumEngagementSections": 2.5,
            "targetEngagementSections": 3.5,
            "minimumInterfaceAreaRatio": 1.5,
            "targetInterfaceAreaRatio": 3.5,
            "minimumLigamentRatio": 0.08,
            "minimumPlaneAngleDeg": 10.0,
            "assemblyDirection": "+Y",
            "targetDamageClearanceSections": 0.25,
        },
        "fitObjective": {
            "positionSamples": 5,
            "rotationsDeg": [0],
            "replacementSides": [1],
        },
    }


def test_name_free_anyjoint_compiles_direct_six_plane_boolean_program():
    candidates, resolved, warnings = joinery_program.program_candidates(
        _anyjoint_plane_program(), beam_id="beam_test"
    )
    assert not warnings
    assert 1 <= len(candidates) <= 3
    assert candidates[0].family == "any_joint"
    assert candidates[0].parameters["axial_scale"] == 1.0
    assert max(candidate.parameters["axial_scale"] for candidate in candidates) > 1.0
    assert resolved["geometry"]["topology"] == "any_joint"
    assert resolved["geometry"]["removalGroups"] == [["P0"]]
    assert len(resolved["geometry"]["planes"]) == 6
    check = joints.check_joint(anyjoint.compile_candidate(candidates[0]), n=30000)
    assert check["accepted"], check


def test_semantic_gemini_directions_resolve_to_local_axis_constraints():
    program = _anyjoint_plane_program()
    program["constructionConstraints"]["assemblyDirection"] = "from above"
    program["constructionConstraints"]["geometricLockDirections"] = [
        "axial_tension_limited_by_squint"
    ]
    _candidates, resolved, warnings = joinery_program.program_candidates(
        program, beam_id="beam_test"
    )
    constraints = resolved["constructionConstraints"]
    assert constraints["assemblyDirection"] == "+Z"
    assert constraints["geometricLockDirections"] == ["+Y", "-Y"]
    assert any("expanded to local +Y, -Y" in warning for warning in warnings)
    assert any("authored as" in note for note in constraints["directionNotes"])


def test_unresolved_direction_is_returned_for_gemini_revision():
    program = _anyjoint_plane_program()
    program["constructionConstraints"]["geometricLockDirections"] = [
        "resists_the_expected_force"
    ]
    try:
        joinery_program.program_candidates(program, beam_id="beam_test")
    except joinery_program.JointProgramError as exc:
        assert "cannot be resolved" in str(exc)
    else:
        raise AssertionError("unresolved direction should require a Gemini revision")


def test_construction_contract_rejects_short_interface_even_if_damage_can_fit():
    candidate = anyjoint.plane_program_candidate(
        _anyjoint_plane_program(axial_run=0.24)["geometry"]["planes"],
        [["P0"]],
        aspect=3.5,
    )
    metrics = anyjoint.candidate_geometry_metrics(candidate)
    failures = anyjoint.construction_failures(
        metrics,
        {
            "minimumEngagementSections": 2.5,
            "minimumInterfaceAreaRatio": 1.5,
        },
    )
    assert metrics["engagementSections"] < 0.5
    assert any("engagement" in failure for failure in failures)
    assert any("interface area" in failure for failure in failures)


def test_name_free_anyjoint_fit_passes_multiple_construction_gates():
    frame, cells, damage = _beam_cells()
    result, resolved, report = joinery_program.fit_program(
        _anyjoint_plane_program(),
        frame,
        cells,
        damage,
        beam_id="beam_test",
    )
    assert result is not None, report
    assert result["family"] == "any_joint"
    assert result["required_left"] == 0
    assert result["construction_metrics"]["engagementSections"] >= 2.5
    assert result["construction_metrics"]["interfaceAreaRatio"] >= 1.5
    record = joinery_program.proposal_record(resolved, result, report)
    assert record["fit"]["status"] == "construction_contract_pass"


def test_workspace_context_follows_part_connections_and_strategy_steps():
    workspace = {
        "schemaVersion": "2.1.0",
        "instance": {
            "id": "frame",
            "name": "Frame",
            "parts": [
                {"id": "rail", "label": "Rail", "connections": ["post"]},
                {"id": "post", "label": "Post", "connections": ["rail"]},
            ],
        },
        "conditions": [
            {"id": "rot", "partRef": "rail", "type": "rot", "evidenceRefs": ["photo"]}
        ],
        "evidence": [{"id": "photo", "kind": "photo", "url": "data:image/jpeg;base64,secret"}],
        "plans": [
            {
                "id": "strategy",
                "intent": {"summary": "Retain historic timber"},
                "constraints": {"tools_available": "hand saw"},
                "steps": [
                    {"id": "repair", "affectedPartRefs": ["rail", "post"]}
                ],
            }
        ],
        "currentPlanId": "strategy",
    }
    context = joinery_program.workspace_context(workspace, "rail")
    assert context["targetPart"]["id"] == "rail"
    assert [part["id"] for part in context["connectedParts"]] == ["post"]
    assert context["conditions"][0]["id"] == "rot"
    assert context["strategy"]["relevantSteps"][0]["id"] == "repair"
    assert "url" not in context["evidence"][0]


def test_workspace_context_shortlists_joinery_step_and_keeps_sequence_context():
    workspace = {
        "instance": {
            "parts": [
                {"id": "rail", "label": "Rail", "connections": ["post"]},
                {"id": "post", "label": "Post", "connections": ["rail"]},
            ]
        },
        "conditions": [{"id": "rot", "partRef": "rail", "type": "rot"}],
        "plans": [
            {
                "id": "plan",
                "steps": [
                    {
                        "id": "shore",
                        "title": "Shore frame",
                        "affectedPartRefs": ["post"],
                    },
                    {
                        "id": "remove",
                        "title": "Remove rotted rail",
                        "affectedPartRefs": ["rail"],
                        "addressesConditionRefs": ["rot"],
                    },
                    {
                        "id": "splice",
                        "title": "Splice repair rail end",
                        "description": "Join a Dutchman extension to the sound rail.",
                        "affectedPartRefs": ["rail"],
                        "addressesConditionRefs": ["rot"],
                    },
                    {
                        "id": "dry",
                        "title": "Dry fit rail",
                        "affectedPartRefs": ["rail"],
                    },
                ],
                "edges": [
                    {"source": "shore", "target": "remove"},
                    {"source": "remove", "target": "splice"},
                    {"source": "splice", "target": "dry"},
                ],
            }
        ],
        "currentPlanId": "plan",
    }
    context = joinery_program.workspace_context(workspace, "rail")
    candidates = context["strategy"]["joineryStepCandidates"]
    assert candidates[0]["id"] == "splice"
    assert "shore" in [step["id"] for step in context["strategy"]["sequenceSteps"]]
    assert context["strategy"]["humanSelectedStep"] is None

    overridden = joinery_program.workspace_context(
        workspace, "rail", repair_step_id="splice"
    )
    assert overridden["strategy"]["humanSelectedStep"]["id"] == "splice"
