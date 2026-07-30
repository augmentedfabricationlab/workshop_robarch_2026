"""Joint catalogue: load, save, and check authored joints.

A joint on disk = data/corpus/joints/<KEY>.json (geometry) + <KEY>.md (datasheet).

Authoring contract (canonical space -- see kernel docstring):
    section 1.0: x, z in [-0.5, 0.5];  length along y in [0, aspect].
    You author the cutters of the PRIMARY part only. Cutters must overshoot
    the stock in x/z (share no face with it). kept/prosthesis derive from them.
"""
from __future__ import annotations
import json
import os
import numpy as np

from . import kernel


def joints_dir(repo_root: str) -> str:
    return os.path.join(repo_root, "data", "corpus", "joints")


def list_keys(repo_root: str):
    d = joints_dir(repo_root)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(d) if f.endswith(".json"))


def load_joint(repo_root: str, key: str) -> dict:
    path = os.path.join(joints_dir(repo_root), key + ".json")
    with open(path, "r", encoding="utf-8") as f:
        j = json.load(f)
    if j.get("schema") != kernel.SCHEMA:
        raise ValueError("%s: schema %r, expected %r" % (key, j.get("schema"), kernel.SCHEMA))
    return j


def load_datasheet(repo_root: str, key: str) -> str:
    path = os.path.join(joints_dir(repo_root), key + ".md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def save_joint(repo_root: str, key: str, aspect: float, cuts, notes: str = "",
               removal_groups=None) -> str:
    """cuts: list of kernel.Cut in canonical space (cutters only, no stock).

    removal_groups: optional lists of 0-based indices into `cuts`. Members
    of a group are intersected, groups are unioned. Omit for a plain union.
    """
    d = joints_dir(repo_root)
    os.makedirs(d, exist_ok=True)
    j = {"schema": kernel.SCHEMA, "key": key, "aspect": float(aspect),
         "section": 1.0, "cuts": [c.to_json() for c in cuts]}
    if removal_groups:
        j["removal_groups"] = [[int(i) for i in g] for g in removal_groups]
    path = os.path.join(d, key + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(j, f, indent=1)
    md = os.path.join(d, key + ".md")
    if not os.path.exists(md):
        with open(md, "w", encoding="utf-8") as f:
            f.write("# %s\n\n%s\n\n"
                    "## Purpose\n(what damage / situation this joint answers)\n\n"
                    "## Behaviour\n(tension / bending / shear, weaknesses)\n\n"
                    "## Effort\n(cutting complexity, tools)\n\n"
                    "## Provenance\n(historical usage, sources)\n" % (key, notes))
    return path


def check_joint(joint: dict, n: int = 40000) -> dict:
    """Headless acceptance test on the canonical stock: kept + prosthesis must
    partition the stock exactly (analytic point classification, no Rhino)."""
    aspect = float(joint["aspect"])
    section = float(joint.get("section", 1.0))
    stock = kernel.canonical_stock(aspect, section)
    cutters = [kernel.Cut.from_json(c) for c in joint["cuts"]]
    for i, c in enumerate(cutters):
        c.name = "lhf_%d" % (i + 1)
    all_cuts = [stock] + cutters
    removal = kernel.removal_expression(joint, [c.name for c in cutters])
    kept_e = "Difference(lhf_0, %s)" % removal
    pros_e = "Intersection(lhf_0, %s)" % removal

    rng = np.random.default_rng(7)
    pts = rng.uniform([-0.5 * section, 0.0, -0.5 * section],
                      [0.5 * section, aspect * section, 0.5 * section], size=(n, 3))
    in_stock = kernel.points_in_part(pts, all_cuts, "lhf_0")
    kept = kernel.points_in_part(pts, all_cuts, kept_e)
    pros = kernel.points_in_part(pts, all_cuts, pros_e)
    overlap = int((kept & pros).sum())
    uncovered = int((in_stock & ~kept & ~pros).sum())
    # orientation: kept must dominate near y=0 and vanish toward y=aspect
    L = aspect * section
    lo_band = in_stock & (pts[:, 1] < 0.15 * L)
    hi_band = in_stock & (pts[:, 1] > 0.85 * L)
    kept_lo = float((kept & lo_band).sum()) / max(1, int(lo_band.sum()))
    kept_hi = float((kept & hi_band).sum()) / max(1, int(hi_band.sum()))
    return {
        "kept_fraction": float(kept.sum()) / max(1, int(in_stock.sum())),
        "prosthesis_fraction": float(pros.sum()) / max(1, int(in_stock.sum())),
        "overlap_points": overlap,
        "uncovered_points": uncovered,
        "partition_ok": overlap == 0 and uncovered == 0,
        "has_both_sides": bool(kept.sum() > 0 and pros.sum() > 0),
        "kept_share_start": kept_lo,
        "kept_share_end": kept_hi,
        "orientation_ok": bool(kept_lo > kept_hi),
        "end_overshoot_ok": _end_overshoot_ok(joint, cutters),
    }


def _end_overshoot_ok(joint, cutters) -> bool:
    """Cutter coverage of the section must not DROP across the prosthesis-end
    plane (y = aspect). A drop means a cutter face lies exactly in the trim
    plane -- the tangency that breaks Rhino booleans with an exact trim."""
    import numpy as np
    from . import kernel as _k
    aspect = float(joint["aspect"])
    section = float(joint.get("section", 1.0))
    L = aspect * section
    rng = np.random.default_rng(11)
    xz = rng.uniform([-0.5 * section, -0.5 * section],
                     [0.5 * section, 0.5 * section], size=(4000, 2))
    groups = _k.removal_groups(joint, len(cutters))
    def coverage(y):
        pts = np.column_stack([xz[:, 0], np.full(len(xz), y), xz[:, 1]])
        acc = np.zeros(len(pts), bool)
        for g in groups:
            m = np.ones(len(pts), bool)
            for i in g:
                m &= _k._points_in_cut(pts, cutters[i])
            acc |= m
        return float(acc.mean())
    inner = coverage(0.995 * L)
    outer = coverage(1.005 * L)
    return outer >= inner - 0.01
