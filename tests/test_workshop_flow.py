import copy
import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from workshop_robarch_2026 import workshop_flow


class WorkshopFlowTests(unittest.TestCase):
    def test_five_stage_records_round_trip_and_detect_changes(self):
        session = {"schema": "repair-session@1", "beamId": "post"}
        setup = workshop_flow.setup_record(
            session, {"targetPart": {"id": "post"}}, {"tolerance": 0.01}
        )
        brief = {"schema": "repair-brief@1", "id": "brief"}
        repair = workshop_flow.repair_record(setup, brief)
        candidate = {
            "schema": "repair-candidate@2", "id": "candidate", "outputs": [],
            "partRefs": [], "actionRefs": [], "assumptions": [], "claims": [],
        }
        entity = {"schema": "repair-candidate-entities@1", "candidateId": "candidate"}
        execution = {"schema": "repair-candidate-execution@1", "candidateId": "candidate"}
        selection = workshop_flow.selection_record(
            candidate, "def build_candidate(ctx, emit):\n    pass", entity, execution
        )
        facts = {"schema": "repair-candidate-facts@1", "candidateId": "candidate"}
        requirements = {"schema": "repair-requirements@1", "candidateId": "candidate"}
        active = workshop_flow.active_record(selection, facts, requirements)

        self.assertEqual(workshop_flow.validate_setup(setup)["session"]["beamId"], "post")
        self.assertEqual(workshop_flow.validate_repair(repair)["brief"]["id"], "brief")
        self.assertEqual(workshop_flow.validate_selection(selection)["candidate"]["id"], "candidate")
        self.assertEqual(workshop_flow.validate_active(active)["source"], "authored")

        changed = copy.deepcopy(active)
        changed["source"] = "changed"
        with self.assertRaisesRegex(ValueError, "changed after"):
            workshop_flow.validate_active(changed)


if __name__ == "__main__":
    unittest.main()
