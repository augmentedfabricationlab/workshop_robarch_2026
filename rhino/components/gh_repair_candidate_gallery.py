"""GH Python 3 -- CANDIDATE GALLERY: execute and browse authored variations.

Inputs: session_json, brief_json, candidate_set_json, picker, box, centers, damage,
        threshold, neighbour_geometry, neighbour_ids, refresh, execute_all, repo
Outputs: candidate_id, candidate_ids, candidate_json, candidate_code, summary,
         candidate_geometry, entity_json, execution_json, report
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


def _expression_value(expression):
    try:
        return str(json.loads(str(expression)))
    except Exception:
        return str(expression).strip().strip('"')


def _value_list():
    import Grasshopper.Kernel.Special as ghs

    parameter = next(
        (item for item in ghenv.Component.Params.Input if item.NickName == "picker"),
        None,
    )
    return next(
        (
            source for source in (parameter.Sources if parameter else [])
            if isinstance(source, ghs.GH_ValueList)
        ),
        None,
    )


def _fill_value_list(options):
    import Grasshopper.Kernel.Special as ghs

    source = _value_list()
    if source is None:
        raise ValueError("wire a Grasshopper Value List into picker")
    previous = (
        _expression_value(source.SelectedItems[0].Value)
        if source.SelectedItems.Count else None
    )
    expected = [(item["label"], item["id"]) for item in options]
    current = [
        (str(item.Name), _expression_value(item.Value)) for item in source.ListItems
    ]
    ids = [item[1] for item in expected]
    selected = previous if previous in ids else ids[0]
    if current == expected:
        if previous != selected:
            source.SelectItem(ids.index(selected))
            source.ExpireSolution(True)
            return selected, True
        return selected, False
    source.ListItems.Clear()
    for label, candidate_id in expected:
        source.ListItems.Add(
            ghs.GH_ValueListItem(label, json.dumps(candidate_id))
        )
    source.SelectItem(ids.index(selected))
    source.ExpireSolution(True)
    return selected, True


candidate_id = candidate_json = candidate_code = summary = ""
candidate_ids = []
candidate_geometry = []
entity_json = execution_json = ""
report = ["Candidate Gallery"]

try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    from workshop_robarch_2026 import (
        candidate_runtime, candidate_variations, grasshopper_jobs, llm_candidate,
    )

    session = llm_candidate.validate_session(globals().get("session_json"))
    brief = llm_candidate.validate_brief(
        globals().get("brief_json"), session["beamId"], session,
        require_review=True,
    )
    candidate_set = candidate_variations.validate_candidate_set(
        globals().get("candidate_set_json"), session=session, brief=brief
    )
    options = candidate_variations.candidate_options(candidate_set)
    candidate_ids = [item["id"] for item in options]
    candidate_id = str(globals().get("picker") or "").strip()
    if bool(globals().get("refresh")):
        candidate_id, changed = _fill_value_list(options)
        report.append("dropdown filled" if changed else "dropdown already current")
    elif candidate_id not in candidate_ids:
        candidate_id = candidate_ids[0]
        report.append("press refresh once to fill the connected Value List")

    selected = candidate_variations.select_candidate(candidate_set, candidate_id)
    candidate_id = selected["id"]
    candidate_json = json.dumps(selected["candidate"], indent=2, ensure_ascii=False)
    candidate_code = str(selected["python"])
    summary = str(selected.get("summary") or "")

    raw_centers = _list(globals().get("centers"))
    raw_damage = _list(globals().get("damage"))
    neighbours = _list(globals().get("neighbour_geometry"))
    neighbour_names = [str(value) for value in _list(globals().get("neighbour_ids"))]
    gate = 0.5 if globals().get("threshold") is None else float(threshold)
    signature = candidate_runtime.runtime_signature(
        session, brief, candidate_set, globals().get("box"), raw_centers, raw_damage,
        gate, neighbours, neighbour_names,
    )

    def execute_set():
        runs, messages, geometry_owners = {}, [], {}
        for entry in candidate_set["candidates"]:
            try:
                geometry, entities, execution, run_report = candidate_runtime.execute_candidate(
                    session, entry["candidate"], entry["python"], globals().get("box"),
                    raw_centers, raw_damage, gate, neighbours, neighbour_names,
                )
                runs[entry["id"]] = {
                    "geometry": geometry,
                    "entities": entities,
                    "execution": execution,
                }
                messages.append("{}: {} geometry item(s)".format(
                    entry["id"], len(geometry)
                ))
                geometry_hash = execution.get("geometryHash")
                if geometry_hash in geometry_owners:
                    messages.append(
                        "  duplicates executed geometry of {}".format(
                            geometry_owners[geometry_hash]
                        )
                    )
                else:
                    geometry_owners[geometry_hash] = entry["id"]
                messages.extend("  " + item for item in run_report[1:])
            except Exception as exc:
                runs[entry["id"]] = {"error": "{}: {}".format(type(exc).__name__, exc)}
                messages.append("{}: ERROR: {}".format(entry["id"], exc))
        return {"runs": runs, "messages": messages}

    state = grasshopper_jobs.cached_step(
        ghenv.Component, "candidate_gallery", signature, execute_set,
        trigger=bool(globals().get("execute_all")),
    )
    report.append(state.get("status", "idle"))
    if state.get("error"):
        report.append("ERROR: " + state["error"])
    if state.get("message"):
        report.append(state["message"])
    result = state.get("result") or {}
    if result:
        run = (result.get("runs") or {}).get(candidate_id) or {}
        if run.get("error"):
            report.append("selected variation failed: " + run["error"])
        elif run:
            candidate_geometry = run["geometry"]
            envelope = candidate_runtime.entity_envelope(
                run["execution"], run["entities"]
            )
            entity_json = json.dumps(envelope, indent=2, ensure_ascii=False)
            execution_json = json.dumps(
                run["execution"], indent=2, ensure_ascii=False
            )
        report.extend(result.get("messages") or [])
    report.append("selected: " + candidate_id)
except Exception as exc:
    report.append("ERROR: {}".format(exc))
