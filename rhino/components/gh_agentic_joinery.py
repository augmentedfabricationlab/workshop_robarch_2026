# r: google-genai
"""GH Python 3 -- AGENTIC JOINERY: execute an LLM-authored JointProgram.

This component is the geometric tool used by the Agentic Joinery Co-Designer.
The LLM reads the Workspace context and writes one semantic JointProgram;
this component fits that program to the cellular damage field and returns one
resolved repair proposal.

Inputs
------
workspace_json      str/dict    Workspace JSON, .json path, or exported .zip
beam_id             str         exact Workspace part id selected for repair
joint_program_json  str/dict    joinery-program@1 JSON from the LLM; optional
gemini_model         str         optional Gemini model [gemini-3.7-flash]
repair_step_id       str         optional expert override; LLM selects when empty
instruction          str         optional human construction instruction
max_revisions        int         agent revisions after a failed fit [2]
box                  Box         oriented box from BEAM CELLS
centers              Point3d[]   world cell centres from BEAM CELLS
damage               float[]     one 0..1 damage value per centre
threshold            float       mandatory-removal threshold [0.50]
repo                 str         optional repository override
run                  bool

When ``joint_program_json`` is empty, the component first looks for an already
stored ``joineryProposal.program`` on a current-plan step affecting ``beam_id``.
If none exists, it reads the participant's ZIP locally, attaches relevant
evidence photographs, and asks Gemini directly. The key is discovered through
``GEMINI_API_KEY`` or ``Documents/robarch/gemini_api_key.txt``. With no key it
returns a context-ready handoff in ``context_json``. A failed damage fit can be
sent back once for a bounded Gemini revision.

Outputs
-------
kept                  Brep[]     retained historic member
prosthesis            Brep[]     fitted replacement member
plane_rectangles      Curve[]    active oriented plane graphics
plane_arrows          Curve[]    half-space polarity arrows
removed_cells         Point3d[]  cell centres removed by the proposal
context_json          str        scoped Workspace context given to the LLM
resolved_program_json str        validated program actually fitted
resolved_json         str        alias for existing Grasshopper canvases
proposal_json         str        record suitable for a Workspace plan step
metrics               str        compact fit result
report                str[]      diagnostics and open questions

The only universal hard gate in this prototype is complete coverage of cells
whose damage value reaches ``threshold``.  Construction behaviour is authored
upstream in the JointProgram and therefore steers topology before fitting.
"""

import hashlib
import json
import mimetypes
import os
import sys
import threading
import time
import zipfile


def _repo_from_component():
    override = globals().get("repo") or os.environ.get("ROBARCH_REPO")
    component_file = globals().get("_p") or globals().get("__file__")
    candidates = []
    if override:
        candidates.append(os.path.abspath(os.path.expanduser(str(override))))
    # A pasted RhinoCode component reports a virtual rhinocode:// URI. It is
    # useful as a traceback location but cannot be walked as a filesystem path.
    if component_file and "://" not in str(component_file):
        candidates.append(
            os.path.abspath(os.path.join(os.path.dirname(component_file), "..", ".."))
        )

    # When the script is pasted into a component, the saved .gh/.ghx file is
    # the most dependable portable anchor. OnPingDocument is available in the
    # normal Grasshopper runtime and harmlessly absent in syntax/unit tests.
    try:
        gh_document = ghenv.Component.OnPingDocument()
        gh_path = str(getattr(gh_document, "FilePath", "") or "").strip()
        if gh_path:
            candidates.append(os.path.dirname(os.path.abspath(gh_path)))
    except Exception:
        pass

    # A Rhino model saved beside the repository is another useful anchor.
    try:
        import Rhino

        rhino_path = str(getattr(Rhino.RhinoDoc.ActiveDoc, "Path", "") or "").strip()
        if rhino_path:
            candidates.append(os.path.dirname(os.path.abspath(rhino_path)))
    except Exception:
        pass

    candidates.append(os.getcwd())
    candidates.extend(
        str(value) for value in sys.path if value and "://" not in str(value)
    )

    seen = set()
    for candidate in candidates:
        root = os.path.abspath(os.path.expanduser(str(candidate)))
        if os.path.isfile(root):
            root = os.path.dirname(root)
        for _ in range(9):
            key = os.path.normcase(root)
            if key in seen:
                break
            seen.add(key)
            if os.path.isdir(os.path.join(root, "src", "workshop_robarch_2026")):
                return root
            parent = os.path.dirname(root)
            if parent == root:
                break
            root = parent
    raise RuntimeError(
        "repository package not found. Connect a Text Panel with the workshop_robarch_2026 "
        "folder to the repo input, save the Grasshopper file inside the repository, or set "
        "ROBARCH_REPO."
    )


