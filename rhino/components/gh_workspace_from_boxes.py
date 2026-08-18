"""
gh_workspace_from_boxes.py
==========================

Builds a repair-workspace bundle (schemaVersion 2.1.0) from a list of oriented
bounding boxes in Rhino/Grasshopper, using Gemini 3.7 Flash only for semantic
labelling. All numbers are computed locally and never pass through the model.

Lives in:  <repo>/rhino/components/gh_workspace_from_boxes.py
Loaded by: a small exec() stub inside the Grasshopper Python 3 component.

Design notes
------------
* Geometry is authoritative. Gemini receives a summary of the boxes plus the
  reference photo, and returns ids/labels only. Origins, dimensions and
  rotations are computed from the Rhino boxes and written straight into the
  JSON, so a hallucinated float can never enter the model.
* Connections default to computed adjacency (inflated OBB/OBB separating-axis
  test), not to the model's opinion.
* Coordinate conversion Rhino (Z-up) -> workspace (Y-up, three.js) is verified
  against the reference bundle "Timber Frame Structure".
* The API key is never an argument you type into Grasshopper. See
  resolve_api_key() for the lookup chain and README for the reasoning.

Public entry point: run_export(...) -> dict
"""

import base64
import io
import json
import math
import os
import random
import re
import time
import zipfile
from datetime import datetime, timezone

SCHEMA_VERSION = "2.1.0"
DEFAULT_MODEL = "gemini-3.7-flash"
DEFAULT_MATERIAL = "historic timber"
DEFAULT_PROVENANCE = "ROB|ARCH 2026 workshop scan"
VALID_STATUS = ("intact", "defective", "missing")

# Environment variables consulted, in order.
ENV_KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
ENV_KEY_FILE = "GEMINI_API_KEY_FILE"
ENV_REPO = "ROBARCH_REPO"


# --------------------------------------------------------------------------
# API key resolution (see README: "Why the key must not live in the .gh file")
# --------------------------------------------------------------------------

