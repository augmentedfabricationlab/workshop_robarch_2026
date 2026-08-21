"""GH Python 3 -- WORKSPACE PART PICKER.

Inputs
------
workspace_json  dict/str  Workspace JSON, JSON path, or exported ZIP path
picker          str       wire a Grasshopper Value List here
refresh         bool      press once after changing the Workspace
repo            str       optional workshop repository override

Outputs
-------
beam_id         str       selected Workspace part id
part_ids        str[]     all available part ids
report          str[]     short setup diagnostics
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


def _value_list():
    import Grasshopper.Kernel.Special as ghs

    parameter = next(
        (p for p in ghenv.Component.Params.Input if p.NickName == "picker"), None
    )
    return next(
        (source for source in (parameter.Sources if parameter else [])
         if isinstance(source, ghs.GH_ValueList)),
        None,
    )


def _expression_value(expression):
    try:
        return str(json.loads(str(expression)))
    except Exception:
        return str(expression).strip().strip('"')


def _fill_value_list(options):
    """Update only when contents changed, avoiding a refresh solution loop."""
    import Grasshopper.Kernel.Special as ghs

    source = _value_list()
    if source is None:
        raise ValueError("wire a Grasshopper Value List into picker")
    previous = None
    if source.SelectedItems.Count:
        previous = _expression_value(source.SelectedItems[0].Value)
    expected = [(item["label"], item["id"]) for item in options]
    ids = [part_id for _, part_id in expected]
    current = [
        (str(item.Name), _expression_value(item.Value))
        for item in source.ListItems
    ]
    if current == expected:
        selected = previous if previous in ids else ids[0]
        if previous != selected:
            source.SelectItem(ids.index(selected))
            source.ExpireSolution(True)
            return selected, True
        return selected, False

    source.ListItems.Clear()
    for label, part_id in expected:
        source.ListItems.Add(ghs.GH_ValueListItem(label, json.dumps(part_id)))
    selected = previous if previous in ids else ids[0]
    source.SelectItem(ids.index(selected))
    source.ExpireSolution(True)
    return selected, True


beam_id = str(globals().get("picker") or "").strip()
part_ids = []
report = ["Workspace Part Picker"]

try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    from workshop_robarch_2026 import workspace_io

    options = workspace_io.part_options(globals().get("workspace_json"))
    part_ids = [item["id"] for item in options]
    if not options:
        raise ValueError("Workspace contains no parts")
    if bool(globals().get("refresh")):
        selected, changed = _fill_value_list(options)
        beam_id = selected or beam_id
        report.append("dropdown filled" if changed else "dropdown already current")
    elif beam_id not in part_ids:
        beam_id = part_ids[0]
        report.append("press refresh once to fill the connected Value List")
    report.append("selected: %s" % beam_id)
except Exception as exc:
    beam_id = ""
    report.append("ERROR: %s" % exc)
