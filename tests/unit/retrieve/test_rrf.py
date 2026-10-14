"""Hand-calculated RRF fusion + deterministic tie-break (SPEC §6 step 3)."""

from __future__ import annotations

import math

import pytest

from app.retrieve.rrf import rrf_fuse

K = 60


def test_rrf_hand_calculated_scores_and_order() -> None:
    """A worked k=60 example: fused scores and the resulting order are exact.

    Lexical ranking: [A, B]. Vector ranking: [B, C].
      - A: lexical rank 1 only           -> 1/(60+1)                 = 1/61
      - B: lexical rank 2 + vector rank 1 -> 1/(60+2) + 1/(60+1)      = 1/62 + 1/61
      - C: vector rank 2 only            -> 1/(60+2)                 = 1/62
    So B > A > C.
    """
    lexical = ["A", "B"]
    vector = ["B", "C"]

    fused = rrf_fuse([lexical, vector], k=K)
    scores = dict(fused)

    assert math.isclose(scores["A"], 1 / 61)
    assert math.isclose(scores["B"], 1 / 62 + 1 / 61)
    assert math.isclose(scores["C"], 1 / 62)

    # B (two lists) beats A (rank 1 in one) beats C (rank 2 in one).
    assert [key for key, _ in fused] == ["B", "A", "C"]


def test_rrf_single_list_example() -> None:
    """A doc appearing only at vector rank 1 scores exactly 1/61."""
    fused = rrf_fuse([[], ["only"]], k=K)
    assert len(fused) == 1
    key, score = fused[0]
    assert key == "only"
    assert math.isclose(score, 1 / 61)


def test_rrf_tie_break_is_deterministic_on_key() -> None:
    """Equal fused scores are ordered by the key ascending, not by input order."""
    # Two docs each at rank 1 of one list -> identical score 1/61.
    fused = rrf_fuse([["z"], ["a"]], k=K)
    scores = dict(fused)
    assert math.isclose(scores["a"], scores["z"])
    # Tie broken by the key ascending: "a" before "z", regardless of the input
    # list order (z was fed first).
    assert [key for key, _ in fused] == ["a", "z"]


def test_rrf_tuple_key_tie_break() -> None:
    """Tuple keys (document_id, ord) break ties lexicographically."""
    fused = rrf_fuse([[("doc_b", 0)], [("doc_a", 3)]], k=K)
    assert [key for key, _ in fused] == [("doc_a", 3), ("doc_b", 0)]


def test_rrf_absent_doc_contributes_nothing() -> None:
    """A doc missing from a list adds no score for that list."""
    fused = rrf_fuse([["A"], ["A"]], k=K)
    # A appears at rank 1 in both -> 2 * 1/61.
    assert math.isclose(dict(fused)["A"], 2 / 61)


def test_rrf_equal_and_omitted_weights_match_plain_rrf() -> None:
    """weights=None and weights all 1.0 reproduce plain RRF byte-for-byte."""
    lexical = ["A", "B"]
    vector = ["B", "C"]

    plain = rrf_fuse([lexical, vector], k=K)
    omitted = rrf_fuse([lexical, vector], k=K, weights=None)
    identity = rrf_fuse([lexical, vector], k=K, weights=[1.0, 1.0])

    assert omitted == plain
    assert identity == plain


def test_rrf_higher_lexical_weight_raises_lexical_favored_rank() -> None:
    """A heavier lexical weight lifts a lexical-only key above a vector-only key.

    Lexical ranking: [A]. Vector ranking: [B]. At equal weights both score 1/61
    and the tie-break orders A before B. Weighting the lexical list 2x makes A's
    fused score strictly larger, so A leads on score (not just the tie-break),
    and B's score is unchanged.
    """
    lexical = ["A"]
    vector = ["B"]

    weighted = rrf_fuse([lexical, vector], k=K, weights=[2.0, 1.0])
    scores = dict(weighted)

    assert math.isclose(scores["A"], 2 / 61)
    assert math.isclose(scores["B"], 1 / 61)
    assert scores["A"] > scores["B"]
    assert [key for key, _ in weighted] == ["A", "B"]


def test_rrf_weights_length_mismatch_raises() -> None:
    """A weights length that does not match the ranked-list count is rejected."""
    with pytest.raises(ValueError, match="weights length"):
        rrf_fuse([["A"], ["B"]], k=K, weights=[1.0])
