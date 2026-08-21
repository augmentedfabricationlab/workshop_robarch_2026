# r: google-genai
"""GH Python 3 -- AGENTIC JOINERY: execute an LLM-authored JointProgram.

This component is the geometric tool used by the Agentic Joinery Co-Designer.
The LLM reads the Workspace context and writes one or more semantic
JointPrograms; this component fits each to the cellular damage field and
returns the resolved repair proposals, ranked, with a measured account of what
each joint does and what it does not do.

WHAT CHANGED IN THIS REVISION
-----------------------------
1. Variants.   The model may return `alternativePrograms` alongside
   `jointProgram`. Each is fitted independently, so one unbuildable concept
   costs one card rather than the whole run. `variant` picks which fitted
   proposal drives the geometry outputs; `variants_json` carries them all.
2. A ladder instead of a refusal.  The strict contract is rung one and is
   unchanged. When it produces nothing the component keeps going -- direction
   claims aside, then the numeric gates dropped to a permissive floor, then
   gates off with full coverage, then partial coverage -- and reports which
   rung the geometry came from and what it gave up. You now only see nothing
   when no placement of those planes builds a solid at all, which is a
   different and much rarer problem, and it is named as such.
   Whatever is built is printed with the full gate table measured against what
   the program ORIGINALLY asked for:

       minimumEngagementSections     measured 0.82   required >= 2.500  FAIL by 1.680
       minimumInterfaceAreaRatio     measured 1.41   required >= 1.500  FAIL by 0.090
       minimumLigamentRatio          measured 0.11   required >= 0.100  ok
3. Bounded Booleans.  Removal groups are rewritten to intersect INTO the
   bounded stock, so Rhino never intersects two oversized cutter prisms whose
   side faces are coincident. That is the "boolean intersection failed
   (tolerance?)" class. Original expression and a tolerance ladder remain as
   fallbacks, and `diagnose_cuts` is reported when everything fails.
4. Explanation.  `explanation` states, from the measured geometry, which
   directions the joint locks and which it leaves open, and flags any lock the
   program claimed but the geometry does not deliver.
5. Contradictory claims no longer erase the run.  `assemblyDirection` and
   `geometricLockDirections` are checked BEFORE placement, so when the program
   claims an insertion direction its own undercut blocks -- or a lock its
   planes do not deliver -- every candidate is discarded and nothing is built,
   which reads as "impossible geometry" when the geometry was never tried. The
   component now retries once with those two claims set aside, keeping every
   numeric gate and full coverage armed, and reports exactly which claim the
   built joint fails to honour.
7. The search window is sized from the decay.  `searchWindowSections` bounds
   where the joint band may sit. The prompt's placeholder value of 1.5 is too
   small for a basal repair: the band cannot clear the rot, so the kernel's end
   trim stops inside the decayed stub and the joint's own features are left to
   cover through-section damage -- which a lap or a scarf cannot do, since both
   keep part of the section. Coverage fails, every strict rung refuses, and the
   fitter escalates to replacing the whole member because that is the only
   thing that covers everything. Measured on the 1.84 m corner post: window 1.5
   leaves 9 required cells uncovered; window 2.0 covers all of them with 9.3%
   sound loss and a prosthesis of 0.017..0.485 m. The component now raises the
   window to `decay + aspect + 0.5` section depths and says so.
8. Open removal groups are caught before placement.  A group that is not
   closed along the member axis on the kept side does not stop at the joint
   window -- every plane is placed as an oversized prism, so the group sweeps
   the entire beam and the "repair" replaces 98% of a sound post. The
   construction metrics cannot see it, because they sample only v in
   [0, aspect], where such a group looks well behaved: engagement, interface
   and ligament all read fine. The component now samples past the window,
   drops the offending variants, and names the group and its planes.
9. The replacement side comes from the decay, not from the search.  `side`
   decides which end of the member becomes the prosthesis. Measured on the
   1.84 m corner post with one identical joint: side -1 removes 0.017..0.619 m
   (126 of 495 cells); side +1 removes 0.284..1.823 m (387 cells). Left free the
   fitter sweeps both, and because damage coverage is ranked first, replacing
   the whole sound post can win -- it covers every damaged cell too. The
   component now infers the side from where the decay actually sits and says so.
10. A conservation ceiling.  `fit_program` returns `results[0]`, and with partial
   coverage allowed the sort puts `required_left` first -- so "replace the
   entire member" wins, because replacing everything covers every damaged cell.
   The full result list is now searched for the best placement under
   `max_sound_loss`, and the prosthesis extent is reported in metres.
6. Housekeeping.  The module purge no longer runs while a background thread is
   using those modules; the Gemini job has a wall-clock deadline; the cache key
   includes the prompt file; a failed re-run no longer blanks the last good
   geometry.

Inputs
------
workspace_json      str/dict    Workspace JSON, .json path, or exported .zip
beam_id             str         exact Workspace part id selected for repair
joint_program_json  str/dict    joinery-program@1 JSON from the LLM; optional
gemini_model        str         optional Gemini model [gemini-3.7-flash]
repair_step_id      str         optional expert override; LLM selects when empty
instruction         str         optional human construction instruction
variation_count     int         how many distinct concepts to ask for [3]
variant             int         which fitted proposal to output [0 = best]
temperature         float       optional sampling temperature [0.7 / 0.35 on revision]
relax_direction_claims bool     build the joint anyway when the program's own
                                assemblyDirection / geometricLockDirections
                                contradict its planes, and report that [True]
max_sound_loss      float       refuse placements that replace more than this
                                fraction of the sound member [0.5]
max_revisions       int         agent revisions after a failed fit [2]
box                 Box         oriented box from BEAM CELLS
centers             Point3d[]   world cell centres from BEAM CELLS
damage              float[]     one 0..1 damage value per centre
threshold           float       mandatory-removal threshold [0.50]
repo                str         optional repository override
run                 bool

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
variants_json         str        every concept: fitted or refused, with reasons
explanation           str[]      what this joint does, and what it does not
metrics               str        compact fit result
report                str[]      diagnostics and open questions
"""

import copy
import dataclasses
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

    try:
        gh_document = ghenv.Component.OnPingDocument()
        gh_path = str(getattr(gh_document, "FilePath", "") or "").strip()
        if gh_path:
            candidates.append(os.path.dirname(os.path.abspath(gh_path)))
    except Exception:
        pass

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

