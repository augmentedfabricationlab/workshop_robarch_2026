# r: google-genai, pillow
"""
Grasshopper Python 3 component: Workspace Export

INPUTS
  boxes       Box    List    oriented bounding boxes, one per part
  imagePath   str    Item    reference photo of the structure
  run         bool   Item    Boolean Toggle

OUTPUTS
  jsonPath    where workspace.json was written
  jsonOutput  the JSON itself
  log         what happened

Writes workspace.json (schema 2.1.0) into the same folder as the image.
The Gemini key goes in a text file, path is printed below on the first run.
"""

import base64
import io
import json
import math
import os
import random
import time

KEY_NAME = "gemini_api_key.txt"
HOME_KEY = os.path.join(os.path.expanduser("~"), "Documents", "robarch", KEY_NAME)
MODEL = "gemini-3.7-flash"
TOLERANCE = 0.01          # metres, contact tolerance for connections
MATERIAL = "historic timber"


# --- key -------------------------------------------------------------------

def key_paths():
    """Next to the .gh, one level up (repo root), then Documents\robarch."""
    paths = []
    try:
        gh = ghenv.Component.OnPingDocument().FilePath   # noqa: F821
        if gh:
            here = os.path.dirname(gh)
            paths.append(os.path.join(here, KEY_NAME))
            paths.append(os.path.join(os.path.dirname(here), KEY_NAME))
    except Exception:
        pass
    paths.append(HOME_KEY)
    return paths


def get_key():
    """Returns (key, note). Note says where it came from, or what was checked."""
    env = os.environ.get("GEMINI_API_KEY")
    if env and env.strip():
        return env.strip(), "key from GEMINI_API_KEY"

    checked = key_paths()
    for path in checked:
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig") as fh:
                text = fh.read().strip().strip('"').strip("'")
            if text:
                return text, "key from %s" % path
            return None, "key file is empty: %s" % path

    os.makedirs(os.path.dirname(HOME_KEY), exist_ok=True)
    open(HOME_KEY, "w").close()
    return None, "no key file found, checked:\n    " + "\n    ".join(checked)


# --- geometry --------------------------------------------------------------

def to_ws(v):
    """Rhino Z-up -> workspace Y-up."""
    return (v[0], v[2], -v[1])


def box_to_part_geometry(box, scale):
    """Rhino Box -> origin / dimensions / rotation, verified against the reference bundle."""
    p = box.Plane
    ax = (p.XAxis.X, p.XAxis.Y, p.XAxis.Z)
    ay = (p.YAxis.X, p.YAxis.Y, p.YAxis.Z)
    az = (p.ZAxis.X, p.ZAxis.Y, p.ZAxis.Z)
    c = box.Center

    o = to_ws((c.X, c.Y, c.Z))
    w, h, d = box.Y.Length, box.Z.Length, box.X.Length

    cx = [-a for a in to_ws(ay)]
    cy = to_ws(az)
    cz = [-a for a in to_ws(ax)]

    m23 = max(-1.0, min(1.0, cz[1]))
    rx = math.asin(-m23)
    if abs(m23) < 0.9999999:
        ry, rz = math.atan2(cz[0], cz[2]), math.atan2(cx[1], cy[1])
    else:
        ry, rz = math.atan2(-cx[2], cx[0]), 0.0

    r = lambda v: round(v, 6)
    return {
        "origin": {"x": r(o[0] * scale), "y": r(o[1] * scale), "z": r(o[2] * scale)},
        "dimensions": {"width": r(w * scale), "height": r(h * scale), "depth": r(d * scale)},
        "rotation": {"x": r(rx), "y": r(ry), "z": r(rz)},
    }


def box_frame(box):
    p = box.Plane
    c = box.Center
    return {
        "c": (c.X, c.Y, c.Z),
        "a": (
            (p.XAxis.X, p.XAxis.Y, p.XAxis.Z),
            (p.YAxis.X, p.YAxis.Y, p.YAxis.Z),
            (p.ZAxis.X, p.ZAxis.Y, p.ZAxis.Z),
        ),
        "h": (box.X.Length / 2.0, box.Y.Length / 2.0, box.Z.Length / 2.0),
    }


def touching(a, b, tol):
    """Separating axis test on both boxes inflated by tol."""
    dot = lambda u, v: u[0] * v[0] + u[1] * v[1] + u[2] * v[2]
    ha = [x + tol / 2 for x in a["h"]]
    hb = [x + tol / 2 for x in b["h"]]
    tw = [b["c"][i] - a["c"][i] for i in range(3)]
    t = [dot(tw, a["a"][i]) for i in range(3)]
    R = [[dot(a["a"][i], b["a"][j]) for j in range(3)] for i in range(3)]
    A = [[abs(R[i][j]) + 1e-9 for j in range(3)] for i in range(3)]

    for i in range(3):
        if abs(t[i]) > ha[i] + sum(hb[j] * A[i][j] for j in range(3)):
            return False
    for j in range(3):
        if abs(sum(t[i] * R[i][j] for i in range(3))) > hb[j] + sum(ha[i] * A[i][j] for i in range(3)):
            return False
    for i in range(3):
        for j in range(3):
            i1, i2 = (i + 1) % 3, (i + 2) % 3
            j1, j2 = (j + 1) % 3, (j + 2) % 3
            ra = ha[i1] * A[i2][j] + ha[i2] * A[i1][j]
            rb = hb[j1] * A[i][j2] + hb[j2] * A[i][j1]
            if abs(t[i2] * R[i1][j] - t[i1] * R[i2][j]) > ra + rb:
                return False
    return True


