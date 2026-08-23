"""GH Python 3, 02 BRIEF: state in words what this joint has to do.

One model call, on the repair plan, the recorded conditions, the evidence, the
damage grid and the participant's own notes. It states the job and designs
nothing. It is never shown a coordinate, so this stage reasons in words and 03
reasons in planes.

Needs
-----
src/workshop_robarch_2026/agents.py, for the model call and the corpus.
data/prompts/brief.md, data/corpus/joints/*.md, and a Gemini key in
gemini_api_key.txt or GEMINI_API_KEY.

Inputs
------
setup_json   str    from 01 SELECT
notes        str    the participant's own notes; anything they want considered
model        str    optional Gemini model
temperature  float  optional [0.4]
run          bool   press to write it; press again to ask afresh
repo         str    the repository folder, if it cannot be found on its own

Outputs
-------
brief_json   str    the brief plus repairKind, mustResist and openQuestions
brief        str    the prose, for a panel
report       str[]
"""

import json
import os
import sys


def _repo_root():
    """The repository folder: the `repo` input, the .gh file, cwd, or sys.path."""
    tries = [globals().get("repo"), os.environ.get("ROBARCH_REPO")]
    try:
        tries.append(str(ghenv.Component.OnPingDocument().FilePath or ""))
    except Exception:
        pass
    for start in tries + [os.getcwd()] + list(sys.path):
        if not start or "://" in str(start):
            continue
        folder = os.path.abspath(os.path.expanduser(str(start)))
        for _ in range(9):
            if os.path.isdir(os.path.join(folder, "src", "workshop_robarch_2026")):
                return folder
            folder, before = os.path.dirname(folder), folder
            if folder == before:
                break
    raise RuntimeError("workshop repository not found; connect its folder to repo")


brief_json = brief = ""
report = ["02 Brief"]

try:
    root = _repo_root()
    if os.path.join(root, "src") not in sys.path:
        sys.path.append(os.path.join(root, "src"))
    import scriptcontext as sc

    # Do not reload the package while a background thread is still using it.
    live = sc.sticky.get("joinery.jobs.%s" % ghenv.Component.InstanceGuid, {})
    if not any(isinstance(j, dict) and not j.get("done") for j in live.values()):
        for name in [n for n in sys.modules if n.startswith("workshop_robarch_2026")]:
            sys.modules.pop(name)

    from workshop_robarch_2026 import agents

    setup = json.loads(str(globals().get("setup_json") or "").strip() or "{}")
    if not setup:
        raise ValueError("connect setup_json from 01 SELECT")
    notes = str(globals().get("notes") or "").strip()
    model = str(globals().get("model") or agents.DEFAULT_MODEL).strip()
    warmth = globals().get("temperature")
    warmth = 0.4 if warmth is None else float(warmth)

    # Families only, no coordinates: this stage reasons in words.
    examples, corpus_notes = agents.corpus_examples(root)
    report.extend(corpus_notes)
    families = [{"key": e["key"], "what": e.get("what"), "faces": len(e["planes"]),
                 "datasheet": e.get("datasheet")}
                for e in examples if e.get("datasheet")]

    payload = {k: setup.get(k) for k in
               ("part", "connectedParts", "plan", "conditions", "evidence",
                "neighbours", "member", "damage")}
    payload.update(families=families, notes=notes)

    answer, lines = agents.ask(
        ghenv.Component, "brief",
        agents.signature("brief-v2", setup, notes, len(families), model, warmth),
        lambda: agents.call(root, "brief.md", payload, model=model,
                            temperature=warmth),
        bool(globals().get("run")), "write the brief")
    report.extend(lines)
    if answer is None:
        raise SystemExit

    brief = str(answer.get("brief") or "")
    brief_json = json.dumps(answer, indent=2, ensure_ascii=False)
    report.append("repair kind: %s" % (answer.get("repairKind") or "not stated"))
    report += ["must resist: %s" % line for line in answer.get("mustResist") or []]
    report += ["open question: %s" % line for line in answer.get("openQuestions") or []]
    report.append("%d character(s) of participant notes were included" % len(notes)
                  if notes else "no participant notes, so the brief rests on "
                                "the plan alone")
except SystemExit:
    pass
except Exception as exc:
    report.append("ERROR: {}".format(exc))