import scriptcontext as sc  # noqa: E402

# Reload the package so edits to the library land without restarting Rhino --
# but NEVER while a background Gemini thread is still executing functions from
# those very module objects, or the thread re-imports a second live copy.
_jobs_snapshot = sc.sticky.get("agentic_joinery_jobs", {})
_busy = any(
    isinstance(item, dict) and not item.get("done") for item in _jobs_snapshot.values()
)
if not _busy:
    for module_name in list(sys.modules):
        if module_name.startswith("workshop_robarch_2026"):
            sys.modules.pop(module_name)

import numpy as np  # noqa: E402
import Rhino  # noqa: E402
import Rhino.Geometry as rg  # noqa: E402

from workshop_robarch_2026 import anyjoint, evaluator, joinery_program, kernel, scoring  # noqa: E402


KEY_NAME = "gemini_api_key.txt"
HOME_KEY = os.path.join(os.path.expanduser("~"), "Documents", "robarch", KEY_NAME)
MODEL = "gemini-3.7-flash"
def _prompt_file():
    """Prefer the revised prompt when it is present; fall back to the original."""
    folder = os.path.join(REPO, "data", "prompts")
    for name in ("design_joinery_standalone_v2.md", "design_joinery_standalone.md"):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return os.path.join(folder, "design_joinery_standalone.md")


PROMPT_FILE = _prompt_file()
PROMPT_CONTRACT_VERSION = "anyjoint-six-plane-v3"
JOB_DEADLINE_SECONDS = 240.0
DIRECTIONS = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")


kept = []
prosthesis = []
plane_rectangles = []
plane_arrows = []
removed_cells = []
context_json = ""
resolved_program_json = ""
resolved_json = ""
proposal_json = ""
variants_json = ""
explanation = []
metrics = ""
report = ["Agentic Joinery Co-Designer geometry tool"]


class _ContextReady(Exception):
    """End this solution normally after preparing the manual LLM handoff."""


# --------------------------------------------------------------------------
# key discovery and workspace reading -- unchanged behaviour
# --------------------------------------------------------------------------


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
    for path in _key_paths():
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


def _stored_programs(workspace, target_id):
    """Programs already stored on the Workspace for this member."""
    found = []
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
                found.append(proposal.get("program") or proposal)
    for proposal in workspace.get("joineryProposals") or []:
        if str(proposal.get("targetPartRef")) == str(target_id):
            found.append(proposal.get("program") or proposal)
    return found


def _unwrap_programs(value, workspace, target_id):
    """Return a list of JointPrograms from the input, or from the Workspace.

    Accepts the single-program shapes this component has always accepted, plus
    a list under `jointPrograms` / `alternativePrograms`.
    """
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
        programs = _programs_from_response(raw)
        if programs:
            return programs
        if isinstance(raw, dict):
            command = raw.get("command")
            if isinstance(command, dict):
                proposal = ((command.get("payload") or {}).get("step") or {}).get(
                    "joineryProposal"
                )
                if isinstance(proposal, dict):
                    return [proposal.get("program") or proposal]
            return [raw]

    stored = _stored_programs(workspace, target_id)
    if stored:
        return stored
    raise ValueError("no JointProgram supplied or stored for beam {}".format(target_id))


def _programs_from_response(raw):
    """Pull every authored program out of a model response, preferred first."""
    if not isinstance(raw, dict):
        return []
    programs = []
    for key in ("jointProgram", "program"):
        if isinstance(raw.get(key), dict):
            programs.append(raw[key])
            break
    for key in ("alternativePrograms", "jointPrograms"):
        for item in raw.get(key) or []:
            if isinstance(item, dict):
                # tolerate {"jointProgram": {...}} wrappers inside the list
                inner = item.get("jointProgram") if isinstance(item.get("jointProgram"), dict) else item
                if inner not in programs:
                    programs.append(inner)
    return programs


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
    if wanted:
        messages.append(
            "{} of {} evidence photograph(s) attached".format(len(attachments), len(wanted))
        )
    return attachments, messages


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------


def _request_gemini_joint_programs(
    api_key,
    context,
    instruction,
    model,
    attachments=None,
    previous=None,
    feedback=None,
    variation_count=3,
    temperature=None,
):
    with open(PROMPT_FILE, "r", encoding="utf-8-sig") as handle:
        system_prompt = handle.read()
    payload = {
        "workspaceContext": context,
        "userMessage": instruction
        or "Design an actionable construction joinery for this repair area.",
        "requestedVariationCount": int(variation_count),
        "previousProgram": previous,
        "fitFeedback": feedback,
    }
    from google import genai
    from google.genai import types

    parts = [
        types.Part.from_text(text=json.dumps(payload, indent=2, ensure_ascii=False))
    ]
    for item in attachments or []:
        parts.append(
            types.Part.from_text(
                text="Evidence image {} ({})".format(item["id"], item["name"])
            )
        )
        parts.append(types.Part.from_bytes(data=item["data"], mime_type=item["mimeType"]))

    client = genai.Client(api_key=str(api_key).strip())
    config = {
        "response_mime_type": "application/json",
        # The prompt is the contract; attached photographs are evidence. Sending
        # it as a system instruction rather than as one more user text part is
        # what keeps that distinction real.
        "system_instruction": system_prompt,
    }
    if temperature is not None:
        config["temperature"] = float(temperature)
    try:
        response = client.models.generate_content(
            model=str(model or MODEL).strip(),
            contents=parts,
            config=types.GenerateContentConfig(**config),
        )
    except TypeError:
        # Older google-genai without system_instruction on this config object.
        config.pop("system_instruction", None)
        response = client.models.generate_content(
            model=str(model or MODEL).strip(),
            contents=[types.Part.from_text(text=system_prompt)] + parts,
            config=types.GenerateContentConfig(**config),
        )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError(
            "Gemini returned no text response (blocked, or the output token limit was hit)"
        )
    result = _json_from_model_text(text)
    programs = _programs_from_response(result)
    if not programs:
        raise RuntimeError("Gemini returned no jointProgram")
    return programs, result