REPO = _repo_from_component()
SRC = os.path.join(REPO, "src")
if SRC not in sys.path:
    sys.path.append(SRC)
for module_name in list(sys.modules):
    if module_name.startswith("workshop_robarch_2026"):
        sys.modules.pop(module_name)

import numpy as np
import Rhino.Geometry as rg
import scriptcontext as sc

from workshop_robarch_2026 import evaluator, joinery_program, kernel, scoring


KEY_NAME = "gemini_api_key.txt"
HOME_KEY = os.path.join(os.path.expanduser("~"), "Documents", "robarch", KEY_NAME)
MODEL = "gemini-3.7-flash"
PROMPT_CONTRACT_VERSION = "anyjoint-six-plane-v2"


kept = []
prosthesis = []
plane_rectangles = []
plane_arrows = []
removed_cells = []
context_json = ""
resolved_program_json = ""
resolved_json = ""
proposal_json = ""
metrics = ""
report = ["Agentic Joinery Co-Designer geometry tool"]


class _ContextReady(Exception):
    """End this solution normally after preparing the manual LLM handoff."""


def _key_paths():
    paths = []
    try:
        gh_path = str(ghenv.Component.OnPingDocument().FilePath or "").strip()
        if gh_path:
            folder = os.path.dirname(gh_path)
            paths.append(os.path.join(folder, KEY_NAME))
            paths.append(os.path.join(os.path.dirname(folder), KEY_NAME))
    except Exception:
        pass
    paths.append(HOME_KEY)
    return paths


def _get_key():
    env = os.environ.get("GEMINI_API_KEY")
    if env and env.strip():
        return env.strip(), "key from GEMINI_API_KEY"
    checked = _key_paths()
    for path in checked:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8-sig") as handle:
                value = handle.read().strip().strip('"').strip("'")
            if value:
                return value, "key from {}".format(path)
            return None, "key file is empty: {}".format(path)
    try:
        os.makedirs(os.path.dirname(HOME_KEY), exist_ok=True)
        with open(HOME_KEY, "a", encoding="utf-8"):
            pass
    except Exception:
        pass
    return None, "no key found; add it to {}".format(HOME_KEY)


def _read_workspace(value):
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError("connect Workspace JSON or an exported Workspace ZIP path")
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
        raise ValueError("Workspace JSON must contain one object")
    return parsed


def _unwrap_program(value, workspace, target_id):
    if value is not None and str(value).strip():
        if isinstance(value, dict):
            raw = value
        else:
            text = str(value).strip()
            path = os.path.abspath(os.path.expanduser(text))
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8-sig") as handle:
                    text = handle.read()
            raw = json.loads(text)
        if isinstance(raw, dict):
            for key in ("jointProgram", "program"):
                if isinstance(raw.get(key), dict):
                    return raw[key]
            command = raw.get("command")
            if isinstance(command, dict):
                proposal = ((command.get("payload") or {}).get("step") or {}).get(
                    "joineryProposal"
                )
                if isinstance(proposal, dict):
                    return proposal.get("program") or proposal
            return raw

    plans = list(workspace.get("plans") or [])
    current = next(
        (p for p in plans if str(p.get("id")) == str(workspace.get("currentPlanId"))),
        plans[0] if len(plans) == 1 else None,
    )
    if current:
        for step in current.get("steps") or []:
            refs = [str(v) for v in (step.get("affectedPartRefs") or [])]
            proposal = step.get("joineryProposal")
            if str(target_id) in refs and isinstance(proposal, dict):
                return proposal.get("program") or proposal
    for proposal in workspace.get("joineryProposals") or []:
        if str(proposal.get("targetPartRef")) == str(target_id):
            return proposal.get("program") or proposal
    raise ValueError(
        "no JointProgram supplied or stored for beam {}".format(target_id)
    )


