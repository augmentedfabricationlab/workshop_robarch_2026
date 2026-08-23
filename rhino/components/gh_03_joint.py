"""GH Python 3, 03 JOINT: design the repair joints, then measure them.

The model reads the brief, picks joints from the corpus that suit it, and
returns a set of variations based on assumptions about the repair.

Each joint is rotated around the member and slid along it until it sits where it
destroys the least sound timber, and it is then measured: how much rot it leaves
behind, how much sound wood it spends, which directions it locks, and whether it
cuts the joinery of another member. Each variation is measured against the corpus
joint it came from, so the report can show what the change actually bought.

Where there are several damages, one repair is taken from each and the group is
measured together as a scheme.

Needs
-----
src/workshop_robarch_2026: agents, context, evaluator, joinery, kernel,
neighbours. data/prompts/joint.md, data/corpus/joints/*.json, and a Gemini key
in gemini_api_key.txt or GEMINI_API_KEY.

Inputs
------
setup_json     str        from 01 SELECT; it carries the member frame
brief_json     str        from 02 BRIEF
workspace_json str        the same ZIP as 01, so the neighbouring parts are known
centers        Point3d[]  cell centroids, from 00
damage         float[]    one 0..1 value per cell, from 00
threshold      float      damage at or above this must be removed [0.50]
variant        int        which scheme to show; 0 is the best ranked
sweep_degrees  int        how fine the rotation sweep is, 5..45 [15]
joint_json     str        paste a joint, or a list of them, to skip the model
model          str        optional Gemini model
temperature    float      optional [0.7]
run            bool       press to design; press again to ask afresh
repo           str        the repository folder, if it cannot be found on its own

Outputs
-------
kept           Brep[]     the historic timber that stays, all repairs removed
prosthesis     Brep[]     the replacement pieces, one per repair
planes         Curve[]    the cutting planes of the scheme shown
removed_cells  Point3d[]  cell centres inside the replacement
summary        str[]      the schemes, ranked, with a line per repair
variants_json  str        all of them with their measurements, for 04 COMPARE
report         str[]
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


def _designs(answer, catalogue, notes, cap=10):
    """Return the corpus joints the model chose, then its variations of them.

    A chosen joint is loaded from the corpus by its key, so it is identical to
    the catalogue entry. That makes the comparison that follows exact: the
    question is whether a variation beats the real SJ4, not whether it beats the
    model's redrawing of SJ4. Variations carry their own planes and name the
    base they came from.
    """
    out = []
    for pick in answer.get("chosen") or []:
        key = str(pick.get("key") or "").strip()
        found = catalogue.get(key)
        if found is None:
            notes.append("chose %r, which is not in the corpus, so it was "
                         "skipped" % key)
            continue
        out.append(dict(found, fromKey=key, base=None, why=pick.get("why"),
                        id=str(found.get("what") or key)))
    for item in answer.get("variations") or []:
        base = str(item.get("from") or "").strip()
        if base and base not in catalogue:
            notes.append("%s says it varies %r, which is not in the corpus"
                         % (item.get("name") or "a variation", base))
        out.append(dict(item, fromKey=None, base=base or None,
                        id=str(item.get("name") or "variation")))
    if len(out) != cap:
        notes.append("%d joints offered, %d asked for" % (len(out), cap))
    return out[:cap]


kept = []
prosthesis = []
planes = []
removed_cells = []
summary = []
variants_json = ""
report = ["03 Joint"]

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

    import numpy as np
    import Rhino
    import Rhino.Geometry as rg
    from workshop_robarch_2026 import (agents, context, evaluator, joinery,
                                       kernel, neighbours)

    setup = json.loads(str(globals().get("setup_json") or "{}"))
    frame = (setup.get("member") or {}).get("frame")
    if not frame:
        raise ValueError("connect setup_json from 01 SELECT")
    centres = list(globals().get("centers") or [])
    values = [float(v) for v in (globals().get("damage") or [])]
    if not centres:
        raise ValueError("connect `centers` and `damage` from 00 CELLS")

    # From 01, which derived it as 00 did. Re-deriving here would not agree.
    built = (setup.get("member") or {}).get("cellCount")
    if built and built != len(centres):
        raise ValueError("00 built %d cells and %d are wired in, so 01 and 03 "
                         "are not looking at the same run" % (built, len(centres)))
    points = [[float(p.X), float(p.Y), float(p.Z)] for p in centres]
    mem = joinery.member(frame, points, values)
    report.append("grid %dx%dx%d = %d cells" % (mem["grid"] + (len(points),)))

    gate = 0.5 if globals().get("threshold") is None else float(threshold)
    if not joinery.damaged(mem, gate).any():
        raise ValueError("no cell reaches %.2f, so there is nothing to repair"
                         % gate)

    # ---- how many separate damages are there? ----------------------------
    aspect = 3.0
    window = joinery.extents(mem)
    found = joinery.clusters(mem, gate)
    if not found:
        raise ValueError("no cell reaches %.2f, so there is nothing to repair"
                         % gate)
    report.append("%d separate damage(s) on this member:" % len(found))
    for index, item in enumerate(found):
        report.append("   %d  v %.3f..%.3f m, %d cell(s), %s -> %s"
                      % (index, item["vRange"][0], item["vRange"][1],
                         item["cells"],
                         "reaches an end" if item["reachesTheEnd"]
                         else "sound timber both sides",
                         "a splice replaces everything past it"
                         if item["reachesTheEnd"] else
                         "a patch, so the timber beyond it stays"))

    report.append("window reaches x +-%.3f, z +-%.3f   (1.0 = %.0f mm, "
                  "one cell = %.0f mm, which is the finest anything is "
                  "measured at)"
                  % (window["xHalf"], window["zHalf"], window["unitMm"],
                     window["cellMm"]))

    # ---- the frame around it --------------------------------------------
    # The model cannot keep clear of a seat it has not been shown, so the
    # report always says where the neighbouring parts came from.
    around, boxes, source = [], [], None
    try:
        wired = str(globals().get("workspace_json") or "").strip()
        workspace = context.load_workspace(globals().get("workspace_json")) if wired else None
        if workspace is not None:
            around = neighbours.around(workspace, setup.get("beamId"), frame)
            to_rhino = neighbours.world_matrix(workspace)
            boxes = [b for b in (neighbours.part_box(p, to_rhino)
                                 for p in context.parts(workspace)
                                 if str(p.get("id")) != str(setup.get("beamId")))
                     if b is not None]
            source = "workspace_json"
        elif setup.get("neighbours"):
            around = list(setup.get("neighbours"))
            source = "setup_json, from 01"
    except Exception as exc:
        report.append("NEIGHBOURS: could not be read (%s), so the model is "
                      "being told nothing about the frame" % exc)

    tasks = []
    for index, item in enumerate(found):
        kind = "splice" if item["reachesTheEnd"] else "patch"
        at, side, note = joinery.anchor(mem, gate, aspect, straddle=True,
                                        kind=kind, within=item["vRange"])
        tasks.append({
            "cluster": item, "kind": kind, "side": side, "station": at,
            "note": note,
            "decay": joinery.decay(mem, gate, at, side, aspect,
                                   within=item["vRange"]),
            "bearing": joinery.nearby(mem, around, at, side, aspect),
        })
    inside = [n for n in tasks[0]["bearing"]["parts"] if n["insideTheJoint"]]
    if not around:
        report.append("NEIGHBOURS: none. %s The model cannot keep clear of a "
                      "seat it has not been shown."
                      % ("setup_json carries no neighbours and workspace_json is "
                         "not connected" if not setup.get("beamId")
                         else "connect workspace_json to 03, or check 01's report "
                              "for how many parts it found"))
    else:
        report.append("%d part(s) touch this member (%s); %d bear inside the "
                      "joint window" % (len(around), source, len(inside)))
        for n in inside:
            report.append("   %s bears on %s over y %.2f..%.2f, so a face "
                          "placed there cuts its seat" % (n["label"] or n["id"],
                                             " ".join(n["bearsOn"]) or "the member",
                                             n["y"][0], n["y"][1]))
        if not inside:
            report.append("   none of them fall within the joint, so none "
                          "constrains it")

    # ---- the joints: pasted, or designed, ONE SET PER DAMAGE -------------
    pasted = str(globals().get("joint_json") or "").strip()
    if pasted:
        parsed = json.loads(pasted)
        designs = parsed if isinstance(parsed, list) else [parsed]
        report.append("%d joint(s) pasted in, so the model was not called"
                      % len(designs))
        for task in tasks:
            task["designs"] = designs
    else:
        brief = json.loads(str(globals().get("brief_json") or "{}"))
        if not brief:
            raise ValueError("connect brief_json from 02 BRIEF")
        model = str(globals().get("model") or agents.DEFAULT_MODEL).strip()
        temperature = globals().get("temperature")
        temperature = 0.7 if temperature is None else float(temperature)
        examples, corpus_notes = agents.corpus_examples(root)
        report.extend(corpus_notes)
        catalogue = {e["key"]: {"aspect": e.get("aspect") or aspect,
                                "planes": e["planes"], "groups": e["groups"],
                                "what": e.get("what")} for e in examples}
        # One call per damage: asked about two at once it averages them.
        waiting, failed = [], []
        for index, task in enumerate(tasks):
            payload = {
                "brief": brief,
                "part": setup.get("part"),
                "plan": setup.get("plan"),
                "member": dict(setup.get("member") or {}, window=window),
                "damage": {"threshold": gate, "decay": task["decay"],
                           "grid": (setup.get("damage") or {}).get("grid"),
                           "repair": task["kind"],
                           "thisIsOneOf": len(tasks),
                           "theOthers": [t["cluster"]["vRange"] for j, t
                                         in enumerate(tasks) if j != index]},
                "neighbours": task["bearing"],
                "examples": examples,
            }
            key = agents.signature("joint-v10", brief, setup.get("part"),
                                   setup.get("plan"), task["decay"], window,
                                   task["bearing"], model, temperature)
            state = agents.background(
                ghenv.Component, "joint-%d" % index, key,
                lambda payload=payload: agents.call(
                    root, "joint.md", payload, model=model,
                    temperature=temperature),
                bool(globals().get("run")))
            task["state"] = state
            if state["status"] == "running":
                waiting.append((index, state))
            elif state["status"] == "error":
                failed.append((index, state))

        if any(t["state"]["status"] == "idle" for t in tasks):
            report.append("press run to design the repairs (%d damage(s))" % len(tasks))
            raise SystemExit
        if waiting:
            report.append("Gemini working on %d of %d damage(s), %.0f s"
                          % (len(waiting), len(tasks),
                             max(s["seconds"] for _, s in waiting)))
            raise SystemExit
        if failed:
            for index, state in failed:
                report.append("ERROR on damage %d: %s" % (index, state["error"]))
            raise SystemExit

        for index, task in enumerate(tasks):
            answer, notes_out = task["state"]["result"]
            if index == 0:
                report.extend(notes_out)
            task["designs"] = _designs(answer, catalogue, report)
            report.append("damage %d (%s): %d chosen, %d varied, in %.0f s"
                          % (index, task["kind"],
                             len(answer.get("chosen") or []),
                             len(answer.get("variations") or []),
                             task["state"]["seconds"]))
    if not any(task.get("designs") for task in tasks):
        raise ValueError("the model returned no joints")

    # ---- place each repair, inside its own damage ------------------------
    step = max(5, min(45, int(globals().get("sweep_degrees") or 15)))
    sample_key = agents.signature(
        "scheme-v6", [t.get("designs") for t in tasks],
        [t["cluster"]["vRange"] for t in tasks], gate, step, mem["grid"],
        len(points), [item["id"] for item in around])
    store = sc.sticky.setdefault("joinery_placed", {})
    picked = store.get(sample_key)
    mark = len(report)          # everything the placing pass says, replayed below

    if picked is None:
        for number, task in enumerate(tasks):
            span = task["cluster"]["vRange"]
            placed = []
            for design in task.get("designs") or []:
                joint = dict(design)
                joint.setdefault("id", "d%d-%d" % (number, len(placed)))
                joint.setdefault("aspect", aspect)
                # The kind the model drew, not the kind this damage calls for.
                joint["kind"] = str(design.get("kind") or task["kind"]).lower()
                if joint["kind"] != task["kind"]:
                    report.append("%s is a %s, but this damage has sound timber "
                                  "on both sides and needs a %s; skipped"
                                  % (joint["id"], joint["kind"], task["kind"]))
                    continue
                try:
                    loose = joinery.open_at_kept_side(joint)
                    if loose:
                        report.append("%s has a group unbounded on the %s "
                                      "side, so it would sweep the whole "
                                      "member. Skipped."
                                      % (joint["id"],
                                         "/".join(sorted({g["side"] for g in loose}))))
                        continue
                    rough, at_side, _ = joinery.anchor(mem, gate, joint["aspect"],
                                                       kind=joint["kind"],
                                                       within=span)
                    best = joinery.place(joint, mem, gate, rough, at_side,
                                         around=around, degrees=step, within=span)
                    full = joinery.measure(best["joint"], mem, best["station"],
                                           at_side, gate, boxes=boxes,
                                           around=around, within=span)
                    asked = joinery.claimed_locks(design.get("locksClaimed"),
                                                  best["twist"], at_side)
                    full.update(id=joint["id"], kind=joint["kind"],
                                what=design.get("what"), why=design.get("why"),
                                twist=best["twist"], station=best["station"],
                                spent=best["spent"], joint=best["joint"],
                                side=at_side, damage=number,
                                # As drawn: the diff compares shape, not twist.
                                drawn=joint,
                                fromKey=design.get("fromKey"),
                                base=design.get("base"),
                                changed=design.get("changed"),
                                expect=design.get("expect"),
                                resists=design.get("resists"),
                                doesNotResist=design.get("doesNotResist"),
                                locksClaimed=asked,
                                locksNotDelivered=[d for d in asked
                                                   if d not in full["locks"]])
                    placed.append(full)
                except Exception as exc:
                    report.append("%s could not be placed: %s"
                                  % (joint.get("id"), exc))
            # ---- did the variations earn their place? ---------------------
            # Printed as families: the chosen corpus joint is the control,
            # its variations under it, each one a single change from it.
            control = {r["fromKey"]: r for r in placed if r.get("fromKey")}
            families = {}
            for one in placed:
                if one.get("base"):
                    families.setdefault(one["base"], []).append(one)
            if families:
                report.append("damage %d, each family against its control:"
                              % number)
            for key in sorted(families):
                against = control.get(key)
                if against is None:
                    report.append("   %s was varied but never chosen, so its "
                                  "variations have nothing to be measured against"
                                  % key)
                    continue
                report.append("   %-38s %5.2f%%   the control, from the corpus"
                              % (against["id"][:38], 100 * against["spent"]))
                for one in sorted(families[key], key=lambda r: r["spent"]):
                    delta = 100 * (one["spent"] - against["spent"])
                    won = [d for d in one["locks"] if d not in against["locks"]]
                    lost = [d for d in against["locks"] if d not in one["locks"]]
                    # `changed` is claimed, `did` is what the planes show.
                    report += [
                        "     %-36s %5.2f%%  %+.2f%%  %-9s%s%s"
                        % (one["id"][:36], 100 * one["spent"], delta,
                           "BETTER" if delta < -0.01 else
                           "worse" if delta > 0.01 else "no change",
                           "  locks %s more" % " ".join(won) if won else "",
                           "  loses %s" % " ".join(lost) if lost else ""),
                        "        says: %s" % (one.get("changed") or "nothing"),
                        "        did:  %s" % joinery.differences(against["drawn"],
                                                                 one["drawn"])["did"],
                    ]
                    if one.get("expect"):
                        report.append("        expected: %s" % one["expect"])
            if placed:
                best = min(placed, key=lambda r: r["spent"])
                line = ("damage %d, best of %d placed: %s at %.2f%%"
                        % (number, len(placed), best["id"][:36], 100 * best["spent"]))
                if control:
                    par = min(control.values(), key=lambda r: r["spent"])
                    line += ("; best corpus joint %.2f%%, so %s"
                             % (100 * par["spent"],
                                "the model beat the corpus"
                                if best["spent"] < par["spent"] - 1e-5
                                else "the corpus still wins"))
                report.append(line)

            placed.sort(key=lambda r: (r["rotLeft"],
                                       len(r.get("cutsConnections") or []),
                                       r["spent"]))
            task["placed"] = placed
            if not placed:
                report.append("WARNING: no repair could be placed for damage %d "
                              "(%s, v %.3f..%.3f). Its %d cell(s) stay in the "
                              "wall and are counted in rotLeft."
                              % (number, task["kind"], span[0], span[1],
                                 task["cluster"]["cells"]))

        # ---- combine into schemes -----------------------------------------
        # One repair per damage, paired by rank, then re-measured together.
        working = [t for t in tasks if t.get("placed")]
        if not working:
            raise ValueError("no repair could be placed for any damage; see above")
        depth = min(len(t["placed"]) for t in working)
        schemes = []
        for rank in range(depth):
            chosen = [t["placed"][rank] for t in working]
            whole = joinery.scheme(
                [{"joint": c["joint"], "station": c["station"], "side": c["side"]}
                 for c in chosen], mem, gate, boxes=boxes, around=around)
            whole["parts"] = chosen
            whole["id"] = " + ".join(str(c["id"]) for c in chosen)
            schemes.append(whole)
        schemes.sort(key=lambda s: (s["rotLeft"],
                                    len(s.get("cutsConnections") or []),
                                    s["overlap"], s["spent"]))
        picked = {"schemes": schemes, "said": report[mark:]}
        store[sample_key] = picked
    else:
        # Replayed, so moving `variant` does not drop the comparison.
        report.extend(picked.get("said") or [])
        report.append("placements cached, so `variant` can be moved freely "
                      "without recomputing anything")

    ranked = picked["schemes"]
    if not ranked:
        raise ValueError("no scheme could be built; see the messages above")
    report.append("%d scheme(s) of %d repair(s); each was rotated through %d "
                  "angles around the member and slid along it to the position "
                  "that spends least sound timber"
                  % (len(ranked), len(tasks), 360 // step))

    for index, item in enumerate(ranked):
        line = ("%d  %-24s oak %5.2f%%  rot left %-3d"
                % (index, str(item["id"])[:24], 100 * item["spent"],
                   item["rotLeft"]))
        if item.get("rotPartly"):
            line += "  %d only part-taken" % item["rotPartly"]
        if item.get("cutsConnections"):
            line += "  CUTS %s" % ", ".join(item["cutsConnections"])
        if item.get("betweenMm") is not None:
            line += ("  repairs OVERLAP by %.0f mm" % -item["betweenMm"]
                     if item["overlap"] else
                     "  %.0f mm of sound timber between them" % item["betweenMm"])
        summary.append(line)
        for one in item["parts"]:
            note = ("      %-6s %-14s turned %+4.0f at %.3f m  locks %s"
                    % (one.get("kind"), str(one["id"])[:14], one["twist"],
                       one["station"], " ".join(one["locks"]) or "none"))
            note += ("  [corpus %s]" % one["fromKey"] if one.get("fromKey")
                     else "  [var of %s]" % one["base"] if one.get("base") else "")
            if one.get("locksNotDelivered"):
                note += "  CLAIMS %s and does not" % " ".join(one["locksNotDelivered"])
            if one.get("insertionBlocked"):
                note += "  cannot fit along %s" % " ".join(one["insertionBlocked"])
            summary.append(note)
            if one.get("what"):
                summary.append("         %s" % one["what"])

    variants_json = json.dumps({
        "damages": [{"vRange": t["cluster"]["vRange"], "cells": t["cluster"]["cells"],
                     "kind": t["kind"], "decay": t["decay"]} for t in tasks],
        "variants": [{
            "id": v["id"],
            "soundTimberSpentPct": round(100 * v["spent"], 3),
            "rotLeft": v["rotLeft"], "rotTotal": v["rotTotal"],
            "rotPartly": v.get("rotPartly"),
            "soundTaken": v["soundTaken"], "soundTotal": v["soundTotal"],
            "locks": v["locks"],
            "cutsConnections": v.get("cutsConnections"),
            "betweenMm": v.get("betweenMm"), "overlap": v.get("overlap"),
            "repairs": [{"id": r["id"], "kind": r.get("kind"),
                         "what": r.get("what"), "why": r.get("why"),
                         # where it came from, so 04 can group the families
                         "fromCorpus": r.get("fromKey"), "variationOf": r.get("base"),
                         "changed": r.get("changed"), "expected": r.get("expect"),
                         "turnedDeg": r["twist"],
                         "stationM": round(r["station"], 4),
                         "extent": r["extent"], "locks": r["locks"],
                         "locksClaimed": r.get("locksClaimed"),
                         "locksNotDelivered": r.get("locksNotDelivered"),
                         "insertionBlocked": r.get("insertionBlocked"),
                         "nearestSeatMm": r.get("nearestSeatMm"),
                         "resists": r.get("resists"),
                         "doesNotResist": r.get("doesNotResist")}
                        for r in v["parts"]],
        } for v in ranked],
    }, indent=2, ensure_ascii=False)

    # ---- the geometry, for the one scheme being shown --------------------
    pick = max(0, min(len(ranked) - 1, int(globals().get("variant") or 0)))
    shown = ranked[pick]
    report.append("showing %d of %d: %s" % (pick, len(ranked), shown["id"]))

    # Rhino's default on a metre document is 10 mm, too coarse for joinery.
    document = float(Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance)
    section = min(float(frame["width"]), float(frame["height"]))
    tolerance = min(document, section / 1000.0)
    if tolerance < document:
        report.append("cutting at %.5f; the document is set to %.5f, which is "
                      "too coarse for a %.0f mm section"
                      % (tolerance, document, 1000 * section))
    try:
        kept.extend(evaluator.evaluate_part(joinery.merge_kept(shown["parts"]),
                                            tolerance))
    except Exception as exc:
        report.append("the retained timber would not cut: %s" % exc)
    for one in shown["parts"]:
        try:
            prosthesis.extend(evaluator.evaluate_part(one["repair"]["parts"][1],
                                                      tolerance))
        except Exception as exc:
            report.append("%s: the replacement piece would not cut: %s"
                          % (one["id"], exc))

    size = 0.75 * max(frame["width"], frame["height"])
    for one in shown["parts"]:
        for cut_json in one["repair"]["parts"][0]["cuts"][1:-1]:
            item = kernel.Cut.from_json(cut_json)
            origin = item.origin()
            normal = np.asarray(item.normal, float)
            normal /= np.linalg.norm(normal)
            surface = rg.Plane(rg.Point3d(*[float(c) for c in origin]),
                               rg.Vector3d(*[float(c) for c in normal]))
            planes.append(rg.Rectangle3d(surface, rg.Interval(-size, size),
                                         rg.Interval(-size, size)).ToNurbsCurve())

    removed_cells = [centres[i] for i, flag in enumerate(shown["removed"]) if flag]
    report.append("kept %d Brep(s), prosthesis %d Brep(s), %d plane(s), "
                  "%d cell(s) replaced"
                  % (len(kept), len(prosthesis), len(planes), len(removed_cells)))
    if not prosthesis or not kept:
        # Measuring uses no boolean, so the numbers stand.
        report.append("%s measures but will not cut at this tolerance (%.4f). "
                      "Its planes are on the `planes` output; the measurements "
                      "in summary and variants_json are unaffected. A thinner "
                      "feature than the tolerance can do this."
                      % (shown["id"], tolerance))
except SystemExit:
    pass
except Exception as exc:
    report.append("ERROR: {}".format(exc))
