"""Build and optionally save a human decision about one repair candidate."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import repair_candidate, workspace_io


def _object(value: Any, label: str) -> dict:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    text = str(value or "").strip()
    if not text:
        return {}
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("{} must contain one JSON object".format(label))
    return parsed


def build_proposal(
    candidate: Any,
    code: str,
    facts: Any,
    requirements: Any,
    decision: str,
    note: str = "",
    geometry_artifact: Any = None,
) -> tuple[dict, bool, list[str]]:
    """Create a replayable proposal plus an explicit human decision."""
    manifest = repair_candidate.normalise_manifest(_object(candidate, "candidate_json"))
    fact_envelope = _object(facts, "facts_json")
    fact_list = fact_envelope.get("facts") or []
    requirements_obj = _object(requirements, "requirements_json")
    artifact = _object(geometry_artifact, "geometry_artifact") if geometry_artifact else {}
    if requirements_obj.get("schema") != "repair-requirements@1":
        raise ValueError("requirements_json must use repair-requirements@1")
    for key in ("compliance", "advisory"):
        if not isinstance(requirements_obj.get(key), list):
            raise ValueError("requirements_json.{} must be a list".format(key))
    beam_id = str(
        fact_envelope.get("beamId")
        or (fact_envelope.get("session") or {}).get("beamId")
        or ""
    )
    fact_session = fact_envelope.get("session") or {}
    manifest = repair_candidate.validate_scope(
        manifest,
        beam_id=beam_id,
        workspace_hash=fact_session.get("workspaceHash"),
        context_hash=fact_session.get("contextHash"),
    )
    expected_hashes = {
        "candidateId": manifest["id"],
        "manifestHash": repair_candidate.stable_json_hash(manifest),
        "codeHash": repair_candidate.stable_json_hash(str(code or "")),
    }
    for key, expected in expected_hashes.items():
        if fact_envelope.get(key) != expected:
            raise ValueError("facts_json {} does not match the active candidate".format(key))
    for key in ("sessionHash", "geometryHash", "entitiesHash", "analysisInputHash"):
        if not fact_envelope.get(key):
            raise ValueError("facts_json is missing {} from execution".format(key))
    if artifact:
        required = (
            "schema", "path", "sha256", "geometryHash", "entitiesHash", "entityCount"
        )
        if artifact.get("schema") != "repair-geometry-artifact@1" or any(
            artifact.get(key) in (None, "") for key in required
        ):
            raise ValueError("geometry_artifact is incomplete")
        if artifact.get("geometryHash") != fact_envelope.get("geometryHash"):
            raise ValueError("geometry_artifact does not match measured candidate geometry")
        if artifact.get("entitiesHash") != fact_envelope.get("entitiesHash"):
            raise ValueError("geometry_artifact metadata does not match measured entities")
    requirement_identity = {
        "candidateId": manifest["id"],
        "beamId": beam_id,
        "manifestHash": fact_envelope.get("manifestHash"),
        "codeHash": fact_envelope.get("codeHash"),
        "sessionHash": fact_envelope.get("sessionHash"),
        "geometryHash": fact_envelope.get("geometryHash"),
        "entitiesHash": fact_envelope.get("entitiesHash"),
        "analysisInputHash": fact_envelope.get("analysisInputHash"),
        "factsHash": repair_candidate.stable_json_hash(fact_list),
    }
    for key, expected in requirement_identity.items():
        if requirements_obj.get(key) != expected:
            raise ValueError(
                "requirements_json {} does not match facts_json".format(key)
            )
    from . import candidate_analysis

    expected_requirements = candidate_analysis.requirement_results(
        manifest,
        repair_candidate.normalise_facts(fact_list),
        {key: value for key, value in requirement_identity.items() if key != "factsHash"},
    )
    if repair_candidate.stable_json_hash(requirements_obj) != repair_candidate.stable_json_hash(
        expected_requirements
    ):
        raise ValueError("requirements_json was changed after local analysis")
    snapshot = repair_candidate.candidate_record(
        manifest,
        code=str(code or ""),
        facts=fact_list,
        metadata={
            "beamId": fact_envelope.get("beamId"),
            "tolerance": fact_envelope.get("tolerance"),
            "session": fact_envelope.get("session") or {},
        },
    )
    snapshot["requirements"] = copy.deepcopy(requirements_obj)
    version_id = "v_{}".format(repair_candidate.stable_json_hash(snapshot)[:12])
    version = repair_candidate.version_record(
        snapshot,
        version_id,
        parent_version_id=manifest.get("parentVersionId"),
        note=str(manifest.get("revisionNote") or ""),
        metadata={
            "revisionOf": manifest.get("revisionOf"),
            "parentManifestHash": manifest.get("parentManifestHash"),
        },
    )
    choice = str(decision or "").strip().lower()
    if not choice:
        choice = "undecided"
    decided = repair_candidate.decision_record(
        manifest["id"], version_id, choice, rationale=str(note or "")
    )
    proposal = {
        "schema": "repair-geometry-proposal@1",
        "id": "proposal_{}".format(repair_candidate.stable_json_hash([snapshot, decided])[:12]),
        "candidateId": manifest["id"],
        "partRefs": manifest["partRefs"],
        "actionRefs": manifest["actionRefs"],
        "candidate": snapshot,
        "version": version,
        "facts": fact_envelope,
        "requirements": requirements_obj,
        "decision": decided,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
    }
    if artifact:
        proposal["geometryArtifact"] = artifact
    unresolved = [
        item for item in requirements_obj.get("compliance", [])
        if (item.get("evaluation") or {}).get("status") in ("unknown", "not_satisfied")
    ]
    report = ["decision recorded: {}".format(choice)]
    if unresolved:
        report.append("{} binding requirement(s) remain unknown or unsatisfied".format(len(unresolved)))
    if not str(note or "").strip():
        report.append("add a decision_note so the participant reasoning is recorded")
    if choice == "accept" and not artifact:
        report.append("accepted geometry will become complete when its 3dm artifact is saved")
    report.append("ready describes record completeness; expert approvals remain separate")
    ready = bool(
        manifest
        and str(code or "").strip()
        and fact_list
        and choice != "undecided"
        and str(note or "").strip()
        and (choice != "accept" or bool(artifact))
    )
    return proposal, ready, report


def add_proposal(workspace: Any, proposal: dict) -> dict:
    """Add or update one proposal without changing existing Workspace fields."""
    result = workspace_io.load_workspace(workspace)
    recorded_workspace_hash = (
        ((proposal.get("facts") or {}).get("session") or {}).get("workspaceHash")
    )
    if recorded_workspace_hash and workspace_io.workspace_digest(result) != recorded_workspace_hash:
        raise ValueError("Workspace changed since measurement; rebuild Repair Context")
    manifest = ((proposal.get("candidate") or {}).get("manifest") or {})
    part_ids = [part.get("id") for part in workspace_io.workspace_parts(result)]
    action_ids = [
        step.get("id")
        for plan in (result.get("plans") or [])
        for step in (plan.get("steps") or [])
        if step.get("id")
    ]
    repair_candidate.validate_scope(
        manifest,
        beam_id=((proposal.get("facts") or {}).get("beamId")),
        part_ids=part_ids,
        action_ids=action_ids,
    )
    proposals = list(result.get("repairGeometryProposals") or [])
    proposals = [item for item in proposals if str(item.get("id")) != str(proposal["id"])]
    proposals.append(copy.deepcopy(proposal))
    result["repairGeometryProposals"] = proposals

    action_refs = set(str(value) for value in proposal.get("actionRefs") or [])
    for plan in result.get("plans") or []:
        for step in plan.get("steps") or []:
            if str(step.get("id")) not in action_refs:
                continue
            refs = [str(value) for value in (step.get("repairGeometryProposalRefs") or [])]
            if proposal["id"] not in refs:
                refs.append(proposal["id"])
            step["repairGeometryProposalRefs"] = refs
    result["updatedAt"] = datetime.now(timezone.utc).isoformat()
    return result


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.stem + "_", suffix=path.suffix, dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.stem + "_", suffix=path.suffix, dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _attachments(value: Any) -> dict[str, bytes]:
    output = {}
    for raw_path, payload in dict(value or {}).items():
        name = str(raw_path).replace("\\", "/").strip("/")
        if not name or any(part in ("", ".", "..") for part in name.split("/")):
            raise ValueError("attachment paths must stay inside the Workspace")
        if not isinstance(payload, (bytes, bytearray)):
            raise ValueError("attachment {} is not binary data".format(name))
        output[name] = bytes(payload)
    return output


def save_workspace(
    source: Any,
    workspace: dict,
    save_path: str,
    attachments: Any = None,
) -> str:
    """Write JSON or a ZIP copy, preserving source-ZIP attachments."""
    target = Path(os.path.abspath(os.path.expanduser(str(save_path or "").strip())))
    if not str(save_path or "").strip():
        raise ValueError("connect an explicit save_path")
    source_text = str(source or "").strip()
    if source_text and "\n" not in source_text and len(source_text) < 1024:
        source_absolute = os.path.normcase(
            os.path.abspath(os.path.expanduser(source_text))
        )
        if source_absolute == os.path.normcase(str(target)):
            raise ValueError("save_path must differ from the original Workspace path")
    payload = json.dumps(workspace, indent=2, ensure_ascii=False)
    extras = _attachments(attachments)
    if target.suffix.lower() != ".zip":
        _atomic_text(target, payload)
        for name, data in extras.items():
            _atomic_bytes(target.parent.joinpath(*name.split("/")), data)
        return str(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=target.stem + "_", suffix=".zip", dir=str(target.parent))
    os.close(handle)
    source_path = Path(os.path.abspath(os.path.expanduser(source_text)))
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as output:
            wrote_workspace = False
            extra_names = {name.lower() for name in extras}
            if source_path.is_file() and source_path.suffix.lower() == ".zip":
                with zipfile.ZipFile(source_path, "r") as original:
                    for entry in original.infolist():
                        normal = entry.filename.replace("\\", "/").lower()
                        if normal in extra_names:
                            continue
                        if normal == "workspace.json" or normal.endswith("/workspace.json"):
                            output.writestr(entry, payload.encode("utf-8"))
                            wrote_workspace = True
                        elif normal == "manifest.json":
                            try:
                                manifest = json.loads(original.read(entry.filename))
                                manifest["exportedAt"] = workspace.get("updatedAt")
                                manifest.setdefault("counts", {})["repairGeometryProposals"] = len(
                                    workspace.get("repairGeometryProposals") or []
                                )
                                output.writestr(
                                    entry,
                                    json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
                                )
                            except Exception:
                                output.writestr(entry, original.read(entry.filename))
                        else:
                            output.writestr(entry, original.read(entry.filename))
            if not wrote_workspace:
                output.writestr("workspace.json", payload.encode("utf-8"))
            for name, data in extras.items():
                output.writestr(name, data)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(target)