def _json_from_model_text(text):
    source = str(text or "").strip()
    if source.startswith("```"):
        lines = source.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        source = "\n".join(lines).strip()
    try:
        return json.loads(source)
    except Exception:
        start = source.find("{")
        end = source.rfind("}")
        if start >= 0 and end > start:
            return json.loads(source[start : end + 1])
        raise


def _evidence_attachments(workspace_value, context, max_total_bytes=12 * 1024 * 1024):
    path = str(workspace_value or "").strip()
    path = os.path.abspath(os.path.expanduser(path)) if path else ""
    if not path.lower().endswith(".zip") or not os.path.isfile(path):
        return [], []
    wanted = {
        str(item.get("id")): item
        for item in (context.get("evidence") or [])
        if item.get("id")
    }
    attachments = []
    messages = []
    total = 0
    with zipfile.ZipFile(path, "r") as archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        for evidence_id, metadata in wanted.items():
            prefix = "photos/{}/".format(evidence_id).lower()
            exact_prefix = "photos/{}.".format(evidence_id).lower()
            entry = next(
                (
                    item
                    for item in entries
                    if item.filename.replace("\\", "/").lower().startswith(exact_prefix)
                    or item.filename.replace("\\", "/").lower().startswith(prefix)
                ),
                None,
            )
            if entry is None:
                messages.append("evidence {} has no matching photo in ZIP".format(evidence_id))
                continue
            if total + int(entry.file_size) > max_total_bytes:
                messages.append("evidence {} skipped: attachment budget exceeded".format(evidence_id))
                continue
            data = archive.read(entry)
            total += len(data)
            mime = metadata.get("mimeType") or mimetypes.guess_type(entry.filename)[0]
            attachments.append(
                {
                    "id": evidence_id,
                    "name": entry.filename,
                    "mimeType": mime or "image/jpeg",
                    "data": data,
                }
            )
    return attachments, messages


