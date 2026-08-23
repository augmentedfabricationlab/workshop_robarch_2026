"""Reading the participant's Workspace and describing the damage.

Two jobs, no cleverness: pull the plan for one member out of the exported ZIP,
and write the damage cells out as numbers a model can read. The damage is never
summarised into statistics -- the grid is small enough to send whole, and a
bounding box throws away exactly the shape that decides the joint.
"""
from __future__ import annotations

import json
import os
import zipfile

import numpy as np


# ------------------------------------------------------------- the workspace


def load_workspace(value):
    """dict, JSON text, .json path or exported .zip -> the workspace dict."""
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError("connect the Workspace JSON or the exported ZIP")
    path = os.path.abspath(os.path.expanduser(text))
    if os.path.isfile(path):
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path, "r") as archive:
                text = archive.read("workspace.json").decode("utf-8-sig")
        else:
            with open(path, "r", encoding="utf-8-sig") as handle:
                text = handle.read()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("the Workspace must be one JSON object")
    return parsed


def parts(workspace) -> list:
    return list((workspace.get("instance") or {}).get("parts") or [])


def part_options(workspace) -> list:
    """[{id, label}] for the Value List, in workspace order."""
    out = []
    for part in parts(workspace):
        part_id = str(part.get("id") or "").strip()
        if part_id:
            out.append({"id": part_id, "label": str(part.get("label") or part_id)})
    return out


def find_part(workspace, beam_id):
    for part in parts(workspace):
        if str(part.get("id")) == str(beam_id):
            return part
    raise ValueError("no part %r in the Workspace" % beam_id)


def matches_geometry(part, frame, tolerance=0.02) -> tuple:
    """Does the picked part have the size of the beam BEAM CELLS handed over?

    Nothing checked this before, so pointing the picker at the wrong post
    analysed the wrong timber in silence. Returns (ok, note).
    """
    dims = part.get("dimensions") or {}
    have = sorted(float(v) for v in (frame["width"], frame["length"], frame["height"]))
    want = sorted(float(dims.get(k, 0.0)) for k in ("width", "height", "depth"))
    if not any(want):
        return True, "the part carries no dimensions; nothing to check against"
    diff = max(abs(a - b) for a, b in zip(have, want))
    if diff <= float(tolerance):
        return True, "part and cellularised beam agree to %.0f mm" % (1000 * diff)
    return False, ("part is %s m but the connected beam is %.2f x %.2f x %.2f m"
                   % (["%.2f" % v for v in want],
                      frame["width"], frame["length"], frame["height"]))


def ambiguous(workspace, frame, tolerance=0.02) -> list:
    """Other parts the connected beam would fit equally well."""
    out = []
    for part in parts(workspace):
        ok, _ = matches_geometry(part, frame, tolerance)
        if ok and (part.get("dimensions") or {}):
            out.append(str(part.get("id")))
    return out


def plan_for(workspace, beam_id) -> dict:
    """The current plan, and the steps that touch this member."""
    plans = list(workspace.get("plans") or [])
    current = next(
        (p for p in plans if str(p.get("id")) == str(workspace.get("currentPlanId"))),
        plans[0] if len(plans) == 1 else None,
    )
    if current is None:
        return {}
    steps = list(current.get("steps") or [])
    mine = [s for s in steps
            if str(beam_id) in [str(v) for v in (s.get("affectedPartRefs") or [])]]
    return {
        "id": current.get("id"),
        "label": current.get("label"),
        "intent": current.get("intent"),
        "constraints": current.get("constraints"),
        "stepsForThisMember": [
            {"id": s.get("id"), "title": s.get("title") or s.get("action"),
             "description": s.get("description"), "affects": s.get("affectedPartRefs")}
            for s in mine
        ],
        "sequence": [{"id": s.get("id"), "title": s.get("title") or s.get("action")}
                     for s in steps],
    }


def conditions_for(workspace, beam_id) -> list:
    return [c for c in (workspace.get("conditions") or [])
            if str(c.get("partRef")) == str(beam_id)]