def _llm_cache_key(context_text, instruction, model, variation_count):
    component_id = str(getattr(ghenv.Component, "InstanceGuid", "component"))
    try:
        with open(PROMPT_FILE, "rb") as handle:
            prompt_hash = hashlib.sha256(handle.read()).hexdigest()[:16]
    except Exception:
        prompt_hash = "no-prompt"
    payload = "\n".join(
        (
            PROMPT_CONTRACT_VERSION,
            prompt_hash,
            context_text,
            str(instruction or ""),
            str(model or ""),
            str(int(variation_count)),
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


def _start_gemini_job(job, api_key, context, instruction, model, attachments,
                      previous=None, feedback=None, variation_count=3,
                      temperature=None):
    job["done"] = False
    job["error"] = None
    job["t0"] = time.time()

    def work():
        try:
            programs, response = _request_gemini_joint_programs(
                api_key, context, instruction, model,
                attachments=attachments, previous=previous, feedback=feedback,
                variation_count=variation_count, temperature=temperature,
            )
            job["programs"] = programs
            job["response"] = response
        except Exception as exc:
            job["error"] = str(exc)
        finally:
            job["done"] = True

    threading.Thread(target=work, daemon=True).start()


# --------------------------------------------------------------------------
# frame and damage
# --------------------------------------------------------------------------


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


def _damage_context(frame, points, values, gate, stations=12):
    """Describe the decay as a shape, not as a bounding box.

    `to_local` measures from a CORNER of the member in model units; the
    normalised figures are fractions of the member and are the ones the model
    should reason with. Both are labelled, and the frame is stated in the
    payload rather than left to be guessed.
    """
    local = scoring.to_local(points, frame)
    mandatory = values >= float(gate)
    dimensions = np.array([frame["width"], frame["length"], frame["height"]], float)
    result = {
        "frameNote": (
            "local coordinates are measured from a CORNER of the member, in model units; "
            "u = section width, v = beam axis, w = section height. Normalised values are "
            "fractions of the member. The joint window itself is placed by the fitter -- "
            "author the SHAPE, the aspect and the replacement side, not an absolute station."
        ),
        "threshold": float(gate),
        "cellCount": int(len(points)),
        "mandatoryCellCount": int(mandatory.sum()),
        "maximumDamage": float(values.max()) if len(values) else 0.0,
        "localAxes": ["section_u", "beam_axis_v", "section_w"],
        "beamDimensions": dimensions.tolist(),
    }

    # How sensitive is the required set to where the threshold was drawn?
    band = [
        {
            "threshold": round(float(gate) + delta, 3),
            "mandatoryCellCount": int((values >= float(gate) + delta).sum()),
        }
        for delta in (-0.1, 0.0, 0.1)
        if 0.0 <= float(gate) + delta <= 1.0
    ]
    result["thresholdSensitivity"] = band

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
                "touchesEnd": {
                    "low": bool(lo[1] <= 0.05 * dimensions[1]),
                    "high": bool(hi[1] >= 0.95 * dimensions[1]),
                },
            }
        )

        # Station profile: decay as a function of position along the member.
        edges = np.linspace(0.0, dimensions[1], int(stations) + 1)
        index = np.clip(np.digitize(local[:, 1], edges) - 1, 0, int(stations) - 1)
        profile = []
        for station in range(int(stations)):
            pick = index == station
            if not pick.any():
                continue
            profile.append(
                {
                    "vFraction": round(float((edges[station] + edges[station + 1]) * 0.5 / dimensions[1]), 3),
                    "requiredFraction": round(float(mandatory[pick].mean()), 3),
                    "maxDamage": round(float(values[pick].max()), 3),
                }
            )
        result["stationProfile"] = profile

        # Where the decay sits in the section, and which faces it breaks out
        # of. This is what says whether the joint may be asymmetric, and which
        # way it should lean.
        span_u = (damaged[:, 0] / dimensions[0])
        span_w = (damaged[:, 2] / dimensions[2])
        result["sectionExtent"] = {
            "u": [round(float(span_u.min()), 3), round(float(span_u.max()), 3)],
            "w": [round(float(span_w.min()), 3), round(float(span_w.max()), 3)],
            "note": "fractions of the section; 0 and 1 are opposite faces",
        }
        result["touchesFace"] = {
            "-u": bool(span_u.min() <= 0.05), "+u": bool(span_u.max() >= 0.95),
            "-w": bool(span_w.min() <= 0.05), "+w": bool(span_w.max() >= 0.95),
        }
    return result


# --------------------------------------------------------------------------
# Boolean evaluation -- bounded, with named failure
# --------------------------------------------------------------------------


def _serialise(node):
    if isinstance(node, str):
        return node
    op, children = node
    return "%s(%s)" % (op, ", ".join(_serialise(child) for child in children))


def _bind_to_stock(node, stock):
    """Rewrite every removal term so it intersects INTO the bounded stock.

    `Intersection(lhf_1, lhf_2)` asks Rhino to intersect two oversized cutter
    prisms that -- by construction in kernel.half_space_cut -- share their side
    faces exactly, and it returns null. Since every removal group is a subset
    of the stock, `Intersection(lhf_0, lhf_1, lhf_2)` is the same solid with
    the accumulator bounded by the beam. Provably identical downstream:
    kept = stock - U(G) and prosthesis = stock & U(G) are unchanged when each
    G is replaced by G & stock.
    """
    if isinstance(node, str):
        return node
    op, children = node
    kids = [_bind_to_stock(child, stock) for child in children]
    if op == "Intersection":
        if not any(isinstance(k, str) and k == stock for k in kids):
            kids = [stock] + kids
    elif op == "Union":
        kids = [
            ("Intersection", [stock, k]) if isinstance(k, str) and k != stock else k
            for k in kids
        ]
    return (op, kids)


def _bounded_expression(expression, stock="lhf_0"):
    try:
        return _serialise(_bind_to_stock(evaluator.parse_expression(expression), stock))
    except Exception:
        return expression


def _model_tolerance():
    try:
        value = float(Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance)
        return value if value > 0 else evaluator.TOL
    except Exception:
        return evaluator.TOL


def _evaluate_part(part, notes):
    """Evaluate one part, bounded expression first, then documented fallbacks."""
    base = _model_tolerance()
    attempts = []
    for value in (base, evaluator.TOL, 0.5 * base, 2.0 * base):
        value = float(value)
        if value > 0 and value not in attempts:
            attempts.append(value)

    bounded = dict(part)
    bounded["expression"] = _bounded_expression(part["expression"])
    errors = []
    for label, candidate in (("bounded", bounded), ("authored", part)):
        if label == "authored" and candidate["expression"] == bounded["expression"]:
            continue
        for tolerance in attempts:
            try:
                breps = evaluator.evaluate_part(candidate, tolerance)
                if label == "authored":
                    notes.append(
                        "part %s: bounded expression failed, authored expression "
                        "succeeded at %g" % (part.get("name"), tolerance)
                    )
                return breps
            except Exception as exc:
                errors.append("%s @ %g: %s" % (label, tolerance, exc))
    try:
        notes.extend(
            "cut diagnosis: %s" % line
            for line in evaluator.diagnose_cuts(part, attempts[0])
        )
    except Exception:
        pass
    raise ValueError(
        "part %s failed every Boolean route: %s"
        % (part.get("name"), " | ".join(errors[:4]))
    )


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


