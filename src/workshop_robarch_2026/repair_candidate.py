"""Open, Rhino-free contract for authored repair candidates.

The manifest describes intent and provenance.  Geometry stays in Rhino and
measurements stay neutral: whether a fact is acceptable comes from a sourced
requirement or a later human decision.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any, Optional


MANIFEST_SCHEMA = "repair-candidate@2"
FACT_STATUSES = (
    "measured",
    "unknown",
    "not_applicable",
    "failed_to_compute",
)
SOURCES = ("workspace", "human", "llm")
EXECUTION_IDENTITY_FIELDS = (
    "candidateId",
    "beamId",
    "manifestHash",
    "codeHash",
    "sessionHash",
    "geometryHash",
    "entitiesHash",
    "analysisInputHash",
)

_MISSING = object()


class RepairCandidateError(ValueError):
    """Raised when a repair-candidate record is ambiguous or malformed."""


def _json_object(value: Any, name: str) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise RepairCandidateError("%s is not valid JSON: %s" % (name, exc))
    if not isinstance(value, Mapping):
        raise RepairCandidateError("%s must be a JSON object" % name)
    return copy.deepcopy(dict(value))


def _text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RepairCandidateError("%s must be a non-empty string" % name)
    return text


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise RepairCandidateError("%s must be true or false" % name)
    return value


def _refs(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)):
        raise RepairCandidateError("%s must be a string or list of strings" % name)
    result = []
    for item in values:
        ref = _text(item, name)
        if ref not in result:
            result.append(ref)
    return result


def _records(value: Any, name: str) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise RepairCandidateError("%s must be a list" % name)
    return [_json_object(item, "%s[%d]" % (name, index)) for index, item in enumerate(value)]


def _unique_ids(records: list[dict], name: str) -> None:
    ids = [record["id"] for record in records]
    repeated = sorted({item for item in ids if ids.count(item) > 1})
    if repeated:
        raise RepairCandidateError("%s IDs must be unique: %s" % (name, ", ".join(repeated)))


def _normalise_outputs(value: Any) -> list[dict]:
    outputs = []
    for index, raw in enumerate(_records(value, "outputs")):
        item = raw
        item["id"] = _text(item.get("id") or "output_%02d" % (index + 1), "outputs.id")
        item["role"] = _text(item.get("role"), "outputs[%d].role" % index)
        if item.get("effect") is None:
            item.pop("effect", None)
        else:
            item["effect"] = _text(item["effect"], "outputs[%d].effect" % index)
        if "actionRefs" in item:
            item["actionRefs"] = _refs(item["actionRefs"], "outputs[%d].actionRefs" % index)
        if "partRefs" in item:
            item["partRefs"] = _refs(item["partRefs"], "outputs[%d].partRefs" % index)
        if "materialEffect" in item:
            effect = str(item.get("materialEffect") or "").strip().lower()
            if effect not in ("add", "remove", "retain", "reference"):
                raise RepairCandidateError(
                    "outputs[%d].materialEffect must be add, remove, retain or reference" % index
                )
            item["materialEffect"] = effect
        outputs.append(item)
    _unique_ids(outputs, "output")
    return outputs


def _normalise_assumptions(value: Any) -> list[dict]:
    assumptions = []
    for index, raw in enumerate(_records(value, "assumptions")):
        item = raw
        item["id"] = _text(item.get("id") or "assumption_%02d" % (index + 1), "assumptions.id")
        item["text"] = _text(item.get("text"), "assumptions[%d].text" % index)
        provenance = str(item.get("provenance") or "").strip().lower()
        if provenance not in SOURCES:
            raise RepairCandidateError(
                "assumptions[%d].provenance must be workspace, human or llm" % index
            )
        item["provenance"] = provenance
        assumptions.append(item)
    _unique_ids(assumptions, "assumption")
    return assumptions


def _normalise_claims(value: Any) -> list[dict]:
    claims = []
    for index, raw in enumerate(_records(value, "claims")):
        item = raw
        item["id"] = _text(item.get("id") or "claim_%02d" % (index + 1), "claims.id")
        item["text"] = _text(item.get("text"), "claims[%d].text" % index)
        source = str(item.get("source") or "").strip().lower()
        if source not in SOURCES:
            raise RepairCandidateError(
                "claims[%d].source must be workspace, human or llm" % index
            )
        item["source"] = source
        item["requirement"] = _bool(item.get("requirement", False), "claims[%d].requirement" % index)
        item["confirmed"] = _bool(item.get("confirmed", False), "claims[%d].confirmed" % index)
        claims.append(item)
    _unique_ids(claims, "claim")
    return claims


def normalise_manifest(value: Any) -> dict:
    """Return a validated ``repair-candidate@2`` manifest.

    Unknown JSON fields are preserved so projects can extend the contract.
    Missing IDs for nested records receive stable, position-based IDs.
    """
    raw = _json_object(value, "candidate manifest")
    schema = raw.get("schema") or MANIFEST_SCHEMA
    if schema != MANIFEST_SCHEMA:
        raise RepairCandidateError("manifest schema must be %s" % MANIFEST_SCHEMA)

    manifest = raw
    manifest["schema"] = MANIFEST_SCHEMA
    manifest["id"] = _text(manifest.get("id"), "manifest.id")
    manifest["title"] = str(manifest.get("title") or manifest["id"]).strip()
    manifest["actionRefs"] = _refs(manifest.get("actionRefs"), "actionRefs")
    manifest["partRefs"] = _refs(manifest.get("partRefs"), "partRefs")
    manifest["outputs"] = _normalise_outputs(manifest.get("outputs"))
    manifest["assumptions"] = _normalise_assumptions(manifest.get("assumptions"))
    manifest["claims"] = _normalise_claims(manifest.get("claims"))
    manifest["openQuestions"] = _refs(manifest.get("openQuestions"), "openQuestions")
    stable_json_hash(manifest)  # also rejects non-JSON extensions and NaN values
    return manifest


normalize_manifest = normalise_manifest


def validate_manifest(value: Any) -> dict:
    """Validate a manifest and return its normalised, independent copy."""
    return normalise_manifest(value)


def validate_scope(
    value: Any,
    beam_id: Any = None,
    part_ids: Iterable[Any] | None = None,
    action_ids: Iterable[Any] | None = None,
    workspace_hash: Any = None,
    context_hash: Any = None,
) -> dict:
    """Validate candidate references against the selected repair scope."""
    manifest = normalise_manifest(value)
    known_parts = {str(item) for item in part_ids} if part_ids is not None else None
    known_actions = {str(item) for item in action_ids} if action_ids is not None else None
    target = str(beam_id or "").strip()
    if target and target not in manifest["partRefs"]:
        raise RepairCandidateError(
            "candidate partRefs must include selected beam_id %r" % target
        )
    if not manifest["actionRefs"]:
        raise RepairCandidateError("candidate actionRefs must include at least one Workspace step")
    authority = manifest.get("requirementsAuthority") or {}
    if workspace_hash is not None:
        if authority.get("workspaceHash") != str(workspace_hash):
            raise RepairCandidateError("candidate authority does not match the active Workspace")
    if context_hash is not None:
        if authority.get("contextHash") != str(context_hash):
            raise RepairCandidateError("candidate authority does not match the active repair context")
    if known_parts is not None:
        missing = [item for item in manifest["partRefs"] if item not in known_parts]
        if missing:
            raise RepairCandidateError("unknown candidate partRefs: %s" % ", ".join(missing))
    if known_actions is not None:
        missing = [item for item in manifest["actionRefs"] if item not in known_actions]
        if missing:
            raise RepairCandidateError("unknown candidate actionRefs: %s" % ", ".join(missing))
    top_parts, top_actions = set(manifest["partRefs"]), set(manifest["actionRefs"])
    for output in manifest["outputs"]:
        output_parts = set(output.get("partRefs") or [])
        output_actions = set(output.get("actionRefs") or [])
        if not output_parts.issubset(top_parts):
            raise RepairCandidateError(
                "output %s partRefs must stay inside candidate partRefs" % output["id"]
            )
        if not output_actions.issubset(top_actions):
            raise RepairCandidateError(
                "output %s actionRefs must stay inside candidate actionRefs" % output["id"]
            )
        if known_parts is not None and not output_parts.issubset(known_parts):
            raise RepairCandidateError("output %s has an unknown partRef" % output["id"])
        if known_actions is not None and not output_actions.issubset(known_actions):
            raise RepairCandidateError("output %s has an unknown actionRef" % output["id"])
    return manifest


def _normal_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def stamp_brief_authority(
    brief: Any,
    context: Any,
    human_message: str = "",
    session: Any = None,
) -> dict:
    """Verify requirement citations before a drafted brief can bind geometry."""
    brief_obj = _json_object(brief, "repair brief")
    context_obj = _json_object(context, "repair context")
    session_obj = _json_object(session, "repair session") if session is not None else {}
    requirements = ((brief_obj.get("repairIdea") or {}).get("requirements") or [])

    sources = {}

    def visit(value):
        if isinstance(value, Mapping):
            if value.get("id"):
                sources[str(value["id"])] = copy.deepcopy(dict(value))
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(context_obj)
    verified, review = [], []
    human_text = _normal_text(human_message)
    for index, raw in enumerate(requirements):
        if not isinstance(raw, Mapping):
            continue
        requirement_id = str(raw.get("id") or "brief_requirement_%02d" % (index + 1))
        if isinstance(raw, dict):
            raw["id"] = requirement_id
        source = str(raw.get("source") or "").strip().lower()
        refs = _refs(raw.get("sourceRefs"), "requirement.sourceRefs")
        quote = _normal_text(raw.get("sourceQuote"))
        quote_is_specific = len(quote) >= 4 and (
            any(character.isdigit() for character in quote) or len(quote.split()) >= 2
        )
        status, reason = "unverified", "source could not be checked"
        if source == "workspace":
            records = [sources[ref] for ref in refs if ref in sources]
            cited = any(
                quote and quote in _normal_text(json.dumps(item, ensure_ascii=False))
                for item in records
            )
            if refs and len(records) == len(refs) and quote_is_specific and cited:
                status, reason = "citation_verified", "exact quote found at scoped Workspace refs"
        elif source == "human":
            cited = quote_is_specific and quote in human_text
            if bool(raw.get("confirmedByHuman")) and cited:
                status, reason = "citation_verified", "exact quote found in participant instruction"
        review.append({"id": requirement_id, "status": status, "reason": reason})
        if status == "citation_verified":
            verified.append(copy.deepcopy(dict(raw)))

    brief_obj["sourceSession"] = {
        key: session_obj.get(key)
        for key in ("workspaceHash", "contextHash", "beamId")
        if session_obj.get(key) is not None
    }
    brief_obj["requirementsAuthority"] = {
        "schema": "repair-brief-authority@1",
        "requirementIds": [str(item.get("id")) for item in verified],
        "requirementsHash": stable_json_hash(verified),
        "humanMessageHash": stable_json_hash(str(human_message or "")),
        "review": review,
    }
    stable_json_hash(brief_obj)
    return brief_obj


def confirm_brief(brief: Any, session: Any, note: str) -> dict:
    """Record the participant's explicit review of one exact drafted brief."""
    brief_obj = _json_object(brief, "repair brief")
    session_obj = _json_object(session, "repair session")
    reason = str(note or "").strip()
    if not reason:
        raise RepairCandidateError("review_note must explain what the participant checked")
    brief_obj.pop("participantReview", None)
    brief_hash = stable_json_hash(brief_obj)
    brief_obj["participantReview"] = {
        "schema": "repair-brief-review@1",
        "briefHash": brief_hash,
        "workspaceHash": session_obj.get("workspaceHash"),
        "contextHash": session_obj.get("contextHash"),
        "beamId": session_obj.get("beamId"),
        "confirmedBy": "participant",
        "note": reason,
    }
    return brief_obj