def adjacency(frames, tol):
    n = len(frames)
    adj = [set() for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if touching(frames[i], frames[j], tol):
                adj[i].add(j)
                adj[j].add(i)
    return [sorted(s) for s in adj]


# --- naming via Gemini -----------------------------------------------------

def name_parts(key, geoms, adj, image_bytes, mime):
    """Returns (object_name, ids, labels). Geometry never goes through the model."""
    rows = []
    for i, g in enumerate(geoms):
        o, d = g["origin"], g["dimensions"]
        touches = ", ".join("part_%02d" % (j + 1) for j in adj[i]) or "none"
        rows.append("part_%02d | centre (%.2f, %.2f, %.2f) | size %.2f x %.2f x %.2f | touches: %s"
                    % (i + 1, o["x"], o["y"], o["z"], d["width"], d["height"], d["depth"], touches))

    prompt = (
        "You see a photo of a timber structure and its parts as boxes.\n"
        "Y is up, lengths in metres.\n\n" + "\n".join(rows) + "\n\n"
        "Name every part using timber framing vocabulary (post, beam, sill beam, "
        "top plate, rail, brace, stud, corner post), adding front/back, left/right, "
        "upper/lower where it disambiguates. Use the adjacency and the sizes to work "
        "out each role.\n"
        "Return only JSON, one entry per part, ids in lower snake_case and unique:\n"
        '{"objectName": "...", "parts": [{"index": 1, "id": "bottom_beam_front", '
        '"label": "Bottom Beam Front"}]}'
    )

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=MODEL,
        contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime),
                  types.Part.from_text(text=prompt)],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    txt = (resp.text or "").strip()
    txt = txt[txt.find("{"): txt.rfind("}") + 1]
    data = json.loads(txt)

    ids = ["part_%02d" % (i + 1) for i in range(len(geoms))]
    labels = [None] * len(geoms)
    for e in data.get("parts", []):
        i = int(e.get("index", 0)) - 1
        if 0 <= i < len(geoms) and e.get("id"):
            ids[i] = str(e["id"]).strip().lower().replace(" ", "_")
            labels[i] = str(e.get("label") or "").strip() or None

    seen = {}
    for i, pid in enumerate(ids):
        if pid in seen:
            seen[pid] += 1
            ids[i] = "%s_%d" % (pid, seen[pid])
        else:
            seen[pid] = 1
        labels[i] = labels[i] or " ".join(w.capitalize() for w in ids[i].split("_"))

    return str(data.get("objectName") or "Timber Structure"), ids, labels


# --- bundle ----------------------------------------------------------------

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def uid(prefix):
    return "%s_%s" % (prefix, "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789")
                                      for _ in range(11)))


