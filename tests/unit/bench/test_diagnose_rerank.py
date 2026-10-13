"""Unit tests for the Defect 2 rerank diagnosis — PURE logic, offline.

The diagnosis driver (:func:`bench.diagnose_rerank.diagnose`) builds the real
disk-cached providers and is human-run; it is never collected here. These tests
exercise ONLY the pure comparison logic (:func:`compare_task`, :func:`summarize`,
:func:`write_comparison_csv`) over SYNTHETIC prepared candidates, so nothing
touches the network, Postgres or the real cache (CLAUDE.md).
"""

from __future__ import annotations

from app.extract.ir import make_document_id
from app.retrieve.select import Candidate
from bench.diagnose_rerank import (
    compare_task,
    summarize,
    write_comparison_csv,
)
from bench.runners.ours import OursRunner, PreparedCandidates
from bench.tasks_schema import Evidence, Task

_EVIDENCE_URL = "https://example.test/evidence"
_OTHER_URL = "https://example.test/other"
_EVIDENCE_DOC = make_document_id(_EVIDENCE_URL)
_OTHER_DOC = make_document_id(_OTHER_URL)


def _task() -> Task:
    """A dev task whose single evidence quote lives at ``_EVIDENCE_URL``."""
    return Task(
        id="t-diag",
        category="guide",
        split="dev",
        urls=[_EVIDENCE_URL, _OTHER_URL],
        question="what?",
        answer_type="judge",
        expected=["x"],
        expected_evidence=[Evidence(url=_EVIDENCE_URL, quote="q")],
        author="human",
        validated=True,
    )


def _candidate(
    doc: str, ord_: int, value: float, embedding: list[float] | None = None
) -> Candidate:
    """A selectable candidate. ``embedding`` defaults to a per-doc orthogonal vec.

    Orthogonal embeddings keep the greedy diversity penalty at 0, so selection is
    driven by ``value`` alone unless a test deliberately overrides the embedding.
    """
    if embedding is None:
        embedding = [1.0, 0.0] if doc == _EVIDENCE_DOC else [0.0, 1.0]
    return Candidate(
        chunk_id=0,
        document_id=doc,
        ord=ord_,
        value=value,
        tokens=10,
        embedding=embedding,
    )


def _prepared(candidates: list[Candidate]) -> PreparedCandidates:
    """Wrap candidates with the md/url maps the runner needs to emit context."""
    md_by_key = {
        (c.document_id, c.ord): f"text-{c.document_id}-{c.ord}" for c in candidates
    }
    url_by_document = {_EVIDENCE_DOC: _EVIDENCE_URL, _OTHER_DOC: _OTHER_URL}
    return PreparedCandidates(
        candidates=candidates,
        md_by_key=md_by_key,
        url_by_document=url_by_document,
        max_rerank=max((c.value for c in candidates), default=None),
        rerank_scores=[c.value for c in candidates],
    )


def _runners() -> tuple[OursRunner, OursRunner]:
    """The rerank-on and rerank-off runners with min_value=0 (select anything)."""
    rerank = OursRunner(min_value=0.0, lambda_=0.3)
    norerank = OursRunner(
        name="ours_no_rerank", use_rerank=False, min_value=0.0, lambda_=0.3
    )
    return rerank, norerank


def test_rerank_demotes_evidence_is_detected() -> None:
    """When rerank gives the evidence a LOW value, the comparison records it.

    rerank-on: evidence value 0.1, other 0.9 -> rerank favors the non-evidence
    fragment. rerank-off (RRF value): evidence 0.9, other 0.1 -> it keeps the
    evidence first. With a budget large enough for both, both variants select
    both fragments, but the per-task value means expose the demotion.
    """
    rerank_runner, norerank_runner = _runners()
    prepared_rerank = _prepared(
        [_candidate(_EVIDENCE_DOC, 0, 0.1), _candidate(_OTHER_DOC, 0, 0.9)]
    )
    prepared_norerank = _prepared(
        [_candidate(_EVIDENCE_DOC, 0, 0.9), _candidate(_OTHER_DOC, 0, 0.1)]
    )

    comp = compare_task(
        _task(),
        prepared_rerank,
        prepared_norerank,
        rerank_runner,
        norerank_runner,
        budget=4000,
    )

    assert comp.evidence_in_candidates is True
    assert comp.evidence_value_mean == 0.1
    assert comp.other_value_mean == 0.9
    # Both fit a 4000 budget, so both variants keep the evidence document.
    assert comp.evidence_in_context_rerank is True
    assert comp.evidence_in_context_norerank is True


