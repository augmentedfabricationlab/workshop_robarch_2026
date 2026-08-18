import os
import sys

import numpy as np


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from workshop_robarch_2026 import anyjoint, joints


def _beam_cells():
    frame = {
        "origin": [0.0, 0.0, 0.0],
        "u": [1.0, 0.0, 0.0],
        "v": [0.0, 1.0, 0.0],
        "w": [0.0, 0.0, 1.0],
        "width": 0.12,
        "length": 1.20,
        "height": 0.12,
    }
    u = np.linspace(0.015, 0.105, 4)
    v = np.linspace(0.015, 1.185, 40)
    w = np.linspace(0.015, 0.105, 4)
    cells = np.array(np.meshgrid(u, v, w, indexing="ij")).reshape(3, -1).T
    damage = np.zeros(len(cells))
    damage[(cells[:, 1] > 0.94) & (cells[:, 2] < 0.08)] = 0.9
    return frame, cells, damage


def test_generated_lap_variants_are_valid_joint_partitions():
    variants = [
        anyjoint.lap_candidate(),
        anyjoint.lap_candidate(0.35, 0.35, 0.35, 0.4),
        anyjoint.lap_candidate(0.685, 0.70, 0.70, 0.6),
        anyjoint.lap_candidate(0.0, -1.0, 1.0, 0.5),
        anyjoint.scarf_candidate(2.25),
    ]
    for candidate in variants:
        joint = anyjoint.compile_candidate(candidate)
        check = joints.check_joint(joint, n=30000)
        assert check["accepted"], (candidate.candidate_id, check)


def test_search_returns_only_damage_covering_generated_candidates():
    frame, cells, damage = _beam_cells()
    grammar = [
        anyjoint.lap_candidate(0.0, 0.0, 0.0, 0.5),
        anyjoint.lap_candidate(0.35, 0.35, 0.35, 0.5),
        anyjoint.scarf_candidate(2.0),
    ]
    results, report = anyjoint.search(
        frame,
        cells,
        damage,
        threshold=0.5,
        grammar=grammar,
        n_positions=5,
        rotations=(0.0, 90.0),
        sides=(+1, -1),
    )
    assert results, report
    assert all(result["valid"] for result in results)
    assert all(result["damage_left"] == 0 for result in results)
    assert all(result["candidate_id"].startswith("AJ-") for result in results)
    selected = anyjoint.shortlist(results, 3)
    assert len({item["candidate_id"] for item in selected}) == len(selected)


def test_default_grammar_contains_novel_and_degenerate_families():
    grammar = anyjoint.default_grammar()
    ids = {candidate.candidate_id for candidate in grammar}
    assert len(grammar) > 20
    assert any(candidate.family == "scarf" for candidate in grammar)
    assert any(
        candidate.parameters.get("chevron") == 0.35
        and candidate.parameters.get("rake_left") == 0.35
        for candidate in grammar
    )
    assert len(ids) == len(grammar)
