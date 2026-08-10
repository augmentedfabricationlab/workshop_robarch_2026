"""Score a placed repair against a beam's damage cells.

`kept_fraction` is a property of the idealized joint and sits near 0.5 for the
whole catalogue, so it cannot rank anything. What ranks a repair is what it
does to *this* beam:

    damage_left        damaged cells the repair does not remove   -> must be 0
    sound_sacrificed   sound cells the repair does remove         -> minimise

Both come from classifying the cell centroids against the placed prosthesis.

FRAME CONVENTION -- read this before using the module
-----------------------------------------------------
`build_repair` takes a frame whose **origin is a corner of the beam, not its
centre**. From that corner:

    along u : 0 .. width
    along v : 0 .. length     <- the beam axis
    along w : 0 .. height

`CellularizedPart.from_mesh` recentres its mesh on the centroid, so a cell
cloud built there is centred on the origin, not cornered at it. Mixing the two
does not raise: the classifier simply reports that nothing is removed, and the
search returns no valid candidate. `check_cells` exists to catch that, and
`score` calls it by default. Do not turn it off to make an error go away.
"""
from __future__ import annotations

import numpy as np

from . import kernel


# --------------------------------------------------------------------- frame
def frame_axes(frame):
    """-> origin, u, v, w, (width, length, height), all unit axes."""
    o = np.asarray(frame["origin"], float)
    u = np.asarray(frame["u"], float); u = u / np.linalg.norm(u)
    v = np.asarray(frame["v"], float); v = v / np.linalg.norm(v)
    w = np.asarray(frame["w"], float); w = w / np.linalg.norm(w)
    return o, u, v, w, (float(frame["width"]), float(frame["length"]),
                        float(frame["height"]))


def to_local(cells, frame):
    """World points -> (u, v, w) coordinates measured from the beam corner."""
    o, u, v, w, _ = frame_axes(frame)
    d = np.asarray(cells, float) - o
    return np.column_stack([d @ u, d @ v, d @ w])


def check_cells(cells, frame, tol=0.02):
    """Do the cells actually sit inside the beam this frame describes?

    tol is a fraction of the corresponding beam dimension. Returns
    (ok, message, local_bbox). Never raises -- callers in Grasshopper want a
    string, not a traceback.
    """
    loc = to_local(cells, frame)
    _, _, _, _, (W, L, H) = frame_axes(frame)
    ext = np.array([W, L, H], float)
    lo, hi = loc.min(axis=0), loc.max(axis=0)
    slack = tol * ext
    under = lo < -slack
    over = hi > ext + slack
    if not (under.any() or over.any()):
        return True, "cells sit inside the beam box", (lo, hi)

    names = ("u/width", "v/length", "w/height")
    bad = []
    for i in range(3):
        if under[i] or over[i]:
            bad.append("%s: cells span %.4f..%.4f, beam is 0..%.4f"
                       % (names[i], lo[i], hi[i], ext[i]))
    hint = ""
    if (lo < -slack).all() and abs(lo + hi).max() < 0.25 * ext.max():
        hint = ("\n  the cloud looks CENTRED on the origin while the frame is "
                "CORNERED at it -- this is the CellularizedPart centroid recentre. "
                "Translate the cells by +half the section, or build the cell grid "
                "in the beam frame instead of the mesh frame.")
    return False, "cells do not match the beam frame:\n  " + "\n  ".join(bad) + hint, (lo, hi)


# ------------------------------------------------------------------ classify
def removed_mask(cells, repair):
    """Which cells fall in the prosthesis (i.e. get cut away)?"""
    cuts = [kernel.Cut.from_json(c) for c in repair["parts"][0]["cuts"]]
    expr = repair["parts"][1]["expression"]
    return kernel.points_in_part(np.asarray(cells, float), cuts, expr)


def score(cells, damage, repair, threshold=0.5, frame=None, verify=True):
    """Score one placed repair.

    cells     (N,3) world centroids
    damage    (N,)  damage score per cell, 0..1
    repair    the dict returned by kernel.build_repair
    threshold cells at or above this are 'damaged'

    Returns a dict. `valid` is True only when no damaged cell survives.
    """
    cells = np.asarray(cells, float)
    damage = np.asarray(damage, float)
    if verify and frame is not None:
        ok, msg, _ = check_cells(cells, frame)
        if not ok:
            return {"valid": False, "error": msg}

    rem = removed_mask(cells, repair)
    dmg = damage >= threshold
    n_d, n_s = int(dmg.sum()), int((~dmg).sum())

    damage_left = int((dmg & ~rem).sum())
    sound_sacrificed = int((~dmg & rem).sum())
    return {
        "valid": damage_left == 0,
        "damage_left": damage_left,
        "damage_removed": int((dmg & rem).sum()),
        "sound_sacrificed": sound_sacrificed,
        # A cell at 0.49 is nearly rotten but counts as a whole sound cell
        # lost; a cell at 0.51 counts as free. Weighting by (1 - damage)
        # removes that cliff -- removing a marginal cell costs little, removing
        # pristine wood costs 1.0. Rank on this, report the integer count.
        "sound_sacrificed_weighted": float((1.0 - damage[~dmg & rem]).sum()),
        "sound_kept": int((~dmg & ~rem).sum()),
        # fractions are what you compare across beams with different cell counts
        "damage_left_frac": (damage_left / n_d) if n_d else 0.0,
        "sound_sacrificed_frac": (sound_sacrificed / n_s) if n_s else 0.0,
        # severity-weighted: leaving one badly rotten cell is worse than five marginal ones
        "damage_left_weighted": float(damage[dmg & ~rem].sum()),
        "n_damaged": n_d,
        "n_sound": n_s,
    }


def pareto_front(results, keys=("sound_sacrificed", "damage_left")):
    """Non-dominated subset, both objectives minimised.

    Hand this to the agent rather than a single ranking: the trade-off between
    sacrificing sound wood and leaving damage is the participant's call, not
    the tool's.
    """
    out = []
    for a in results:
        pa = [a[k] for k in keys]
        if not any(all(b[k] <= a[k] for k in keys) and
                   any(b[k] < a[k] for k in keys) for b in results):
            out.append(a)
    return out
