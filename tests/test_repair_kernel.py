import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from workshop_robarch_2026 import kernel, joints

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_sw1_partitions_canonical_stock():
    j = joints.load_joint(ROOT, "SW1")
    rep = joints.check_joint(j)
    assert rep["partition_ok"] and rep["has_both_sides"] and rep["orientation_ok"]


def test_build_repair_partitions_beam_both_sides():
    j = joints.load_joint(ROOT, "SW1")
    frame = {"origin": [3.0, 1.0, -0.5], "u": [1, 0, 0], "v": [0, 1, 0],
             "w": [0, 0, 1], "width": 0.14, "height": 0.14, "length": 2.0}
    rng = np.random.default_rng(3)
    pts = rng.uniform([3.0, 1.0, -0.5], [3.14, 3.0, -0.36], size=(30000, 3))
    for side in (+1, -1):
        r = kernel.build_repair(j, frame, position=0.8, rotate_deg=45.0, side=side)
        cuts = [kernel.Cut.from_json(c) for c in r["parts"][0]["cuts"]]
        kept = kernel.points_in_part(pts, cuts, r["parts"][0]["expression"])
        pros = kernel.points_in_part(pts, cuts, r["parts"][1]["expression"])
        stock = kernel.points_in_part(pts, cuts, "lhf_0")
        assert int((kept & pros).sum()) == 0
        assert int((stock & ~kept & ~pros).sum()) == 0
        assert kept.sum() > 0 and pros.sum() > 0
        # prosthesis must sit on the requested side
        vk = pts[kept][:, 1].mean(); vp = pts[pros][:, 1].mean()
        assert (vp > vk) == (side > 0)
