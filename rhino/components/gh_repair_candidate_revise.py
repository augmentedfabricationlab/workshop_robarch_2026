# r: google-genai==1.47.0
"""GH Python 3 -- CANDIDATE REVISE: one focused participant-led revision.

Inputs: session_json, context_json, brief_json, candidate_json, candidate_code,
        facts_json, requirements_json, feedback, gemini_model, revise, reset, repo
Outputs: candidate_json_out, candidate_code_out, change_summary, status
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


candidate_json_out = candidate_code_out = change_summary = ""
status = ["Candidate Revise"]
try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    from workshop_robarch_2026 import grasshopper_jobs, llm_candidate, repair_candidate

    values = {
        name: llm_candidate.as_object(globals().get(name), name)
        for name in ("session_json", "context_json", "brief_json", "candidate_json", "facts_json", "requirements_json")
    }
    code = str(globals().get("candidate_code") or "")
    message = str(globals().get("feedback") or "")
    model = str(globals().get("gemini_model") or llm_candidate.DEFAULT_MODEL)
    signature = llm_candidate.stable_signature(values, code, message, model)

    def work():
        result = llm_candidate.revise_candidate(
            root, values["session_json"], values["context_json"], values["brief_json"],
            values["candidate_json"], code, values["facts_json"],
            values["requirements_json"], message, model,
        )
        previous = repair_candidate.normalise_manifest(values["candidate_json"])
        revised = repair_candidate.apply_brief_authority(
            result["candidate"], values["brief_json"]
        )
        action_ids = [
            str(step.get("id"))
            for step in ((values["context_json"].get("currentPlan") or {}).get("steps") or [])
            if step.get("id")
        ]
        part_ids = [
            str(part.get("id"))
            for part in [values["context_json"].get("targetPart")] + list(values["context_json"].get("connectedParts") or [])
            if part and part.get("id")
        ]
        revised = repair_candidate.validate_scope(
            revised,
            beam_id=values["session_json"].get("beamId"),
            part_ids=part_ids,
            action_ids=action_ids,
            workspace_hash=values["session_json"].get("workspaceHash"),
            context_hash=values["session_json"].get("contextHash"),
        )
        revised["revisionOf"] = previous["id"]
        revised["parentManifestHash"] = repair_candidate.stable_json_hash(previous)
        revised["revisionNote"] = str(result.get("changeSummary") or "")
        result["candidate"] = revised
        return result

    state = grasshopper_jobs.background_step(
        ghenv.Component, "revise", signature, work,
        trigger=bool(globals().get("revise")), reset=bool(globals().get("reset")),
    )
    status.append(state.get("status", "idle"))
    if state.get("error"):
        status.append("ERROR: " + state["error"])
    result = state.get("result") or {}
    if result:
        candidate_json_out = json.dumps(result["candidate"], indent=2, ensure_ascii=False)
        candidate_code_out = str(result["python"])
        change_summary = str(result.get("changeSummary") or "")
except Exception as exc:
    status.append("ERROR: {}".format(exc))
