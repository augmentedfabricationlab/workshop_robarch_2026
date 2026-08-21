import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from workshop_robarch_2026 import workspace_io


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            suite.addTest(unittest.FunctionTestCase(function))
    return suite


def _workspace():
    return {
        "schemaVersion": "2.1.0",
        "instance": {
            "id": "assembly_1",
            "name": "Frame",
            "parts": [
                {"id": "corner_post", "label": "Corner Post", "connections": ["sill"]},
                {"id": "sill", "connections": ["corner_post"]},
            ],
        },
        "conditions": [],
        "plans": [],
    }


def test_loads_dict_without_mutating_the_input():
    source = _workspace()
    loaded = workspace_io.load_workspace(source)
    loaded["instance"]["name"] = "Changed"
    assert source["instance"]["name"] == "Frame"


def test_loads_json_text_and_path():
    source = _workspace()
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "workspace.json"
        path.write_text(json.dumps(source), encoding="utf-8")
        assert workspace_io.load_workspace(json.dumps(source))["instance"]["id"] == "assembly_1"
        assert workspace_io.load_workspace(path)["instance"]["id"] == "assembly_1"


def test_loads_exported_zip():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "workspace.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("workspace.json", json.dumps(_workspace()))
            archive.writestr("photos/example.jpg", b"image")
        assert workspace_io.load_workspace(path)["instance"]["name"] == "Frame"


def test_part_options_use_labels_and_id_fallback():
    assert workspace_io.part_options(_workspace()) == [
        {"id": "corner_post", "label": "Corner Post"},
        {"id": "sill", "label": "sill"},
    ]
    assert workspace_io.find_part(_workspace(), "corner_post")["label"] == "Corner Post"


def test_rejects_duplicate_ids_and_missing_parts():
    duplicate = _workspace()
    duplicate["instance"]["parts"][1]["id"] = "corner_post"
    try:
        workspace_io.workspace_parts(duplicate)
        assert False, "duplicate id accepted"
    except workspace_io.WorkspaceError as exc:
        assert "duplicate" in str(exc)

    missing = _workspace()
    del missing["instance"]["parts"]
    try:
        workspace_io.workspace_parts(missing)
        assert False, "missing parts accepted"
    except workspace_io.WorkspaceError as exc:
        assert "parts list" in str(exc)


def test_workspace_digest_is_stable_for_key_order():
    source = _workspace()
    reordered = json.loads(json.dumps(source, sort_keys=True))
    assert workspace_io.workspace_digest(source) == workspace_io.workspace_digest(reordered)


def test_damage_field_validates_alignment_and_hashes_all_cells():
    field = workspace_io.damage_field([(0, 0, 0), (1, 2, 3)], [0.1, 0.8], 0.5)
    assert field["summary"]["aboveThresholdCellCount"] == 1
    assert field["summary"]["aboveThresholdWorldBounds"]["min"] == [1.0, 2.0, 3.0]
    changed = workspace_io.damage_field([(0, 0, 0), (1, 2, 4)], [0.1, 0.8], 0.5)
    assert field["dataHash"] != changed["dataHash"]

    try:
        workspace_io.damage_field([(0, 0, 0)], [], 0.5)
        assert False, "mismatched cell data accepted"
    except workspace_io.WorkspaceError as exc:
        assert "length mismatch" in str(exc)


def test_part_context_keeps_connected_parts_and_complete_current_plan():
    source = _workspace()
    source["currentPlanId"] = "plan_1"
    source["plans"] = [{"id": "plan_1", "steps": [{"id": "step_1"}], "edges": []}]
    source["conditions"] = [
        {"id": "condition_1", "partRef": "corner_post", "evidenceRefs": ["photo_1"]}
    ]
    source["evidence"] = [
        {"id": "photo_1", "kind": "photo", "fileName": "photo.jpg", "url": "data:large"}
    ]
    context = workspace_io.part_context(source, "corner_post")
    assert context["targetPart"]["id"] == "corner_post"
    assert [part["id"] for part in context["connectedParts"]] == ["sill"]
    assert context["currentPlan"]["steps"] == [{"id": "step_1"}]
    assert context["evidence"] == [
        {"id": "photo_1", "kind": "photo", "fileName": "photo.jpg"}
    ]