def _request_gemini_joint_program(
    api_key,
    context,
    instruction,
    model,
    attachments=None,
    previous=None,
    feedback=None,
):
    prompt_path = os.path.join(REPO, "data", "prompts", "design_joinery_standalone.md")
    with open(prompt_path, "r", encoding="utf-8-sig") as handle:
        system_prompt = handle.read()
    payload = {
        "workspaceContext": context,
        "userMessage": instruction
        or "Design an actionable construction joinery for this repair area.",
        "previousProgram": previous,
        "fitFeedback": feedback,
    }
    from google import genai
    from google.genai import types

    parts = [types.Part.from_text(text=system_prompt)]
    parts.append(
        types.Part.from_text(
            text=json.dumps(payload, indent=2, ensure_ascii=False)
        )
    )
    for item in attachments or []:
        parts.append(
            types.Part.from_text(
                text="Evidence image {} ({})".format(item["id"], item["name"])
            )
        )
        parts.append(
            types.Part.from_bytes(data=item["data"], mime_type=item["mimeType"])
        )
    client = genai.Client(api_key=str(api_key).strip())
    response = client.models.generate_content(
        model=str(model or MODEL).strip(),
        contents=parts,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned no text response")
    result = _json_from_model_text(text)
    if not isinstance(result, dict) or not isinstance(result.get("jointProgram"), dict):
        raise RuntimeError("Gemini returned no jointProgram")
    return result["jointProgram"], result


def _damage_context(frame, points, values, gate):
    local = scoring.to_local(points, frame)
    mandatory = values >= float(gate)
    dimensions = np.array(
        [frame["width"], frame["length"], frame["height"]], dtype=float
    )
    result = {
        "threshold": float(gate),
        "cellCount": int(len(points)),
        "mandatoryCellCount": int(mandatory.sum()),
        "maximumDamage": float(values.max()) if len(values) else 0.0,
        "localAxes": ["section_u", "beam_axis_v", "section_w"],
        "beamDimensions": dimensions.tolist(),
    }
    if mandatory.any():
        damaged = local[mandatory]
        lo = damaged.min(axis=0)
        hi = damaged.max(axis=0)
        weights = np.maximum(values[mandatory], 1e-9)
        centroid = np.average(damaged, axis=0, weights=weights)
        result.update(
            {
                "localBounds": {"min": lo.tolist(), "max": hi.tolist()},
                "normalisedBounds": {
                    "min": (lo / dimensions).tolist(),
                    "max": (hi / dimensions).tolist(),
                },
                "weightedCentroid": centroid.tolist(),
                "axialRange": [float(lo[1]), float(hi[1])],
            }
        )
    return result


def _llm_cache_key(context_text, instruction, model):
    component_id = str(getattr(ghenv.Component, "InstanceGuid", "component"))
    payload = "\n".join(
        (
            PROMPT_CONTRACT_VERSION,
            context_text,
            str(instruction or ""),
            str(model or ""),
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return "robarch.agentic_joinery.{}.{}".format(component_id, digest)


def _wake_up(delay=500):
    import Grasshopper

    document = ghenv.Component.OnPingDocument()
    if document:
        document.ScheduleSolution(
            delay,
            Grasshopper.Kernel.GH_Document.GH_ScheduleDelegate(
                lambda _: ghenv.Component.ExpireSolution(False)
            ),
        )


def _start_gemini_job(
    job,
    api_key,
    context,
    instruction,
    model,
    attachments,
    previous=None,
    feedback=None,
):
    job["done"] = False
    job["error"] = None
    job["t0"] = time.time()

    def work():
        try:
            program, response = _request_gemini_joint_program(
                api_key,
                context,
                instruction,
                model,
                attachments=attachments,
                previous=previous,
                feedback=feedback,
            )
            job["program"] = program
            job["response"] = response
        except Exception as exc:
            job["error"] = str(exc)
        finally:
            job["done"] = True

    threading.Thread(target=work, daemon=True).start()


def _box_frame(value):
    plane = value.Plane
    axes = [
        np.array([plane.XAxis.X, plane.XAxis.Y, plane.XAxis.Z], float),
        np.array([plane.YAxis.X, plane.YAxis.Y, plane.YAxis.Z], float),
        np.array([plane.ZAxis.X, plane.ZAxis.Y, plane.ZAxis.Z], float),
    ]
    extents = [float(value.X.Length), float(value.Y.Length), float(value.Z.Length)]
    axis_v = int(np.argmax(extents))
    axis_u, axis_w = (axis_v + 1) % 3, (axis_v + 2) % 3
    U, V = axes[axis_u], axes[axis_v]
    W = np.cross(U, V)
    W /= np.linalg.norm(W)
    intervals = [value.X, value.Y, value.Z]
    origin = (
        np.array([plane.Origin.X, plane.Origin.Y, plane.Origin.Z], float)
        + intervals[0].Min * axes[0]
        + intervals[1].Min * axes[1]
        + intervals[2].Min * axes[2]
    )
    if float(W @ axes[axis_w]) < 0:
        origin = origin + extents[axis_w] * axes[axis_w]
    return {
        "origin": origin.tolist(),
        "u": U.tolist(),
        "v": V.tolist(),
        "w": W.tolist(),
        "width": extents[axis_u],
        "length": extents[axis_v],
        "height": extents[axis_w],
    }


def _xyz(point):
    if hasattr(point, "X"):
        return [float(point.X), float(point.Y), float(point.Z)]
    return [float(point[0]), float(point[1]), float(point[2])]


def _plane_graphics(cut_json, size):
    cut = kernel.Cut.from_json(cut_json)
    origin = cut.origin()
    normal = np.asarray(cut.normal, float)
    normal /= np.linalg.norm(normal)
    point = rg.Point3d(float(origin[0]), float(origin[1]), float(origin[2]))
    vector = rg.Vector3d(float(normal[0]), float(normal[1]), float(normal[2]))
    plane = rg.Plane(point, vector)
    rectangle = rg.Rectangle3d(
        plane, rg.Interval(-size, size), rg.Interval(-size, size)
    ).ToNurbsCurve()
    arrow = rg.LineCurve(point, point + float(0.35 * size) * vector)
    return rectangle, arrow


if bool(globals().get("run")):
    try:
        if globals().get("box") is None:
            raise ValueError("connect the oriented box from BEAM CELLS")
        target_id = str(globals().get("beam_id") or "").strip()
        if not target_id:
            raise ValueError("beam_id is required and must match a Workspace part id")
        raw_centres = list(globals().get("centers") or [])
        raw_damage = list(globals().get("damage") or [])
        if not raw_centres:
            raise ValueError("connect cell centres from BEAM CELLS")
        if len(raw_centres) != len(raw_damage):
            raise ValueError(
                "centres/damage length mismatch: {} vs {}".format(
                    len(raw_centres), len(raw_damage)
                )
            )

        step_id = str(globals().get("repair_step_id") or "").strip() or None
        workspace_value = globals().get("workspace_json")
        workspace = _read_workspace(workspace_value)
        context = joinery_program.workspace_context(
            workspace, target_id, repair_step_id=step_id
        )
        frame = _box_frame(box)
        points = np.asarray([_xyz(point) for point in raw_centres], float)
        values = np.asarray([float(value) for value in raw_damage], float)
        gate = (
            0.5
            if globals().get("threshold") is None
            else float(globals().get("threshold"))
        )
        context["cellularDamage"] = _damage_context(frame, points, values, gate)
        context_json = json.dumps(context, indent=2, ensure_ascii=False)
        gemini_model = str(
            globals().get("gemini_model")
            or os.environ.get("GEMINI_MODEL")
            or MODEL
        ).strip()
        attachments, attachment_report = _evidence_attachments(
            workspace_value, context
        )
        report.extend(attachment_report)
        instruction_text = str(globals().get("instruction") or "")
        signature = _llm_cache_key(context_json, instruction_text, gemini_model)
        program_from_gemini = False
        gemini_job = None
        gemini_key = None
        try:
            program = _unwrap_program(
                globals().get("joint_program_json"), workspace, target_id
            )
            report.append("program source: input or stored Workspace proposal")
        except ValueError:
            gemini_key, key_note = _get_key()
            report.append(key_note)
            if not gemini_key:
                report.append(
                    "STATUS: local LLM context ready for beam {}; no geometry generated yet".format(
                        target_id
                    )
                )
                report.append(
                    "add the key to the reported file, or paste a Gemini response into joint_program_json"
                )
                raise _ContextReady()
            jobs = sc.sticky.setdefault("agentic_joinery_jobs", {})
            job_id = str(ghenv.Component.InstanceGuid)
            gemini_job = jobs.get(job_id)
            if not isinstance(gemini_job, dict) or gemini_job.get("signature") != signature:
                gemini_job = {
                    "signature": signature,
                    "done": False,
                    "error": None,
                    "program": None,
                    "response": None,
                    "revision": 0,
                    "t0": time.time(),
                }
                jobs[job_id] = gemini_job
                _start_gemini_job(
                    gemini_job,
                    gemini_key,
                    context,
                    instruction_text,
                    gemini_model,
                    attachments,
                )
                report.append(
                    "STATUS: Gemini is authoring the JointProgram in the background"
                )
                _wake_up()
                raise _ContextReady()
            if not gemini_job.get("done"):
                report.append(
                    "STATUS: Gemini working, {:.0f} s so far".format(
                        time.time() - float(gemini_job.get("t0") or time.time())
                    )
                )
                _wake_up()
                raise _ContextReady()
            if gemini_job.get("error"):
                raise RuntimeError("Gemini failed: {}".format(gemini_job["error"]))
            program = gemini_job.get("program")
            agent_response = gemini_job.get("response") or {}
            if not isinstance(program, dict):
                raise RuntimeError("Gemini job completed without a JointProgram")
            program_from_gemini = True
            report.append(
                "Gemini authored program directly: {}".format(
                    agent_response.get("summary", "done")
                )
            )
        raw_revisions = globals().get("max_revisions")
        revisions = (
            2
            if raw_revisions is None
            else max(0, min(3, int(raw_revisions)))
        )
        try:
            result, resolved, fit_report = joinery_program.fit_program(
                program,
                frame,
                points,
                values,
                beam_id=target_id,
                threshold=gate,
            )
        except (joinery_program.JointProgramError, ValueError) as exc:
            # Feed executable-contract errors into the bounded Gemini revision
            # cycle instead of terminating the Grasshopper solution.
            result = None
            resolved = program
            fit_report = ["JointProgram validation failed: {}".format(exc)]
        report.extend(fit_report)
        if (
            result is None
            and program_from_gemini
            and gemini_job is not None
            and int(gemini_job.get("revision") or 0) < revisions
        ):
            feedback = joinery_program.proposal_record(resolved, None, fit_report)
            next_revision = int(gemini_job.get("revision") or 0) + 1
            gemini_job["revision"] = next_revision
            _start_gemini_job(
                gemini_job,
                gemini_key,
                context,
                instruction_text,
                gemini_model,
                attachments,
                previous=resolved,
                feedback=feedback,
            )
            report.append(
                "STATUS: Gemini revision {} running after failed damage fit".format(
                    next_revision
                )
            )
            _wake_up()
            raise _ContextReady()
        resolved_program_json = json.dumps(resolved, indent=2, ensure_ascii=False)
        resolved_json = resolved_program_json
        proposal = joinery_program.proposal_record(resolved, result, report)
        proposal_json = json.dumps(proposal, indent=2, ensure_ascii=False)

        if result is None:
            report.append("STATUS: program needs revision; no damage-covering fit found")
        else:
            repair = result["repair"]
            for part in repair["parts"]:
                breps = evaluator.evaluate_part(part)
                if part["name"] == "kept":
                    kept.extend(breps)
                elif part["name"] == "prosthesis":
                    prosthesis.extend(breps)

            predicate_count = int(result["predicate_count"])
            cut_jsons = repair["parts"][0]["cuts"][1 : 1 + predicate_count]
            display_size = 0.75 * max(frame["width"], frame["height"])
            for cut_json in cut_jsons:
                rectangle, arrow = _plane_graphics(cut_json, display_size)
                plane_rectangles.append(rectangle)
                plane_arrows.append(arrow)

            removed = scoring.removed_mask(points, repair)
            removed_cells = [
                raw_centres[index] for index, flag in enumerate(removed) if bool(flag)
            ]
            metrics = joinery_program.anyjoint.result_summary(result)
            report.append(
                "STATUS: AnyJoint passed the current damage and construction contract; "
                "neighbour connections and unmodelled fasteners remain human-review items"
            )
            for question in resolved.get("openQuestions") or []:
                report.append("OPEN QUESTION: {}".format(question))
    except _ContextReady:
        pass
    except Exception as exc:
        report.append("ERROR: {}".format(exc))
else:
    try:
        sc.sticky.get("agentic_joinery_jobs", {}).pop(
            str(ghenv.Component.InstanceGuid), None
        )
    except Exception:
        pass