def brief_review_is_valid(brief: Any, session: Any = None) -> bool:
    brief_obj = _json_object(brief, "repair brief")
    review = brief_obj.pop("participantReview", None) or {}
    if review.get("schema") != "repair-brief-review@1":
        return False
    if review.get("briefHash") != stable_json_hash(brief_obj):
        return False
    if not str(review.get("note") or "").strip():
        return False
    if session is not None:
        session_obj = _json_object(session, "repair session")
        for key in ("workspaceHash", "contextHash", "beamId"):
            if review.get(key) != session_obj.get(key):
                return False
    return True


def apply_brief_authority(value: Any, brief: Any) -> dict:
    """Replace model-asserted binding claims with reviewed brief requirements."""
    manifest = normalise_manifest(value)
    brief_obj = _json_object(brief, "repair brief")
    if isinstance(brief_obj.get("brief"), Mapping):
        brief_obj = dict(brief_obj["brief"])
    requirements = ((brief_obj.get("repairIdea") or {}).get("requirements") or [])
    authority = brief_obj.get("requirementsAuthority") or {}
    verified_ids = [str(item) for item in (authority.get("requirementIds") or [])]
    verified_requirements = [
        copy.deepcopy(dict(item))
        for item in requirements
        if isinstance(item, Mapping) and str(item.get("id")) in verified_ids
    ]
    authority_valid = (
        authority.get("schema") == "repair-brief-authority@1"
        and authority.get("requirementsHash") == stable_json_hash(verified_requirements)
        and brief_review_is_valid(brief_obj)
    )
    verified_ids = verified_ids if authority_valid else []

    brief_claims = []
    for index, raw in enumerate(requirements):
        if not isinstance(raw, Mapping):
            continue
        source = str(raw.get("source") or "").strip().lower()
        if source not in ("workspace", "human"):
            continue
        item = {
            "id": _text(raw.get("id") or "brief_requirement_%02d" % (index + 1), "requirement.id"),
            "text": _text(raw.get("text"), "requirement.text"),
            "source": source,
            "requirement": True,
            "confirmed": source == "workspace" or bool(raw.get("confirmedByHuman")),
            "sourceRefs": _refs(raw.get("sourceRefs"), "requirement.sourceRefs"),
        }
        if raw.get("sourceQuote") is not None:
            item["sourceQuote"] = str(raw.get("sourceQuote") or "").strip()
        if isinstance(raw.get("test"), Mapping):
            item["test"] = copy.deepcopy(dict(raw["test"]))
        brief_claims.append(item)
    _unique_ids(brief_claims, "brief requirement")

    advisory = []
    used = {item["id"] for item in brief_claims}
    for index, claim in enumerate(manifest["claims"]):
        if claim["source"] != "llm":
            continue
        item = copy.deepcopy(claim)
        if item["id"] in used:
            item["id"] = "llm_{}_{}".format(item["id"], index + 1)
        item["confirmed"] = False
        used.add(item["id"])
        advisory.append(item)
    manifest["claims"] = brief_claims + advisory
    authoritative = [item for item in brief_claims if item["id"] in verified_ids]
    claim_hash = stable_json_hash(authoritative)
    manifest["requirementsAuthority"] = {
        "schema": "repair-candidate-authority@1",
        "briefId": brief_obj.get("id"),
        "briefHash": stable_json_hash(brief_obj),
        "workspaceHash": (brief_obj.get("sourceSession") or {}).get("workspaceHash"),
        "contextHash": (brief_obj.get("sourceSession") or {}).get("contextHash"),
        "briefReviewHash": stable_json_hash(brief_obj.get("participantReview") or {}),
        "claimIds": [item["id"] for item in authoritative],
        "claimsHash": claim_hash,
    }
    return normalise_manifest(manifest)


