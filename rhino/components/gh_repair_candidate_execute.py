"""GH Python 3 -- CANDIDATE EXECUTE: run the visible RhinoCommon function.

Inputs: session_json, candidate_json, candidate_code, box, centers, damage,
        threshold, neighbour_geometry, neighbour_ids, execute, repo
Outputs: candidate_geometry, entity_json, execution_json, report
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


candidate_geometry, entity_json, execution_json = [], "", ""
report = ["Candidate Execute"]
try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    from workshop_robarch_2026 import candidate_runtime, grasshopper_jobs, llm_candidate

    neighbours = _list(globals().get("neighbour_geometry"))
    neighbour_names = [str(value) for value in _list(globals().get("neighbour_ids"))]
    session = llm_candidate.as_object(globals().get("session_json"), "session_json")
    candidate = llm_candidate.as_object(globals().get("candidate_json"), "candidate_json")
    code = str(globals().get("candidate_code") or "")
    raw_centers = _list(globals().get("centers"))
    raw_damage = _list(globals().get("damage"))
    gate = 0.5 if globals().get("threshold") is None else float(threshold)
    signature = candidate_runtime.runtime_signature(
        session, candidate, code, globals().get("box"), raw_centers,
        raw_damage, gate, neighbours, neighbour_names,
    )

    def work():
        return candidate_runtime.execute_candidate(
            session, candidate, code, globals().get("box"), raw_centers,
            raw_damage, gate, neighbours, neighbour_names,
        )

    state = grasshopper_jobs.cached_step(
        ghenv.Component, "execute", signature, work,
        trigger=bool(globals().get("execute")),
    )
    report.append(state.get("status", "idle"))
    if state.get("error"):
        report.append("ERROR: " + state["error"])
    if state.get("message"):
        report.append(state["message"])
    result = state.get("result")
    if result:
        candidate_geometry, entities, execution, messages = result
        entity_json = json.dumps(
            candidate_runtime.entity_envelope(execution, entities),
            indent=2, ensure_ascii=False,
        )
        execution_json = json.dumps(execution, indent=2, ensure_ascii=False)
        report.extend(messages)
except Exception as exc:
    report.append("ERROR: {}".format(exc))
