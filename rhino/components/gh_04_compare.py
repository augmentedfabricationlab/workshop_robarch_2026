"""GH Python 3, 04 COMPARE: say what each joint does and does not do.

One model call, made on the measurements alone. The model is not shown the
planes, so it cannot describe geometry it has not seen. It can only describe
what that geometry achieved.

Needs
-----
src/workshop_robarch_2026/agents.py, for the model call.
data/prompts/compare.md, and a Gemini key in gemini_api_key.txt or
GEMINI_API_KEY.

Inputs
------
variants_json  str    from 03 JOINT
brief_json     str    from 02 BRIEF
model          str    optional Gemini model
temperature    float  optional [0.3]
run            bool   press to compare
repo           str    the repository folder, if it cannot be found on its own

Outputs
-------
comparison      str[]  one block per variation: what it does, what it does not
recommendation  str    which one to use, and why, argued against the brief
against_brief   str[]  where a variation fails something the brief asked for
compare_json    str
report          str[]
"""

import json
import os
import sys


def _repo_root():
    starts = [globals().get("repo"), os.environ.get("ROBARCH_REPO")]
    try:
        starts.append(os.path.dirname(str(ghenv.Component.OnPingDocument().FilePath or "")))
    except Exception:
        pass
    starts.extend([os.getcwd(), *sys.path])
    for start in starts:
        if not start or "://" in str(start):
            continue
        folder = os.path.abspath(os.path.expanduser(str(start)))
        if os.path.isfile(folder):
            folder = os.path.dirname(folder)
        for _ in range(9):
            if os.path.isdir(os.path.join(folder, "src", "workshop_robarch_2026")):
                return folder
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
    raise RuntimeError("workshop repository not found; connect its folder to repo")


comparison = []
against_brief = []
recommendation = compare_json = ""
report = ["04 Compare"]

try:
    root = _repo_root()
    source_path = os.path.join(root, "src")
    if source_path not in sys.path:
        sys.path.append(source_path)
    import scriptcontext as sc

    # Do not reload the package while a background thread is still using it.
    _live = sc.sticky.get("joinery.jobs.%s" % ghenv.Component.InstanceGuid, {})
    if not any(isinstance(j, dict) and not j.get("done") for j in _live.values()):
        for name in list(sys.modules):
            if name.startswith("workshop_robarch_2026"):
                sys.modules.pop(name)

    from workshop_robarch_2026 import agents

    raw = str(globals().get("variants_json") or "").strip()
    if not raw:
        raise ValueError("connect variants_json from 03 JOINT")
    variants = json.loads(raw)
    brief = json.loads(str(globals().get("brief_json") or "{}"))
    if not brief:
        raise ValueError("connect brief_json from 02 BRIEF")

    model = str(globals().get("model") or agents.DEFAULT_MODEL).strip()
    temperature = globals().get("temperature")
    temperature = 0.3 if temperature is None else float(temperature)

    # The measurements only. The planes are deliberately left out.
    payload = {
        "brief": brief,
        "claimed": {"resists": variants.get("resists"),
                    "doesNotResist": variants.get("doesNotResist")},
        "variants": variants.get("variants"),
    }
    key = agents.signature("compare-v1", variants.get("variants"), brief, model, temperature)
    state = agents.background(
        ghenv.Component, "compare", key,
        lambda: agents.call(root, "compare.md", payload, model=model,
                            temperature=temperature),
        bool(globals().get("run")))

    if state["status"] == "idle":
        report.append("press run to compare")
        raise SystemExit
    if state["status"] == "running":
        report.append("Gemini working, %.0f s%s" % (
        state["seconds"],
        "" if state["attempt"] < 2 else "  (run %d)" % state["attempt"]))
        raise SystemExit
    if state["status"] == "error":
        report.append("ERROR: %s" % state["error"])
        raise SystemExit

    cached, notes_out = state["result"]
    report.extend(notes_out)
    report.append("answered in %.0f s%s" % (
        state["seconds"],
        "" if state["attempt"] < 2 else "  (run %d; press again to ask afresh)"
        % state["attempt"]))

    for item in cached.get("variants") or []:
        comparison.append(
            "%s\n   does:      %s\n   does not:  %s\n   choose when: %s"
            % (item.get("id"), item.get("does"), item.get("doesNot"),
               item.get("chooseWhen")))
    if cached.get("comparison"):
        comparison.append("")
        comparison.append(str(cached["comparison"]))

    recommendation = str(cached.get("recommendation") or "")
    against_brief = [str(v) for v in (cached.get("againstTheBrief") or [])]
    compare_json = json.dumps(cached, indent=2, ensure_ascii=False)

    report.append("%d variation(s) compared" % len(cached.get("variants") or []))
    if against_brief:
        for line in against_brief:
            report.append("AGAINST THE BRIEF: %s" % line)
    else:
        report.append("no variation contradicts the brief")
except SystemExit:
    pass
except Exception as exc:
    report.append("ERROR: {}".format(exc))