def test_tiny_budget_rerank_drops_evidence_norerank_keeps_it() -> None:
    """A tight budget surfaces the selection divergence the diagnosis hunts for.

    With room for ONE 10-token fragment, rerank-on picks the high-value
    non-evidence fragment and DROPS the evidence; rerank-off picks the evidence
    (its RRF value is higher). The comparison must flag the asymmetry.
    """
    rerank_runner, norerank_runner = _runners()
    prepared_rerank = _prepared(
        [_candidate(_EVIDENCE_DOC, 0, 0.1), _candidate(_OTHER_DOC, 0, 0.9)]
    )
    prepared_norerank = _prepared(
        [_candidate(_EVIDENCE_DOC, 0, 0.9), _candidate(_OTHER_DOC, 0, 0.1)]
    )

    comp = compare_task(
        _task(),
        prepared_rerank,
        prepared_norerank,
        rerank_runner,
        norerank_runner,
        budget=10,
    )

    assert comp.selected_rerank == 1
    assert comp.selected_norerank == 1
    assert comp.evidence_in_context_rerank is False
    assert comp.evidence_in_context_norerank is True
    # The two variants disagree on exactly which fragment to keep.
    assert comp.only_rerank == 1
    assert comp.only_norerank == 1


def test_summarize_counts_evidence_loss_asymmetry() -> None:
    """``summarize`` aggregates the per-task flags into headline counts."""
    rerank_runner, norerank_runner = _runners()
    prepared_rerank = _prepared(
        [_candidate(_EVIDENCE_DOC, 0, 0.1), _candidate(_OTHER_DOC, 0, 0.9)]
    )
    prepared_norerank = _prepared(
        [_candidate(_EVIDENCE_DOC, 0, 0.9), _candidate(_OTHER_DOC, 0, 0.1)]
    )
    comp = compare_task(
        _task(),
        prepared_rerank,
        prepared_norerank,
        rerank_runner,
        norerank_runner,
        budget=10,
    )

    summary = summarize([comp])

    assert summary["tasks"] == 1
    assert summary["evidence_in_candidates"] == 1
    assert summary["evidence_in_context_rerank"] == 0
    assert summary["evidence_in_context_norerank"] == 1
    assert summary["evidence_lost_by_rerank_only"] == 1
    assert summary["evidence_lost_by_norerank_only"] == 0
    assert summary["selection_disagreements"] == 1


def test_write_comparison_csv_is_deterministic(tmp_path: object) -> None:
    """The CSV artifact is byte-stable (sorted by task_id) and has a header."""
    from pathlib import Path

    out = Path(str(tmp_path)) / "rerank_vs_norerank.csv"
    rerank_runner, norerank_runner = _runners()
    prepared = _prepared([_candidate(_EVIDENCE_DOC, 0, 0.5)])
    comp = compare_task(
        _task(), prepared, prepared, rerank_runner, norerank_runner, budget=4000
    )

    first = write_comparison_csv(out, [comp])
    second = write_comparison_csv(out, [comp])

    assert first == second
    assert first.splitlines()[0].startswith("task_id,category,")
    assert "t-diag" in first


def test_empty_candidates_select_nothing() -> None:
    """A task with no candidates selects nothing and reports no evidence."""
    rerank_runner, norerank_runner = _runners()
    empty = PreparedCandidates(
        candidates=[],
        md_by_key={},
        url_by_document={},
        max_rerank=None,
        rerank_scores=[],
    )
    comp = compare_task(
        _task(), empty, empty, rerank_runner, norerank_runner, budget=4000
    )
    assert comp.selected_rerank == 0
    assert comp.selected_norerank == 0
    assert comp.evidence_in_candidates is False
    assert comp.evidence_value_mean is None
