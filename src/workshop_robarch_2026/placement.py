"""Search the catalogue for repairs that clear a beam's damage.

Deterministic and exhaustive over a derived window. The agent does not search
-- it reads the result and argues for one of the survivors.

Gate order matters. Scoring a candidate that does not even cover the damage is
wasted work, and measured on a real beam three quarters of a naive grid falls
in that bucket. So: build, gate on coverage, and only then score.

What is derived and what is searched
------------------------------------
    interface_scale   DERIVED from the damage length. Measured on a test beam,
                      searching it changed nothing but cost 4x the runtime.
    position          SEARCHED over a derived window. Pinning it to the damage
                      extent lost the optimum -- 63 sound cells sacrificed
                      instead of 48 -- so the window is derived, not the value.
    rotation          SEARCHED, optionally filtered by which faces the site
                      leaves accessible.
    side              SEARCHED, two values.
    joint             all eight.
"""
from __future__ import annotations

import numpy as np

from . import kernel, joints, scoring


def damage_extent(cells, damage, frame, threshold=0.5):
    """Axial span of the damage, in beam-local v (metres from the corner).

    Returns (v0, v1, mask) or (None, None, mask) if nothing is damaged.
    """
    loc = scoring.to_local(cells, frame)
    mask = np.asarray(damage, float) >= threshold
    if not mask.any():
        return None, None, mask
    return float(loc[mask, 1].min()), float(loc[mask, 1].max()), mask


def derive_scale(joint, v0, v1, frame, margin=1.0):
    """interface_scale so the joint's interface spans the damage plus margin.

    margin is in sections. Returns at least 1.0 -- never shrink a catalogue
    joint below its authored proportions.
    """
    section = min(float(frame["width"]), float(frame["height"]))
    need = (v1 - v0) + 2.0 * margin * section
    nominal = float(joint["aspect"]) * section
    return max(1.0, need / nominal)


def candidates(frame, v0, v1, scale, n_positions=6, window=1.5,
               rotations=(0.0, 90.0, 180.0, 270.0), sides=(1, -1)):
    """Placement candidates over a window derived from the damage.

    window is in sections either side of the damage span -- the slack the
    search is allowed to explore. Positions are normalised 0..1.
    """
    L = float(frame["length"])
    section = min(float(frame["width"]), float(frame["height"]))
    lo = max(0.02, (v0 - window * section) / L)
    hi = min(0.98, (v1 + window * section) / L)
    if hi <= lo:
        lo, hi = 0.15, 0.85
    out = []
    for p in np.linspace(lo, hi, n_positions):
        for r in rotations:
            for s in sides:
                out.append({"position": float(p), "rotate_deg": float(r),
                            "side": int(s), "interface_scale": float(scale)})
    return out


def search(repo_root, frame, cells, damage, threshold=0.5, keys=None,
           n_positions=6, window=1.5, rotations=(0.0, 90.0, 180.0, 270.0),
           sides=(1, -1), margin=1.0, verify=True):
    """Exhaustive search. Returns (results, report).

    results are scored survivors, sorted by sound_sacrificed. report is a list
    of strings for the GH panel.
    """
    cells = np.asarray(cells, float)
    damage = np.asarray(damage, float)
    report = []

    if verify:
        ok, msg, _ = scoring.check_cells(cells, frame)
        report.append(msg)
        if not ok:
            return [], report

    v0, v1, mask = damage_extent(cells, damage, frame, threshold)
    if v0 is None:
        report.append("no cell reaches the damage threshold %.2f" % threshold)
        return [], report
    section = min(float(frame["width"]), float(frame["height"]))
    report.append("damage: %d of %d cells, axial %.3f..%.3f m (%.1f sections)"
                  % (int(mask.sum()), len(cells), v0, v1, (v1 - v0) / section))

    keys = list(keys) if keys else joints.list_keys(repo_root)
    results = []
    n_built = n_valid = 0
    for key in keys:
        j = joints.load_joint(repo_root, key)
        scale = derive_scale(j, v0, v1, frame, margin)
        for c in candidates(frame, v0, v1, scale, n_positions, window,
                            rotations, sides):
            n_built += 1
            try:
                rep = kernel.build_repair(j, frame, **c)
            except Exception as exc:                      # degenerate placement
                continue
            # GATE first, score second
            rem = scoring.removed_mask(cells, rep)
            dmg = damage >= threshold
            if (dmg & ~rem).any():
                continue
            n_valid += 1
            s = scoring.score(cells, damage, rep, threshold, verify=False)
            s.update(key=key, **c)
            s["kept_fraction_nominal"] = None
            results.append(s)

    results.sort(key=lambda r: (r["sound_sacrificed_weighted"],
                                r["sound_sacrificed"], r["key"]))
    report.append("built %d candidates, %d cleared the damage (%.0f%%)"
                  % (n_built, n_valid, 100.0 * n_valid / max(n_built, 1)))
    if results:
        b = results[0]
        report.append("best: %s at position %.2f, %.0f deg, side %+d -- "
                      "sacrifices %d of %d sound cells (%.0f%%)"
                      % (b["key"], b["position"], b["rotate_deg"], b["side"],
                         b["sound_sacrificed"], b["n_sound"],
                         100 * b["sound_sacrificed_frac"]))
    return results, report


def brief(results, top=8):
    """Compact table for the agent prompt -- verified options only.

    The agent chooses among these; it does not invent placements. Feed it the
    Pareto front rather than a single ranking so the trade-off stays the
    participant's to make.
    """
    front = scoring.pareto_front(results)
    front.sort(key=lambda r: r["sound_sacrificed_weighted"])
    # collapse rows that score identically -- four rotations of the same joint
    # at the same station are one option, not four, and the agent should not
    # spend prompt on the duplicates
    seen = {}
    for r in front:
        sig = (r["key"], round(r["position"], 3), r["side"],
               r["sound_sacrificed"], r["damage_left"])
        seen.setdefault(sig, []).append(r)
    lines = ["key  pos   side  rot        sound_lost  weighted"]
    for sig, group in list(seen.items())[:top]:
        r = group[0]
        rots = "/".join("%.0f" % g["rotate_deg"] for g in group)
        lines.append("%-4s %.2f  %+d    %-9s  %4d/%-4d    %.1f"
                     % (r["key"], r["position"], r["side"], rots,
                        r["sound_sacrificed"], r["n_sound"],
                        r["sound_sacrificed_weighted"]))
    return "\n".join(lines)
