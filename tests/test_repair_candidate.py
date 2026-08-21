import json
import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from workshop_robarch_2026 import repair_candidate as rc


def _manifest():
    return {
        "schema": "repair-candidate@2",
        "id": "corner-repair-a",
        "title": "Bridled splice with keys",
        "actionRefs": ["cut-decay", "fit-replacement", "fit-replacement"],
        "partRefs": "corner_post",
        "outputs": [
            {
                "id": "insert",
                "role": "replacement timber",
                "effect": "adds material",
                "partRefs": ["corner_post"],
                "geometryRef": "entity:insert",
            },
            {"role": "drawbore key", "material": "oak"},
        ],
        "assumptions": [
            {"text": "The sill can remain in place", "provenance": "llm"}
        ],
        "claims": [
            {
                "id": "workspace-access",
                "text": "Assembly access is from outside",
                "source": "workspace",
                "requirement": True,
            },
            {
                "id": "participant-grain",
                "text": "Keep the replacement grain vertical",
                "source": "human",
                "requirement": True,
                "confirmed": True,
            },
            {
                "id": "drafted-one-piece",
                "text": "Use one connected insert",
                "source": "llm",
                "requirement": True,
                "confirmed": True,
            },
            {
                "id": "unconfirmed-preference",
                "text": "Avoid visible keys",
                "source": "human",
                "requirement": True,
            },
        ],
        "studioExtension": {"freeForm": True},
    }


def test_manifest_is_open_ended_and_normalised_without_mutating_input():
    raw = _manifest()
    result = rc.normalise_manifest(raw)

    assert raw["partRefs"] == "corner_post"
    assert result["partRefs"] == ["corner_post"]
    assert result["actionRefs"] == ["cut-decay", "fit-replacement"]
    assert [item["id"] for item in result["outputs"]] == ["insert", "output_02"]
    assert result["outputs"][0]["geometryRef"] == "entity:insert"
    assert result["outputs"][1]["role"] == "drawbore key"
    assert result["studioExtension"] == {"freeForm": True}


def test_manifest_allows_zero_outputs_and_free_roles_and_effects():
    manifest = rc.validate_manifest(
        {
            "id": "survey-only",
            "outputs": [],
            "actionRefs": [],
            "partRefs": [],
            "assumptions": [],
            "claims": [],
        }
    )
    assert manifest["schema"] == rc.MANIFEST_SCHEMA
    assert manifest["outputs"] == []

    manifest["outputs"] = [{"role": "temporary ritual support", "effect": "marks a sequence"}]
    assert rc.normalize_manifest(manifest)["outputs"][0]["effect"] == "marks a sequence"


def test_manifest_rejects_ambiguous_contract_data():
    cases = [
        ({"schema": "repair-candidate@1"}, "schema"),
        ({"id": ""}, "manifest.id"),
        ({"outputs": [{"id": "a", "role": "x"}, {"id": "a", "role": "y"}]}, "unique"),
        ({"assumptions": [{"text": "guess", "provenance": "model"}]}, "provenance"),
        ({"claims": [{"text": "claim", "source": "teacher"}]}, "source"),
    ]
    for change, message in cases:
        manifest = _manifest()
        manifest.update(change)
        with unittest.TestCase().assertRaisesRegex(rc.RepairCandidateError, message):
            rc.normalise_manifest(manifest)


def test_requirement_resolution_uses_source_and_confirmation_only():
    resolved = rc.resolve_requirements(_manifest())

    assert [item["id"] for item in resolved["compliance"]] == [
        "workspace-access",
        "participant-grain",
    ]
    assert [item["id"] for item in resolved["advisory"]] == [
        "drafted-one-piece",
        "unconfirmed-preference",
    ]
    assert all(item["mode"] == "compliance" for item in resolved["compliance"])
    assert all(item["mode"] == "advisory" for item in resolved["advisory"])


def test_facts_are_neutral_and_support_every_availability_state():
    facts = [
        rc.fact_record("solid-count", "measured", 2, unit="closed Breps"),
        rc.fact_record("assembly-path", "unknown", note="No path was declared"),
        rc.fact_record("tool-radius", "not_applicable"),
        rc.fact_record("contact-area", "failed_to_compute", reason="Boolean failed"),
    ]
    normalised = rc.normalise_facts(facts)

    assert [fact["status"] for fact in normalised] == list(rc.FACT_STATUSES)
    assert normalised[0]["value"] == 2
    assert "value" not in normalised[1]
    assert not any("pass" in fact or "compliance" in fact for fact in normalised)

    with unittest.TestCase().assertRaisesRegex(rc.RepairCandidateError, "needs a value"):
        rc.fact_record("volume", "measured")
    with unittest.TestCase().assertRaisesRegex(rc.RepairCandidateError, "only a measured"):
        rc.fact_record("volume", "unknown", 12.0)


def test_candidate_version_and_decision_records_are_compact_and_replayable():
    facts = [rc.fact_record("output-count", "measured", 2)]
    candidate = rc.candidate_record(
        _manifest(),
        code="def build_candidate(ctx, emit): pass",
        facts=facts,
        metadata={"contextHash": "abc"},
    )
    version = rc.version_record(candidate, "v2", "v1", "changed the shoulder")
    decision = rc.decision_record(
        candidate["id"], version["id"], "accept_with_deviation", "Review fasteners"
    )

    assert candidate["requirements"]["advisory"][0]["source"] == "llm"
    assert candidate["manifestHash"] == rc.stable_json_hash(candidate["manifest"])
    assert version["candidateHash"] == rc.stable_json_hash(candidate)
    assert decision["decidedBy"] == "human"
    json.dumps([candidate, version, decision], allow_nan=False)


def test_stable_hash_ignores_dictionary_order_and_rejects_non_json_values():
    assert rc.stable_json_hash({"b": 2, "a": [1]}) == rc.stable_json_hash(
        {"a": [1], "b": 2}
    )
    with unittest.TestCase().assertRaisesRegex(rc.RepairCandidateError, "stable JSON"):
        rc.stable_json_hash({"bad": float("nan")})


def test_records_with_duplicate_fact_ids_are_rejected():
    fact = rc.fact_record("same", "measured", 1)
    with unittest.TestCase().assertRaisesRegex(rc.RepairCandidateError, "unique"):
        rc.candidate_record(_manifest(), facts=[fact, fact])


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            suite.addTest(unittest.FunctionTestCase(function))
    return suite
