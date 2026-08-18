"""Verify the shared six-plane grammar against stored SJ1--SJ7 solids.

The comparison samples each resulting solid geometrically.  It therefore
works across the corpus' two authoring styles: finite profile extrusions and
oriented half-spaces.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from workshop_robarch_2026 import six_plane_grammar as grammar  # noqa: E402


def verify(n_random: int = 1_000_000, seed: int = 20260817) -> list[dict]:
    results = [
        grammar.compare_to_stored(REPO, key, n_random=n_random, seed=seed)
        for key in grammar.list_keys()
    ]
    failed = [result for result in results
              if result["mismatch"] or not result["accepted"]]
    if failed:
        raise AssertionError("six-plane verification failed: %s" % failed)
    return results


def _print_report(results: list[dict]) -> None:
    print("joint  supports  predicates  samples    mismatch  accepted  rule")
    for result in results:
        print(
            "%-5s  %8d  %10d  %8d  %8d  %-8s  %s"
            % (
                result["key"],
                result["support_plane_count"],
                result["predicate_count"],
                result["samples"],
                result["mismatch"],
                str(result["accepted"]),
                result["rule"],
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    args = parser.parse_args()
    results = verify(args.samples, args.seed)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        _print_report(results)


if __name__ == "__main__":
    main()