# --------------------------------------------------------------------------
# fitting: one program in, one verdict out
# --------------------------------------------------------------------------


# Every hard gate in anyjoint.construction_failures, as (measured key,
# constraint key, library default, sense). Printing this table is the whole
# point: a refusal must say which number missed which number, and by how much.
_GATES = (
    ("engagementSections", "minimumEngagementSections", 1.0, ">="),
    ("interfaceAreaRatio", "minimumInterfaceAreaRatio", 1.0, ">="),
    ("medianLigamentRatio", "minimumLigamentRatio", 0.08, ">="),
    ("minimumPlaneAngleDeg", "minimumPlaneAngleDeg", 10.0, ">="),
    ("supportPlaneCount", "maximumSupportPlanes", 6, "<="),
    ("replacementVolumeFraction", "minimumReplacementVolumeFraction", 0.02, ">="),
    ("replacementVolumeFraction", "maximumReplacementVolumeFraction", 0.90, "<="),
)

# Deliberately permissive floor used by the "relaxed gates" rung. These are not
# good joints -- they are the widest values still worth showing on screen.
_PERMISSIVE = {
    "minimumEngagementSections": 0.5,
    "targetEngagementSections": 0.5,
    "minimumInterfaceAreaRatio": 0.6,
    "targetInterfaceAreaRatio": 0.6,
    "minimumLigamentRatio": 0.03,
    "minimumPlaneAngleDeg": 4.0,
    "maximumSupportPlanes": 8,
    "minimumReplacementVolumeFraction": 0.005,
    "maximumReplacementVolumeFraction": 0.98,
    "assemblyDirection": None,
    "geometricLockDirections": [],
}


def _gate_table(metrics, constraints):
    """Measured against required, one line per gate, worst first."""
    constraints = constraints or {}
    rows = []
    for measured_key, constraint_key, default, sense in _GATES:
        required = float(constraints.get(constraint_key, default))
        actual = float(metrics.get(measured_key, 0.0))
        ok = actual + 1e-9 >= required if sense == ">=" else actual <= required + 1e-9
        slack = (actual - required) if sense == ">=" else (required - actual)
        rows.append(
            {
                "gate": constraint_key,
                "measured": round(actual, 3),
                "required": "%s %.3f" % (sense, required),
                "pass": bool(ok),
                "slack": round(slack, 3),
            }
        )
    rows.sort(key=lambda row: (row["pass"], row["slack"]))
    return rows


def _gate_lines(metrics, constraints, only_failures=False):
    lines = []
    for row in _gate_table(metrics, constraints):
        if only_failures and row["pass"]:
            continue
        lines.append(
            "  {:<34} measured {:>8}   required {:<10} {}".format(
                row["gate"], row["measured"], row["required"],
                "ok" if row["pass"] else "FAIL by %.3f" % abs(row["slack"]),
            )
        )
    return lines


def _without_direction_claims(program):
    """The same program with only its two self-declared direction claims dropped.

    `assemblyDirection` and `geometricLockDirections` are the only gates that
    test the model's *intent* rather than the geometry's fitness, and they are
    checked BEFORE placement -- so when they disagree with the authored planes
    every candidate is discarded and nothing is ever built. Every numeric gate
    and full mandatory coverage stay armed.
    """
    relaxed = copy.deepcopy(program)
    constraints = relaxed.setdefault("constructionConstraints", {})
    constraints["assemblyDirection"] = None
    constraints["geometricLockDirections"] = []
    return relaxed


def _with_permissive_gates(program):
    """Drop to the permissive floor, keeping full mandatory damage coverage."""
    relaxed = copy.deepcopy(program)
    constraints = relaxed.setdefault("constructionConstraints", {})
    constraints.update(copy.deepcopy(_PERMISSIVE))
    return relaxed


def _claim_check(resolved_original, result):
    """Which declared direction claims the built geometry does not deliver."""
    measured = result.get("construction_metrics") or {}
    clear = [str(value) for value in (measured.get("clearExtractionDirections") or [])]
    constraints = (resolved_original or {}).get("constructionConstraints") or {}
    unmet = []
    assembly = constraints.get("assemblyDirection")
    if assembly is not None and str(assembly).upper() not in clear:
        unmet.append(
            "declared insertion along {} is blocked by this geometry".format(assembly)
        )
    for value in constraints.get("geometricLockDirections") or []:
        if str(value).upper() in clear:
            unmet.append(
                "declared geometric lock in {} is not delivered; that direction is open".format(value)
            )
    return unmet, clear


# The ladder. Each rung gives up one thing and says so. The run only ends with
# nothing on screen when no placement of these planes builds a solid at all.
_RUNGS = (
    ("contract", None, True, False, "as authored"),
    ("claims_relaxed", _without_direction_claims, True, False,
     "the program's own assemblyDirection and geometricLockDirections set aside"),
    ("relaxed_gates", _with_permissive_gates, True, False,
     "the program's numeric construction gates dropped to a permissive floor"),
    ("coverage_only", None, False, False,
     "every construction gate off; full damage coverage still required"),
    ("partial", None, False, True,
     "every construction gate off and partial damage coverage allowed"),
)


# Half a section depth of overhang is absorbed by the kernel's coverage-probed
# trim; beyond that the group is genuinely open and will sweep the member.
_ESCAPE_MARGIN = 0.5


def _escape_depth(template, aspect, margin=_ESCAPE_MARGIN):
    """How far a removal reaches PAST the kept-side end of the joint window.

    Every plane is placed as a deliberately oversized prism, so a removal group
    that is not closed along the member axis on the kept side does not stop at
    the window -- it sweeps the whole beam. Nothing upstream notices: the
    construction metrics sample only v in [0, aspect], where such a group looks
    perfectly well behaved. This is the difference between a splice and a
    demolition, and it is invisible until the geometry is placed.
    """
    ys = np.linspace(-3.0 * float(aspect), float(aspect), 200)
    xs = zs = np.linspace(-0.48, 0.48, 7)
    grid = np.array([[x, y, z] for y in ys for x in xs for z in zs])
    inside = anyjoint._template_removal_mask(template, grid)
    if not inside.any():
        return 0.0
    return max(0.0, -(float(grid[inside][:, 1].min()) + float(margin)))