def evidence_for(workspace, conditions, beam_id=None) -> list:
    """Every piece of evidence that belongs to these conditions.

    The link is written from BOTH ends and in practice only one of them is
    filled in. A condition may list `evidenceRefs`, and an evidence record may
    carry `attachedTo: {"type": "condition", "id": ...}` or name the condition it
    confirms or refutes. Exports seen so far leave `evidenceRefs` empty and put
    everything on the evidence side, so reading only the condition's list
    returns nothing and the photographs never reach a model call.

    Pass `beam_id` to also pick up evidence attached to the part itself.
    """
    ids = {str(c.get("id")) for c in conditions}
    wanted = set()
    for condition in conditions:
        wanted.update(str(v) for v in (condition.get("evidenceRefs") or []))

    out = []
    for item in workspace.get("evidence") or []:
        attached = item.get("attachedTo") or {}
        target = str(attached.get("id") or "")
        kind = str(attached.get("type") or "")
        linked = (
            str(item.get("id")) in wanted
            or (kind == "condition" and target in ids)
            or (beam_id is not None and kind == "part" and target == str(beam_id))
            or str(item.get("confirmsConditionRef") or "") in ids
            or str(item.get("refutesConditionRef") or "") in ids
        )
        if not linked:
            continue
        record = {k: item.get(k) for k in
                  ("id", "kind", "text", "measurement", "capturedAt", "fileName",
                   "mimeType", "url") if item.get(k) is not None}
        if target:
            record["about"] = target
        out.append(record)
    return out


def fill_picker(component, options, name: str = "picker"):
    """Fill a connected Grasshopper Value List with the Workspace's parts.

    -> (chosen id, whether the list was rewritten). Keeps the current choice if
    it still exists, so re-running does not jump to another member.
    """
    import Grasshopper.Kernel.Special as ghs

    def value(item):
        try:
            return str(json.loads(str(item)))
        except Exception:
            return str(item).strip().strip('"')

    parameter = next((p for p in component.Params.Input if p.NickName == name), None)
    source = next((s for s in (parameter.Sources if parameter else [])
                   if isinstance(s, ghs.GH_ValueList)), None)
    if source is None:
        raise ValueError("wire a Grasshopper Value List into %s" % name)

    before = value(source.SelectedItems[0].Value) if source.SelectedItems.Count else None
    wanted = [(item["label"], item["id"]) for item in options]
    ids = [item[1] for item in wanted]
    chosen = before if before in ids else ids[0]
    if [(str(i.Name), value(i.Value)) for i in source.ListItems] == wanted:
        return chosen, False
    source.ListItems.Clear()
    for label, part_id in wanted:
        source.ListItems.Add(ghs.GH_ValueListItem(label, json.dumps(part_id)))
    source.SelectItem(ids.index(chosen))
    source.ExpireSolution(True)
    return chosen, True


IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".webp": "image/webp", ".gif": "image/gif"}


def evidence_images(value, evidence, limit: int = 6,
                    max_bytes: int = 6_000_000) -> tuple:
    """Photographs named by the evidence records, ready to attach to a call.

    -> (attachments, notes)

    The exported Workspace is a ZIP and the photographs travel inside it. When
    the workspace was handed over as loose JSON they are looked for beside it.
    Anything not found is named in the notes rather than raised: a missing
    photograph makes for a thinner answer, not a broken run.
    """
    wanted = [item for item in evidence or []
              if str(item.get("kind") or "").lower() == "photo"
              or os.path.splitext(str(item.get("fileName") or ""))[1].lower() in IMAGE_TYPES]
    if not wanted:
        return [], []

    text = value if isinstance(value, str) else ""
    path = os.path.abspath(os.path.expanduser(text.strip())) if text.strip() else ""
    names, reader, where = [], None, None
    if path and os.path.isfile(path) and path.lower().endswith(".zip"):
        archive = zipfile.ZipFile(path, "r")
        names = archive.namelist()
        reader, where = archive.read, "the ZIP"
    elif path and os.path.isfile(path):
        root = os.path.dirname(path)
        for base, _, files in os.walk(root):
            for name in files:
                names.append(os.path.relpath(os.path.join(base, name), root))

        def reader(name, _root=root):
            with open(os.path.join(_root, name), "rb") as handle:
                return handle.read()

        where = "beside the workspace file"

    if reader is None:
        return [], ["%d photograph(s) are referenced but the workspace was given "
                    "as text, not as a path, so there is nowhere to read them from"
                    % len(wanted)]

    # The archive names its photographs by EVIDENCE ID, not by `fileName` --
    # several records share the fileName "image.jpg", so matching on that finds
    # the wrong picture or none. Id first, filename only as a fallback.
    by_stem, by_base = {}, {}
    for name in names:
        base = os.path.basename(name)
        if os.path.splitext(base)[1].lower() in IMAGE_TYPES:
            by_stem.setdefault(os.path.splitext(base)[0].lower(), name)
            by_base.setdefault(base.lower(), name)

    out, missing, big, local_only = [], [], [], []
    for item in wanted:
        item_id = str(item.get("id") or "")
        file_name = str(item.get("fileName") or "")
        found = (by_stem.get(item_id.lower())
                 or by_base.get(os.path.basename(file_name).lower()))
        if found is None:
            # `idb://` means it never left the browser's own storage
            (local_only if str(item.get("url") or "").startswith("idb://")
             else missing).append(item_id or file_name)
            continue
        if len(out) >= int(limit):
            break
        try:
            data = reader(found)
        except Exception:
            missing.append(item_id or file_name)
            continue
        if len(data) > int(max_bytes):
            big.append(item_id or file_name)
            continue
        out.append({
            "id": item_id or found,
            "name": "%s (%s)" % (found, item.get("about") or file_name or "evidence"),
            "data": data,
            "mimeType": str(item.get("mimeType")
                            or IMAGE_TYPES.get(os.path.splitext(found)[1].lower())
                            or "image/jpeg"),
        })

    notes = ["%d photograph(s) attached from %s" % (len(out), where)] if out else []
    if local_only:
        notes.append("%d photograph(s) were never exported -- their url is idb://, "
                     "so they live only in the browser that captured them: %s"
                     % (len(local_only), ", ".join(local_only[:4])))
    if missing:
        notes.append("referenced but not in the export: %s" % ", ".join(missing[:4]))
    if big:
        notes.append("too large to send: %s" % ", ".join(big[:4]))
    if len(wanted) > int(limit):
        notes.append("%d more photograph(s) not sent -- limit is %d"
                     % (len(wanted) - int(limit), int(limit)))
    return out, notes


