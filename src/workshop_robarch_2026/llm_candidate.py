"""Gemini calls for the staged repair-geometry conversation.

This module knows prompts and JSON.  Rhino geometry stays in the execution
stage, which makes the model exchange easy to inspect, save, and teach.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_MODEL = "gemini-3.7-flash"
KEY_NAME = "gemini_api_key.txt"


def as_object(value: Any, label: str = "JSON") -> dict:
    """Read one object from a dict, JSON text, or JSON file."""
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    path = Path(os.path.expanduser(text))
    if path.is_file():
        text = path.read_text(encoding="utf-8-sig")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("{} must contain one JSON object".format(label))
    return parsed


def json_from_model_text(value: str) -> dict:
    """Accept strict JSON plus the common fenced-JSON response."""
    text = str(value or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines.pop()
        text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        result = json.loads(text[start : end + 1])
    if not isinstance(result, dict):
        raise ValueError("model response must contain one JSON object")
    return result


def stable_signature(*values: Any) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_session(value: Any) -> dict:
    session = as_object(value, "session_json")
    if session.get("schema") != "repair-session@1" or not session.get("beamId"):
        raise ValueError("session_json must be a repair-session@1 with beamId")
    return session


def validate_context(value: Any, beam_id: str = "", session: Any = None) -> dict:
    context = as_object(value, "context_json")
    target = context.get("targetPart") or {}
    if not target.get("id") or not isinstance((context.get("currentPlan") or {}).get("steps"), list):
        raise ValueError("context_json needs a targetPart and currentPlan steps")
    if beam_id and str(target.get("id")) != str(beam_id):
        raise ValueError("context targetPart does not match session beamId")
    if session is not None:
        from . import workspace_io

        session_obj = validate_session(session)
        expected = workspace_io.json_digest(
            {
                "context": context,
                "box": (context.get("rhinoContext") or {}).get("targetBox"),
                "cellDataHash": session_obj.get("cellDataHash"),
                "threshold": session_obj.get("threshold"),
            }
        )
        if session_obj.get("contextHash") != expected:
            raise ValueError("context_json does not match session_json; rebuild Repair Context")
    return context


def validate_brief(
    value: Any,
    beam_id: str = "",
    session: Any = None,
    require_review: bool = False,
) -> dict:
    brief = as_object(value, "brief_json")
    if isinstance(brief.get("brief"), dict):
        brief = brief["brief"]
    if brief.get("schema") != "repair-brief@1":
        raise ValueError("brief_json must be a repair-brief@1")
    if beam_id and str(brief.get("targetPartRef")) != str(beam_id):
        raise ValueError("brief targetPartRef does not match session beamId")
    for key in ("actionRefs", "partRefs", "workspaceFacts", "llmInferences", "openQuestions"):
        if not isinstance(brief.get(key), list):
            raise ValueError("brief.{} must be a list".format(key))
    if not isinstance((brief.get("repairIdea") or {}).get("requirements"), list):
        raise ValueError("brief.repairIdea.requirements must be a list")
    if session is not None:
        session_obj = validate_session(session)
        source = brief.get("sourceSession") or {}
        for key in ("workspaceHash", "contextHash", "beamId"):
            if source.get(key) != session_obj.get(key):
                raise ValueError("brief sourceSession.{} does not match session_json".format(key))
        if (brief.get("requirementsAuthority") or {}).get("schema") != "repair-brief-authority@1":
            raise ValueError("brief requirements have no locally verified authority record")
        if require_review:
            from . import repair_candidate

            if not repair_candidate.brief_review_is_valid(brief, session_obj):
                raise ValueError("brief_json needs an explicit participant review")
    return brief


def validate_fact_bundle(
    facts: Any,
    requirements: Any,
    candidate: Any = None,
    code: str = "",
    session: Any = None,
) -> tuple[dict, dict]:
    fact_obj = as_object(facts, "facts_json")
    requirement_obj = as_object(requirements, "requirements_json")
    if fact_obj.get("schema") != "repair-candidate-facts@1" or not isinstance(fact_obj.get("facts"), list):
        raise ValueError("facts_json must be measured repair-candidate-facts@1")
    if requirement_obj.get("schema") != "repair-requirements@1":
        raise ValueError("requirements_json must be repair-requirements@1")
    for key in ("compliance", "advisory"):
        if not isinstance(requirement_obj.get(key), list):
            raise ValueError("requirements_json.{} must be a list".format(key))
    if candidate is not None:
        from . import repair_candidate

        manifest = repair_candidate.normalise_manifest(as_object(candidate, "candidate_json"))
        session_obj = validate_session(session)
        expected = {
            "candidateId": manifest["id"],
            "beamId": session_obj["beamId"],
            "manifestHash": repair_candidate.stable_json_hash(manifest),
            "codeHash": repair_candidate.stable_json_hash(str(code or "")),
            "sessionHash": repair_candidate.stable_json_hash(_public_session(session_obj)),
        }
        for key, value in expected.items():
            if fact_obj.get(key) != value:
                raise ValueError("facts_json {} does not match the active revision".format(key))
        for key in ("geometryHash", "entitiesHash", "analysisInputHash"):
            if not fact_obj.get(key):
                raise ValueError("facts_json is missing {}".format(key))
            expected[key] = fact_obj[key]
        expected["factsHash"] = repair_candidate.stable_json_hash(fact_obj["facts"])
        for key, value in expected.items():
            if requirement_obj.get(key) != value:
                raise ValueError(
                    "requirements_json {} does not match facts_json".format(key)
                )
        from . import candidate_analysis

        expected_requirements = candidate_analysis.requirement_results(
            manifest,
            repair_candidate.normalise_facts(fact_obj["facts"]),
            {key: value for key, value in expected.items() if key != "factsHash"},
        )
        if repair_candidate.stable_json_hash(requirement_obj) != repair_candidate.stable_json_hash(
            expected_requirements
        ):
            raise ValueError("requirements_json was changed after local analysis")
    return fact_obj, requirement_obj


def find_api_key(repo: str | os.PathLike | None = None) -> tuple[str | None, str]:
    env = os.environ.get("GEMINI_API_KEY", "").strip()
    if env:
        return env, "GEMINI_API_KEY"
    candidates = []
    if repo:
        candidates.append(Path(repo) / KEY_NAME)
    candidates.append(Path.home() / "Documents" / "robarch" / KEY_NAME)
    for path in candidates:
        if path.is_file():
            value = path.read_text(encoding="utf-8-sig").strip().strip("\"'")
            if value:
                return value, str(path)
    return None, " or ".join(str(path) for path in candidates)


def _prompt(repo: str | os.PathLike, filename: str) -> str:
    path = Path(repo) / "data" / "prompts" / filename
    return path.read_text(encoding="utf-8-sig")


def _generation_record(
    repo: str | os.PathLike,
    prompt_file: str,
    model: str | None,
    payload: dict,
    attachments: Iterable[dict],
    response: dict,
) -> dict:
    prompt = _prompt(repo, prompt_file)
    return {
        "schema": "llm-generation@1",
        "provider": "gemini",
        "model": str(model or DEFAULT_MODEL).strip(),
        "promptFile": prompt_file,
        "promptHash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "requestHash": stable_signature(payload),
        "responseHash": stable_signature(response),
        "evidenceRefs": [str(item.get("id")) for item in attachments if item.get("id")],
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }


def _public_session(value: Any) -> dict:
    result = as_object(value, "session_json").copy()
    result.pop("workspaceSource", None)
    return result


def evidence_attachments(session: Any, context: Any, limit: int = 12 * 1024 * 1024) -> list[dict]:
    """Load only evidence images referenced by the scoped context ZIP."""
    session_obj = as_object(session, "session_json")
    source = session_obj.get("workspaceSource") or {}
    path = Path(str(source.get("path") or "")) if isinstance(source, dict) else Path("")
    if not path.is_file() or path.suffix.lower() != ".zip":
        return []
    refs = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in ("id", "evidenceref") and str(item).startswith("ev_"):
                    refs.add(str(item))
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
        elif isinstance(value, str) and value.startswith("ev_"):
            refs.add(value)

    visit(as_object(context, "context_json"))
    output, total = [], 0
    with zipfile.ZipFile(path, "r") as archive:
        entries = [item for item in archive.infolist() if not item.is_dir()]
        workspace_entry = next(
            (
                item for item in entries
                if item.filename.replace("\\", "/").lower().rsplit("/", 1)[-1] == "workspace.json"
            ),
            None,
        )
        base = ""
        if workspace_entry is not None:
            normal = workspace_entry.filename.replace("\\", "/")
            base = normal.rsplit("/", 1)[0] + "/" if "/" in normal else ""
        for evidence_id in sorted(refs):
            wanted_folder = (base + "photos/").lower()
            wanted_stem = evidence_id.lower()
            entry = next(
                (
                    item for item in entries
                    if item.filename.replace("\\", "/").lower().rsplit("/", 1)[0] + "/" == wanted_folder
                    and item.filename.replace("\\", "/").lower().rsplit("/", 1)[-1].rsplit(".", 1)[0] == wanted_stem
                ),
                None,
            )
            if entry is None or total + entry.file_size > limit:
                continue
            data = archive.read(entry)
            total += len(data)
            output.append(
                {
                    "id": evidence_id,
                    "name": entry.filename,
                    "mimeType": mimetypes.guess_type(entry.filename)[0] or "image/jpeg",
                    "data": data,
                }
            )
    return output


def _contents(types: Any, prompt: str, payload: dict, attachments: Iterable[dict]):
    parts = [types.Part.from_text(text=prompt)]
    parts.append(
        types.Part.from_text(text=json.dumps(payload, indent=2, ensure_ascii=False))
    )
    for item in attachments:
        parts.append(
            types.Part.from_text(
                text="Evidence {} ({})".format(item.get("id", "image"), item.get("name", ""))
            )
        )
        parts.append(
            types.Part.from_bytes(data=item["data"], mime_type=item.get("mimeType", "image/jpeg"))
        )
    return parts


def request_json(
    repo: str | os.PathLike,
    prompt_file: str,
    payload: dict,
    model: str | None = None,
    attachments: Iterable[dict] = (),
    api_key: str | None = None,
    temperature: float | None = None,
) -> dict:
    """Call Gemini once and return its JSON object."""
    key, where = (api_key, "explicit key") if api_key else find_api_key(repo)
    if not key:
        raise RuntimeError("Gemini key missing; checked {}".format(where))
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=key,
        http_options=types.HttpOptions(timeout=120_000),
    )
    config = {"response_mime_type": "application/json"}
    if temperature is not None:
        config["temperature"] = float(temperature)
    response = client.models.generate_content(
        model=str(model or DEFAULT_MODEL).strip(),
        contents=_contents(types, _prompt(repo, prompt_file), payload, attachments),
        config=types.GenerateContentConfig(**config),
    )
    if not str(response.text or "").strip():
        raise RuntimeError("Gemini returned an empty response")
    return json_from_model_text(response.text)


def draft_brief(
    repo: str | os.PathLike,
    session: Any,
    context: Any,
    instruction: str = "",
    model: str | None = None,
) -> dict:
    session_obj = validate_session(session)
    context_obj = validate_context(context, session_obj["beamId"], session_obj)
    prompt_file = "draft_repair_brief.md"
    payload = {
        "session": _public_session(session_obj),
        "workspaceContext": context_obj,
        "humanMessage": str(instruction or ""),
    }
    attachments = evidence_attachments(session_obj, context_obj)
    result = request_json(
        repo, prompt_file, payload, model=model, attachments=attachments,
    )
    brief = result.get("brief") if isinstance(result.get("brief"), dict) else {}
    for key in ("workspaceFacts", "llmInferences", "openQuestions"):
        result.setdefault(key, brief.get(key, []))
    missing = [key for key in ("brief", "workspaceFacts", "llmInferences", "openQuestions") if key not in result]
    if missing:
        raise ValueError("brief response missing {}".format(", ".join(missing)))
    from . import repair_candidate

    checked = validate_brief(result["brief"], session_obj["beamId"])
    checked["generation"] = _generation_record(
        repo, prompt_file, model, payload, attachments, result
    )
    result["brief"] = repair_candidate.stamp_brief_authority(
        checked, context_obj, str(instruction or ""), session_obj
    )
    validate_brief(result["brief"], session_obj["beamId"], session_obj)
    return result


def author_candidate(
    repo: str | os.PathLike,
    session: Any,
    context: Any,
    brief: Any,
    instruction: str = "",
    model: str | None = None,
    authorship_run: Any = None,
) -> dict:
    session_obj = validate_session(session)
    context_obj = validate_context(context, session_obj["beamId"], session_obj)
    brief_obj = validate_brief(
        brief, session_obj["beamId"], session_obj, require_review=True
    )
    prompt_file = "author_repair_candidate.md"
    payload = {
        "session": _public_session(session_obj),
        "context": context_obj,
        "brief": brief_obj,
        "participantInstruction": str(instruction or ""),
    }
    if authorship_run is not None:
        payload["authorshipRun"] = authorship_run
    attachments = evidence_attachments(session_obj, context_obj)
    result = request_json(
        repo, prompt_file, payload, model=model, attachments=attachments,
        temperature=0.9,
    )
    _validate_candidate_response(result)
    result["candidate"]["generation"] = _generation_record(
        repo, prompt_file, model, payload, attachments, result
    )
    result["candidate"]["authorSummary"] = str(result.get("summary") or "")
    return result


def finalise_candidate_response(
    result: dict, session: Any, context: Any, brief: Any
) -> dict:
    """Apply reviewed requirement authority and the active Workspace scope."""
    from . import repair_candidate

    session_obj = validate_session(session)
    context_obj = validate_context(context, session_obj["beamId"], session_obj)
    brief_obj = validate_brief(
        brief, session_obj["beamId"], session_obj, require_review=True
    )
    checked = dict(result)
    candidate = repair_candidate.apply_brief_authority(
        checked.get("candidate"), brief_obj
    )
    action_ids = [
        str(step.get("id"))
        for step in ((context_obj.get("currentPlan") or {}).get("steps") or [])
        if step.get("id")
    ]
    part_ids = [
        str(part.get("id"))
        for part in [context_obj.get("targetPart")] + list(context_obj.get("connectedParts") or [])
        if part and part.get("id")
    ]
    checked["candidate"] = repair_candidate.validate_scope(
        candidate,
        beam_id=session_obj.get("beamId"),
        part_ids=part_ids,
        action_ids=action_ids,
        workspace_hash=session_obj.get("workspaceHash"),
        context_hash=session_obj.get("contextHash"),
    )
    return checked


def _previous_attempt(result: dict) -> dict:
    candidate = result.get("candidate") or {}
    return {
        "candidateId": candidate.get("id"),
        "title": candidate.get("title"),
        "summary": result.get("summary"),
        "outputs": [
            {
                "role": item.get("role"),
                "effect": item.get("effect"),
                "materialEffect": item.get("materialEffect"),
            }
            for item in (candidate.get("outputs") or [])
        ],
    }


def author_candidate_set(
    repo: str | os.PathLike,
    session: Any,
    context: Any,
    brief: Any,
    instruction: str = "",
    model: str | None = None,
    count: int = 3,
    progress: Any = None,
) -> dict:
    """Run several complete LLM authorships under one unchanged reviewed brief."""
    from . import candidate_variations

    session_obj = validate_session(session)
    context_obj = validate_context(context, session_obj["beamId"], session_obj)
    brief_obj = validate_brief(
        brief, session_obj["beamId"], session_obj, require_review=True
    )
    requested = candidate_variations.variation_count(count)
    results, errors, previous = [], [], []
    for index in range(requested):
        if callable(progress):
            progress(index, requested, "authoring variation {}".format(index + 1))
        run = {
            "index": index + 1,
            "count": requested,
            "sameRepairIdea": True,
            "instruction": (
                "Author a complete geometric realisation of the unchanged reviewed repair idea. "
                "Use previousResults only to avoid geometric duplication; do not treat them as "
                "a parameter template or a menu of repair strategies."
            ),
            "previousResults": json.loads(json.dumps(previous)),
        }
        try:
            result = author_candidate(
                repo, session_obj, context_obj, brief_obj, instruction, model, run
            )
            result = finalise_candidate_response(
                result, session_obj, context_obj, brief_obj
            )
            result["authorshipRun"] = index + 1
            results.append(result)
            previous.append(_previous_attempt(result))
        except Exception as exc:
            errors.append("run {}: {}: {}".format(index + 1, type(exc).__name__, exc))
        if callable(progress):
            progress(index + 1, requested, "completed variation {}".format(index + 1))
    if len(results) < candidate_variations.MIN_COUNT:
        raise RuntimeError(
            "fewer than two candidate authorship runs succeeded: {}".format(
                "; ".join(errors) or "no additional error detail"
            )
        )
    return candidate_variations.build_candidate_set(
        results, session_obj, brief_obj, requested, str(model or DEFAULT_MODEL), errors
    )


def revise_candidate(
    repo: str | os.PathLike,
    session: Any,
    context: Any,
    brief: Any,
    candidate: Any,
    code: str,
    facts: Any,
    requirements: Any,
    feedback: str = "",
    model: str | None = None,
) -> dict:
    session_obj = validate_session(session)
    context_obj = validate_context(context, session_obj["beamId"], session_obj)
    brief_obj = validate_brief(
        brief, session_obj["beamId"], session_obj, require_review=True
    )
    fact_obj, requirement_obj = validate_fact_bundle(
        facts, requirements, candidate, code, session_obj
    )
    prompt_file = "revise_repair_candidate.md"
    payload = {
        "session": _public_session(session_obj),
        "context": context_obj,
        "brief": brief_obj,
        "previousCandidate": as_object(candidate, "candidate_json"),
        "previousPython": str(code or ""),
        "measuredFacts": fact_obj,
        "resolvedRequirements": requirement_obj,
        "participantFeedback": str(feedback or ""),
    }
    attachments = evidence_attachments(session_obj, context_obj)
    result = request_json(
        repo, prompt_file, payload, model=model, attachments=attachments,
    )
    _validate_candidate_response(result)
    if "changeSummary" not in result:
        raise ValueError("revision response missing changeSummary")
    result["candidate"]["generation"] = _generation_record(
        repo, prompt_file, model, payload, attachments, result
    )
    result["candidate"]["authorSummary"] = str(result.get("summary") or "")
    return result


def _validate_candidate_response(result: dict) -> None:
    if not isinstance(result.get("candidate"), dict):
        raise ValueError("candidate response missing candidate object")
    code = str(result.get("python") or "")
    if "build_candidate" not in code:
        raise ValueError("candidate response missing build_candidate(ctx, emit)")
