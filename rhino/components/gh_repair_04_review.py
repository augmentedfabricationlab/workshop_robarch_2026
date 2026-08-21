# r: google-genai==1.47.0
"""GH Python 3 -- 04 REVIEW: measure and optionally revise one variation.

Inputs: repair_json, selection_json, candidate_geometry, box, centers, damage,
        threshold, neighbour_geometry, neighbour_ids, measure, feedback,
        gemini_model, revise, use_revision, reset, repo
Outputs: active_json, active_geometry, facts_json, requirements_json,
         diagnostic_geometry, change_summary, report
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


active_json = facts_json = requirements_json = change_summary = ""
active_geometry = diagnostic_geometry = []
report = ["04 Review / Revise"]

try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    for module_name in list(sys.modules):
        if module_name == "workshop_robarch_2026" or module_name.startswith("workshop_robarch_2026."):
            sys.modules.pop(module_name, None)
    from workshop_robarch_2026 import (
        candidate_analysis, candidate_runtime, grasshopper_jobs, llm_candidate,
        plane_variations, repair_candidate, workshop_flow,
    )

    repair = workshop_flow.validate_repair(globals().get("repair_json"))
    selected = workshop_flow.validate_selection(globals().get("selection_json"))
    session, context, brief = repair["session"], repair["context"], repair["brief"]
    runtime_box = candidate_runtime.box_from_context(context, globals().get("box"))
    original_geometry = _list(globals().get("candidate_geometry"))
    raw_centers = _list(globals().get("centers"))
    raw_damage = _list(globals().get("damage"))
    neighbours = _list(globals().get("neighbour_geometry"))
    neighbour_names = [str(value) for value in _list(globals().get("neighbour_ids"))]
    gate = 0.5 if globals().get("threshold") is None else float(threshold)
    reset_value = bool(globals().get("reset"))
    if reset_value:
        for key in ("simple_review_measure", "simple_review_revise", "simple_review_revision"):
            grasshopper_jobs.clear_job(ghenv.Component, key)

    def analyze(candidate, geometry, entity):
        return candidate_analysis.analyze_candidate(
            session, candidate, geometry, json.dumps(entity), runtime_box,
            raw_centers, raw_damage, gate, neighbours, neighbour_names,
        )

    measure_signature = candidate_runtime.runtime_signature(
        selected, original_geometry, runtime_box, raw_centers, raw_damage,
        gate, neighbours, neighbour_names,
    )
    measured = grasshopper_jobs.cached_step(
        ghenv.Component, "simple_review_measure", measure_signature,
        lambda: analyze(selected["candidate"], original_geometry, selected["entity"]),
        trigger=bool(globals().get("measure")),
    )
    report.append("measure: " + measured.get("status", "idle"))
    if measured.get("error"):
        report.append("MEASURE ERROR: " + measured["error"])
    original_result = measured.get("result")

    revision_result = None
    if original_result:
        original_facts, original_requirements = original_result[:2]
        feedback_text = str(globals().get("feedback") or "")
        model = str(globals().get("gemini_model") or llm_candidate.DEFAULT_MODEL)
        revise_signature = llm_candidate.stable_signature(
            repair["recordHash"], selected["recordHash"], original_facts,
            original_requirements, feedback_text, model,
        )

        def revise_candidate():
            plane_program = selected["candidate"].get("planeProgram")
            if plane_program:
                result = plane_variations.revise_program(
                    root, session, context, brief, plane_program,
                    feedback_text, model,
                )
                return {
                    "planeProgram": result["program"],
                    "changeSummary": result["summary"],
                    "warnings": result.get("warnings") or [],
                }
            result = llm_candidate.revise_candidate(
                root, session, context, brief, selected["candidate"], selected["python"],
                original_facts, original_requirements, feedback_text, model,
            )
            previous = repair_candidate.normalise_manifest(selected["candidate"])
            revised = repair_candidate.apply_brief_authority(result["candidate"], brief)
            action_ids = [
                str(step["id"]) for step in ((context.get("currentPlan") or {}).get("steps") or [])
                if step.get("id")
            ]
            parts = [context.get("targetPart")] + list(context.get("connectedParts") or [])
            part_ids = [str(part["id"]) for part in parts if part and part.get("id")]
            revised = repair_candidate.validate_scope(
                revised, beam_id=session.get("beamId"), part_ids=part_ids,
                action_ids=action_ids, workspace_hash=session.get("workspaceHash"),
                context_hash=session.get("contextHash"),
            )
            revised["revisionOf"] = previous["id"]
            revised["parentManifestHash"] = repair_candidate.stable_json_hash(previous)
            revised["revisionNote"] = str(result.get("changeSummary") or "")
            result["candidate"] = revised
            return result

        revised = grasshopper_jobs.background_step(
            ghenv.Component, "simple_review_revise", revise_signature, revise_candidate,
            trigger=bool(globals().get("revise")), reset=reset_value,
        )
        report.append("revise: " + revised.get("status", "idle"))
        if revised.get("error"):
            report.append("REVISION ERROR: " + revised["error"])
        revision = revised.get("result")
        if revision:
            change_summary = str(revision.get("changeSummary") or "")
            revision_signature = candidate_runtime.runtime_signature(
                repair["recordHash"], revision, runtime_box, raw_centers,
                raw_damage, gate, neighbours, neighbour_names,
            )

            def execute_revision():
                if revision.get("planeProgram"):
                    result = plane_variations.evaluate(
                        revision["planeProgram"], runtime_box, raw_centers,
                        raw_damage, gate,
                    )
                    candidate, code, geometry, entities, execution = plane_variations.selection_payload(
                        result, brief, session, runtime_box, raw_centers,
                        raw_damage, gate, neighbours, neighbour_names,
                    )
                    entity = candidate_runtime.entity_envelope(execution, entities)
                    facts, requirements, diagnostics, analysis_messages = analyze(
                        candidate, geometry, entity
                    )
                    selection = workshop_flow.selection_record(
                        candidate, code, entity, execution,
                        selected.get("candidateSetId", ""),
                    )
                    return (
                        selection, geometry, facts, requirements, diagnostics,
                        result.get("report", []) + analysis_messages,
                    )
                geometry, entities, execution, messages = candidate_runtime.execute_candidate(
                    session, revision["candidate"], revision["python"], runtime_box,
                    raw_centers, raw_damage, gate, neighbours, neighbour_names,
                )
                entity = candidate_runtime.entity_envelope(execution, entities)
                facts, requirements, diagnostics, analysis_messages = analyze(
                    revision["candidate"], geometry, entity
                )
                selection = workshop_flow.selection_record(
                    revision["candidate"], revision["python"], entity, execution,
                    selected.get("candidateSetId", ""),
                )
                return selection, geometry, facts, requirements, diagnostics, messages + analysis_messages

            revision_state = grasshopper_jobs.cached_step(
                ghenv.Component, "simple_review_revision", revision_signature,
                execute_revision, trigger=True,
            )
            report.append("revised geometry: " + revision_state.get("status", "idle"))
            if revision_state.get("error"):
                report.append("REVISED GEOMETRY ERROR: " + revision_state["error"])
            revision_result = revision_state.get("result")
        else:
            # Re-arm the automatic local execution while the next LLM call runs.
            grasshopper_jobs.cached_step(
                ghenv.Component, "simple_review_revision", "waiting",
                lambda: None, trigger=False,
            )

    use_revised = bool(globals().get("use_revision")) and revision_result
    if use_revised:
        active_selection, active_geometry, facts, requirements, diagnostic_geometry, messages = revision_result
        source = "revised"
        report.extend(messages)
    elif original_result:
        facts, requirements, diagnostic_geometry, messages = original_result
        active_selection, active_geometry, source = selected, original_geometry, "authored"
        report.extend(messages)
    else:
        facts = requirements = None

    if facts and requirements:
        active = workshop_flow.active_record(active_selection, facts, requirements, source)
        active_json = json.dumps(active, indent=2, ensure_ascii=False)
        facts_json = json.dumps(facts, indent=2, ensure_ascii=False)
        requirements_json = json.dumps(requirements, indent=2, ensure_ascii=False)
        report.append("active: " + source)
except Exception as exc:
    report.append("ERROR: {}".format(exc))
