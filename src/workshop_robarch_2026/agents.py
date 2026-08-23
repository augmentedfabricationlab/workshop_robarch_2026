"""One way to call Gemini, used by all three agent components."""

from __future__ import annotations

import hashlib
import json
import os

DEFAULT_MODEL = "gemini-3.7-flash"
KEY_FILE = "gemini_api_key.txt"


def find_key(repo: str):
    """(key, where) from the environment, the repo, or Documents/robarch."""
    value = os.environ.get("GEMINI_API_KEY")
    if value and value.strip():
        return value.strip(), "GEMINI_API_KEY"
    candidates = [
        os.path.join(repo, KEY_FILE),
        os.path.join(os.path.expanduser("~"), "Documents", "robarch", KEY_FILE),
    ]
    for path in candidates:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8-sig") as handle:
                key = handle.read().strip().strip('"').strip("'")
            if key:
                return key, path
            return None, "empty key file: %s" % path
    return None, "no key; add one to %s" % candidates[-1]


def prompt_text(repo: str, name: str) -> str:
    path = os.path.join(repo, "data", "prompts", name)
    with open(path, "r", encoding="utf-8-sig") as handle:
        return handle.read()


def signature(*values) -> str:
    payload = json.dumps(values, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _json_from(text: str) -> dict:
    body = str(text or "").strip()
    if body.startswith("```"):
        lines = body.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    try:
        return json.loads(body)
    except Exception:
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(body[start:end + 1])


def call(repo: str, prompt_file: str, payload: dict, model=None,
         temperature=None, attachments=None, retries: int = 1) -> tuple:
    """-> (result dict, notes list). Raises only when it truly cannot answer."""
    key, where = find_key(repo)
    if not key:
        raise RuntimeError(where)
    from google import genai
    from google.genai import types

    system = prompt_text(repo, prompt_file)
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=180_000))
    notes = ["key from %s" % where, "prompt %s" % prompt_file]
    problem = None

    for attempt in range(int(retries) + 1):
        body = dict(payload)
        if problem:
            body["previousAttemptFailed"] = problem
        parts = [types.Part.from_text(text=json.dumps(body, indent=2, ensure_ascii=False))]
        for item in attachments or []:
            parts.append(types.Part.from_text(
                text="Evidence %s (%s)" % (item.get("id"), item.get("name"))))
            parts.append(types.Part.from_bytes(
                data=item["data"], mime_type=item.get("mimeType", "image/jpeg")))

        config = {"response_mime_type": "application/json", "system_instruction": system}
        if temperature is not None:
            config["temperature"] = float(temperature)
        try:
            response = client.models.generate_content(
                model=str(model or DEFAULT_MODEL).strip(),
                contents=parts,
                config=types.GenerateContentConfig(**config),
            )
        except TypeError:
            config.pop("system_instruction", None)
            response = client.models.generate_content(
                model=str(model or DEFAULT_MODEL).strip(),
                contents=[types.Part.from_text(text=system)] + parts,
                config=types.GenerateContentConfig(**config),
            )

        text = (response.text or "").strip()
        if not text:
            problem = "the model returned nothing -- blocked, or the output limit was hit"
            notes.append("attempt %d: %s" % (attempt + 1, problem))
            continue
        try:
            return _json_from(text), notes
        except Exception as exc:
            problem = "the reply was not valid JSON: %s" % exc
            notes.append("attempt %d: %s" % (attempt + 1, problem))

    raise RuntimeError(problem or "the model did not answer")


# ------------------------------------------------------- running in the background


def jobs_for(component) -> dict:
    import scriptcontext as sc

    return sc.sticky.setdefault(
        "joinery.jobs.%s" % getattr(component, "InstanceGuid", "component"), {})


def edges_for(component) -> dict:
    """Button edge state, kept apart from the jobs so nothing iterates over it."""
    import scriptcontext as sc

    return sc.sticky.setdefault(
        "joinery.edges.%s" % getattr(component, "InstanceGuid", "component"), {})


def busy(component) -> bool:
    """Is a thread from this component still working?

    Grasshopper components reload the package on every solve so library edits
    land without restarting Rhino. Doing that while a worker thread is inside
    those modules gives the thread a second, separate copy of them. Ask this
    first.
    """
    return any(isinstance(job, dict) and not job.get("done")
               for job in jobs_for(component).values())


