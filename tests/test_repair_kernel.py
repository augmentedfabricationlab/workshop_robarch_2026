import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from workshop_robarch_2026 import kernel, joints

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_sj1_partitions_canonical_stock():
    j = joints.load_joint(ROOT, "SJ1")
    rep = joints.check_joint(j)
    assert rep["partition_ok"] and rep["has_both_sides"] and rep["orientation_ok"]


def test_build_repair_partitions_beam_both_sides():
    j = joints.load_joint(ROOT, "SJ1")
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


def test_every_catalogue_joint_passes_acceptance():
    for key in joints.list_keys(ROOT):
        j = joints.load_joint(ROOT, key)
        chk = joints.check_joint(j)
        assert chk["partition_ok"], key
        assert chk["has_both_sides"], key
        assert chk["orientation_ok"], key
        assert chk["end_overshoot_ok"], key


def test_sj4_needs_intersect_groups():
    """SJ4's removal is a union of intersect groups. Read as a plain union
    it removes the entire stock, so a group-unaware kernel must fail loudly
    rather than return a wrong solid."""
    j = joints.load_joint(ROOT, "SJ4")
    assert j.get("removal_groups"), "SJ4 must declare removal_groups"
    names = ["lhf_%d" % (i + 1) for i in range(len(j["cuts"]))]
    assert (
        kernel.removal_expression(j, names).count("Intersection")
        == len(j["removal_groups"])
    )

    flat = dict(j)
    flat.pop("removal_groups")
    chk = joints.check_joint(flat)
    assert not chk["has_both_sides"]
    assert not chk["orientation_ok"]


def test_parse_groups():
    assert kernel.parse_groups("", 3) is None
    assert kernel.parse_groups("0,1; 1,2", 3) == [[0, 1], [1, 2]]
    assert kernel.parse_groups(" 0 1 ;\n 1 2 ", 3) == [[0, 1], [1, 2]]
    for bad in ("0,9", "0,1"):          # out of range, and cut 2 unused
        try:
            kernel.parse_groups(bad, 3)
            assert False, bad
        except ValueError:
            pass


def test_author_sj4_from_planes_only():
    """The half-space + groups route the GH author component now offers,
    exercised end to end without Rhino."""
    import tools.author_sj4 as a
    cuts = [kernel.half_space_cut(nm, n, p, a.ASPECT) for nm, n, p in a.PLANES]
    grp = kernel.parse_groups("0,1,4; 0,1,5; 2,6,7; 3,6,7", len(cuts))
    assert grp == a.GROUPS
    j = {"schema": kernel.SCHEMA, "key": "SJ4x", "aspect": a.ASPECT,
         "section": 1.0, "removal_groups": grp,
         "cuts": [c.to_json() for c in cuts]}
    chk = joints.check_joint(j)
    assert chk["partition_ok"] and chk["has_both_sides"]
    assert chk["orientation_ok"] and chk["end_overshoot_ok"]
    assert abs(chk["kept_fraction"] - 0.504) < 0.01