def _read_key_file(path):
    """
    First non-comment line of the file. Tolerates what people actually produce:
    a BOM from Notepad, wrapping quotes, a trailing newline, or the whole
    GEMINI_API_KEY=AIza... line pasted in instead of the bare key.
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    name, value = line.split("=", 1)
                    if name.strip().upper() in ENV_KEY_NAMES:
                        line = value.strip()
                return line.strip().strip('"').strip("'").strip()
    except Exception:
        return None
    return None


def _parse_dotenv(path):
    """Minimal .env parser: KEY=value, ignores comments, strips quotes."""
    found = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                found[k.strip()] = v
    except Exception:
        return {}
    return found


KEY_FILE_NAME = "gemini_api_key.txt"
KEY_FOLDER_NAME = "robarch"


def ensure_key_file():
    """
    Create an empty key file if none exists at any of the known locations.
    Returns (path, created). Never overwrites an existing file.
    """
    for path in _user_config_paths():
        if os.path.exists(path):
            return path, False

    path = recommended_key_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("")
        return path, True
    except Exception:
        return path, False


def recommended_key_path():
    """
    The one path to tell participants about. Same shape on Windows and macOS,
    visible in Explorer and in Finder, outside every git repo.

      Windows  C:\\Users\\<you>\\Documents\\robarch\\gemini_api_key.txt
      macOS    /Users/<you>/Documents/robarch/gemini_api_key.txt
    """
    return os.path.join(
        os.path.expanduser("~"), "Documents", KEY_FOLDER_NAME, KEY_FILE_NAME
    )


def _user_config_paths():
    """Candidate key files, most obvious first."""
    home = os.path.expanduser("~")
    paths = [recommended_key_path()]

    # OneDrive redirects Documents on many managed Windows machines
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if onedrive:
        paths.append(os.path.join(onedrive, "Documents", KEY_FOLDER_NAME, KEY_FILE_NAME))

    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(os.path.join(appdata, KEY_FOLDER_NAME, KEY_FILE_NAME))

    paths.append(os.path.join(home, ".config", KEY_FOLDER_NAME, KEY_FILE_NAME))
    paths.append(os.path.join(home, ".config", KEY_FOLDER_NAME, "gemini_api_key"))
    paths.append(os.path.join(home, ".gemini_api_key"))

    seen, unique = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def resolve_repo_root(start=None):
    """ROBARCH_REPO if set, else walk up from this file looking for a repo marker."""
    env = os.environ.get(ENV_REPO)
    if env and os.path.isdir(env):
        return env
    here = start or os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isdir(os.path.join(here, ".git")) or os.path.exists(
            os.path.join(here, "pyproject.toml")
        ):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return None


def resolve_api_key(explicit=None, repo_root=None):
    """
    Returns (key, source_description). Never returns the key inside the
    description, and never writes the key anywhere.

    Lookup order:
      1. explicit argument   -- discouraged, flagged loudly in the log
      2. GEMINI_API_KEY / GOOGLE_API_KEY environment variables
      3. file named by GEMINI_API_KEY_FILE
      4. .env at the repo root (repo root is gitignored for .env)
      5. per-user key file outside any repo (%APPDATA%\\robarch\\..., ~/.config/robarch/...)
    """
    if explicit and str(explicit).strip() and not str(explicit).startswith("<"):
        return str(explicit).strip(), "component input (UNSAFE: stored inside the .gh file)"

    for name in ENV_KEY_NAMES:
        val = os.environ.get(name)
        if val and val.strip():
            return val.strip(), "environment variable %s" % name

    file_env = os.environ.get(ENV_KEY_FILE)
    if file_env:
        key = _read_key_file(file_env)
        if key:
            return key, "file from %s" % ENV_KEY_FILE

    repo_root = repo_root or resolve_repo_root()
    if repo_root:
        env_path = os.path.join(repo_root, ".env")
        if os.path.exists(env_path):
            pairs = _parse_dotenv(env_path)
            for name in ENV_KEY_NAMES:
                if pairs.get(name):
                    return pairs[name], "%s in <repo>/.env" % name

    for path in _user_config_paths():
        if os.path.exists(path):
            key = _read_key_file(path)
            if key:
                return key, "user key file %s" % path

    return None, "not found"


def mask_key(key):
    if not key:
        return "-"
    tail = key[-4:] if len(key) > 8 else "****"
    return "%s...%s (%d chars)" % (key[:3], tail, len(key))


# --------------------------------------------------------------------------
# Small vector helpers (plain tuples, so this module is testable without Rhino)
# --------------------------------------------------------------------------

def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _norm(a):
    length = math.sqrt(_dot(a, a))
    if length < 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / length, a[1] / length, a[2] / length)


def _neg(a):
    return (-a[0], -a[1], -a[2])


# --------------------------------------------------------------------------
# Frames: the Rhino-independent description of one oriented box
# --------------------------------------------------------------------------

def frame_from_rhino_box(box):
    """
    Rhino.Geometry.Box -> dict with centre, unit axes and half extents,
    all in Rhino model units and Rhino world coordinates.
    """
    plane = box.Plane
    centre = box.Center
    return {
        "center": (centre.X, centre.Y, centre.Z),
        "axes": (
            _norm((plane.XAxis.X, plane.XAxis.Y, plane.XAxis.Z)),
            _norm((plane.YAxis.X, plane.YAxis.Y, plane.YAxis.Z)),
            _norm((plane.ZAxis.X, plane.ZAxis.Y, plane.ZAxis.Z)),
        ),
        "half": (box.X.Length / 2.0, box.Y.Length / 2.0, box.Z.Length / 2.0),
    }


def rhino_to_workspace_vector(v):
    """
    Rhino is Z-up right handed, the workspace viewer is Y-up (three.js).
    Mapping: (x, y, z)_rhino -> (x, z, -y)_workspace.
    """
    return (v[0], v[2], -v[1])


def euler_yxz_from_columns(col_x, col_y, col_z):
    """
    Extract three.js YXZ Euler angles from a rotation matrix given by its three
    columns (the world directions of the local x, y, z axes).
    Mirrors THREE.Euler.setFromRotationMatrix(m, 'YXZ').
    """
    m11, m21, m31 = col_x
    m12, m22, m32 = col_y
    m13, m23, m33 = col_z

    m23c = max(-1.0, min(1.0, m23))
    rot_x = math.asin(-m23c)

    if abs(m23c) < 0.9999999:
        rot_y = math.atan2(m13, m33)
        rot_z = math.atan2(m21, m22)
    else:
        rot_y = math.atan2(-m31, m11)
        rot_z = 0.0

    return rot_x, rot_y, rot_z


def frame_to_geometry(frame, scale=1.0, decimals=6):
    """
    Frame (Rhino world) -> workspace origin / dimensions / rotation.

    Convention taken from the reference bundle: the workspace box carries
    width  = length along the Rhino local Y axis
    height = length along the Rhino local Z axis
    depth  = length along the Rhino local X axis
    and the rotation maps the local box axes onto those Rhino axes after the
    Z-up to Y-up conversion. An axis-aligned Rhino box therefore comes out as
    rotation (0, -pi/2, 0), which is exactly what the reference file contains.
    """
    ax, ay, az = frame["axes"]
    hx, hy, hz = frame["half"]

    origin = rhino_to_workspace_vector(frame["center"])
    origin = tuple(c * scale for c in origin)

    dims = (hy * 2.0 * scale, hz * 2.0 * scale, hx * 2.0 * scale)

    col_x = _neg(rhino_to_workspace_vector(ay))
    col_y = rhino_to_workspace_vector(az)
    col_z = _neg(rhino_to_workspace_vector(ax))
    rot = euler_yxz_from_columns(col_x, col_y, col_z)

    r = lambda v: round(v + 0.0, decimals)
    return {
        "origin": {"x": r(origin[0]), "y": r(origin[1]), "z": r(origin[2])},
        "dimensions": {"width": r(dims[0]), "height": r(dims[1]), "depth": r(dims[2])},
        "rotation": {"x": r(rot[0]), "y": r(rot[1]), "z": r(rot[2])},
    }


# --------------------------------------------------------------------------
# Adjacency: separating axis test on boxes inflated by the joint tolerance
# --------------------------------------------------------------------------

def boxes_touch(a, b, tol):
    """True if the two OBBs overlap after inflating each by tol/2 on every face."""
    ha = tuple(h + tol / 2.0 for h in a["half"])
    hb = tuple(h + tol / 2.0 for h in b["half"])
    aa, ab = a["axes"], b["axes"]

    t_world = _sub(b["center"], a["center"])
    t = tuple(_dot(t_world, aa[i]) for i in range(3))

    R = [[_dot(aa[i], ab[j]) for j in range(3)] for i in range(3)]
    absR = [[abs(R[i][j]) + 1e-9 for j in range(3)] for i in range(3)]

    for i in range(3):
        ra = ha[i]
        rb = sum(hb[j] * absR[i][j] for j in range(3))
        if abs(t[i]) > ra + rb:
            return False

    for j in range(3):
        ra = sum(ha[i] * absR[i][j] for i in range(3))
        rb = hb[j]
        if abs(sum(t[i] * R[i][j] for i in range(3))) > ra + rb:
            return False

    for i in range(3):
        for j in range(3):
            i1, i2 = (i + 1) % 3, (i + 2) % 3
            j1, j2 = (j + 1) % 3, (j + 2) % 3
            ra = ha[i1] * absR[i2][j] + ha[i2] * absR[i1][j]
            rb = hb[j1] * absR[i][j2] + hb[j2] * absR[i][j1]
            dist = abs(t[i2] * R[i1][j] - t[i1] * R[i2][j])
            if dist > ra + rb:
                return False

    return True


def compute_adjacency(frames, tol):
    """Symmetric index adjacency list."""
    n = len(frames)
    adj = [set() for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if boxes_touch(frames[i], frames[j], tol):
                adj[i].add(j)
                adj[j].add(i)
    return [sorted(s) for s in adj]


# --------------------------------------------------------------------------
# Reference photo
# --------------------------------------------------------------------------

def load_cover_image(path, max_px=1600, quality=82):
    """
    Returns (data_uri, original_bytes, mime_type, file_name).
    The data URI is a downscaled JPEG so the bundle stays small; the original
    bytes are what gets written into photos/ inside the bundle.
    """
    with open(path, "rb") as fh:
        original = fh.read()

    file_name = os.path.basename(path)
    ext = os.path.splitext(file_name)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"

    try:
        from PIL import Image

        img = Image.open(io.BytesIO(original))
        img = img.convert("RGB")
        if max(img.size) > max_px:
            ratio = float(max_px) / max(img.size)
            img = img.resize(
                (int(img.size[0] * ratio), int(img.size[1] * ratio)), Image.LANCZOS
            )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        preview, preview_mime = buf.getvalue(), "image/jpeg"
        if len(preview) >= len(original) and mime == "image/jpeg":
            preview, preview_mime = original, mime
    except Exception:
        preview, preview_mime = original, mime

    data_uri = "data:%s;base64,%s" % (
        preview_mime,
        base64.b64encode(preview).decode("ascii"),
    )
    return data_uri, original, mime, file_name


# --------------------------------------------------------------------------
# Ids and timestamps in the same shape the web app produces
# --------------------------------------------------------------------------

_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def make_id(prefix):
    stamp = ""
    n = int(time.time() * 1000)
    while n > 0:
        stamp = _ALPHABET[n % 36] + stamp
        n //= 36
    tail = "".join(random.choice(_ALPHABET) for _ in range(3))
    return "%s_%s%s" % (prefix, stamp[-8:], tail)


def iso_now():
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (now.microsecond // 1000)


def slugify(text, fallback):
    s = re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")
    return s or fallback


def titleize(part_id):
    return " ".join(w.capitalize() for w in part_id.split("_") if w)


# --------------------------------------------------------------------------
# Prompt and model call
# --------------------------------------------------------------------------

def build_prompt(geoms, adjacency, structure_hint=""):
    """
    The model sees a compact, unit-free description plus the photo, and returns
    naming only. Numbers are given for context but are never read back.
    """
    lines = []
    for i, g in enumerate(geoms):
        o, d = g["origin"], g["dimensions"]
        neighbours = ", ".join("part_%02d" % (j + 1) for j in adjacency[i]) or "none"
        lines.append(
            "part_%02d | centre (%.3f, %.3f, %.3f) | size w %.3f h %.3f d %.3f | touches: %s"
            % (i + 1, o["x"], o["y"], o["z"], d["width"], d["height"], d["depth"], neighbours)
        )
    box_block = "\n".join(lines)

    hint = ("\nContext: %s\n" % structure_hint) if structure_hint else ""

    return """You are an expert in historic timber framing (Fachwerk) and in reading