# ----------------------------------------------------------------- the damage


def damage_text(mem: dict, threshold: float, pad: int = 2) -> str:
    """The cells as numbers, station by station along the member.

    Stations with no damage are collapsed to one line -- there are usually many
    of them and they carry no information. Everything else is printed whole.
    """
    nu, nv, nw = mem["grid"]
    values = np.rint(mem["damage"].reshape(nu, nv, nw) * 100).astype(int)
    local = mem["local"].reshape(nu, nv, nw, 3)
    hot = np.any(values >= int(round(float(threshold) * 100)), axis=(0, 2))
    show = np.zeros(nv, bool)
    for j in np.flatnonzero(hot):
        show[max(0, j - pad):min(nv, j + pad + 1)] = True

    lines = [
        "member %.0f x %.0f x %.0f mm (width x length x height)"
        % (1000 * mem["frame"]["width"], 1000 * mem["frame"]["length"],
           1000 * mem["frame"]["height"]),
        "grid %d across x %d along x %d deep, cell %.0f x %.0f x %.0f mm"
        % (nu, nv, nw,
           1000 * mem["frame"]["width"] / nu, 1000 * mem["frame"]["length"] / nv,
           1000 * mem["frame"]["height"] / nw),
        "damage 0-100 per cell; each block is one station along the member,",
        "rows run across the width (u), columns across the height (w).",
        "",
    ]
    quiet = 0
    for j in range(nv):
        if not show[j]:
            quiet += 1
            continue
        if quiet:
            lines.append("... %d station(s) with no damage ..." % quiet)
            quiet = 0
        v = float(local[0, j, 0, 1]) * 1000.0
        lines.append("v = %.0f mm" % v)
        for i in range(nu):
            lines.append("   " + " ".join("%3d" % values[i, j, k] for k in range(nw)))
    if quiet:
        lines.append("... %d station(s) with no damage ..." % quiet)
    return "\n".join(lines)


# ------------------------------------------------------------------ the bundle


def setup(workspace, beam_id, mem: dict, threshold: float,
          tolerance: float, units: str, around=None) -> dict:
    """Everything the brief needs, in one record."""
    part = find_part(workspace, beam_id)
    conditions = conditions_for(workspace, beam_id)
    frame = mem["frame"]
    rot = mem["damage"] >= float(threshold)
    return {
        "schema": "repair-setup@2",
        "beamId": str(beam_id),
        "part": {k: part.get(k) for k in
                 ("id", "label", "dimensions", "rotation", "connections",
                  "material", "status", "notes", "function") if part.get(k) is not None},
        "connectedParts": [str(v) for v in (part.get("connections") or [])],
        "plan": plan_for(workspace, beam_id),
        "neighbours": list(around or []),
        "conditions": conditions,
        "evidence": evidence_for(workspace, conditions, beam_id),
        "member": {
            "widthMm": round(1000 * frame["width"], 1),
            "lengthMm": round(1000 * frame["length"], 1),
            "heightMm": round(1000 * frame["height"], 1),
            "grid": list(mem["grid"]),
            "cellCount": int(len(mem["damage"])),
            "modelUnits": units,
            "modelTolerance": tolerance,
        },
        "damage": {
            "threshold": float(threshold),
            "cellsAtOrAbove": int(rot.sum()),
            "grid": damage_text(mem, threshold),
        },
    }
