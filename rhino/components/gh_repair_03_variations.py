# r: google-genai==1.47.0
# r: numpy
"""GH Python 3 -- 03 VARIATIONS: author one joint and browse local studies.

Inputs: repair_json, instruction, gemini_model, variation_count, generate,
        reset, picker, refresh, box, centers, damage, threshold,
        neighbour_geometry, neighbour_ids, execute_all, repo
Outputs: candidate_set_json, selection_json, candidate_id, candidate_ids,
         candidate_json, candidate_code, summary, kept, prosthesis, frames, other,
         candidate_geometry, execution_json, report

`frames` contains the active cutting-plane rectangles and direction arrows. `other`
is the same list for canvases that still use the old output name.
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


def _value(value):
    try:
        return str(json.loads(str(value)))
    except Exception:
        return str(value).strip().strip('"')


def _fill_picker(options):
    import Grasshopper.Kernel.Special as ghs

    parameter = next((p for p in ghenv.Component.Params.Input if p.NickName == "picker"), None)
    source = next((s for s in (parameter.Sources if parameter else []) if isinstance(s, ghs.GH_ValueList)), None)
    if source is None:
        raise ValueError("wire a Grasshopper Value List into picker")
    previous = _value(source.SelectedItems[0].Value) if source.SelectedItems.Count else None
    ids = [item[1] for item in options]
    selected = previous if previous in ids else ids[0]
    current = [(str(item.Name), _value(item.Value)) for item in source.ListItems]
    if current != options:
        source.ListItems.Clear()
        for label, item_id in options:
            source.ListItems.Add(ghs.GH_ValueListItem(label, json.dumps(item_id)))
        source.SelectItem(ids.index(selected))
        source.ExpireSolution(True)
        return selected, True
    return selected, False


def _clear_picker():
    import Grasshopper.Kernel.Special as ghs

    parameter = next((p for p in ghenv.Component.Params.Input if p.NickName == "picker"), None)
    source = next((s for s in (parameter.Sources if parameter else []) if isinstance(s, ghs.GH_ValueList)), None)
    if source is not None and source.ListItems.Count:
        source.ListItems.Clear()
        source.ExpireSolution(True)


candidate_set_json = selection_json = candidate_id = ""
candidate_json = candidate_code = summary = execution_json = ""
candidate_ids = []
kept = prosthesis = frames = other = candidate_geometry = []
report = ["03 Repair Variations"]

try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    for name in list(sys.modules):
        if name == "workshop_robarch_2026" or name.startswith("workshop_robarch_2026."):
            sys.modules.pop(name, None)
    from workshop_robarch_2026 import (
        candidate_runtime, grasshopper_jobs, llm_candidate, plane_variations,
        repair_variations, workshop_flow,
    )

    repair = workshop_flow.validate_repair(globals().get("repair_json"))
    session, context, brief = repair["session"], repair["context"], repair["brief"]
    runtime_box = candidate_runtime.box_from_context(context, globals().get("box"))
    centers_value = _list(globals().get("centers"))
    damage_value = _list(globals().get("damage"))
    neighbours = _list(globals().get("neighbour_geometry"))
    neighbour_names = [str(value) for value in _list(globals().get("neighbour_ids"))]
    gate = 0.5 if globals().get("threshold") is None else float(threshold)
    count = max(2, min(8, int(globals().get("variation_count") or 5)))
    model = str(globals().get("gemini_model") or llm_candidate.DEFAULT_MODEL)
    instruction_value = str(globals().get("instruction") or "")
    damage_summary = repair_variations.damage_context(
        runtime_box, centers_value, damage_value, gate
    )
    fit_probe = {
        "frame": plane_variations.box_frame(runtime_box),
        "centers": [[float(point.X), float(point.Y), float(point.Z)] for point in centers_value],
        "damage": [float(value) for value in damage_value],
        "threshold": gate,
    }
    reset_value = bool(globals().get("reset"))
    if reset_value:
        grasshopper_jobs.clear_job(ghenv.Component, "repair_variations_author")
        grasshopper_jobs.clear_job(ghenv.Component, "repair_variations_explore")

    author_signature = llm_candidate.stable_signature(
        "repair-variations-v7", repair["recordHash"], instruction_value, model, count
    )
    author_state = grasshopper_jobs.background_step(
        ghenv.Component,
        "repair_variations_author",
        author_signature,
        lambda: repair_variations.author_joint(
            root, session, context, brief, damage_summary, fit_probe,
            instruction_value, model, count,
        ),
        trigger=bool(globals().get("generate")),
        reset=reset_value,
    )
    report.append("author: " + author_state.get("status", "idle"))
    if author_state.get("error"):
        report.append("AUTHOR ERROR: " + author_state["error"])
        _clear_picker()
    source = author_state.get("result")

    if source:
        source = repair_variations.validate_source(source)
        candidate_set_json = json.dumps(source, indent=2, ensure_ascii=False)
        report.append("one six-plane joint authored in {} pass(es)".format(source["authoringAttempts"]))
        if source.get("summary"):
            report.append("concept: " + source["summary"])
        report.extend("author warning: " + value for value in (source.get("warnings") or []))
        execute_signature = candidate_runtime.runtime_signature(
            "repair-explore-v8", source, runtime_box, centers_value, damage_value,
            gate, count, neighbours, neighbour_names,
        )

        def execute_set():
            explored = repair_variations.explore(
                source, runtime_box, centers_value, damage_value, gate, count
            )
            runs = {}
            for variation in explored["variations"]:
                result = {
                    "kept": variation["kept"],
                    "prosthesis": variation["prosthesis"],
                    "other": [],
                    "resolvedProgram": variation["resolvedProgram"],
                    "fit": variation["fit"],
                    "report": variation["report"] + [variation["analysis"]["text"]],
                }
                result["fit"]["variationAnalysis"] = variation["analysis"]
                manifest, code, geometry, entities, execution = plane_variations.selection_payload(
                    result, brief, session, runtime_box, centers_value, damage_value,
                    gate, neighbours, neighbour_names,
                )
                entity = candidate_runtime.entity_envelope(execution, entities)
                selection = workshop_flow.selection_record(
                    manifest, code, entity, execution, source["sourceHash"]
                )
                runs[variation["id"]] = {
                    "result": result, "frames": variation["frames"],
                    "rank": variation["rank"],
                    "analysis": variation["analysis"], "manifest": manifest,
                    "code": code, "geometry": geometry, "selection": selection,
                    "execution": execution,
                }
            explored["runs"] = runs
            return explored

        execute_state = grasshopper_jobs.automatic_step(
            ghenv.Component, "repair_variations_explore", execute_signature,
            execute_set,
        )
        report.append("fit: " + execute_state.get("status", "idle"))
        if execute_state.get("error"):
            report.append("EXECUTION ERROR: " + execute_state["error"])
        if execute_state.get("message"):
            report.append(execute_state["message"])
        explored = execute_state.get("result") or {}
        runs = explored.get("runs") or {}
        if runs:
            report.append(
                "{} studies evaluated: {} full coverage, {} produced exact Breps".format(
                    explored.get("fitCount", 0), explored.get("fullCoverageCount", 0), len(runs)
                )
            )
            failure_counts = {}
            for failure in explored.get("failures") or []:
                stage = failure.get("stage", "unknown")
                failure_counts[stage] = failure_counts.get(stage, 0) + 1
            if failure_counts:
                report.append("skipped: " + ", ".join(
                    "{} {}".format(value, key) for key, value in sorted(failure_counts.items())
                ))
            options = []
            for item_id, value in runs.items():
                analysis = value["analysis"]
                transform = analysis.get("transform") or {}
                study_name = str(transform.get("summary") or "angle study")
                label = "{:02d} | damage {}/{} | sound {}/{} | {}".format(
                    int(value.get("rank") or 0),
                    analysis["requiredDamageRemoved"], analysis["requiredDamageCount"],
                    analysis["soundRemoved"], analysis["soundCount"],
                    study_name[:42],
                )
                options.append((label, item_id))
            candidate_ids = [item[1] for item in options]
            candidate_id = str(globals().get("picker") or "").strip()
            try:
                dropdown_id, changed = _fill_picker(options)
                if changed or candidate_id not in candidate_ids:
                    candidate_id = dropdown_id
                if changed:
                    report.append("variation dropdown updated")
            except Exception as exc:
                if candidate_id not in candidate_ids:
                    candidate_id = candidate_ids[0]
                report.append("picker: {}".format(exc))
        run = runs.get(candidate_id) or {}
        if run:
            result = run["result"]
            kept, prosthesis, frames = result["kept"], result["prosthesis"], run["frames"]
            other = frames
            candidate_geometry = run["geometry"]
            candidate_json = json.dumps(run["manifest"], indent=2, ensure_ascii=False)
            candidate_code = run["code"]
            selection_json = json.dumps(run["selection"], indent=2, ensure_ascii=False)
            execution_json = json.dumps(run["execution"], indent=2, ensure_ascii=False)
            summary = run["analysis"]["text"]
            report.append("selected: {} Kept, {} Prosthesis, {} frame curves".format(
                len(kept), len(prosthesis), len(frames)
            ))
            report.append(summary)
            report.append("selected id: " + candidate_id)
except Exception as exc:
    report.append("ERROR: {}".format(exc))
