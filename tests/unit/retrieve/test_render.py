"""Concise rendering of an assemble result (app/retrieve/render.py)."""

from __future__ import annotations

from app.retrieve.assemble import (
    AssembleResult,
    Evidence,
    Fragment,
    FragmentScores,
    SourceStatus,
)
from app.retrieve.render import NOTES_HEADER, UNTRUSTED_LINE, render_concise

_SCORES = FragmentScores(lexical_rank=1, vector_rank=2, rrf=0.03, rerank=0.9)


def _frag(chunk: str, doc: str, url: str, path: list[str], md: str,
          page: int | None = None) -> Fragment:  # fmt: skip
    return Fragment(
        chunk_id=chunk, document_id=doc, version_id="ver_" + doc[-3:],
        source_url=url, heading_path=path, md=md, tokens=10, scores=_SCORES,
        evidence=[Evidence(block_id="b1", page=page, quote=md[:20])],
    )  # fmt: skip


def _result() -> AssembleResult:
    return AssembleResult(
        query="Which header authenticates?",
        fragments=[
            _frag(
                "11",
                "doc_aaa",
                "https://d.example/auth",
                ["Auth", "Keys"],
                "Send the key in the `X-Api-Key` header.",
            ),
            _frag(
                "12",
                "doc_aaa",
                "https://d.example/auth",
                ["Auth", "Rotation"],
                "Rotate keys every 90 days.",
            ),
            _frag(
                "40",
                "doc_bbb",
                "https://d.example/spec.pdf",
                [],
                "Keys are opaque strings.",
                page=4,
            ),
        ],  # fmt: skip
        sources=[
            SourceStatus("https://d.example/auth", "doc_aaa", "ver_aaa", "t", "ok"),
            SourceStatus("https://d.example/spec.pdf", "doc_bbb", "ver_bbb", "t", "ok"),
            SourceStatus("https://d.example/gone", None, None, None, "fetch_failed"),
        ],
        output_tokens=30,
        budget_tokens=4000,
        coverage="partial",
        warnings=[{"code": "fetch_failed", "message": "https://d.example/gone: 502"}],
    )


def test_concise_keeps_every_fragment_verbatim_with_its_provenance() -> None:
    text = render_concise(_result())
    assert text.splitlines()[0] == UNTRUSTED_LINE
    assert "coverage: partial | 30/4000 tokens | sources: 2 ok, 1 failed" in text
    for frag in _result().fragments:
        assert frag.md in text and f"chunk {frag.chunk_id}" in text
    # one header per document, with its url and ids
    assert text.count("## https://d.example/auth (doc_aaa, ver_aaa)") == 1
    assert "## https://d.example/spec.pdf (doc_bbb, ver_bbb)" in text
    assert "### Auth > Keys · chunk 11\n" in text
    assert "### (no heading) · chunk 40 · p.4\n" in text
    notes = text[text.index(NOTES_HEADER) :]
    assert "- source_failed: https://d.example/gone was not used" in notes
    assert "- warning fetch_failed: https://d.example/gone: 502" in text


def test_concise_has_no_scores_quotes_or_query_echo() -> None:
    text = render_concise(_result())
    assert "0.9" not in text and "rerank" not in text
    assert "Which header authenticates?" not in text
    assert render_concise(_result()) == text  # deterministic


def test_empty_result_still_says_what_happened() -> None:
    text = render_concise(AssembleResult(query="q", budget_tokens=4000))
    assert text == (
        UNTRUSTED_LINE + "\ncoverage: insufficient | 0/4000 tokens | "
        "sources: 0 ok, 0 failed\n\n" + NOTES_HEADER + "\n- low_coverage: The "
        "highest relevance found is low: these URLs may not contain the answer. "
        "Options: other URLs or a more specific query.\n"
    )
