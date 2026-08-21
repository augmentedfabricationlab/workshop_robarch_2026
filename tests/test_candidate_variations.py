import copy
import os
import sys
import unittest
from unittest import mock


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from workshop_robarch_2026 import candidate_variations
from workshop_robarch_2026 import llm_candidate
from workshop_robarch_2026 import repair_candidate
from workshop_robarch_2026 import workspace_io


def reviewed_inputs():
    context = {
        "targetPart": {"id": "post"},
        "connectedParts": [],
        "currentPlan": {"id": "plan", "steps": [{"id": "cut"}]},
        "rhinoContext": {"targetBox": None},
    }
    session = {
        "schema": "repair-session@1",
        "beamId": "post",
        "workspaceHash": "workspace",
        "cellDataHash": "cells",
        "threshold": 0.5,
    }
    session["contextHash"] = workspace_io.json_digest(
        {
            "context": context,
            "box": None,
            "cellDataHash": session["cellDataHash"],
            "threshold": session["threshold"],
        }
    )
    brief = {
        "schema": "repair-brief@1",
        "id": "brief",
        "targetPartRef": "post",
        "actionRefs": ["cut"],
        "partRefs": ["post"],
        "repairIdea": {"requirements": []},
        "workspaceFacts": [],
        "llmInferences": [],
        "openQuestions": [],
    }
    brief = repair_candidate.stamp_brief_authority(brief, context, "", session)
    brief = repair_candidate.confirm_brief(brief, session, "reviewed unchanged idea")
    return session, context, brief


def authored_response(index):
    return {
        "summary": "geometry run {}".format(index),
        "candidate": {
            "schema": "repair-candidate@2",
            "id": "candidate",
            "title": "Geometry run {}".format(index),
            "partRefs": ["post"],
            "actionRefs": ["cut"],
            "outputs": [],
            "assumptions": [],
            "claims": [],
            "openQuestions": [],
        },
        "python": "def build_candidate(ctx, emit):\n    # run {}\n    pass".format(index),
    }


class CandidateVariationTests(unittest.TestCase):
    def test_count_is_bounded(self):
        self.assertEqual(candidate_variations.variation_count(None), 3)
        self.assertEqual(candidate_variations.variation_count(5), 5)
        with self.assertRaises(ValueError):
            candidate_variations.variation_count(6)

    def test_repeated_authorship_keeps_one_brief_and_avoids_duplicate_ids(self):
        session, context, brief = reviewed_inputs()
        runs, brief_hashes = [], []

        def fake_author(repo, session_arg, context_arg, brief_arg, instruction, model, run):
            runs.append(run)
            brief_hashes.append(repair_candidate.stable_json_hash(brief_arg))
            return authored_response(run["index"])

        with mock.patch.object(llm_candidate, "author_candidate", side_effect=fake_author):
            result = llm_candidate.author_candidate_set(
                ".", session, context, brief, "", "model", 3
            )

        self.assertEqual(result["requestedCount"], 3)
        self.assertEqual(result["completedCount"], 3)
        self.assertEqual(len({item["id"] for item in result["candidates"]}), 3)
        self.assertEqual([len(run["previousResults"]) for run in runs], [0, 1, 2])
        self.assertTrue(all(run["sameRepairIdea"] for run in runs))
        self.assertEqual(len(set(brief_hashes)), 1)
        self.assertEqual(brief_hashes[0], repair_candidate.stable_json_hash(brief))
        checked = candidate_variations.validate_candidate_set(
            result, session=session, brief=brief
        )
        selected = candidate_variations.select_candidate(
            checked, checked["candidates"][1]["id"]
        )
        self.assertEqual(selected["authorshipRun"], 2)
        self.assertEqual(selected["candidate"]["authorship"]["requestedCount"], 3)
        changed = copy.deepcopy(result)
        changed["candidates"][0]["summary"] = "changed after authorship"
        with self.assertRaisesRegex(ValueError, "changed after authorship"):
            candidate_variations.validate_candidate_set(changed)


if __name__ == "__main__":
    unittest.main()
