"""GH Python 3 -- 05 SAVE: record a decision and save a Workspace copy.

Inputs: workspace_json, active_json, active_geometry, decision, decision_note,
        save_path, save, repo
Outputs: proposal_json, workspace_updated_json, saved_path, ready, report
"""

import json
import os
import sys


def _repo_root():
    starts = [globals().get("repo"), os.environ.get("ROBARCH_REPO")]
    try:
        starts.append(os.path.dirname(str(ghenv.Component.OnPingDocument().FilePath or "")))
    except Exception:
        pass
    starts.extend([os.getcwd(), *sys.path])
    for start in starts:
        if not start or "://" in str(start):
            continue
        folder = os.path.abspath(os.path.expanduser(str(start)))
        if os.path.isfile(folder):
            folder = os.path.dirname(folder)
        for _ in range(9):
            if os.path.isdir(os.path.join(folder, "src", "workshop_robarch_2026")):
                return folder
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
    raise RuntimeError("workshop repository not found; connect its folder to repo")


def _list(value):
    if value is None:
        return []
    try:
        return list(value)
    except TypeError:
        return [value]


proposal_json = workspace_updated_json = saved_path = ""
ready = False
report = ["05 Save"]

try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    for module_name in list(sys.modules):
        if module_name == "workshop_robarch_2026" or module_name.startswith("workshop_robarch_2026."):
            sys.modules.pop(module_name, None)
    from workshop_robarch_2026 import (
        candidate_runtime, geometry_archive, grasshopper_jobs, proposal_store,
        repair_candidate, workshop_flow,
    )

    active = workshop_flow.validate_active(globals().get("active_json"))
    geometry = _list(globals().get("active_geometry"))
    workspace_input = globals().get("workspace_json")
    output_path = str(globals().get("save_path") or "")
    record_inputs = (
        active["candidate"], active["python"], active["facts"],
        active["requirements"], str(globals().get("decision") or ""),
        str(globals().get("decision_note") or ""),
    )
    signature = candidate_runtime.runtime_signature(
        active["recordHash"], workspace_input, output_path, geometry, record_inputs[4:]
    )

    def checked_entities():
        facts, entity = active["facts"], active["entity"]
        if candidate_runtime.runtime_signature(geometry) != facts.get("geometryHash"):
            raise ValueError("active_geometry does not match the reviewed facts")
        for key in repair_candidate.EXECUTION_IDENTITY_FIELDS:
            if entity.get(key) != facts.get(key):
                raise ValueError("saved entities do not match the reviewed facts")
        records = entity.get("entities") or []
        if len(records) != len(geometry):
            raise ValueError("active_geometry/entity count mismatch")
        return records

    def build(write_file=False):
        entities = checked_entities()
        payload = artifact = None
        if write_file and geometry:
            artifact_id = "{}_{}".format(
                active["facts"].get("candidateId"),
                str(active["facts"].get("geometryHash") or "")[:12],
            )
            payload, artifact = geometry_archive.build_3dm_bytes(
                artifact_id, geometry, entities, active["facts"]["geometryHash"]
            )
        proposal, complete, messages = proposal_store.build_proposal(
            *record_inputs, geometry_artifact=artifact
        )
        updated = proposal_store.add_proposal(workspace_input, proposal)
        if write_file and not complete:
            raise ValueError("record is incomplete; see report before saving")
        attachments = {artifact["path"]: payload} if artifact and payload else {}
        path = proposal_store.save_workspace(
            workspace_input, updated, output_path, attachments
        ) if write_file else ""
        if artifact:
            messages.append("embedded candidate geometry: " + artifact["path"])
        return proposal, updated, path, complete, messages

    state = grasshopper_jobs.cached_step(
        ghenv.Component, "simple_save", signature, lambda: build(True),
        trigger=bool(globals().get("save")),
    )
    if state.get("error"):
        report.append("SAVE ERROR: " + state["error"])
    result = state.get("result") if state.get("status") == "complete" else build(False)
    proposal, updated, saved_path, ready, messages = result
    proposal_json = json.dumps(proposal, indent=2, ensure_ascii=False)
    workspace_updated_json = json.dumps(updated, indent=2, ensure_ascii=False)
    report.extend(messages)
    if saved_path:
        report.append("saved copy: " + saved_path)
except Exception as exc:
    report.append("ERROR: {}".format(exc))
