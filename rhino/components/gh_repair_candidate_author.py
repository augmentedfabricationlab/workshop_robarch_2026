# r: google-genai==1.47.0
"""GH Python 3 -- CANDIDATE AUTHOR: repeat geometry authorship under one brief.

Inputs: session_json, context_json, brief_json, instruction, gemini_model,
        variation_count, generate, reset, repo
Outputs: candidate_set_json, candidate_json, candidate_code, summary,
         candidate_ids, status
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


candidate_set_json = candidate_json = candidate_code = summary = ""
candidate_ids = []
status = ["Candidate Author"]
try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    from workshop_robarch_2026 import candidate_variations, grasshopper_jobs, llm_candidate

    session = llm_candidate.as_object(globals().get("session_json"), "session_json")
    context = llm_candidate.as_object(globals().get("context_json"), "context_json")
    brief = llm_candidate.as_object(globals().get("brief_json"), "brief_json")
    message = str(globals().get("instruction") or "")
    model = str(globals().get("gemini_model") or llm_candidate.DEFAULT_MODEL)
    count = candidate_variations.variation_count(globals().get("variation_count"))
    signature = llm_candidate.stable_signature(
        session, context, brief, message, model, count
    )

    def work():
        def progress(completed, total, note):
            grasshopper_jobs.update_progress(
                ghenv.Component, "author", completed, total, note
            )

        return llm_candidate.author_candidate_set(
            root, session, context, brief, message, model, count, progress
        )

    state = grasshopper_jobs.background_step(
        ghenv.Component, "author", signature, work,
        trigger=bool(globals().get("generate")), reset=bool(globals().get("reset")),
    )
    status.append(state.get("status", "idle"))
    progress = state.get("progress") or {}
    if progress:
        status.append(
            "{}/{} — {}".format(
                progress.get("completed", 0),
                progress.get("total", count),
                progress.get("message", ""),
            )
        )
    if state.get("error"):
        status.append("ERROR: " + state["error"])
    result = state.get("result") or {}
    if result:
        candidate_set_json = json.dumps(result, indent=2, ensure_ascii=False)
        candidate_ids = [item["id"] for item in result["candidates"]]
        first = result["candidates"][0]
        candidate_json = json.dumps(first["candidate"], indent=2, ensure_ascii=False)
        candidate_code = str(first["python"])
        summary = str(first.get("summary") or "")
        status.append(
            "authored {}/{} variation(s)".format(
                result["completedCount"], result["requestedCount"]
            )
        )
        status.extend(result.get("errors") or [])
except Exception as exc:
    status.append("ERROR: {}".format(exc))
