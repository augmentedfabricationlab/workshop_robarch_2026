import ast
import json
import os
import sys
import tempfile
import unittest
import zipfile


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from workshop_robarch_2026 import candidate_analysis
from workshop_robarch_2026 import candidate_runtime
from workshop_robarch_2026 import llm_candidate
from workshop_robarch_2026 import proposal_store
from workshop_robarch_2026 import repair_candidate


def manifest(claims=None):
    return {
        "schema": "repair-candidate@2",
        "id": "candidate_a",
        "title": "Candidate A",
        "partRefs": ["post"],
        "actionRefs": ["cut"],
        "outputs": [],
        "assumptions": [],
        "claims": claims or [],
        "openQuestions": [],
    }


class CandidateRuntimeTests(unittest.TestCase):
    def test_common_box_dimension_spelling_is_made_compatible(self):
        source = (
            "def build_candidate(ctx, emit):\n"
            "    local_box = ctx.box\n"
            "    size = local_box.Dx + local_box.Dy + local_box.Dz"
        )
        result = ast.unparse(candidate_runtime.compatible_tree(source))
        self.assertIn("local_box.X.Length", result)
        self.assertIn("local_box.Y.Length", result)
        self.assertIn("local_box.Z.Length", result)

    def test_visible_function_contract_accepts_helpers(self):
        tree = candidate_runtime.validate_code(
            "def helper(value):\n    return value * 2\n\n"
            "def build_candidate(ctx, emit):\n"
            "    emit('point', rg.Point3d(0, 0, helper(2)), role='reference')\n"
        )
        self.assertEqual(tree.body[-1].name, "build_candidate")

    def test_visible_function_contract_rejects_hidden_access(self):
        bad = [
            "import os\ndef build_candidate(ctx, emit):\n    pass",
            "def build_candidate(ctx, emit):\n    return ctx.__class__",
            "def build_candidate(ctx, emit):\n    while True:\n        pass",
        ]
        for source in bad:
            with self.assertRaises(ValueError):
                candidate_runtime.validate_code(source)

    def test_runtime_signature_tracks_point_values(self):
        class Point:
            def __init__(self, x, y, z):
                self.X, self.Y, self.Z = x, y, z

        first = candidate_runtime.runtime_signature([Point(1, 2, 3)])
        second = candidate_runtime.runtime_signature([Point(1, 2, 4)])
        self.assertNotEqual(first, second)

    def test_runtime_signature_uses_box_values_without_converting_it(self):
        class Point:
            def __init__(self, x, y, z):
                self.X, self.Y, self.Z = x, y, z

        class Interval:
            def __init__(self, minimum, maximum):
                self.Min, self.Max = minimum, maximum

        class Plane:
            Origin = Point(0, 0, 0)
            XAxis = Point(1, 0, 0)
            YAxis = Point(0, 1, 0)
            ZAxis = Point(0, 0, 1)

        class Box:
            X, Y, Z = Interval(0, 10), Interval(-2, 2), Interval(-3, 3)

            def ToBrep(self):
                raise AssertionError("signature must not convert a Box to a new Brep")

        Box.Plane = Plane()

        first = candidate_runtime.runtime_signature(Box())
        second = candidate_runtime.runtime_signature(Box())
        self.assertEqual(first, second)


