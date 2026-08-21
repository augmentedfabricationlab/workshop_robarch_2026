# r: google-genai==1.47.0
"""GH Python 3 -- 02 REPAIR BRIEF: draft, inspect, and confirm one brief.

Inputs: setup_json, instruction, gemini_model, draft, review_note, confirm,
        reset, repo
Outputs: repair_json, brief_json, workspace_facts, llm_inferences,
         open_questions, status
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


repair_json = brief_json = workspace_facts = llm_inferences = open_questions = ""
status = ["02 Repair Brief"]

try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    for module_name in list(sys.modules):
        if module_name == "workshop_robarch_2026" or module_name.startswith("workshop_robarch_2026."):
            sys.modules.pop(module_name, None)
    from workshop_robarch_2026 import (
        grasshopper_jobs, llm_candidate, repair_candidate, workshop_flow,
    )

    setup_input = globals().get("setup_json")
    setup = workshop_flow.validate_setup(setup_input)
    session, context = setup["session"], setup["context"]
    instruction_text = str(globals().get("instruction") or "")
    model = str(globals().get("gemini_model") or llm_candidate.DEFAULT_MODEL)
    reset_value = bool(globals().get("reset"))
    if reset_value:
        grasshopper_jobs.clear_job(ghenv.Component, "brief_stage_confirm")
    draft_signature = llm_candidate.stable_signature(
        setup["recordHash"], instruction_text, model
    )
    draft_state = grasshopper_jobs.background_step(
        ghenv.Component,
        "brief_stage_draft",
        draft_signature,
        lambda: llm_candidate.draft_brief(
            root, session, context, instruction_text, model
        ),
        trigger=bool(globals().get("draft")),
        reset=reset_value,
    )
    status.append("draft: " + draft_state.get("status", "idle"))
    if draft_state.get("error"):
        status.append("DRAFT ERROR: " + draft_state["error"])
    draft_result = draft_state.get("result") or {}
    if draft_result:
        brief = draft_result["brief"]
        brief_json = json.dumps(brief, indent=2, ensure_ascii=False)
        workspace_facts = json.dumps(
            draft_result.get("workspaceFacts") or brief.get("workspaceFacts") or [],
            indent=2, ensure_ascii=False,
        )
        llm_inferences = json.dumps(
            draft_result.get("llmInferences") or brief.get("llmInferences") or [],
            indent=2, ensure_ascii=False,
        )
        open_questions = json.dumps(
            draft_result.get("openQuestions") or brief.get("openQuestions") or [],
            indent=2, ensure_ascii=False,
        )
        note = str(globals().get("review_note") or "")
        confirm_signature = llm_candidate.stable_signature(
            setup["recordHash"], brief, note
        )

        def confirm_brief():
            reviewed = repair_candidate.confirm_brief(brief, session, note)
            llm_candidate.validate_brief(
                reviewed, session["beamId"], session, require_review=True
            )
            return reviewed

        confirm_state = grasshopper_jobs.cached_step(
            ghenv.Component,
            "brief_stage_confirm",
            confirm_signature,
            confirm_brief,
            trigger=bool(globals().get("confirm")),
        )
        status.append("review: " + confirm_state.get("status", "idle"))
        if confirm_state.get("error"):
            status.append("REVIEW ERROR: " + confirm_state["error"])
        reviewed = confirm_state.get("result")
        if reviewed:
            repair = workshop_flow.repair_record(setup, reviewed)
            repair_json = json.dumps(repair, indent=2, ensure_ascii=False)
            brief_json = json.dumps(reviewed, indent=2, ensure_ascii=False)
            verified = len(
                (reviewed.get("requirementsAuthority") or {}).get("requirementIds") or []
            )
            status.append("confirmed; {} cited requirement(s) can bind".format(verified))
except Exception as exc:
    status.append("ERROR: {}".format(exc))
