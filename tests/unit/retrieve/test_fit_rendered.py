"""Compact + concise: max_tokens covers the whole render (app/api/assemble.py)."""

from __future__ import annotations

from app.api.assemble import _fit_rendered
from app.index.tokens import count_tokens
from app.retrieve.assemble import (
    AssembleResult,
    Fragment,
    FragmentScores,
    SourceStatus,
)
from app.retrieve.compact import compact_result, pool_budget
from app.retrieve.render import render_concise

FILLER = " ".join(f"Unrelated sentence {i} talks about gardening." for i in range(40))
QUERY = "Which header carries the key?"


def _fragment(n: int, doc: str) -> Fragment:
    md = f"## Keys\n\n{FILLER} Send the key in the X-Api-Key header. {FILLER}"
    return Fragment(
        chunk_id=f"chunk_{n}",
        document_id=f"doc_{doc}",
        version_id=f"ver_{doc}",
        source_url=f"https://docs.example.com/{doc}",
        heading_path=["Auth", "Keys"],
        md=md,
        tokens=count_tokens(md),
        scores=FragmentScores(lexical_rank=1, vector_rank=1, rrf=0.03, rerank=0.9),
        evidence=[],
    )


def test_compact_render_fits_the_budget_on_several_documents() -> None:
    max_tokens = 340
    fragments = [_fragment(i, d) for i, d in enumerate("abc")]
    result = AssembleResult(
        query=QUERY,
        fragments=fragments,
        sources=[
            SourceStatus(f.source_url, f.document_id, f.version_id, "t", "ok")
            for f in fragments
        ],
        budget_tokens=pool_budget(max_tokens),
    )
    pooled = (result.fragments, result.budget_limited)
    compact_result(result, query=QUERY, budget=max_tokens)
    assert count_tokens(render_concise(result)) > max_tokens  # the wrapper overshoots

    _fit_rendered(result, pooled, query=QUERY, max_tokens=max_tokens)
    rendered = render_concise(result)
    assert result.fragments
    assert count_tokens(rendered) <= max_tokens
    assert result.output_tokens == count_tokens(rendered)
    assert f"/{max_tokens} tokens" in rendered
