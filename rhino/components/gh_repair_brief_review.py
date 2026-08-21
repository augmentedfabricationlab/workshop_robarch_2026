"""GH Python 3 -- BRIEF REVIEW: confirm one exact repair brief locally.

Inputs: session_json, context_json, brief_json, review_note, confirm, repo
Outputs: brief_json_out, report
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


brief_json_out = ""
report = ["Brief Review"]
try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    from workshop_robarch_2026 import (
        grasshopper_jobs, llm_candidate, repair_candidate,
    )

    session = llm_candidate.validate_session(globals().get("session_json"))
    context = llm_candidate.validate_context(
        globals().get("context_json"), session["beamId"], session
    )
    brief = llm_candidate.validate_brief(
        globals().get("brief_json"), session["beamId"], session
    )
    note = str(globals().get("review_note") or "")
    signature = llm_candidate.stable_signature(session, context, brief, note)

    def work():
        reviewed = repair_candidate.confirm_brief(brief, session, note)
        llm_candidate.validate_brief(
            reviewed, session["beamId"], session, require_review=True
        )
        return reviewed

    state = grasshopper_jobs.cached_step(
        ghenv.Component, "brief_review", signature, work,
        trigger=bool(globals().get("confirm")),
    )
    report.append(state.get("status", "idle"))
    if state.get("error"):
        report.append("ERROR: " + state["error"])
    if state.get("message"):
        report.append(state["message"])
    reviewed = state.get("result")
    if reviewed:
        brief_json_out = json.dumps(reviewed, indent=2, ensure_ascii=False)
        count = len(
            (reviewed.get("requirementsAuthority") or {}).get("requirementIds") or []
        )
        report.append("participant confirmed this exact brief; {} cited requirement(s) can bind".format(count))
except Exception as exc:
    report.append("ERROR: {}".format(exc))
