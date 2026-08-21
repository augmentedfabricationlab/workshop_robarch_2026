"""Small, Rhino-free helpers for reading Repair Workspace exports."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import zipfile
from typing import Any, Optional


class WorkspaceError(ValueError):
    """Raised when a Workspace source has no usable Workspace object."""


def _workspace_text(path: str) -> str:
    if path.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(path, "r") as archive:
                names = [n for n in archive.namelist() if not n.endswith("/")]
                name = next(
                    (n for n in names if n.replace("\\", "/").lower() == "workspace.json"),
                    None,
                )
                if name is None:
                    name = next(
                        (
                            n
                            for n in names
                            if n.replace("\\", "/").lower().endswith("/workspace.json")
                        ),
                        None,
                    )
                if name is None:
                    raise WorkspaceError("exported ZIP contains no workspace.json")
                return archive.read(name).decode("utf-8-sig")
        except (OSError, zipfile.BadZipFile, UnicodeError) as exc:
            raise WorkspaceError("cannot read Workspace ZIP: %s" % exc) from exc

    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return handle.read()
    except OSError as exc:
        raise WorkspaceError("cannot read Workspace file: %s" % exc) from exc


def load_workspace(value: Any) -> dict:
    """Read a dict, JSON text, JSON path, or exported Workspace ZIP path."""
    if isinstance(value, dict):
        workspace = copy.deepcopy(value)
    else:
        text = str(value or "").strip()
        if not text:
            raise WorkspaceError("connect Workspace JSON or an exported Workspace ZIP")
        path = os.path.abspath(os.path.expanduser(text))
        if os.path.isfile(path):
            text = _workspace_text(path)
        elif text.lower().endswith((".json", ".zip")) and not text.startswith(("{", "[")):
            raise WorkspaceError("Workspace path does not exist: %s" % text)
        try:
            workspace = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise WorkspaceError("Workspace JSON is invalid: %s" % exc) from exc

    if not isinstance(workspace, dict):
        raise WorkspaceError("Workspace JSON must contain one object")
    if not isinstance(workspace.get("instance"), dict):
        raise WorkspaceError("Workspace has no instance object")
    return workspace


def workspace_parts(workspace: Any) -> list[dict]:
    """Return validated parts in their Workspace order."""
    ws = load_workspace(workspace)
    raw = ws["instance"].get("parts")
    if not isinstance(raw, list):
        raise WorkspaceError("Workspace instance has no parts list")

    result, seen = [], set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise WorkspaceError("Workspace part %d is not an object" % index)
        part_id = str(item.get("id") or "").strip()
        if not part_id:
            raise WorkspaceError("Workspace part %d has no id" % index)
        if part_id in seen:
            raise WorkspaceError("duplicate Workspace part id: %s" % part_id)
        seen.add(part_id)
        result.append(item)
    return result


def part_options(workspace: Any) -> list[dict]:
    """Return stable ``id``/``label`` pairs suitable for a GH Value List."""
    return [
        {"id": str(part["id"]), "label": str(part.get("label") or part["id"])}
        for part in workspace_parts(workspace)
    ]


def find_part(workspace: Any, part_id: str) -> dict:
    wanted = str(part_id or "").strip()
    for part in workspace_parts(workspace):
        if str(part["id"]) == wanted:
            return part
    available = ", ".join(option["id"] for option in part_options(workspace)) or "(none)"
    raise WorkspaceError("beam_id %r is unavailable; choose one of: %s" % (wanted, available))


def part_context(workspace: Any, part_id: str) -> dict:
    """Extract the selected part, its neighbours, conditions, and Action Model."""
    ws = load_workspace(workspace)
    target = find_part(ws, part_id)
    parts = workspace_parts(ws)
    by_id = {str(part["id"]): part for part in parts}
    neighbours = [
        by_id[str(ref)]
        for ref in (target.get("connections") or [])
        if str(ref) in by_id
    ]
    scope_ids = {str(target["id"])}
    scope_ids.update(str(part["id"]) for part in neighbours)
    conditions = [
        item
        for item in (ws.get("conditions") or [])
        if str(item.get("partRef")) in scope_ids
    ]
    condition_ids = {str(item.get("id")) for item in conditions if item.get("id")}
    evidence_ids = {
        str(ref)
        for condition in conditions
        for ref in (condition.get("evidenceRefs") or [])
    }
    evidence = []
    for item in ws.get("evidence") or []:
        attached = item.get("attachedTo")
        attached_id = attached.get("id") if isinstance(attached, dict) else attached
        if (
            str(item.get("id")) in evidence_ids
            or str(attached_id) in scope_ids
            or str(attached_id) in condition_ids
        ):
            evidence.append(
                {
                    key: copy.deepcopy(item[key])
                    for key in (
                        "id",
                        "kind",
                        "attachedTo",
                        "capturedAt",
                        "text",
                        "measurement",
                        "fileName",
                        "mimeType",
                    )
                    if item.get(key) is not None
                }
            )

    plans = list(ws.get("plans") or [])
    current = next(
        (plan for plan in plans if str(plan.get("id")) == str(ws.get("currentPlanId"))),
        plans[0] if len(plans) == 1 else None,
    )
    keep = (
        "id",
        "label",
        "origin",
        "dimensions",
        "rotation",
        "connections",
        "material",
        "status",
        "notes",
        "function",
    )

    def summary(part: dict) -> dict:
        return {key: copy.deepcopy(part[key]) for key in keep if key in part}

    return {
        "schemaVersion": ws.get("schemaVersion"),
        "instance": {
            key: copy.deepcopy(ws["instance"].get(key))
            for key in ("id", "name", "location", "provenance", "notes")
            if ws["instance"].get(key) is not None
        },
        "targetPart": summary(target),
        "connectedParts": [summary(part) for part in neighbours],
        "conditions": copy.deepcopy(conditions),
        "evidence": evidence,
        "currentPlan": copy.deepcopy(current),
    }


def workspace_digest(workspace: Any) -> str:
    """Content hash used to mark downstream candidates stale after an edit."""
    ws = load_workspace(workspace)
    payload = json.dumps(ws, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def json_digest(value: Any) -> str:
    """Stable hash for JSON-compatible session data."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def damage_field(centers: Any, damage: Any, threshold: float) -> dict:
    """Validate aligned cell data and return a concise summary plus a data hash."""
    points = [] if centers is None else list(centers)
    values = [] if damage is None else list(damage)
    if len(points) != len(values):
        raise WorkspaceError(
            "centers/damage length mismatch: %d centers and %d values"
            % (len(points), len(values))
        )
    gate = float(threshold)
    if not 0.0 <= gate <= 1.0:
        raise WorkspaceError("threshold must stay between 0 and 1")

    def xyz(point: Any) -> list[float]:
        if hasattr(point, "X"):
            result = [float(point.X), float(point.Y), float(point.Z)]
        else:
            result = [float(point[0]), float(point[1]), float(point[2])]
        if not all(math.isfinite(number) for number in result):
            raise WorkspaceError("centers contain a non-finite coordinate")
        return result

    coordinates = [xyz(point) for point in points]
    numbers = [float(value) for value in values]
    if not all(math.isfinite(value) for value in numbers):
        raise WorkspaceError("damage contains a non-finite value")
    if any(value < 0.0 or value > 1.0 for value in numbers):
        raise WorkspaceError("damage values must stay between 0 and 1")
    selected = [point for point, value in zip(coordinates, numbers) if value >= gate]

    def bounds(items: list[list[float]]) -> Optional[dict]:
        if not items:
            return None
        return {
            "min": [min(point[axis] for point in items) for axis in range(3)],
            "max": [max(point[axis] for point in items) for axis in range(3)],
        }

    summary = None
    if coordinates:
        summary = {
            "threshold": gate,
            "cellCount": len(coordinates),
            "aboveThresholdCellCount": len(selected),
            "minimumDamage": min(numbers),
            "maximumDamage": max(numbers),
            "meanDamage": sum(numbers) / len(numbers),
            "worldBounds": bounds(coordinates),
            "aboveThresholdWorldBounds": bounds(selected),
        }
    serialised = [[point, value] for point, value in zip(coordinates, numbers)]
    return {"summary": summary, "dataHash": json_digest(serialised)}