def wake(component, milliseconds: int = 600):
    """Ask Grasshopper to solve this component again shortly."""
    import Grasshopper

    document = component.OnPingDocument()
    if document:
        document.ScheduleSolution(
            int(milliseconds),
            Grasshopper.Kernel.GH_Document.GH_ScheduleDelegate(
                lambda _: component.ExpireSolution(False)))


def _pressed(component, name: str, run: bool) -> bool:
    """True only on the solve where `run` goes false -> true.

    A Button is true for one solve, so this changes nothing for it. A Toggle
    left on would otherwise restart the call on every 600 ms poll, which is the
    kind of thing that quietly spends a hundred euros of tokens.
    """
    edges = edges_for(component)
    before = bool(edges.get(name))
    edges[name] = bool(run)
    return bool(run) and not before


def background(component, name: str, key: str, work, run: bool,
               deadline: float = 240.0) -> dict:
    """Run `work()` on a thread and poll it, so the canvas stays alive.

    -> {"status": idle | running | done | error, "result", "error", "seconds"}

    `key` identifies the request. The same key returns the finished answer
    without calling again -- unless `run` is pressed, which always asks again.
    Pressing run on a finished job is a re-roll: same question, new answer,
    because the model is not deterministic. That is usually what you want when
    a joint comes back wrong.

    The deadline is the part that was missing before: without it a call that
    never returns leaves the component polling itself forever.
    """
    import threading
    import time

    jobs = jobs_for(component)
    pressed = _pressed(component, name, run)
    job = jobs.get(name)
    running = job is not None and job.get("key") == key and not job.get("done")

    if job is not None and job.get("key") == key and not pressed:
        if job.get("done"):
            return {"status": "error" if job.get("error") else "done",
                    "result": job.get("result"), "error": job.get("error"),
                    "seconds": float(job.get("seconds") or 0.0),
                    "attempt": int(job.get("attempt") or 1)}
        waited = time.time() - float(job.get("started") or time.time())
        if waited > float(deadline):
            job.update(done=True, seconds=waited,
                       error="no answer after %.0f s -- press run again" % waited)
            return {"status": "error", "result": None,
                    "error": job["error"], "seconds": waited, "attempt": 1}
        wake(component)
        return {"status": "running", "result": None, "error": None,
                "seconds": waited, "attempt": int(job.get("attempt") or 1)}

    if running:
        # pressed while a call of the same key is in flight -- let it finish
        wake(component)
        waited = time.time() - float(job.get("started") or time.time())
        return {"status": "running", "result": None, "error": None,
                "seconds": waited, "attempt": int(job.get("attempt") or 1)}

    if not pressed:
        return {"status": "idle", "result": None, "error": None,
                "seconds": 0.0, "attempt": 0}

    attempt = 1
    if job is not None and job.get("key") == key:
        attempt = int(job.get("attempt") or 1) + 1
    job = {"key": key, "started": time.time(), "done": False, "attempt": attempt,
           "result": None, "error": None, "seconds": 0.0}
    jobs[name] = job

    def runner():
        started = time.time()
        try:
            job["result"] = work()
        except Exception as exc:
            job["error"] = str(exc)
        finally:
            job["seconds"] = time.time() - started
            job["done"] = True

    threading.Thread(target=runner, daemon=True).start()
    wake(component)
    return {"status": "running", "result": None, "error": None,
            "seconds": 0.0, "attempt": attempt}


def ask(component, name: str, key: str, work, run: bool, doing: str) -> tuple:
    """Ask in the background, and say where it got to. -> (answer, lines).

    `answer` is None whenever there is nothing to use yet -- not run, still
    working, or failed -- and `lines` says which. The caller reports the lines
    and stops for this solve; Grasshopper wakes it again on its own.

    Every component did this by hand, in twenty lines, three times over.
    """
    state = background(component, name, key, work, run)
    attempt, waited = int(state["attempt"] or 1), float(state["seconds"] or 0.0)
    if state["status"] == "idle":
        return None, ["press run to %s" % doing]
    if state["status"] == "running":
        return None, ["Gemini working, %.0f s%s"
                      % (waited, "" if attempt < 2 else "  (run %d)" % attempt)]
    if state["status"] == "error":
        return None, ["ERROR: %s" % state["error"]]
    answer, notes = state["result"]
    return answer, list(notes) + [
        "answered in %.0f s%s"
        % (waited, "" if attempt < 2 else
           "  -- run %d, press again to ask afresh" % attempt)]


