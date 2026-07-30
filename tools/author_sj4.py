"""Regenerate SJ4 from its five parameters, via the same half-space helper
the Grasshopper author component uses.

    python tools/author_sj4.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import numpy as np
from workshop_robarch_2026 import kernel, joints

ROOT    = os.path.join(os.path.dirname(__file__), "..")
ASPECT  = 3.0
R_RIDGE = 0.125          # sogi: ridge height above mid-section at x = 0
H_CHEV  = 0.5            # yahazu: how far the point runs ahead of the flank
Y_TIP   = 2.5            # spear tip of the lower tongue, at x = 0
Y_STEP  = ASPECT - Y_TIP
ROOF_K  = 4.0 * R_RIDGE
CHEV_K  = H_CHEV / 0.5

# (name, normal pointing INTO the removed material, a point on the plane)
PLANES = [
    ("below_l1",  (-ROOF_K, 0.0, -1.0), (0.0, 0.0,  R_RIDGE)),
    ("below_l2",  ( ROOF_K, 0.0, -1.0), (0.0, 0.0,  R_RIDGE)),
    ("above_l1",  ( ROOF_K, 0.0,  1.0), (0.0, 0.0,  R_RIDGE)),
    ("above_l2",  (-ROOF_K, 0.0,  1.0), (0.0, 0.0,  R_RIDGE)),
    ("beyond_f1", ( CHEV_K, 1.0, 0.0),  (0.0, Y_TIP,  0.0)),
    ("beyond_f2", (-CHEV_K, 1.0, 0.0),  (0.0, Y_TIP,  0.0)),
    ("beyond_g1", (-CHEV_K, 1.0, 0.0),  (0.0, Y_STEP, 0.0)),
    ("beyond_g2", ( CHEV_K, 1.0, 0.0),  (0.0, Y_STEP, 0.0)),
]
GROUPS = [[0, 1, 4], [0, 1, 5], [2, 6, 7], [3, 6, 7]]

if __name__ == "__main__":
    cuts = [kernel.half_space_cut(nm, n, p, ASPECT) for nm, n, p in PLANES]
    j = {"schema": kernel.SCHEMA, "key": "SJ4", "aspect": ASPECT, "section": 1.0,
         "removal_groups": GROUPS, "cuts": [c.to_json() for c in cuts]}
    chk = joints.check_joint(j)
    print("check_joint:", {k: v for k, v in chk.items()
                           if k.endswith("_ok") or k == "kept_fraction"})
    assert chk["partition_ok"] and chk["has_both_sides"] \
        and chk["orientation_ok"] and chk["end_overshoot_ok"]
    print(joints.save_joint(ROOT, "SJ4", ASPECT, cuts, removal_groups=GROUPS))