def fact_record(fact_id: Any, status: str, value: Any = _MISSING, **details: Any) -> dict:
    """Build one neutral measurement record, without a pass/fail judgement."""
    status = str(status or "").strip().lower()
    if status not in FACT_STATUSES:
        raise RepairCandidateError("fact status must be one of: %s" % ", ".join(FACT_STATUSES))
    if status == "measured" and value is _MISSING:
        raise RepairCandidateError("a measured fact needs a value")
    if status != "measured" and value is not _MISSING:
        raise RepairCandidateError("only a measured fact may contain a value")

    record = copy.deepcopy(details)
    record.update({"id": _text(fact_id, "fact.id"), "status": status})
    if value is not _MISSING:
        record["value"] = copy.deepcopy(value)
    stable_json_hash(record)
    return record


def normalise_facts(
    values: Optional[Iterable[Mapping[str, Any]]]
) -> list[dict]:
    """Validate fact dictionaries and preserve optional measurement metadata."""
    facts = []
    for index, raw in enumerate(values or []):
        item = _json_object(raw, "facts[%d]" % index)
        value = item.pop("value", _MISSING)
        fact_id = item.pop("id", None)
        status = item.pop("status", None)
        facts.append(fact_record(fact_id, status, value, **item))
    _unique_ids(facts, "fact")
    return facts


