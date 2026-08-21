"""GH Python 3 -- REPAIR CONTEXT: prepare local inputs for candidate design.

Inputs
------
workspace_json  dict/str  Workspace JSON, JSON path, or exported ZIP path
beam_id         str       selected Workspace part id
box             Box       oriented box from BEAM CELLS
centers         Point3d[] cell centres from BEAM CELLS
damage          float[]   one damage value per centre
threshold       float     display/selection threshold [0.50]
repo            str       optional workshop repository override

Outputs
-------
session_json       str    stable ids and hashes for this local session
context_json       str    selected Workspace, Action Model, and Rhino summary
capabilities_json  str    checks supported by the currently connected inputs
report             str[]  concise diagnostics

This component reads and measures. It makes no model call and changes no Rhino
objects. Rhino's document tolerance is read automatically.
"""

import json
import os
import sys


def _repo_root():
    candidates = [globals().get("repo"), os.environ.get("ROBARCH_REPO")]
    component_file = globals().get("_p") or globals().get("__file__")
    if component_file and "://" not in str(component_file):
        candidates.append(os.path.dirname(os.path.abspath(str(component_file))))
    try:
        path = str(ghenv.Component.OnPingDocument().FilePath or "").strip()
        if path:
            candidates.append(os.path.dirname(os.path.abspath(path)))
    except Exception:
        pass
    candidates.extend([os.getcwd(), *sys.path])

    seen = set()
    for candidate in candidates:
        if not candidate or "://" in str(candidate):
            continue
        folder = os.path.abspath(os.path.expanduser(str(candidate)))
        if os.path.isfile(folder):
            folder = os.path.dirname(folder)
        for _ in range(9):
            key = os.path.normcase(folder)
            if key in seen:
                break
            seen.add(key)
            if os.path.isdir(os.path.join(folder, "src", "workshop_robarch_2026")):
                return folder
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
    raise RuntimeError("workshop_robarch_2026 repository not found; connect its folder to repo")


def _as_list(value):
    if value is None:
        return []
    try:
        return list(value)
    except TypeError:
        return [value]


def _point(value):
    return [float(value.X), float(value.Y), float(value.Z)]


def _box_summary(value):
    if value is None:
        return None
    if hasattr(value, "IsValid") and not value.IsValid:
        raise ValueError("box is invalid")
    plane = value.Plane
    return {
        "center": _point(value.Center),
        "axes": {
            "x": _point(plane.XAxis),
            "y": _point(plane.YAxis),
            "z": _point(plane.ZAxis),
        },
        "size": {
            "x": float(value.X.Length),
            "y": float(value.Y.Length),
            "z": float(value.Z.Length),
        },
    }


def _rhino_settings():
    try:
        import Rhino

        document = Rhino.RhinoDoc.ActiveDoc
        return float(document.ModelAbsoluteTolerance), str(document.ModelUnitSystem)
    except Exception:
        return None, None


session_json = ""
context_json = ""
capabilities_json = ""
report = ["Repair Context"]

try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    from workshop_robarch_2026 import workspace_io

    workspace = workspace_io.load_workspace(globals().get("workspace_json"))
    selected_id = str(globals().get("beam_id") or "").strip()
    target = workspace_io.find_part(workspace, selected_id)
    gate = 0.5 if globals().get("threshold") is None else float(threshold)
    raw_centers = _as_list(globals().get("centers"))
    raw_damage = _as_list(globals().get("damage"))
    damage_field = workspace_io.damage_field(raw_centers, raw_damage, gate)
    damage_summary = damage_field["summary"]
    box_summary = _box_summary(globals().get("box"))
    tolerance, units = _rhino_settings()

    context = workspace_io.part_context(workspace, selected_id)
    context["rhinoContext"] = {
        "targetBox": box_summary,
        "cellularDamage": damage_summary,
        "absoluteTolerance": tolerance,
        "modelUnits": units,
    }
    context_json = json.dumps(context, indent=2, ensure_ascii=False)
    cell_data_hash = damage_field["dataHash"]
    context_hash = workspace_io.json_digest(
        {
            "context": context,
            "box": box_summary,
            "cellDataHash": cell_data_hash,
            "threshold": gate,
        }
    )
    scoped_parts = [context.get("targetPart")] + list(context.get("connectedParts") or [])
    part_ids = [str(item.get("id")) for item in scoped_parts if item and item.get("id")]
    action_ids = [
        str(item.get("id"))
        for item in ((context.get("currentPlan") or {}).get("steps") or [])
        if item.get("id")
    ]
    session = {
        "schema": "repair-session@1",
        "workspaceHash": workspace_io.workspace_digest(workspace),
        "contextHash": context_hash,
        "cellDataHash": cell_data_hash,
        "instanceId": (workspace.get("instance") or {}).get("id"),
        "beamId": selected_id,
        "partIds": part_ids,
        "actionIds": action_ids,
        "threshold": gate,
        "rhinoTolerance": tolerance,
        "modelUnits": units,
        "cellCount": len(raw_centers),
    }
    raw_source = str(globals().get("workspace_json") or "").strip()
    if os.path.isfile(os.path.abspath(os.path.expanduser(raw_source))):
        source_file = os.path.abspath(os.path.expanduser(raw_source))
        session["workspaceSource"] = {
            "kind": "zip" if source_file.lower().endswith(".zip") else "json",
            "path": source_file,
        }
    capabilities = {
        "schema": "repair-capabilities@1",
        "workspace": "available",
        "targetPart": "available",
        "targetBox": "available" if box_summary else "not connected",
        "cellularDamage": "available" if damage_summary else "not connected",
        "currentPlan": "available" if context.get("currentPlan") else "missing",
        "neighbourGeometry": "not connected",
        "checks": {
            "localGeometry": "available" if box_summary else "limited",
            "neighbourCollision": "requires neighbour geometry",
            "insertionSampled": "requires candidate movement data",
            "toolRadius": "requires a candidate tool",
        },
    }
    session_json = json.dumps(session, indent=2, ensure_ascii=False)
    capabilities_json = json.dumps(capabilities, indent=2, ensure_ascii=False)

    report.append("selected: %s (%s)" % (target.get("label") or selected_id, selected_id))
    report.append("keep box, centers and damage connected to this same member")
    if damage_summary:
        report.append(
            "%d cells; %d at or above threshold %.3f"
            % (len(raw_centers), damage_summary["aboveThresholdCellCount"], gate)
        )
        if len(raw_centers) == 1:
            report.append("WARNING: only one center arrived; set centers and damage inputs to List access")
    else:
        report.append("centers and damage are not connected")
    if tolerance is not None:
        report.append("Rhino tolerance: %g %s" % (tolerance, units))
except Exception as exc:
    report.append("ERROR: %s" % exc)