def _open_groups(candidate):
    """(total escape, [(group index, plane ids, escape)]) for one candidate."""
    template = candidate.template
    aspect = float(candidate.parameters.get("aspect", 3.0))
    try:
        total = _escape_depth(template, aspect)
    except Exception:
        return 0.0, []
    if total <= 0.0:
        return 0.0, []
    culprits = []
    for index, group in enumerate(template.groups):
        try:
            depth = _escape_depth(dataclasses.replace(template, groups=(group,)), aspect)
        except Exception:
            continue
        if depth > 0.0:
            culprits.append(
                (index, ["P%d" % slot for slot in group], round(depth, 2))
            )
    return total, culprits


def _screen_candidates(grammar):
    """Split a grammar bank into groups that stay in the window and ones that do not."""
    clean, escaping, lines = [], [], []
    for candidate in grammar:
        total, culprits = _open_groups(candidate)
        if total <= 0.0:
            clean.append(candidate)
            continue
        escaping.append(candidate)
        for index, ids, depth in culprits:
            lines.append(
                "removalGroup {} ({}) is OPEN along the beam axis on the kept side: its "
                "removal reaches {:.1f} section depths past the joint window, so the "
                "placed cutter sweeps the whole member instead of cutting off the "
                "decayed end. Close it with a plane whose normal has a negative "
                "v-component -- a shoulder facing the timber that stays.".format(
                    index, ", ".join(ids), depth
                )
            )
        if not culprits:
            lines.append(
                "candidate {} removes material {:.1f} section depths past the kept-side "
                "end of the joint window".format(candidate.candidate_id, total)
            )
    seen, unique = set(), []
    for line in lines:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    return clean, escaping, unique


def _replacement_sides(frame, v0, v1):
    """Which end the damage actually sits at, as the fitter's `side` values.

    `side` decides which end of the member becomes the prosthesis, and it is the
    single largest lever on the outcome: the same planes on the same post remove
    0.017..0.619 m at side -1 and 0.284..1.823 m at side +1. Left free, the
    fitter sweeps both, and with damage coverage ranked first the wrong end can
    win -- replacing the whole sound member covers every damaged cell too. The
    decay tells us the answer, so we take it rather than search for it.
    """
    length = float(frame["length"])
    near, far = float(v0), length - float(v1)
    if near <= far and near <= 0.25 * length:
        return [-1], "damage reaches the low end ({:.3f} m in)".format(near)
    if far < near and far <= 0.25 * length:
        return [1], "damage reaches the high end ({:.3f} m in)".format(far)
    return None, "damage is not end-localised -- this is a mid-member patch, not a splice"


def _numbers(value, default):
    try:
        out = [float(item) for item in value]
        return out if out else list(default)
    except Exception:
        return list(default)


def _prosthesis_extent(frame, points, result):
    """Where the replacement piece actually starts and ends along the member."""
    try:
        removed = scoring.removed_mask(points, result["repair"])
        if not removed.any():
            return None
        local = scoring.to_local(points, frame)[removed][:, 1]
        length = float(frame["length"])
        return {
            "from": float(local.min()), "to": float(local.max()),
            "length": float(local.max() - local.min()),
            "fractionOfMember": float((local.max() - local.min()) / max(length, 1e-9)),
            "memberLength": length,
        }
    except Exception:
        return None


def _fit_rung(program, frame, points, values, target_id, gate, enforce, partial,
              ceiling):
    """One rung of the ladder, choosing the least destructive placement.

    `fit_program` returns only `results[0]`, and with partial coverage allowed
    the sort puts `required_left` first -- so the placement that replaces the
    ENTIRE member always wins, because replacing everything covers every
    damaged cell. That is how a base repair ends up proposing to renew 93% of a
    sound post. Here the full result list is searched for the best placement
    that stays under a sound-loss ceiling, and the ceiling itself is reported.
    """
    grammar, resolved, warnings = joinery_program.program_candidates(
        program, beam_id=target_id
    )
    warnings = list(warnings)

    # Screen out plane programs whose removal escapes the joint window before
    # spending any placements on them.
    clean, escaping, escape_lines = _screen_candidates(grammar)
    warnings.extend(escape_lines)
    if clean:
        if escaping:
            warnings.append(
                "dropped {} of {} plane variant(s) whose removal escapes the joint "
                "window".format(len(escaping), len(grammar))
            )
        grammar = clean
    elif escaping:
        warnings.append(
            "EVERY plane variant of this program escapes the joint window. Whatever is "
            "built below will replace most of the member; the fix is in removalGroups, "
            "not in the angles."
        )

    objective = resolved.get("fitObjective") or {}
    threshold = float(
        gate if gate is not None else objective.get("damageThreshold", 0.5)
    )

    # The search window bounds where the joint band may sit:
    #   lo = (v0 - window*section)/length,  hi = (v1 + window*section)/length
    # If it is too small the band cannot clear the decay, so the kernel's end
    # trim stops short of the rotten stub and the joint features are left to
    # cover it -- which a lap or a scarf cannot do, because they keep part of
    # the section. Coverage then fails and every strict rung refuses, for a
    # reason that has nothing to do with the joint. Size the floor from the
    # decay itself.
    authored_window = float(objective.get("searchWindowSections", 1.5))
    window = authored_window
    window_note = None
    sides = [int(value) for value in _numbers(objective.get("replacementSides"), (1, -1))]
    side_note = None
    try:
        v0, v1, _mandatory = anyjoint.damage_extent(points, values, frame, threshold)
        if v0 is not None:
            inferred, reason = _replacement_sides(frame, v0, v1)
            if inferred and inferred != sides:
                side_note = (
                    "replacementSides forced to {} -- {}. Left free, the fitter can "
                    "replace the sound end instead, which also covers every damaged "
                    "cell and therefore ranks first.".format(inferred, reason)
                )
                sides = inferred
            section = min(float(frame["width"]), float(frame["height"]))
            decay_sections = (float(v1) - float(v0)) / max(section, 1e-9)
            aspect = float((resolved.get("geometry") or {}).get("aspect", 3.0))
            needed = decay_sections + aspect + 0.5
            if needed > authored_window:
                window = needed
                window_note = (
                    "searchWindowSections raised {:.1f} -> {:.1f}: the decay runs {:.1f} "
                    "section depths and the joint is {:.1f} long, so the authored window "
                    "could not place the joint clear of the rot".format(
                        authored_window, needed, decay_sections, aspect
                    )
                )
    except Exception:
        pass

    results, report_lines = anyjoint.search(
        frame, points, values, threshold=threshold, grammar=grammar,
        n_positions=max(2, min(25, int(objective.get("positionSamples", 7)))),
        window=window,
        margin=float(objective.get("damageMarginSections", 1.0)),
        rotations=_numbers(objective.get("rotationsDeg"), (0.0, 90.0, 180.0, 270.0)),
        sides=sides,
        complexity_weight=float(objective.get("complexityWeight", 0.0)),
        construction_constraints=(
            resolved.get("constructionConstraints") or None if enforce else None
        ),
        verify=True, allow_partial=partial,
    )
    lines = list(warnings)
    if side_note:
        lines.append(side_note)
    if window_note:
        lines.append(window_note)
    lines.extend(report_lines)
    if not results:
        return None, resolved, lines, None

    def loss(item):
        denominator = max(1, int(item.get("n_sound", 0)))
        return float(item.get("sound_sacrificed", 0)) / denominator

    within = [item for item in results if loss(item) <= float(ceiling)]
    if within:
        chosen, note = within[0], None
        if chosen is not results[0]:
            note = (
                "skipped {} higher-ranked placement(s) that would have replaced more "
                "than {:.0%} of the sound member".format(
                    results.index(chosen), float(ceiling)
                )
            )
    else:
        chosen = results[0]
        note = (
            "EVERY placement of these planes replaces more than {:.0%} of the sound "
            "member ({:.0%} here). That is a demolition, not a repair. The usual cause "
            "is that the authored planes reach full section at the far end of the joint "
            "window, so the kernel's end trim runs the length of the member instead of "
            "cutting off the decayed stub.".format(float(ceiling), loss(chosen))
        )
    return chosen, resolved, lines, note


