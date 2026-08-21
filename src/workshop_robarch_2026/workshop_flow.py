"""Compact, hash-bound records used by the five-component workshop canvas."""

from __future__ import annotations

import copy
import json
from typing import Any

from . import repair_candidate


def _object(value: Any, label: str) -> dict:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    text = str(value or "").strip()
    if not text:
        raise ValueError("{} is empty".format(label))
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("{} must contain one JSON object".format(label))
    return result


def _seal(schema: str, values: dict) -> dict:
    result = {"schema": schema, **copy.deepcopy(values)}
    result["recordHash"] = repair_candidate.stable_json_hash(result)
    return result


def _open(value: Any, schema: str, label: str) -> dict:
    result = _object(value, label)
    if result.get("schema") != schema:
        raise ValueError("{} must use {}".format(label, schema))
    recorded = result.pop("recordHash", None)
    if recorded != repair_candidate.stable_json_hash(result):
        raise ValueError("{} changed after it was created".format(label))
    result["recordHash"] = recorded
    return result


def setup_record(session: dict, context: dict, capabilities: dict) -> dict:
    return _seal(
        "repair-workshop-setup@1",
        {"session": session, "context": context, "capabilities": capabilities},
    )


def validate_setup(value: Any) -> dict:
    result = _open(value, "repair-workshop-setup@1", "setup_json")
    if (result.get("session") or {}).get("schema") != "repair-session@1":
        raise ValueError("setup_json has no repair session")
    if not (result.get("context") or {}).get("targetPart"):
        raise ValueError("setup_json has no selected repair context")
    return result


def repair_record(setup: Any, brief: dict) -> dict:
    setup_obj = validate_setup(setup)
    return _seal(
        "repair-workshop-brief@1",
        {
            "setupHash": setup_obj["recordHash"],
            "session": setup_obj["session"],
            "context": setup_obj["context"],
            "brief": brief,
        },
    )


def validate_repair(value: Any) -> dict:
    result = _open(value, "repair-workshop-brief@1", "repair_json")
    if (result.get("session") or {}).get("schema") != "repair-session@1":
        raise ValueError("repair_json has no repair session")
    if (result.get("brief") or {}).get("schema") != "repair-brief@1":
        raise ValueError("repair_json has no reviewed repair brief")
    return result


def selection_record(
    candidate: dict,
    code: str,
    entity: dict | None = None,
    execution: dict | None = None,
    candidate_set_id: str = "",
) -> dict:
    return _seal(
        "repair-workshop-selection@1",
        {
            "candidateSetId": str(candidate_set_id or ""),
            "candidate": candidate,
            "python": str(code or ""),
            "entity": entity or {},
            "execution": execution or {},
        },
    )


def validate_selection(value: Any, require_execution: bool = True) -> dict:
    result = _open(value, "repair-workshop-selection@1", "selection_json")
    candidate = repair_candidate.normalise_manifest(result.get("candidate"))
    if "build_candidate" not in str(result.get("python") or ""):
        raise ValueError("selection_json has no candidate source")
    if require_execution:
        if (result.get("entity") or {}).get("schema") != "repair-candidate-entities@1":
            raise ValueError("selection_json has no executed entity record")
        if (result.get("execution") or {}).get("schema") != "repair-candidate-execution@1":
            raise ValueError("selection_json has no execution record")
    result["candidate"] = candidate
    return result


def active_record(
    selection: Any,
    facts: dict,
    requirements: dict,
    source: str = "authored",
) -> dict:
    selected = validate_selection(selection)
    return _seal(
        "repair-workshop-active@1",
        {
            "source": str(source or "authored"),
            "selectionHash": selected["recordHash"],
            "candidate": selected["candidate"],
            "python": selected["python"],
            "entity": selected["entity"],
            "execution": selected["execution"],
            "facts": facts,
            "requirements": requirements,
        },
    )


def validate_active(value: Any) -> dict:
    result = _open(value, "repair-workshop-active@1", "active_json")
    candidate = repair_candidate.normalise_manifest(result.get("candidate"))
    if (result.get("facts") or {}).get("schema") != "repair-candidate-facts@1":
        raise ValueError("active_json has no measured facts")
    if (result.get("requirements") or {}).get("schema") != "repair-requirements@1":
        raise ValueError("active_json has no resolved requirements")
    for key in ("entity", "execution", "facts", "requirements"):
        if (result.get(key) or {}).get("candidateId") != candidate["id"]:
            raise ValueError("active_json {} belongs to another candidate".format(key))
    result["candidate"] = candidate
    return result


__all__ = [
    "active_record",
    "repair_record",
    "selection_record",
    "setup_record",
    "validate_active",
    "validate_repair",
    "validate_selection",
    "validate_setup",
]