def cover_uri(image_bytes, mime):
    """Downscaled JPEG data URI, so the JSON does not carry the full photo."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        if max(img.size) > 1600:
            f = 1600.0 / max(img.size)
            img = img.resize((int(img.size[0] * f), int(img.size[1] * f)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
        small = buf.getvalue()
        if len(small) < len(image_bytes):
            return "data:image/jpeg;base64," + base64.b64encode(small).decode("ascii")
    except Exception:
        pass
    return "data:%s;base64,%s" % (mime, base64.b64encode(image_bytes).decode("ascii"))


def build(geoms, ids, labels, adj, name, image_path, image_bytes, mime):
    ev_id = uid("ev")
    stamp = now()
    parts = [{
        "id": ids[i],
        "label": labels[i],
        "origin": g["origin"],
        "dimensions": g["dimensions"],
        "rotation": g["rotation"],
        "connections": [ids[j] for j in adj[i]],
        "material": MATERIAL,
        "status": "intact",
        "notes": "",
    } for i, g in enumerate(geoms)]

    return {
        "schemaVersion": "2.1.0",
        "template": None,
        "instance": {
            "id": uid("inst"),
            "name": name,
            "templateRef": None,
            "parts": parts,
            "location": "",
            "provenance": "ROB|ARCH 2026 workshop scan",
            "notes": "%s divided into %d connected components for condition mapping." % (name, len(parts)),
            "coverImage": cover_uri(image_bytes, mime),
            "sourcePhotoEvidenceId": ev_id,
            "defaultDisplayMode": "boxes",
            "createdAt": stamp,
        },
        "evidence": [{
            "id": ev_id,
            "kind": "photo",
            "attachedTo": None,
            "capturedAt": stamp,
            "capturedBy": None,
            "url": "idb://%s" % ev_id,
            "text": None,
            "measurement": None,
            "confirmsConditionRef": None,
            "refutesConditionRef": None,
            "fileName": os.path.basename(image_path),
            "byteSize": len(image_bytes),
            "mimeType": mime,
        }],
        "conditions": [],
        "plans": [],
        "currentPlanId": None,
        "executionLog": [],
        "conversations": [],
        "createdAt": stamp,
        "updatedAt": stamp,
        "collaboration": {"projectId": "workshop:%s" % ids[0], "modelVersion": "1"},
    }


# --- run -------------------------------------------------------------------
# Split in two: prepare() touches Rhino and must stay on the main thread, but it
# is fast. finish() is plain Python and does the slow network call, so it runs on
# a background thread and Grasshopper stays responsive.

def prepare(boxes, image_path, scale):
    boxes = [b for b in boxes if b and b.IsValid]
    if not boxes:
        raise ValueError("No valid boxes connected.")
    if not image_path or not os.path.exists(image_path):
        raise ValueError("Image not found: %s" % image_path)

    with open(image_path, "rb") as fh:
        image_bytes = fh.read()

    return {
        "geoms": [box_to_part_geometry(b, scale) for b in boxes],
        "adj": adjacency([box_frame(b) for b in boxes], TOLERANCE / scale),
        "image_bytes": image_bytes,
        "mime": "image/png" if image_path.lower().endswith(".png") else "image/jpeg",
        "image_path": image_path,
    }


def finish(prep):
    """Returns (json_text, out_path, log_lines). No Rhino types in here."""
    geoms, adj = prep["geoms"], prep["adj"]
    lines = ["%d boxes, %d connections." % (len(geoms), sum(len(a) for a in adj) // 2)]

    name = "Timber Structure"
    ids = ["part_%02d" % (i + 1) for i in range(len(geoms))]
    labels = [" ".join(w.capitalize() for w in i.split("_")) for i in ids]

    key, note = get_key()
    lines.append(note)
    if not key:
        lines.append("Using generic part names.")
    else:
        try:
            t0 = time.time()
            name, ids, labels = name_parts(key, geoms, adj, prep["image_bytes"], prep["mime"])
            lines.append("Named by %s in %.1f s: %s" % (MODEL, time.time() - t0, name))
        except Exception as exc:
            lines.append("Model failed (%s), using generic part names." % exc)

    ws = build(geoms, ids, labels, adj, name, prep["image_path"],
               prep["image_bytes"], prep["mime"])
    text = json.dumps(ws, indent=2, ensure_ascii=False)

    folder = os.path.dirname(os.path.abspath(prep["image_path"]))
    out_path = os.path.join(folder, "workspace.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)

    photos = os.path.join(folder, "photos")
    os.makedirs(photos, exist_ok=True)
    with open(os.path.join(photos, "%s.jpg" % ws["evidence"][0]["id"]), "wb") as fh:
        fh.write(prep["image_bytes"])

    lines.append("Written: %s" % out_path)
    return text, out_path, lines


jsonPath = None
jsonOutput = None
log = "Toggle run to export."

if globals().get("run"):
    import threading

    import Grasshopper
    import Rhino
    import scriptcontext as sc

    STATE = sc.sticky.setdefault("workspace_export_jobs", {})
    job_id = str(ghenv.Component.InstanceGuid)          # noqa: F821
    job = STATE.get(job_id)

    def wake_up(delay=500):
        """Ask Grasshopper to re-solve this component a moment from now."""
        doc = ghenv.Component.OnPingDocument()           # noqa: F821
        if doc:
            doc.ScheduleSolution(
                delay,
                Grasshopper.Kernel.GH_Document.GH_ScheduleDelegate(
                    lambda _: ghenv.Component.ExpireSolution(False)   # noqa: F821
                ),
            )

    if job is None:
        scale = Rhino.RhinoMath.UnitScale(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Meters)
        try:
            prep = prepare(list(globals().get("boxes") or []), globals().get("imagePath"), scale)
        except Exception as exc:
            STATE[job_id] = {"done": True, "result": None, "error": str(exc), "t0": time.time()}
            log = str(exc)
        else:
            job = {"done": False, "result": None, "error": None, "t0": time.time()}
            STATE[job_id] = job

            def work(prep=prep, job=job):
                try:
                    job["result"] = finish(prep)
                except Exception as exc:
                    job["error"] = str(exc)
                finally:
                    job["done"] = True

            threading.Thread(target=work, daemon=True).start()
            log = "Working, Grasshopper stays usable."
            wake_up()

    elif not job["done"]:
        log = "Working, %.0f s so far." % (time.time() - job["t0"])
        wake_up()

    else:
        if job["error"]:
            log = job["error"]
        else:
            jsonOutput, jsonPath, lines = job["result"]
            log = "\n".join(lines)

else:
    # toggling run off clears the job, so the next True starts a fresh export
    try:
        import scriptcontext as sc
        sc.sticky.get("workspace_export_jobs", {}).pop(
            str(ghenv.Component.InstanceGuid), None                  # noqa: F821
        )
    except Exception:
        pass

print(log)