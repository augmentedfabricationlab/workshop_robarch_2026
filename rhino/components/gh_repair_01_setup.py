"""GH Python 3 -- 01 REPAIR SETUP: select a member and build its context.

Inputs: workspace_json, picker, refresh, box, centers, damage, threshold, repo
Outputs: setup_json, beam_id, capabilities_json, report
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


def _xyz(value):
    return [float(value.X), float(value.Y), float(value.Z)]


def _box_summary(value):
    if value is None:
        return None
    if hasattr(value, "IsValid") and not value.IsValid:
        raise ValueError("box is invalid")
    return {
        "center": _xyz(value.Center),
        "axes": {
            "x": _xyz(value.Plane.XAxis),
            "y": _xyz(value.Plane.YAxis),
            "z": _xyz(value.Plane.ZAxis),
        },
        "size": {
            "x": float(value.X.Length),
            "y": float(value.Y.Length),
            "z": float(value.Z.Length),
        },
    }


def _rhino_settings():
    import Rhino

    document = Rhino.RhinoDoc.ActiveDoc
    return float(document.ModelAbsoluteTolerance), str(document.ModelUnitSystem)


def _expression_value(value):
    try:
        return str(json.loads(str(value)))
    except Exception:
        return str(value).strip().strip('"')


def _fill_picker(options):
    import Grasshopper.Kernel.Special as ghs

    parameter = next(
        (item for item in ghenv.Component.Params.Input if item.NickName == "picker"),
        None,
    )
    source = next(
        (
            item for item in (parameter.Sources if parameter else [])
            if isinstance(item, ghs.GH_ValueList)
        ),
        None,
    )
    if source is None:
        raise ValueError("wire a Grasshopper Value List into picker")
    previous = (
        _expression_value(source.SelectedItems[0].Value)
        if source.SelectedItems.Count else None
    )
    expected = [(item["label"], item["id"]) for item in options]
    ids = [item[1] for item in expected]
    selected = previous if previous in ids else ids[0]
    current = [(str(item.Name), _expression_value(item.Value)) for item in source.ListItems]
    if current != expected:
        source.ListItems.Clear()
        for label, part_id in expected:
            source.ListItems.Add(ghs.GH_ValueListItem(label, json.dumps(part_id)))
        source.SelectItem(ids.index(selected))
        source.ExpireSolution(True)
        return selected, True
    if previous != selected:
        source.SelectItem(ids.index(selected))
        source.ExpireSolution(True)
        return selected, True
    return selected, False


setup_json = beam_id = capabilities_json = ""
report = ["01 Repair Setup"]

try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    for module_name in list(sys.modules):
        if module_name == "workshop_robarch_2026" or module_name.startswith("workshop_robarch_2026."):
            sys.modules.pop(module_name, None)
    from workshop_robarch_2026 import workshop_flow, workspace_io

    workspace_input = globals().get("workspace_json")
    workspace = workspace_io.load_workspace(workspace_input)
    options = workspace_io.part_options(workspace)
    if not options:
        raise ValueError("Workspace contains no parts")
    beam_id = str(globals().get("picker") or "").strip()
    if bool(globals().get("refresh")):
        beam_id, changed = _fill_picker(options)
        report.append("dropdown filled" if changed else "dropdown already current")
    elif beam_id not in [item["id"] for item in options]:
        beam_id = options[0]["id"]
        report.append("press refresh once to fill the connected Value List")

    target = workspace_io.find_part(workspace, beam_id)
    gate = 0.5 if globals().get("threshold") is None else float(threshold)
    raw_centers = _list(globals().get("centers"))
    raw_damage = _list(globals().get("damage"))
    field = workspace_io.damage_field(raw_centers, raw_damage, gate)
    box_summary = _box_summary(globals().get("box"))
    tolerance, units = _rhino_settings()
    context = workspace_io.part_context(workspace, beam_id)
    context["rhinoContext"] = {
        "targetBox": box_summary,
        "cellularDamage": field["summary"],
        "absoluteTolerance": tolerance,
        "modelUnits": units,
    }
    context_hash = workspace_io.json_digest(
        {
            "context": context,
            "box": box_summary,
            "cellDataHash": field["dataHash"],
            "threshold": gate,
        }
    )
    scoped_parts = [context.get("targetPart")] + list(context.get("connectedParts") or [])
    session = {
        "schema": "repair-session@1",
        "workspaceHash": workspace_io.workspace_digest(workspace),
        "contextHash": context_hash,
        "cellDataHash": field["dataHash"],
        "instanceId": (workspace.get("instance") or {}).get("id"),
        "beamId": beam_id,
        "partIds": [str(item["id"]) for item in scoped_parts if item and item.get("id")],
        "actionIds": [
            str(item["id"])
            for item in ((context.get("currentPlan") or {}).get("steps") or [])
            if item.get("id")
        ],
        "threshold": gate,
        "rhinoTolerance": tolerance,
        "modelUnits": units,
        "cellCount": len(raw_centers),
    }
    raw_source = str(workspace_input or "").strip()
    if (
        raw_source and len(raw_source) < 1024 and "\n" not in raw_source
        and os.path.isfile(os.path.abspath(os.path.expanduser(raw_source)))
    ):
        source_file = os.path.abspath(os.path.expanduser(raw_source))
        session["workspaceSource"] = {
            "kind": "zip" if source_file.lower().endswith(".zip") else "json",
            "path": source_file,
        }
    capabilities = {
        "schema": "repair-capabilities@1",
        "targetBox": "available" if box_summary else "not connected",
        "cellularDamage": "available" if raw_centers else "not connected",
        "currentPlan": "available" if context.get("currentPlan") else "missing",
        "neighbourGeometry": "connect later when relevant",
        "checks": {
            "localGeometry": "available" if box_summary else "limited",
            "neighbourCollision": "requires neighbour geometry",
            "insertionSampled": "requires candidate movement data",
            "toolRadius": "requires candidate tool data",
        },
    }
    setup = workshop_flow.setup_record(session, context, capabilities)
    setup_json = json.dumps(setup, indent=2, ensure_ascii=False)
    capabilities_json = json.dumps(capabilities, indent=2, ensure_ascii=False)
    report.append("selected: {} ({})".format(target.get("label") or beam_id, beam_id))
    report.append("{} cells; {} at or above {:.3f}".format(
        len(raw_centers), field["summary"]["aboveThresholdCellCount"], gate
    ))
    report.append("Rhino tolerance: {} {}".format(tolerance, units))
except Exception as exc:
    report.append("ERROR: {}".format(exc))
