"""Candidate-set records for repeated authorship under one repair brief."""

from __future__ import annotations

import copy
import json
from typing import Any, Iterable

from . import repair_candidate


SCHEMA = "repair-candidate-set@1"
MIN_COUNT = 2
MAX_COUNT = 5


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


def variation_count(value: Any, default: int = 3) -> int:
    """Return a workshop-sized number of independent authorship runs."""
    if value in (None, ""):
        return default
    count = int(value)
    if count < MIN_COUNT or count > MAX_COUNT:
        raise ValueError("variation_count must be between 2 and 5")
    return count


def _unique_id(candidate_id: str, used: set[str], index: int) -> str:
    if candidate_id not in used:
        return candidate_id
    base = candidate_id
    suffix = index + 1
    candidate_id = "{}_run_{:02d}".format(base, suffix)
    while candidate_id in used:
        suffix += 1
        candidate_id = "{}_run_{:02d}".format(base, suffix)
    return candidate_id


def build_candidate_set(
    results: Iterable[dict],
    session: Any,
    brief: Any,
    requested_count: int,
    model: str,
    errors: Iterable[str] = (),
) -> dict:
    """Package several complete LLM-authored responses for one unchanged brief."""
    session_obj = _object(session, "session_json")
    brief_obj = _object(brief, "brief_json")
    entries, used, design_hashes = [], set(), set()
    error_list = [str(item) for item in errors]
    for index, raw in enumerate(results):
        response = _object(raw, "candidate response")
        candidate = repair_candidate.normalise_manifest(response.get("candidate"))
        candidate_id = _unique_id(candidate["id"], used, index)
        if candidate_id != candidate["id"]:
            candidate["id"] = candidate_id
        used.add(candidate_id)
        code = str(response.get("python") or "")
        if "build_candidate" not in code:
            raise ValueError("candidate {} has no build_candidate function".format(candidate_id))
        design_hash = repair_candidate.stable_json_hash(
            {
                "python": code,
                "outputs": candidate.get("outputs") or [],
                "analysis": candidate.get("analysis") or {},
            }
        )
        if design_hash in design_hashes:
            error_list.append(
                "run {} duplicated an earlier authored construction".format(
                    response.get("authorshipRun") or index + 1
                )
            )
            continue
        design_hashes.add(design_hash)
        run_number = int(response.get("authorshipRun") or index + 1)
        candidate["authorship"] = {
            "schema": "repair-candidate-authorship@1",
            "run": run_number,
            "requestedCount": int(requested_count),
            "designHash": design_hash,
        }
        entries.append(
            {
                "id": candidate_id,
                "label": candidate.get("title") or "Candidate {}".format(index + 1),
                "summary": str(response.get("summary") or candidate.get("authorSummary") or ""),
                "candidate": candidate,
                "python": code,
                "authorshipRun": run_number,
                "designHash": design_hash,
            }
        )
    if len(entries) < MIN_COUNT:
        detail = "; ".join(error_list)
        raise ValueError(
            "at least two candidate variations must be authored{}".format(
                ": " + detail if detail else ""
            )
        )
    source = {
        "workspaceHash": session_obj.get("workspaceHash"),
        "contextHash": session_obj.get("contextHash"),
        "beamId": session_obj.get("beamId"),
        "briefId": brief_obj.get("id"),
        "briefHash": repair_candidate.stable_json_hash(brief_obj),
    }
    payload = {
        "schema": SCHEMA,
        "source": source,
        "requestedCount": int(requested_count),
        "completedCount": len(entries),
        "model": str(model or ""),
        "candidates": entries,
        "errors": error_list,
    }
    payload["id"] = "candidate_set_{}".format(
        repair_candidate.stable_json_hash(payload)[:12]
    )
    return validate_candidate_set(payload, session_obj, brief_obj)


def validate_candidate_set(value: Any, session: Any = None, brief: Any = None) -> dict:
    result = _object(value, "candidate_set_json")
    if result.get("schema") != SCHEMA:
        raise ValueError("candidate_set_json must use {}".format(SCHEMA))
    entries = result.get("candidates")
    if not isinstance(entries, list) or len(entries) < MIN_COUNT:
        raise ValueError("candidate_set_json needs at least two candidates")
    ids = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError("candidate_set candidates must be objects")
        candidate = repair_candidate.normalise_manifest(entry.get("candidate"))
        if str(entry.get("id")) != candidate["id"]:
            raise ValueError("candidate-set entry id does not match its manifest")
        if "build_candidate" not in str(entry.get("python") or ""):
            raise ValueError("candidate-set entry {} has no executable code".format(index + 1))
        entry["candidate"] = candidate
        ids.append(candidate["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("candidate-set ids must be unique")
    requested = variation_count(result.get("requestedCount"))
    if int(result.get("completedCount", -1)) != len(entries):
        raise ValueError("candidate-set completedCount is inconsistent")
    if len(entries) > requested:
        raise ValueError("candidate-set completedCount exceeds requestedCount")
    if not isinstance(result.get("errors"), list):
        raise ValueError("candidate-set errors must be a list")
    source = result.get("source") or {}
    if session is not None:
        session_obj = _object(session, "session_json")
        for key in ("workspaceHash", "contextHash", "beamId"):
            if source.get(key) != session_obj.get(key):
                raise ValueError("candidate set does not match active {}".format(key))
    if brief is not None:
        brief_obj = _object(brief, "brief_json")
        if source.get("briefId") != brief_obj.get("id"):
            raise ValueError("candidate set does not match the reviewed brief id")
        if source.get("briefHash") != repair_candidate.stable_json_hash(brief_obj):
            raise ValueError("candidate set does not match the reviewed brief contents")
    unhashed = copy.deepcopy(result)
    unhashed.pop("id", None)
    expected_id = "candidate_set_{}".format(
        repair_candidate.stable_json_hash(unhashed)[:12]
    )
    if result.get("id") != expected_id:
        raise ValueError("candidate_set_json changed after authorship")
    repair_candidate.stable_json_hash(result)
    return result


def candidate_options(value: Any) -> list[dict]:
    result = validate_candidate_set(value)
    return [
        {"id": item["id"], "label": "{:02d} — {}".format(index + 1, item["label"])}
        for index, item in enumerate(result["candidates"])
    ]


def select_candidate(value: Any, candidate_id: str = "") -> dict:
    result = validate_candidate_set(value)
    selected = str(candidate_id or "").strip()
    return next(
        (item for item in result["candidates"] if item["id"] == selected),
        result["candidates"][0],
    )


__all__ = [
    "MAX_COUNT",
    "MIN_COUNT",
    "SCHEMA",
    "build_candidate_set",
    "candidate_options",
    "select_candidate",
    "validate_candidate_set",
    "variation_count",
]