normalize_facts = normalise_facts


def resolve_requirements(value: Any, require_authority: bool = False) -> dict:
    """Separate binding requirements from advisory claims by provenance.

    Workspace requirements bind directly. Human requirements bind after an
    explicit confirmation. LLM claims always remain advisory.
    """
    manifest = normalise_manifest(value)
    claims = manifest["claims"]
    verified_ids = None
    if require_authority:
        authority = manifest.get("requirementsAuthority") or {}
        ids = [str(item) for item in (authority.get("claimIds") or [])]
        selected = [claim for claim in claims if claim["id"] in ids]
        valid = (
            authority.get("schema") == "repair-candidate-authority@1"
            and authority.get("claimsHash") == stable_json_hash(selected)
        )
        verified_ids = set(ids) if valid else set()
    compliance, advisory = [], []
    for claim in claims:
        binds = claim["requirement"] and (
            claim["source"] == "workspace"
            or (claim["source"] == "human" and claim["confirmed"])
        )
        if verified_ids is not None:
            binds = binds and claim["id"] in verified_ids
        item = copy.deepcopy(claim)
        item["mode"] = "compliance" if binds else "advisory"
        (compliance if binds else advisory).append(item)
    return {"compliance": compliance, "advisory": advisory}


def stable_json_hash(value: Any) -> str:
    """SHA-256 of canonical UTF-8 JSON; dictionary order has no effect."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RepairCandidateError("record is not stable JSON: %s" % exc)
    return hashlib.sha256(encoded).hexdigest()


def candidate_record(
    manifest: Any,
    code: Optional[str] = None,
    facts: Optional[Iterable[Mapping[str, Any]]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Create a replayable snapshot of one authored candidate."""
    manifest = normalise_manifest(manifest)
    record = {
        "schema": "repair-candidate-record@1",
        "id": manifest["id"],
        "manifest": manifest,
        "manifestHash": stable_json_hash(manifest),
        "facts": normalise_facts(facts),
        "requirements": resolve_requirements(manifest),
        "metadata": copy.deepcopy(dict(metadata or {})),
    }
    if code is not None:
        record["code"] = str(code)
        record["codeHash"] = stable_json_hash(record["code"])
    stable_json_hash(record)
    return record