def _datasheet(folder: str, key: str):
    path = os.path.join(folder, key + ".md")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8-sig") as handle:
        return handle.read()


def _is_planes_only(joint: dict) -> bool:
    """Is every cutter in this file a plain half-space?

    A cutter is stored as a plate: a plane, an outline drawn on it, and a
    thickness. When the outline is a square far bigger than the joint, the plate
    behaves as a half-space and the plane alone describes it completely -- which
    is exactly what `kernel.half_space_cut` builds. When the outline is shaped,
    the geometry lives in the outline and the plane alone describes nothing.

    Joinery here is made of planes. A file that needs its outline is not
    something this pipeline can learn from, so it is skipped and named.
    """
    aspect = float(joint.get("aspect") or 3.0)
    section = float(joint.get("section") or 1.0)
    for cut in joint.get("cuts") or []:
        sets = cut.get("polysets") or []
        if len(sets) != 1:
            return False
        points = [(float(a), float(b)) for a, b in sets[0]]
        xs = sorted({round(p[0], 6) for p in points})
        ys = sorted({round(p[1], 6) for p in points})
        if len(points) != 4 or len(xs) != 2 or len(ys) != 2:
            return False
        if min(xs[1] - xs[0], ys[1] - ys[0]) < 4.0 * aspect * section:
            return False
    return True


def corpus_examples(repo: str) -> tuple:
    """The catalogue joints as planes, with their datasheet reasoning.

    -> (examples, notes)

    One folder, one language. Every joint in the corpus is a set of oriented
    half-spaces and a removal rule -- the same thing `joint.md` asks for -- so an
    example is written the way an answer is written.

    A file whose cutters need their outline to mean anything is skipped and
    named. It is not a defect in the file; it is a different way of describing a
    solid, and this pipeline does not speak it. Re-author it as planes to have
    it learned from. `*.plates.json` are the original plate-form archives kept
    beside the joints as the independent record, and are not read.
    """
    folder = os.path.join(repo, "data", "corpus", "joints")
    out, notes = [], []
    if not os.path.isdir(folder):
        return out, ["no corpus at %s" % folder]

    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".json") or name.lower().endswith(".plates.json"):
            continue
        key = os.path.splitext(name)[0]
        with open(os.path.join(folder, name), "r", encoding="utf-8-sig") as handle:
            joint = json.load(handle)
        if str(joint.get("kind") or "").strip().lower() == "patch":
            # Patches are deliberately not shown. A splice is a family with a
            # grammar worth learning; a patch is a hole shaped to one particular
            # decay, and a catalogue of them teaches shapes rather than reasons.
            notes.append("%s is a patch -- not shown as an example; patches are "
                         "shaped to the damage, not recalled" % key)
            continue
        if not _is_planes_only(joint):
            notes.append("%s is stored as shaped cutters rather than planes -- "
                         "skipped; re-author it as half-spaces to have it learned "
                         "from" % key)
            continue
        cuts = joint.get("cuts") or []
        out.append({
            "key": key,
            "what": joint.get("what") or joint.get("kind"),
            "aspect": joint.get("aspect"),
            "planes": [{"id": "P%d" % i,
                        "normal": [round(float(v), 6) for v in c["normal"]],
                        "d": round(float(c.get("offset", 0.0)), 6),
                        "role": c.get("name")}
                       for i, c in enumerate(cuts)],
            "groups": [["P%d" % i for i in g] for g in (joint.get("removal_groups") or [])],
            "rule": joint.get("rule"),
            "datasheet": _datasheet(folder, key),
        })
    notes.insert(0, "%d joint(s) read from the corpus, %d plane(s) in total"
                 % (len(out), sum(len(e["planes"]) for e in out)))
    return out, notes
