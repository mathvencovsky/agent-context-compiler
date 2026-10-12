"""The POST /v1/context/assemble endpoint (SPEC §6/§8) — M7.

This is NINA's public context-assembly surface. Given a query and 1-20 URLs it
resolves each URL (reusing the M5 resolve path + 24h cache), indexes its current
version (M6), runs the FEAT-002 retrieval/selection core, and returns an
agent-ready context package: selected fragments with full provenance, literal
evidence, per-source status, token accounting and a coverage heuristic (SPEC §8).

Like ``app/api/documents.py`` the handler is a plain ``def`` (NOT ``async``):
FastAPI runs it on a threadpool so the audited SYNCHRONOUS fetch/SQLAlchemy stack
is reused as-is. Every collaborator is an injectable ``Depends`` seam overridable
in tests via ``app.dependency_overrides`` — the resolve seams (session factory,
blobs, robots, resolver, transport, now, settings) PLUS the two provider seams:
``provide_embeddings`` (real OpenAI by default, ``FakeEmbeddingProvider`` in
tests) and ``provide_reranker`` (real Jina by default, ``FakeRerankProvider`` in
tests). No provider is ever reached in tests (CLAUDE.md).

Partial failure (SPEC §8): one URL failing stays a 200 — its failure is recorded
in ``sources[].status`` + ``warnings`` and the surviving URLs still produce a
context. When ALL URLs fail there is nothing to assemble, so the handler raises
:class:`~app.api.errors.AllSourcesFailed` -> 422 (DECISIONS.md 2026-10-13).

Ablation flags (``use_lexical`` / ``use_vector`` / ``use_rerank`` / ``selection``;
SPEC §6) are accepted only when ``settings.env != "production"``. A non-default
value sent in production is rejected with
:class:`~app.api.errors.AblationNotAllowed` -> 400.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.documents import (
    BlobStoreDep,
    NowDep,
    ResolverDep,
    RobotsCheckerDep,
    SessionFactoryDep,
    SettingsDep,
    TransportDep,
)
from app.api.errors import AblationNotAllowed, AllSourcesFailed
from app.api.schemas import (
    AssembleRequest,
    AssembleResponse,
    EvidenceItem,
    FragmentModel,
    FragmentScoresModel,
    SourceStatusModel,
    TokenUsage,
)
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.embeddings.openai import OpenAIEmbeddingProvider
from app.providers.rerank.base import RerankProvider
from app.providers.rerank.jina import JinaRerankProvider
from app.retrieve.assemble import AssembleResult, assemble_context

router = APIRouter()


# --------------------------------------------------------------------------- #
# Provider dependency seams (overridable in tests via app.dependency_overrides)
# --------------------------------------------------------------------------- #
def provide_embeddings() -> EmbeddingProvider:
    """Return the real OpenAI embeddings provider (fake in tests)."""
    return OpenAIEmbeddingProvider()


def provide_reranker() -> RerankProvider:
    """Return the real Jina reranker (fake in tests)."""
    return JinaRerankProvider()


EmbeddingsDep = Annotated[EmbeddingProvider, Depends(provide_embeddings)]
RerankerDep = Annotated[RerankProvider, Depends(provide_reranker)]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _reject_ablations_in_production(req: AssembleRequest) -> None:
    """Reject ablation flags set away from the full pipeline in production.

    The ablation flags exist for benchmarking (SPEC §6); outside production they
    are honored, in production a non-default value is a bad request.
    """
    changed: list[str] = []
    if not req.use_lexical:
        changed.append("use_lexical")
    if not req.use_vector:
        changed.append("use_vector")
    if not req.use_rerank:
        changed.append("use_rerank")
    if req.selection != "greedy":
        changed.append("selection")
    if changed:
        raise AblationNotAllowed(
            "ablation flags are not accepted in production",
            details={"flags": changed},
        )


def _to_response(result: AssembleResult) -> AssembleResponse:
    """Map the orchestrator's :class:`AssembleResult` to the SPEC §8 envelope."""
    fragments = [
        FragmentModel(
            chunk_id=frag.chunk_id,
            document_id=frag.document_id,
            version_id=frag.version_id,
            source_url=frag.source_url,
            heading_path=list(frag.heading_path),
            md=frag.md,
            tokens=frag.tokens,
            scores=FragmentScoresModel(
                lexical_rank=frag.scores.lexical_rank,
                vector_rank=frag.scores.vector_rank,
                rrf=frag.scores.rrf,
                rerank=frag.scores.rerank,
            ),
            evidence=[
                EvidenceItem(block_id=ev.block_id, page=ev.page, quote=ev.quote)
                for ev in frag.evidence
            ],
        )
        for frag in result.fragments
    ]
    sources = [
        SourceStatusModel(
            url=src.url,
            document_id=src.document_id,
            version_id=src.version_id,
            fetched_at=src.fetched_at,
            status=src.status,
        )
        for src in result.sources
    ]
    return AssembleResponse(
        query=result.query,
        fragments=fragments,
        sources=sources,
        token_usage=TokenUsage(
            output_tokens=result.output_tokens,
            budget_tokens=result.budget_tokens,
        ),
        coverage=result.coverage,  # type: ignore[arg-type]
        warnings=result.warnings,
    )


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@router.post("/v1/context/assemble", response_model=AssembleResponse)
def assemble(
    req: AssembleRequest,
    session_factory: SessionFactoryDep,
    blobs: BlobStoreDep,
    robots: RobotsCheckerDep,
    resolver: ResolverDep,
    transport: TransportDep,
    embeddings: EmbeddingsDep,
    reranker: RerankerDep,
    now: NowDep,
    settings: SettingsDep,
) -> AssembleResponse:
    """Assemble an agent-ready context package for a query over 1-20 URLs."""
    if settings.env == "production":
        _reject_ablations_in_production(req)

    result = assemble_context(
        req,
        session_factory=session_factory,
        blobs=blobs,
        robots=robots,
        resolver=resolver,
        transport=transport,
        embeddings=embeddings,
        reranker=reranker,
        settings=settings,
        now=now,
    )

    # All URLs failed to resolve: there is nothing to assemble (SPEC §8).
    if result.sources and all(src.status != "ok" for src in result.sources):
        raise AllSourcesFailed(
            "every source URL failed to resolve",
            details={
                "sources": [
                    {"url": src.url, "status": src.status} for src in result.sources
                ]
            },
        )

    return _to_response(result)