class ModelAndRequirementTests(unittest.TestCase):
    def test_partial_output_reference_resolution_is_visible(self):
        entities = [{"id": "path:1", "groupId": "path"}]
        indices, missing = candidate_analysis._entity_indices(
            entities, ["path", "missing_piece"]
        )
        self.assertEqual(indices, [0])
        self.assertEqual(missing, ["missing_piece"])

    def test_fenced_model_json_is_accepted(self):
        result = llm_candidate.json_from_model_text("```json\n{\"ok\": true}\n```")
        self.assertEqual(result, {"ok": True})

    def test_only_sourced_machine_test_is_evaluated(self):
        claims = [
            {
                "id": "workspace_limit",
                "text": "At most one quarter sound-cell loss",
                "source": "workspace",
                "requirement": True,
                "confirmed": True,
                "test": {
                    "factId": "condition.sound_removal_fraction",
                    "operator": "lte",
                    "expected": 0.25,
                },
            },
            {
                "id": "model_preference",
                "text": "One output is elegant",
                "source": "llm",
                "requirement": True,
                "confirmed": True,
            },
        ]
        facts = [
            repair_candidate.fact_record(
                "condition.sound_removal_fraction", "measured", 0.3
            )
        ]
        brief = {
            "schema": "repair-brief@1",
            "id": "brief",
            "targetPartRef": "post",
            "actionRefs": ["cut"],
            "partRefs": ["post"],
            "repairIdea": {
                "requirements": [
                    {
                        "id": "workspace_limit",
                        "text": "At most one quarter sound-cell loss",
                        "source": "workspace",
                        "sourceRefs": ["cut"],
                        "sourceQuote": "At most one quarter sound-cell loss",
                        "confirmedByHuman": False,
                        "test": claims[0]["test"],
                    }
                ]
            },
            "workspaceFacts": [],
            "llmInferences": [],
            "openQuestions": [],
        }
        context = {
            "targetPart": {"id": "post"},
            "connectedParts": [],
            "currentPlan": {
                "id": "plan",
                "steps": [{"id": "cut", "description": "At most one quarter sound-cell loss"}],
            },
        }
        session = {"workspaceHash": "workspace", "contextHash": "context", "beamId": "post"}
        brief = repair_candidate.stamp_brief_authority(brief, context, "", session)
        brief = repair_candidate.confirm_brief(brief, session, "checked source and limit")
        candidate = repair_candidate.apply_brief_authority(manifest(claims), brief)
        result = candidate_analysis._requirement_results(candidate, facts)
        self.assertEqual(result["compliance"][0]["evaluation"]["status"], "not_satisfied")
        self.assertEqual(result["advisory"][0]["evaluation"]["status"], "unknown")

    def test_valid_revision_bundle_keeps_the_full_execution_identity(self):
        source_manifest = repair_candidate.normalise_manifest(manifest())
        code = "def build_candidate(ctx, emit):\n    pass"
        session = {
            "schema": "repair-session@1",
            "beamId": "post",
            "workspaceHash": "workspace",
            "contextHash": "context",
        }
        fact_list = [repair_candidate.fact_record("geometry.output_count", "measured", 0)]
        identity = {
            "candidateId": "candidate_a",
            "beamId": "post",
            "manifestHash": repair_candidate.stable_json_hash(source_manifest),
            "codeHash": repair_candidate.stable_json_hash(code),
            "sessionHash": repair_candidate.stable_json_hash(session),
            "geometryHash": "geometry",
            "entitiesHash": "entities",
            "analysisInputHash": "inputs",
        }
        facts = {"schema": "repair-candidate-facts@1", **identity, "facts": fact_list}
        requirements = candidate_analysis.requirement_results(
            source_manifest, fact_list, identity
        )
        checked_facts, checked_requirements = llm_candidate.validate_fact_bundle(
            facts, requirements, source_manifest, code, session
        )
        self.assertEqual(checked_facts["entitiesHash"], "entities")
        self.assertEqual(checked_requirements["entitiesHash"], "entities")


class ProposalTests(unittest.TestCase):
    def test_decision_is_additive_and_replayable(self):
        source_manifest = repair_candidate.normalise_manifest(manifest())
        code = "def build_candidate(ctx, emit):\n    pass"
        facts = {
            "schema": "repair-candidate-facts@1",
            "candidateId": "candidate_a",
            "beamId": "post",
            "manifestHash": repair_candidate.stable_json_hash(source_manifest),
            "codeHash": repair_candidate.stable_json_hash(code),
            "sessionHash": "session",
            "geometryHash": "geometry",
            "entitiesHash": "entities",
            "analysisInputHash": "inputs",
            "facts": [repair_candidate.fact_record("geometry.output_count", "measured", 0)],
        }
        requirements = {
            "schema": "repair-requirements@1",
            "candidateId": "candidate_a",
            "beamId": "post",
            "manifestHash": facts["manifestHash"],
            "codeHash": facts["codeHash"],
            "sessionHash": facts["sessionHash"],
            "geometryHash": facts["geometryHash"],
            "entitiesHash": facts["entitiesHash"],
            "analysisInputHash": facts["analysisInputHash"],
            "factsHash": repair_candidate.stable_json_hash(facts["facts"]),
            **repair_candidate.resolve_requirements(source_manifest),
        }
        proposal, ready, _ = proposal_store.build_proposal(
            source_manifest, code, facts,
            requirements, "accept", "workshop review",
            geometry_artifact={
                "schema": "repair-geometry-artifact@1",
                "path": "repair_geometry/candidate_a.3dm",
                "format": "3dm",
                "sha256": "artifact",
                "geometryHash": "geometry",
                "entitiesHash": "entities",
                "entityCount": 1,
            },
        )
        workspace = {
            "instance": {"id": "one", "parts": [{"id": "post"}]},
            "plans": [{"id": "plan", "steps": [{"id": "cut"}]}],
        }
        updated = proposal_store.add_proposal(workspace, proposal)
        self.assertTrue(ready)
        self.assertEqual(workspace["plans"][0]["steps"][0].get("repairGeometryProposalRefs"), None)
        self.assertEqual(
            updated["plans"][0]["steps"][0]["repairGeometryProposalRefs"],
            [proposal["id"]],
        )

    def test_zip_copy_keeps_attachments(self):
        workspace = {"instance": {"id": "one", "parts": []}}
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "source.zip")
            target = os.path.join(folder, "target.zip")
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("workspace.json", json.dumps(workspace))
                archive.writestr("photos/evidence.jpg", b"image")
            changed = {"instance": {"id": "two", "parts": []}}
            proposal_store.save_workspace(
                source,
                changed,
                target,
                {"repair_geometry/candidate.3dm": b"3dm"},
            )
            with zipfile.ZipFile(target, "r") as archive:
                self.assertEqual(archive.read("photos/evidence.jpg"), b"image")
                self.assertEqual(archive.read("repair_geometry/candidate.3dm"), b"3dm")
                self.assertEqual(json.loads(archive.read("workspace.json"))["instance"]["id"], "two")


if __name__ == "__main__":
    unittest.main()
