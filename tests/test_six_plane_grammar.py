import os
import sys

import numpy as np


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from workshop_robarch_2026 import six_plane_grammar as grammar


ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_sj1_to_sj6_share_six_slots_and_one_rule():
    for key in ("SJ1", "SJ2", "SJ3", "SJ4", "SJ5", "SJ6"):
        template = grammar.get_template(key)
        assert len(template.planes) == 6
        assert template.groups == grammar.COMMON_GROUPS
        assert template.rule == grammar.COMMON_RULE


def test_expected_degeneracies_and_rhino_predicate_simplification():
    support_counts = {"SJ1": 3, "SJ2": 3, "SJ3": 5, "SJ4": 5,
                      "SJ5": 1, "SJ6": 3, "SJ7": 6}
    predicate_counts = {"SJ1": 4, "SJ2": 4, "SJ3": 6, "SJ4": 6,
                        "SJ5": 1, "SJ6": 4, "SJ7": 6}
    for key in grammar.list_keys():
        template = grammar.get_template(key)
        predicates, groups, remap = grammar.simplify(template)
        assert len(grammar.support_groups(template)) == support_counts[key]
        assert len(predicates) == predicate_counts[key]
        assert len(remap) == 6
        assert groups


def test_simplified_and_full_grammar_are_logically_equal():
    rng = np.random.default_rng(613)
    points = rng.uniform([-0.5, 0.0, -0.5], [0.5, 3.0, 0.5], size=(60000, 3))
    for key in grammar.list_keys():
        full = grammar.removal_mask(grammar.build_joint(key), points)
        simplified = grammar.removal_mask(
            grammar.build_joint(key, simplify_predicates=True), points
        )
        assert np.array_equal(full, simplified), key


def test_every_template_matches_the_stored_corpus_geometry():
    for key in grammar.list_keys():
        result = grammar.compare_to_stored(ROOT, key, n_random=40000, seed=20260817)
        assert result["mismatch"] == 0, result
        assert result["accepted"], result

