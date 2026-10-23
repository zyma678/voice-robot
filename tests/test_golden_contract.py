"""Drift gate for the on-device memory port contract (plan P0 / ADR 0003).

The firmware C port must reproduce the deterministic memory + prompt behaviour
captured in ``tests/golden/memory_contract.json``. This test fails if the Python
reference implementation changes that behaviour without an intentional baseline
refresh. To refresh after an intended change:

    python -m tests.golden.generate_memory_contract

then review the diff and commit the updated JSON alongside the code change.
"""

import json

from tests.golden.generate_memory_contract import BASELINE_PATH, build_contract


def test_memory_contract_matches_baseline() -> None:
    assert BASELINE_PATH.exists(), (
        "missing golden baseline; run "
        "`python -m tests.golden.generate_memory_contract`"
    )
    expected = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    actual = build_contract()
    assert actual == expected, (
        "memory contract drifted from committed baseline. If intentional, "
        "regenerate with `python -m tests.golden.generate_memory_contract` "
        "and review the diff (this also flags the firmware C port needs updating)."
    )


def test_retrieval_skips_deleted_but_scores_remain_for_unit_check() -> None:
    """Guard the subtle rule: search() filters status==deleted *before* ranking,
    yet the isolated scorer still returns a positive score for such an entry.
    The C port must filter deleted in the ranking pipeline, not in the scorer."""
    contract = build_contract()
    dinosaur = contract["retrieval"]["恐龙 故事"]
    assert "mem_e5" not in dinosaur["ranked_ids"]
    assert dinosaur["scores"]["mem_e5"] > dinosaur["scores"]["mem_a1"]