def version_record(
    candidate: Mapping[str, Any],
    version_id: Any,
    parent_version_id: Any = None,
    note: str = "",
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Reference an immutable candidate snapshot from a revision history."""
    snapshot = _json_object(candidate, "candidate record")
    candidate_id = _text(snapshot.get("id"), "candidate.id")
    record = {
        "schema": "repair-candidate-version@1",
        "id": _text(version_id, "version.id"),
        "candidateId": candidate_id,
        "candidateHash": stable_json_hash(snapshot),
        "parentVersionId": (
            _text(parent_version_id, "parentVersionId") if parent_version_id is not None else None
        ),
        "note": str(note or ""),
        "metadata": copy.deepcopy(dict(metadata or {})),
    }
    stable_json_hash(record)
    return record


def decision_record(
    candidate_id: Any,
    version_id: Any,
    decision: Any,
    rationale: str = "",
    decided_by: str = "human",
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Record an explicit decision while allowing project-specific labels."""
    record = {
        "schema": "repair-candidate-decision@1",
        "candidateId": _text(candidate_id, "candidateId"),
        "versionId": _text(version_id, "versionId"),
        "decision": _text(decision, "decision"),
        "rationale": str(rationale or ""),
        "decidedBy": _text(decided_by, "decidedBy"),
        "metadata": copy.deepcopy(dict(metadata or {})),
    }
    stable_json_hash(record)
    return record


__all__ = [
    "EXECUTION_IDENTITY_FIELDS",
    "FACT_STATUSES",
    "MANIFEST_SCHEMA",
    "SOURCES",
    "RepairCandidateError",
    "candidate_record",
    "apply_brief_authority",
    "brief_review_is_valid",
    "confirm_brief",
    "decision_record",
    "fact_record",
    "normalise_facts",
    "normalise_manifest",
    "normalize_facts",
    "normalize_manifest",
    "resolve_requirements",
    "stable_json_hash",
    "stamp_brief_authority",
    "validate_manifest",
    "validate_scope",
    "version_record",
]