def _fit_with_diagnosis(program, frame, points, values, target_id, gate,
                        relax=True, ceiling=0.5):
    """Fit down a ladder of increasingly relaxed contracts and report the rung.

    The first rung is the unchanged strict contract. Each rung below it gives up
    one specific thing, and whatever is finally built is reported together with
    the full gate table measured against what the program ORIGINALLY asked for,
    so a participant can see exactly how far the geometry is from its own brief.
    """
    attempts = []
    original_constraints = None
    rungs = _RUNGS if relax else _RUNGS[:1]

    for name, transform, enforce, partial, note in rungs:
        candidate = transform(program) if transform is not None else program
        try:
            result, resolved, fit_report, ceiling_note = _fit_rung(
                candidate, frame, points, values, target_id, gate,
                enforce, partial, ceiling,
            )
        except (joinery_program.JointProgramError, ValueError) as exc:
            return {
                "result": None, "resolved": program, "mode": None, "unmet": [],
                "report": ["JointProgram validation failed: {}".format(exc)],
                "diagnosis": {"stage": "validation", "error": str(exc)},
            }
        if original_constraints is None:
            original_constraints = copy.deepcopy(
                (resolved or {}).get("constructionConstraints") or {}
            )

        if result is None:
            attempts.append((name, note, fit_report))
            continue

        measured = result.get("construction_metrics") or {}
        unmet, clear = _claim_check({"constructionConstraints": original_constraints}, result)
        lines = []
        if name != "contract":
            lines.append(
                "BUILT ON RUNG '{}' -- {}. The stricter rungs above produced nothing.".format(
                    name, note
                )
            )
        lines.extend(fit_report)
        if ceiling_note:
            lines.append(ceiling_note)
        try:
            repair = result["repair"]
            band = [round(float(value), 3) for value in repair.get("band", [])]
            names = [cut.get("name") for cut in repair["parts"][0]["cuts"]]
            lines.append(
                "placed band {} m, interface {:.3f} m, flipped {}, cutters {}".format(
                    band, float(repair.get("interface_length", 0.0)),
                    bool(repair.get("flipped")), ", ".join(str(n) for n in names),
                )
            )
            lines.append(
                "removal expression: " + str(repair["parts"][1].get("expression"))
            )
        except Exception:
            pass
        extent = _prosthesis_extent(frame, points, result)
        if extent:
            lines.append(
                "prosthesis spans {:.3f}..{:.3f} m of a {:.2f} m member ({:.0%}); "
                "removes {} of {} sound cells".format(
                    extent["from"], extent["to"], extent["memberLength"],
                    extent["fractionOfMember"],
                    int(result.get("sound_sacrificed", 0)),
                    int(result.get("n_sound", 0)),
                )
            )
        lines.append("measured against what the program asked for:")
        lines.extend(_gate_lines(measured, original_constraints))
        lines.append(
            "  clear extraction directions: {}".format(
                ", ".join(clear) if clear else "none -- fully interlocked"
            )
        )
        lines.extend("  claim not met: " + item for item in unmet)
        if int(result.get("required_left", 0)):
            lines.append(
                "  WARNING: {} required damaged cell(s) remain in the kept member".format(
                    int(result.get("required_left", 0))
                )
            )
        return {"result": result, "resolved": resolved, "mode": name,
                "unmet": unmet, "report": lines,
                "gates": _gate_table(measured, original_constraints),
                "diagnosis": None}

    # Nothing built on any rung: the planes themselves do not produce a solid.
    lines = ["no rung produced geometry -- the planes do not build a valid two-part solid"]
    for name, note, fit_report in attempts:
        tail = [line for line in fit_report if "rejected before placement" in line]
        lines.append("  rung '{}' ({}): {}".format(
            name, note, tail[0] if tail else (fit_report[-1] if fit_report else "no fit")
        ))
    lines.append(
        "The Boolean region is probably empty, degenerate, or it swallows the whole "
        "joint window. Check removalGroups before changing angles."
    )
    return {
        "result": None, "resolved": program, "mode": None, "unmet": [],
        "report": lines,
        "diagnosis": {"stage": "fit", "rungs": [name for name, _, _ in attempts],
                      "note": lines[-1]},
    }