3D component layouts. You are given a reference photograph of a structure and a
list of its parts as oriented boxes.

Coordinate frame: Y is up, X runs to the right, Z runs towards the viewer
(negative Z goes into the depth of the structure). Lengths are in metres.
%s
PARTS
%s

TASK
Give every part a descriptive id and label from the vocabulary of timber framing:
post, beam, sill beam, top plate, rail, brace, strut, sill, stud, tie beam,
knee brace, corner post, middle post. Include the position in the id where it
disambiguates (front/back, left/right, upper/lower, inner/outer).

RULES
- One entry per input part, using the exact index given.
- id: lower snake_case ascii, unique across the assembly, no numbering suffixes
  unless two parts are genuinely identical in role and position.
- label: the same name in Title Case.
- Use the adjacency list and the box sizes to reason about the role of a part:
  long horizontal members at the bottom are sill beams, at the top they are top
  plates, vertical members are posts or studs, inclined members are braces,
  short members between a post and a brace are rails.
- Return ONLY a JSON object, no prose and no code fences:

{"objectName": "Timber Frame Structure",
 "parts": [{"index": 1, "id": "bottom_beam_front", "label": "Bottom Beam Front"}]}
""" % (hint, box_block)


def call_gemini(api_key, model_name, prompt, image_bytes, mime_type, timeout=120):
    """
    Calls Gemini through the current google-genai SDK, falling back to the
    legacy google-generativeai SDK if that is what the Rhino environment has.
    Returns the raw text response.
    """
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        contents = [
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            types.Part.from_text(text=prompt),
        ]
        config = types.GenerateContentConfig(response_mime_type="application/json")
        resp = client.models.generate_content(
            model=model_name, contents=contents, config=config
        )
        return resp.text or "", "google-genai"
    except ImportError:
        pass

    import google.generativeai as legacy

    legacy.configure(api_key=api_key)
    model = legacy.GenerativeModel(model_name)
    resp = model.generate_content(
        [{"mime_type": mime_type, "data": image_bytes}, prompt]
    )
    return (resp.text or ""), "google-generativeai (legacy)"


def clean_json_text(txt):
    t = (txt or "").strip()
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 2:
            t = parts[1].strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        t = t[start : end + 1]
    return t


def naming_from_response(text, count):
    """
    Parse the model response into (object_name, [ids], [labels]) with hard
    guarantees: exactly `count` entries, unique ids, safe fallbacks.
    """
    data = json.loads(clean_json_text(text))
    object_name = str(data.get("objectName") or "Timber Structure").strip()

    ids = [None] * count
    labels = [None] * count
    for entry in data.get("parts", []):
        try:
            idx = int(entry.get("index", 0)) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < count:
            continue
        ids[idx] = slugify(entry.get("id"), "part_%02d" % (idx + 1))
        labels[idx] = str(entry.get("label") or "").strip() or None

    seen = {}
    for i in range(count):
        base = ids[i] or "part_%02d" % (i + 1)
        if base in seen:
            seen[base] += 1
            base = "%s_%d" % (base, seen[base])
        else:
            seen[base] = 1
        ids[i] = base
        labels[i] = labels[i] or titleize(base)

    return object_name, ids, labels


# --------------------------------------------------------------------------
# Bundle assembly
# --------------------------------------------------------------------------

def build_workspace(
    geoms,
    ids,
    labels,
    adjacency,
    object_name,
    cover_data_uri=None,
    evidence=None,
    location="",
    provenance=DEFAULT_PROVENANCE,
    notes=None,
    material=DEFAULT_MATERIAL,
    instance_id=None,
):
    now = iso_now()
    parts = []
    for i, g in enumerate(geoms):
        parts.append(
            {
                "id": ids[i],
                "label": labels[i],
                "origin": g["origin"],
                "dimensions": g["dimensions"],
                "rotation": g["rotation"],
                "connections": [ids[j] for j in adjacency[i]],
                "material": material,
                "status": "intact",
                "notes": "",
            }
        )

    instance = {
        "id": instance_id or make_id("inst"),
        "name": object_name,
        "templateRef": None,
        "parts": parts,
        "location": location,
        "provenance": provenance,
        "notes": notes
        if notes is not None
        else "%s divided into %d connected components for condition mapping."
        % (object_name, len(parts)),
        "coverImage": cover_data_uri or "",
        "sourcePhotoEvidenceId": evidence["id"] if evidence else None,
        "defaultDisplayMode": "boxes",
        "createdAt": now,
    }

    return {
        "schemaVersion": SCHEMA_VERSION,
        "template": None,
        "instance": instance,
        "evidence": [evidence] if evidence else [],
        "conditions": [],
        "plans": [],
        "currentPlanId": None,
        "executionLog": [],
        "conversations": [],
        "createdAt": now,
        "updatedAt": now,
        "collaboration": {
            "projectId": "example:%s" % slugify(object_name, "instance"),
            "modelVersion": "1",
        },
    }


def write_bundle(workspace, target, photo_bytes=None, photo_name=None):
    """
    target ending in .zip  -> importable bundle (workspace.json + photos/)
    target ending in .json -> plain workspace.json next to a photos/ folder
    target is a folder     -> workspace.json + photos/ inside it
    Returns the path written.
    """
    payload = json.dumps(workspace, indent=2, ensure_ascii=False)

    if target.lower().endswith(".zip"):
        os.makedirs(os.path.dirname(os.path.abspath(target)) or ".", exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("workspace.json", payload)
            if photo_bytes and photo_name:
                zf.writestr("photos/%s" % photo_name, photo_bytes)
        return target

    if target.lower().endswith(".json"):
        folder = os.path.dirname(os.path.abspath(target))
        json_path = target
    else:
        folder = target
        json_path = os.path.join(target, "workspace.json")

    os.makedirs(folder, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as fh:
        fh.write(payload)
    if photo_bytes and photo_name:
        photos = os.path.join(folder, "photos")
        os.makedirs(photos, exist_ok=True)
        with open(os.path.join(photos, photo_name), "wb") as fh:
            fh.write(photo_bytes)
    return json_path


# --------------------------------------------------------------------------
# Entry point called by the Grasshopper stub
# --------------------------------------------------------------------------

def run_export(
    boxes,
    image_path=None,
    unit_scale=1.0,
    tolerance=0.01,
    model_name=DEFAULT_MODEL,
    api_key=None,
    use_model=True,
    structure_hint="",
    location="",
    provenance=DEFAULT_PROVENANCE,
    material=DEFAULT_MATERIAL,
    output_path=None,
    fallback_name="Timber Structure",
):
    """
    Returns a dict: {json, workspace, path, log, error}.
    Never raises for expected failure modes; the error goes into the result.
    """
    log = []
    result = {"json": None, "workspace": None, "path": None, "log": log, "error": None}

    frames = [frame_from_rhino_box(b) for b in boxes if b and b.IsValid]
    if not frames:
        result["error"] = "No valid boxes connected."
        return result
    log.append("Boxes: %d valid of %d connected." % (len(frames), len(boxes)))
    log.append("Unit scale to metres: %.6f" % unit_scale)

    geoms = [frame_to_geometry(f, unit_scale) for f in frames]
    adjacency = compute_adjacency(frames, tolerance / unit_scale if unit_scale else tolerance)
    log.append(
        "Adjacency: %d contacts at %.1f mm tolerance."
        % (sum(len(a) for a in adjacency) // 2, tolerance * 1000.0)
    )

    cover_uri, photo_bytes, mime, photo_file = None, None, None, None
    evidence = None
    if image_path and os.path.exists(image_path):
        cover_uri, photo_bytes, mime, photo_file = load_cover_image(image_path)
        ev_id = make_id("ev")
        evidence = {
            "id": ev_id,
            "kind": "photo",
            "attachedTo": None,
            "capturedAt": iso_now(),
            "capturedBy": None,
            "url": "idb://%s" % ev_id,
            "text": None,
            "measurement": None,
            "confirmsConditionRef": None,
            "refutesConditionRef": None,
            "fileName": photo_file,
            "byteSize": len(photo_bytes),
            "mimeType": mime,
        }
        log.append("Reference photo: %s (%.1f MB)" % (photo_file, len(photo_bytes) / 1e6))
    elif image_path:
        log.append("WARNING: image path not found, continuing without a photo: %s" % image_path)

    object_name = fallback_name
    ids = ["part_%02d" % (i + 1) for i in range(len(geoms))]
    labels = [titleize(pid) for pid in ids]

    if use_model:
        key, source = resolve_api_key(api_key)
        if not key:
            key_path, created = ensure_key_file()
            log.append("No API key yet, writing generic part ids for now.")
            log.append(
                "%s Paste your key into it, save, then toggle run again:"
                % ("Created an empty key file for you." if created else "Key file:")
            )
            log.append("    %s" % key_path)
            log.append("Get a key at https://aistudio.google.com/apikey")
        elif not photo_bytes:
            log.append("No reference photo, skipping the model and writing generic part ids.")
        else:
            log.append("API key from %s -> %s" % (source, mask_key(key)))
            if "UNSAFE" in source:
                log.append(
                    "WARNING: this key is stored in the Grasshopper file and will be "
                    "committed if you push the .gh. Move it to an environment variable."
                )
            try:
                prompt = build_prompt(geoms, adjacency, structure_hint)
                log.append("Calling %s ..." % model_name)
                started = time.time()
                raw, sdk = call_gemini(key, model_name, prompt, photo_bytes, mime)
                log.append("Response from %s via %s in %.1f s." % (model_name, sdk, time.time() - started))
                object_name, ids, labels = naming_from_response(raw, len(geoms))
                log.append("Named %d parts, object: %s" % (len(ids), object_name))
            except Exception as exc:
                result["error"] = "Model call failed: %s" % exc
                log.append("ERROR: %s -- falling back to generic ids." % exc)
    else:
        log.append("Model disabled, writing generic part ids.")

    workspace = build_workspace(
        geoms,
        ids,
        labels,
        adjacency,
        object_name,
        cover_data_uri=cover_uri,
        evidence=evidence,
        location=location,
        provenance=provenance,
        material=material,
    )
    payload = json.dumps(workspace, indent=2, ensure_ascii=False)
    result["workspace"] = workspace
    result["json"] = payload

    if output_path:
        photo_target = ("%s.jpg" % evidence["id"]) if evidence else None
        try:
            result["path"] = write_bundle(workspace, output_path, photo_bytes, photo_target)
            log.append("Written: %s (%.2f MB)" % (result["path"], len(payload) / 1e6))
        except Exception as exc:
            result["error"] = "Write failed: %s" % exc
            log.append("ERROR: %s" % exc)

    return result
