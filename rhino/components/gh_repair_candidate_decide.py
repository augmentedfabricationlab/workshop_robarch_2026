"""GH Python 3 -- CANDIDATE DECIDE: record and optionally save a decision.

Inputs: workspace_json, candidate_json, candidate_code, candidate_geometry,
        entity_json, facts_json, requirements_json, decision, decision_note,
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
report = ["Candidate Decision"]
try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    from workshop_robarch_2026 import (
        candidate_runtime, geometry_archive, grasshopper_jobs, llm_candidate,
        proposal_store, repair_candidate,
    )

    record_inputs = (
        globals().get("candidate_json"), str(globals().get("candidate_code") or ""),
        globals().get("facts_json"), globals().get("requirements_json"),
        str(globals().get("decision") or ""), str(globals().get("decision_note") or ""),
    )
    workspace_input = globals().get("workspace_json")
    output_path = str(globals().get("save_path") or "")
    items = _list(globals().get("candidate_geometry"))
    entity_envelope = llm_candidate.as_object(globals().get("entity_json"), "entity_json")
    fact_envelope = llm_candidate.as_object(record_inputs[2], "facts_json")
    signature = candidate_runtime.runtime_signature(
        record_inputs, workspace_input, output_path, items, entity_envelope
    )

    def checked_entities():
        if candidate_runtime.runtime_signature(items) != fact_envelope.get("geometryHash"):
            raise ValueError("candidate_geometry does not match facts_json")
        for key in repair_candidate.EXECUTION_IDENTITY_FIELDS:
            if entity_envelope.get(key) != fact_envelope.get(key):
                raise ValueError("entity_json {} does not match facts_json".format(key))
        records = entity_envelope.get("entities") or []
        if not isinstance(records, list) or len(records) != len(items):
            raise ValueError("entity_json/candidate_geometry length mismatch")
        return records

    def build(write_file=False):
        entities = checked_entities()
        payload, artifact = None, None
        if write_file and items:
            artifact_id = "{}_{}".format(
                fact_envelope.get("candidateId"),
                str(fact_envelope.get("geometryHash") or "")[:12],
            )
            payload, artifact = geometry_archive.build_3dm_bytes(
                artifact_id, items, entities, fact_envelope["geometryHash"]
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
        ghenv.Component, "decide", signature, lambda: build(True),
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
