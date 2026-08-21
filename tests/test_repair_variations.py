import copy
import os
import sys
import unittest

import numpy as np


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from workshop_robarch_2026 import repair_variations


def program():
    return {
        "schema": "joinery-program@1",
        "id": "explorer",
        "targetPartRef": "post",
        "jointBehaviour": {"retention": "positive_lock"},
        "geometry": {
            "topology": "any_joint",
            "aspect": 3.0,
            "planes": [
                {"id": "P0", "normal": [1, 0, 0], "d": 0.0},
                {"id": "P1", "normal": [0, 1, 0], "d": 1.0},
                {"id": "P2", "normal": [0, 0, 1], "d": 0.0},
                {"id": "P3", "normal": [-1, 0, 0], "d": 0.0},
                {"id": "P4", "normal": [0, 1, 0], "d": 1.2},
                {"id": "P5", "normal": [0, 0, -1], "d": 0.0},
            ],
            "removalGroups": [["P0", "P1", "P2"], ["P3", "P4", "P5"]],
        },
        "fitObjective": {},
    }


class RepairVariationTests(unittest.TestCase):
    def test_face_only_regions_are_rejected_for_a_connected_prosthesis(self):
        with self.assertRaisesRegex(ValueError, "volume-disconnected"):
            repair_variations._validate_program(
                program(), {"beamId": "post", "actionIds": []}
            )

    def test_llm_studies_preserve_topology_and_use_small_changes(self):
        original = program()
        original["geometry"]["prosthesisIntent"] = {"connected": False, "reason": "test fixture"}
        untouched = copy.deepcopy(original)
        response = {
            "summary": "reference",
            "variationStudies": [
                {"id": "bearing_plus", "summary": "more bearing", "reason": "test",
                 "wholeRotationDeg": 0, "changes": [{"planeIds": ["P0"], "angleDeltaDeg": 3}]},
                {"id": "shoulder_minus", "summary": "less removal", "reason": "test",
                 "wholeRotationDeg": 2, "changes": [{"planeIds": ["P3"], "angleDeltaDeg": -2}]},
            ],
        }
        bank = repair_variations._study_programs(original, response, 3)
        self.assertEqual(len(bank), 3)
        self.assertEqual(original, untouched)
        self.assertEqual(bank[0]["program"]["geometry"]["removalGroups"], original["geometry"]["removalGroups"])
        self.assertEqual([item["rotationDeg"] for item in bank], [0.0, 0.0, 2.0])
        self.assertEqual(bank[0]["kind"], "LLM reference")
        angle_study = next(item for item in bank if item["planeIds"])
        changed = {
            item["id"]: item["normal"]
            for item in angle_study["program"]["geometry"]["planes"]
        }
        base = {item["id"]: item["normal"] for item in original["geometry"]["planes"]}
        self.assertNotEqual(changed[angle_study["planeIds"][0]], base[angle_study["planeIds"][0]])

    def test_quarter_turn_study_is_rejected(self):
        original = program()
        original["geometry"]["prosthesisIntent"] = {"connected": False, "reason": "test fixture"}
        response = {"variationStudies": [
            {"id": "bad", "wholeRotationDeg": 90, "changes": [{"planeIds": ["P0"], "angleDeltaDeg": 3}]}
        ]}
        with self.assertRaisesRegex(ValueError, "exceeds 10"):
            repair_variations._study_programs(original, response, 2)

    def test_comparison_names_cell_tradeoffs(self):
        base = {"removed": np.array([True, True, True, False]), "rotationDeg": 0.0, "planeIds": [], "angleDeltaDeg": 0.0,
                "resolvedProgram": {"geometry": {"variationTransform": {"rotationAroundMemberAxisDeg": 0.0}}}}
        item = copy.deepcopy(base)
        item.update(removed=np.array([True, True, False, True]), rotationDeg=3.0, summary="shallower shoulder")
        item["resolvedProgram"]["geometry"]["variationTransform"]["rotationAroundMemberAxisDeg"] = 3.0
        result = repair_variations.comparison(item, base, np.array([0.9, 0.7, 0.2, 0.3]), 0.5)
        self.assertEqual(result["requiredDamageRemoved"], 2)
        self.assertEqual(result["recoveredSoundCellIndices"], [2])
        self.assertEqual(result["extraSoundCellIndices"], [3])
        self.assertIn("#3", result["text"])

    def test_replacement_side_follows_the_damaged_member_end(self):
        frame = {
            "origin": [0, 0, 0], "u": [1, 0, 0], "v": [0, 1, 0], "w": [0, 0, 1],
            "width": 1.0, "length": 10.0, "height": 1.0,
        }
        points = np.array([[0, 0.2, 0], [0, 5.0, 0], [0, 9.8, 0]], float)
        self.assertEqual(repair_variations._replacement_sides(frame, points, np.array([1, 0, 0]), 0.5), [-1])
        self.assertEqual(repair_variations._replacement_sides(frame, points, np.array([0, 0, 1]), 0.5), [1])


if __name__ == "__main__":
    unittest.main()