# --------------------------------------------------------------------------
# explanation: measured, not narrated
# --------------------------------------------------------------------------


def _explain(resolved, result, mode="contract", unmet=()):
    """What this joint does, and what it does not -- from the geometry."""
    lines = []
    _RUNG_NOTE = {
        "claims_relaxed": "This joint was built with the program's own assemblyDirection "
                          "and geometricLockDirections set aside. Every numeric gate and "
                          "full damage coverage still passed.",
        "relaxed_gates": "This joint does NOT meet the construction gates the program "
                         "asked for. It is shown so the shape can be judged; the gate "
                         "table in the report says by how much it misses.",
        "coverage_only": "No construction gate was applied. This joint covers the damage "
                         "and nothing more has been checked about it.",
        "partial": "No construction gate was applied AND the damage is not fully covered. "
                   "This is a diagnostic shape, not a repair.",
    }
    if mode in _RUNG_NOTE:
        lines.append(_RUNG_NOTE[mode])
    if unmet:
        lines.extend("  " + item for item in unmet)
    measured = result.get("construction_metrics") or {}
    clear = [str(value) for value in (measured.get("clearExtractionDirections") or [])]
    blocked = [value for value in DIRECTIONS if value not in clear]

    if blocked:
        lines.append(
            "LOCKS: the prosthesis cannot be withdrawn along {}.".format(", ".join(blocked))
        )
    else:
        lines.append("LOCKS: none. The prosthesis is free in every axis direction.")
    if clear:
        lines.append(
            "OPEN: nothing in this geometry resists movement along {}. "
            "Retention there depends on bearing, friction or a fastener, which "
            "this model does not represent.".format(", ".join(clear))
        )

    behaviour = resolved.get("jointBehaviour") or {}
    constraints = resolved.get("constructionConstraints") or {}
    claimed = [str(value) for value in (constraints.get("geometricLockDirections") or [])]
    unmet = [value for value in claimed if value in clear]
    if claimed:
        lines.append(
            "CLAIMED LOCKS: {}{}".format(
                ", ".join(claimed),
                "" if not unmet else "  --  NOT DELIVERED in {}".format(", ".join(unmet)),
            )
        )
    if behaviour.get("tensionRetention"):
        lines.append("AUTHORED TENSION RETENTION: {}".format(behaviour["tensionRetention"]))

    lines.append(
        "BEARING AND FIT: engagement {:.2f} sections, interface ratio {:.2f}, "
        "median ligament {:.2f}, {} support plane(s), smallest plane angle {:.0f} deg.".format(
            float(measured.get("engagementSections", 0.0)),
            float(measured.get("interfaceAreaRatio", 0.0)),
            float(measured.get("medianLigamentRatio", 0.0)),
            int(measured.get("supportPlaneCount", 0)),
            float(measured.get("minimumPlaneAngleDeg", 0.0)),
        )
    )
    lines.append(
        "MATERIAL: removes {} sound cell(s) of {} (weighted {:.2f}); leaves {} "
        "required damaged cell(s); replacement is {:.0%} of the joint window.".format(
            int(result.get("sound_sacrificed", 0)),
            int(result.get("n_sound", 0)),
            float(result.get("sound_sacrificed_weighted", 0.0)),
            int(result.get("required_left", 0)),
            float(measured.get("replacementVolumeFraction", 0.0)),
        )
    )
    lines.append(
        "PLACEMENT: position {:.3f}, rotation {:.0f} deg, replacement side {:+d}, "
        "interface scale {:.2f}.".format(
            float(result.get("position", 0.0)),
            float(result.get("rotate_deg", 0.0)),
            int(result.get("side", 1)),
            float(result.get("interface_scale", 1.0)),
        )
    )
    for question in resolved.get("openQuestions") or []:
        lines.append("OPEN QUESTION: {}".format(question))
    lines.append(
        "NOT MODELLED: pegs, keys and any fastener; neighbour connections; "
        "shrinkage and fitting clearance."
    )
    return lines


def _rank_key(entry):
    result = entry["result"]
    measured = result.get("construction_metrics") or {}
    clear = len(measured.get("clearExtractionDirections") or [])
    return (
        int(result.get("required_left", 0)),
        0 if entry.get("mode") == "contract" else 1,      # met its own claims first
        clear,                                            # fewer open directions next
        float(result.get("sound_sacrificed_weighted", 0.0)),
        int(measured.get("supportPlaneCount", 0)),
        str(result.get("candidate_id", "")),
    )


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

