# r: google-genai==1.47.0
"""GH Python 3 -- REPAIR BRIEF: separate facts, inferences and questions.

Inputs: session_json, context_json, instruction, gemini_model, draft, reset, repo
Outputs: brief_json, workspace_facts, llm_inferences, open_questions, status
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
    seen = set()
    for start in starts:
        if not start or "://" in str(start):
            continue
        folder = os.path.abspath(os.path.expanduser(str(start)))
        if os.path.isfile(folder):
            folder = os.path.dirname(folder)
        for _ in range(9):
            if os.path.normcase(folder) in seen:
                break
            seen.add(os.path.normcase(folder))
            if os.path.isdir(os.path.join(folder, "src", "workshop_robarch_2026")):
                return folder
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
    raise RuntimeError("workshop repository not found; connect its folder to repo")


brief_json = workspace_facts = llm_inferences = open_questions = ""
status = ["Repair Brief"]
try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    from workshop_robarch_2026 import grasshopper_jobs, llm_candidate

    session = llm_candidate.as_object(globals().get("session_json"), "session_json")
    context = llm_candidate.as_object(globals().get("context_json"), "context_json")
    message = str(globals().get("instruction") or "")
    model = str(globals().get("gemini_model") or llm_candidate.DEFAULT_MODEL)
    signature = llm_candidate.stable_signature(session, context, message, model)
    state = grasshopper_jobs.background_step(
        ghenv.Component,
        "brief",
        signature,
        lambda: llm_candidate.draft_brief(root, session, context, message, model),
        trigger=bool(globals().get("draft")),
        reset=bool(globals().get("reset")),
    )
    status.append(state.get("status", "idle"))
    if state.get("error"):
        status.append("ERROR: " + state["error"])
    result = state.get("result") or {}
    if result:
        brief = result["brief"]
        brief_json = json.dumps(brief, indent=2, ensure_ascii=False)
        workspace_facts = json.dumps(result.get("workspaceFacts") or brief.get("workspaceFacts") or [], indent=2, ensure_ascii=False)
        llm_inferences = json.dumps(result.get("llmInferences") or brief.get("llmInferences") or [], indent=2, ensure_ascii=False)
        open_questions = json.dumps(result.get("openQuestions") or brief.get("openQuestions") or [], indent=2, ensure_ascii=False)
        if result.get("summary"):
            status.append(str(result["summary"]))
except Exception as exc:
    status.append("ERROR: {}".format(exc))