_STICKY_LAST = "agentic_joinery_last_good"

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
            0.5 if globals().get("threshold") is None else float(globals().get("threshold"))
        )
        context["cellularDamage"] = _damage_context(frame, points, values, gate)
        context_json = json.dumps(context, indent=2, ensure_ascii=False)

        gemini_model = str(
            globals().get("gemini_model") or os.environ.get("GEMINI_MODEL") or MODEL
        ).strip()
        wanted = max(1, min(6, int(globals().get("variation_count") or 3)))
        temperature_input = globals().get("temperature")
        attachments, attachment_report = _evidence_attachments(workspace_value, context)
        report.extend(attachment_report)
        instruction_text = str(globals().get("instruction") or "")
        signature = _llm_cache_key(context_json, instruction_text, gemini_model, wanted)

        programs_from_gemini = False
        gemini_job = None
        gemini_key = None
        try:
            programs = _unwrap_programs(
                globals().get("joint_program_json"), workspace, target_id
            )
            report.append(
                "program source: input or stored Workspace proposal ({} concept(s))".format(
                    len(programs)
                )
            )
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
                    "signature": signature, "done": False, "error": None,
                    "programs": None, "response": None, "revision": 0, "t0": time.time(),
                }
                jobs[job_id] = gemini_job
                _start_gemini_job(
                    gemini_job, gemini_key, context, instruction_text, gemini_model,
                    attachments, variation_count=wanted,
                    temperature=(0.7 if temperature_input is None else float(temperature_input)),
                )
                report.append(
                    "STATUS: Gemini is authoring {} JointProgram concept(s)".format(wanted)
                )
                _wake_up()
                raise _ContextReady()
            if not gemini_job.get("done"):
                waited = time.time() - float(gemini_job.get("t0") or time.time())
                if waited > JOB_DEADLINE_SECONDS:
                    gemini_job["done"] = True
                    gemini_job["error"] = (
                        "no response after {:.0f} s; press run again to retry".format(waited)
                    )
                else:
                    report.append("STATUS: Gemini working, {:.0f} s so far".format(waited))
                    _wake_up()
                    raise _ContextReady()
            if gemini_job.get("error"):
                raise RuntimeError("Gemini failed: {}".format(gemini_job["error"]))
            programs = gemini_job.get("programs") or []
            agent_response = gemini_job.get("response") or {}
            if not programs:
                raise RuntimeError("Gemini job completed without a JointProgram")
            programs_from_gemini = True
            report.append(
                "Gemini authored {} concept(s): {}".format(
                    len(programs), agent_response.get("summary", "done")
                )
            )

        raw_revisions = globals().get("max_revisions")
        revisions = 2 if raw_revisions is None else max(0, min(3, int(raw_revisions)))

        # ---- fit every concept independently -----------------------------
        relax = globals().get("relax_direction_claims")
        relax = True if relax is None else bool(relax)
        ceiling = globals().get("max_sound_loss")
        ceiling = 0.5 if ceiling is None else max(0.05, min(1.0, float(ceiling)))
        fitted, refused = [], []
        for index, program in enumerate(programs):
            outcome = _fit_with_diagnosis(
                program, frame, points, values, target_id, gate,
                relax=relax, ceiling=ceiling,
            )
            label = str(program.get("id") or "concept_{}".format(index))
            lines = outcome["report"]
            if outcome["result"] is None:
                refused.append({"id": label, "resolved": outcome["resolved"],
                                "diagnosis": outcome["diagnosis"], "report": lines})
                report.append("REFUSED {}".format(label))
                report.extend(lines)
            else:
                fitted.append({"id": label, "result": outcome["result"],
                               "resolved": outcome["resolved"], "mode": outcome["mode"],
                               "unmet": outcome["unmet"], "gates": outcome.get("gates"),
                               "report": lines})
                report.append(
                    "FITTED {} [rung: {}] {}".format(
                        label, outcome["mode"], anyjoint.result_summary(outcome["result"])
                    )
                )
                report.extend(lines)

        # ---- one bounded revision, driven by the named gate failures ------
        if not fitted and programs_from_gemini and gemini_job is not None \
                and int(gemini_job.get("revision") or 0) < revisions:
            worst = refused[0] if refused else {}
            feedback = {
                "attempt": int(gemini_job.get("revision") or 0) + 1,
                "whatFailed": worst.get("diagnosis"),
                "reportLines": worst.get("report", [])[-4:],
                "instruction": (
                    "Every concept was refused. Change the planes or the Boolean "
                    "groups so the named gates pass with margin, keeping the "
                    "construction reasoning that is still valid. Do not restate "
                    "the previous program."
                ),
            }
            next_revision = int(gemini_job.get("revision") or 0) + 1
            gemini_job["revision"] = next_revision
            _start_gemini_job(
                gemini_job, gemini_key, context, instruction_text, gemini_model,
                attachments, previous=worst.get("resolved"), feedback=feedback,
                variation_count=wanted,
                temperature=(0.35 if temperature_input is None else float(temperature_input)),
            )
            report.append(
                "STATUS: Gemini revision {} running, with the failed gates named".format(
                    next_revision
                )
            )
            _wake_up()
            raise _ContextReady()

        fitted.sort(key=_rank_key)
        variants_json = json.dumps(
            {
                "fitted": [
                    {
                        "id": entry["id"],
                        "mode": entry.get("mode"),
                        "gates": entry.get("gates"),
                        "claimsNotMet": entry.get("unmet"),
                        "summary": (entry["resolved"].get("contextAssessment") or {}).get("reasoning"),
                        "constructionNotes": entry["resolved"].get("constructionNotes"),
                        "metrics": entry["result"].get("construction_metrics"),
                        "requiredLeft": int(entry["result"].get("required_left", 0)),
                        "soundRemoved": int(entry["result"].get("sound_sacrificed", 0)),
                        "explanation": _explain(
                            entry["resolved"], entry["result"],
                            entry.get("mode"), entry.get("unmet") or (),
                        ),
                    }
                    for entry in fitted
                ],
                "refused": [
                    {"id": item["id"], "diagnosis": item["diagnosis"]} for item in refused
                ],
            },
            indent=2, ensure_ascii=False,
        )

        if not fitted:
            report.append(
                "STATUS: no concept passed the construction contract. The gate "
                "failures above are what to change."
            )
            previous = sc.sticky.get(_STICKY_LAST)
            if previous:
                report.append(
                    "showing the last good geometry from this component; it is STALE"
                )
                kept, prosthesis = previous["kept"], previous["prosthesis"]
                plane_rectangles = previous["rectangles"]
                plane_arrows = previous["arrows"]
                removed_cells = previous["removed"]
                resolved_program_json = previous["program"]
                resolved_json = previous["program"]
                proposal_json = previous["proposal"]
                explanation = ["STALE -- from the previous run"] + previous["explanation"]
                metrics = previous["metrics"]
        else:
            pick = int(globals().get("variant") or 0)
            pick = max(0, min(len(fitted) - 1, pick))
            chosen = fitted[pick]
            result, resolved = chosen["result"], chosen["resolved"]
            report.append(
                "showing variant {} of {}: {}".format(pick + 1, len(fitted), chosen["id"])
            )

            resolved_program_json = json.dumps(resolved, indent=2, ensure_ascii=False)
            resolved_json = resolved_program_json
            proposal = joinery_program.proposal_record(resolved, result, report)
            proposal_json = json.dumps(proposal, indent=2, ensure_ascii=False)

            repair = result["repair"]
            notes = []
            for part in repair["parts"]:
                breps = _evaluate_part(part, notes)
                if part["name"] == "kept":
                    kept.extend(breps)
                elif part["name"] == "prosthesis":
                    prosthesis.extend(breps)
            report.extend(notes)

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
            metrics = anyjoint.result_summary(result)
            explanation = _explain(
                resolved, result, chosen.get("mode"), chosen.get("unmet") or ()
            )
            report.extend(explanation)

            sc.sticky[_STICKY_LAST] = {
                "kept": list(kept), "prosthesis": list(prosthesis),
                "rectangles": list(plane_rectangles), "arrows": list(plane_arrows),
                "removed": list(removed_cells), "program": resolved_program_json,
                "proposal": proposal_json, "explanation": list(explanation),
                "metrics": metrics,
            }
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
