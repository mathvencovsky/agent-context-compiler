"""M10b retrieval experiments on the development slice, at zero API cost.

Research round 4 (fronts H and I) proposes four retrieval changes, all built
into ``app/`` behind settings that are OFF by default: BM25F with heading
fields, identifier tokens and an exact-symbol bonus, a page prior, and local
open models for rerank and embeddings. This script measures them on the keyed
dev tasks (``bench/results/m10a/qrels.jsonl``), with the half A/B discipline:
iterate on half A, confirm ONCE on half B (the look is logged in
``bench/results/m10b/looks.jsonl`` and a second look is refused).

Paid APIs are never called. OpenAI embeddings and Jina rerank are read from the
disk cache only (``bench/.cache``): a miss stops the run with "paid calls: 0
(refused)". Local models are called on the human's machine through the local
server (``--embeddings-url`` / ``--reranker-url``, loopback only) and cached in
``bench/.cache/local``. Lexical diagnostics need no provider at all
(``--vector none``).

Acceptance rules, written BEFORE any measurement (DECISIONS 2026-10-29). "Pool"
is the fused top-30 the reranker sees; "chunk" counts tasks where one pool
chunk holds >= 0.5 of the answer section, "set" where the pool together does
(``bench.m10a_pool``). n is the half's task count.

* **L1, BM25F** (``lexical``): the best of ``bm25f_t1/t3/t5`` (most chunk
  hits; ties: more set hits, then the lower weight) passes half A when chunk >=
  max(91, current + 4) and set >= max(105, current set); the absolute 91/105
  (research H, measured with 139 tasks before the M10b extraction) apply only
  when n == 139. Otherwise L1 stops and the base stays ``current``.
* **L3a, identifier tokens**, then **L3b, symbol bonus 5** (only on a base with
  identifier tokens), then **L4, coverage factor** (only on a BM25F base): each
  candidate (base + the change) passes half A when chunk >= base + 4, set >=
  base set, and on the tasks whose question names no code symbol chunk hits
  do not fall. A passing candidate becomes the base for the next step.
* **L2, page prior** (``pages --base V``): gate first — with the product query
  (first 16 or 32 words, the better on A), the answer's page must rank 1st among
  the task's pages in more than 40% of the tasks, or L2 stops. Then the best of
  rrf w in {0.25, 0.5} and multiply alpha in {0.5, 1} passes when chunk >= base
  + 4 and set >= base set. (The question-title query is reported, never used.)
* **Half B for L1-L4 and L2** (``--half B --confirm V``): full pipeline, both
  arms reranked by the same reranker; V passes if the paired 95% bootstrap CI
  of the section hit rate at 4k is above zero, no budget (1k/2k/4k/8k) falls
  more than 2 pts, NINA's coverage per 1k tokens stays above strong_rag's at
  every budget, and the median lexical+fusion+rerank time per task rises at
  most 50% (DECISIONS 2026-10-16, 2026-10-17).
* **R1, reranker swap** (``rerank``): same pool (current pipeline), Jina v2
  from the cache vs the local reranker. Half A: local hit at 4k >= Jina's and
  no budget below Jina by more than 2 pts. Half B: the paired 95% CI of
  (local - Jina) at 4k has a lower bound above -0.02 and no budget's estimate
  is below -0.02 (non-inferiority: the swap removes a per-call cost and a
  non-commercial license). A pass means coverage_low/high are recalibrated
  (``coverage``) before any default changes.
* **E1, embedding swap** (``embed``): current lexical, vector list from the
  local model vs OpenAI from the cache. Half A: local chunk >= OpenAI + 4 and
  set >= OpenAI set. Half B: paired 95% CI of the pool chunk-hit difference
  above zero and set not lower. Adoption in the product also needs the vector
  column migration (DECISIONS 2026-10-29).

Lexical-only diagnostics (no acceptance role): Recall@10, Recall@50 and MRR of
the first chunk holding >= 0.5 of the section in the lexical list alone, over
the tasks where one chunk can hold it.

Usage:
    uv run python -m bench.m10b_retrieval lexical --half A [--vector cache|local|none]
    uv run python -m bench.m10b_retrieval pages --half A --base bm25f_t3
    uv run python -m bench.m10b_retrieval lexical --half B --confirm bm25f_t3 \
        --reranker local --reranker-url http://127.0.0.1:8081 --reranker-model M
    uv run python -m bench.m10b_retrieval rerank --half A --reranker-url ... \
        --reranker-model Qwen3-Reranker-0.6B-Q8_0
    uv run python -m bench.m10b_retrieval embed --half A --embeddings-url ... \
        --embeddings-model Qwen3-Embedding-0.6B-Q8_0 --embeddings-dim 1024 \
        --query-prefix "Instruct: ...\\nQuery:"
    uv run python -m bench.m10b_retrieval coverage --half A --reranker-url ...
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.retrieve.lexical import LexicalConfig, TokenCache, query_symbols
from app.retrieve.page_prior import page_ranks, page_scores

#: Budgets of the full-pipeline comparisons; 4k is the target budget.
BUDGETS = (1_000, 2_000, 4_000, 8_000)
TARGET_BUDGET = 4_000
#: The fused prefix the reranker sees (``app.retrieve.rerank``).
POOL = 30
#: Research H thresholds, valid for the 139-task half A they were measured on.
L1_ABS_CHUNK = 91
L1_ABS_SET = 105
L1_ABS_N = 139
#: Minimum chunk-hit gain over the base for any lexical/page/embedding step.
MIN_GAIN = 4
#: Page-prior gate: answer page ranked 1st in more than this share of tasks.
PAGE_GATE = 0.40
PAGE_QUERY_WORDS = (16, 32)
PAGE_VARIANTS = ("rrf_w0.25", "rrf_w0.5", "mul_a0.5", "mul_a1")
#: Half B, general rule: max drop at any budget; latency ceiling.
MAX_DROP = 0.02
MAX_LATENCY_RATIO = 1.5
#: R1 non-inferiority margin on half B.
NONINFERIORITY = -0.02
#: R1 fallback (no Jina cache): minimum gain over no rerank at 4k on half A.
RERANK_MIN_GAIN_VS_NONE = 0.02
TITLE_WEIGHTS = (1, 3, 5)
_OUT = Path("bench/results/m10b")
_LOOKS = _OUT / "looks.jsonl"
_LOCAL_CACHE = Path("bench/.cache/local")


# --------------------------------------------------------------------------- #
# Variants
# --------------------------------------------------------------------------- #
def lexical_variants() -> list[str]:
    """Every lexical variant measured on half A, fixed before measurement."""
    names = ["current", "bm25", "ident"]
    for t in TITLE_WEIGHTS:
        base = f"bm25f_t{t}"
        names += [
            base,
            f"{base}_ident",
            f"{base}_ident_sym5",
            f"{base}_cov",
            f"{base}_ident_cov",
            f"{base}_ident_sym5_cov",
        ]
    return names


def variant_config(name: str) -> LexicalConfig:
    """The :class:`LexicalConfig` a lexical variant name stands for.

    ``bm25`` (textbook BM25 from ``bench.m10a_pool``) is a reference ranker, not
    a config; it maps to the default here and is handled by the caller.
    """
    if name in ("current", "bm25"):
        return LexicalConfig()
    if name == "ident":
        return LexicalConfig(identifiers=True)
    parts = name.split("_")
    if parts[0] != "bm25f" or not parts[1].startswith("t"):
        raise ValueError(f"unknown lexical variant {name!r}")
    flags = set(parts[2:])
    if unknown := flags - {"ident", "sym5", "cov"}:
        raise ValueError(f"unknown flags {sorted(unknown)} in {name!r}")
    return LexicalConfig(
        scorer="bm25f",
        title_weight=float(parts[1][1:]),
        identifiers="ident" in flags,
        symbol_bonus=5.0 if "sym5" in flags else 0.0,
        coverage="cov" in flags,
    )


def with_page_prior(config: LexicalConfig, variant: str, words: int) -> LexicalConfig:
    """``config`` plus one page-prior variant (``rrf_w0.5``, ``mul_a1``...)."""
    from dataclasses import replace

    kind, _, value = variant.partition("_")
    number = float(value[1:])
    if kind == "rrf":
        return replace(
            config,
            page_prior="rrf",
            page_prior_weight=number,
            page_prior_query_words=words,
        )
    if kind == "mul":
        return replace(
            config,
            page_prior="multiply",
            page_prior_alpha=number,
            page_prior_query_words=words,
        )
    raise ValueError(f"unknown page variant {variant!r}")


# --------------------------------------------------------------------------- #
# Metrics and rules (pure)
# --------------------------------------------------------------------------- #
@dataclass
class PoolTally:
    """Per-variant pool hits, one entry per task, in task order."""

    chunk: list[bool] = field(default_factory=list)
    set: list[bool] = field(default_factory=list)

    def add(self, chunk: bool, pool_set: bool) -> None:
        self.chunk.append(chunk)
        self.set.append(pool_set)

    @property
    def n_chunk(self) -> int:
        return sum(self.chunk)

    @property
    def n_set(self) -> int:
        return sum(self.set)


def first_hit_rank(ranked: Sequence[Any], hits: set[Any]) -> int | None:
    """1-based position of the first ranked key in ``hits``, or ``None``."""
    for pos, key in enumerate(ranked, start=1):
        if key in hits:
            return pos
    return None


def rank_diagnostics(ranks: Sequence[int | None]) -> dict[str, float]:
    """Recall@10, Recall@50 and MRR over tasks (``None`` = not in the list)."""
    n = len(ranks)
    if not n:
        return {"n": 0, "recall@10": 0.0, "recall@50": 0.0, "mrr": 0.0}
    return {
        "n": n,
        "recall@10": sum(1 for r in ranks if r is not None and r <= 10) / n,
        "recall@50": sum(1 for r in ranks if r is not None and r <= 50) / n,
        "mrr": sum(1 / r for r in ranks if r is not None) / n,
    }


def step_passes(
    cand: PoolTally,
    base: PoolTally,
    *,
    min_chunk: int,
    min_set: int,
    plain: Sequence[bool] | None = None,
) -> bool:
    """A half-A step: enough chunk and set hits, no loss on symbol-free tasks.

    ``plain`` marks the tasks whose question names no code symbol; when given,
    the candidate's chunk hits on them must not fall below the base's.
    """
    if cand.n_chunk < min_chunk or cand.n_set < min_set:
        return False
    if plain is not None:
        mine = sum(h for h, p in zip(cand.chunk, plain, strict=True) if p)
        theirs = sum(h for h, p in zip(base.chunk, plain, strict=True) if p)
        if mine < theirs:
            return False
    return True


def l1_thresholds(current: PoolTally) -> tuple[int, int]:
    """(chunk, set) thresholds of L1 on half A."""
    n = len(current.chunk)
    abs_chunk = L1_ABS_CHUNK if n == L1_ABS_N else 0
    abs_set = L1_ABS_SET if n == L1_ABS_N else 0
    return max(abs_chunk, current.n_chunk + MIN_GAIN), max(abs_set, current.n_set)


def best_title_weight(tallies: Mapping[str, PoolTally]) -> str:
    """The best ``bm25f_t*`` variant: chunk hits, then set hits, then lower t."""
    names = [f"bm25f_t{t}" for t in TITLE_WEIGHTS]
    return max(names, key=lambda n: (tallies[n].n_chunk, tallies[n].n_set, -int(n[7:])))


def decide_lexical(
    tallies: Mapping[str, PoolTally], plain: Sequence[bool]
) -> tuple[str, list[str]]:
    """Run the L1 -> L3a -> L3b -> L4 chain on half A; (chosen variant, log)."""
    log: list[str] = []
    current = tallies["current"]
    min_chunk, min_set = l1_thresholds(current)
    best = best_title_weight(tallies)
    if step_passes(tallies[best], current, min_chunk=min_chunk, min_set=min_set):
        base = best
        log.append(
            f"L1 PASS: {best} (chunk {tallies[best].n_chunk} >= {min_chunk}, "
            f"set {tallies[best].n_set} >= {min_set})"
        )
    else:
        base = "current"
        log.append(
            f"L1 FAIL: best {best} chunk {tallies[best].n_chunk}/"
            f"set {tallies[best].n_set}, needs {min_chunk}/{min_set}"
        )

    def step(label: str, cand: str) -> None:
        nonlocal base
        b = tallies[base]
        ok = step_passes(
            tallies[cand],
            b,
            min_chunk=b.n_chunk + MIN_GAIN,
            min_set=b.n_set,
            plain=plain,
        )
        log.append(
            f"{label} {'PASS' if ok else 'FAIL'}: {cand} chunk "
            f"{tallies[cand].n_chunk} vs {b.n_chunk}+{MIN_GAIN}, set "
            f"{tallies[cand].n_set} vs {b.n_set}"
        )
        if ok:
            base = cand

    step("L3a", "ident" if base == "current" else f"{base}_ident")
    if base.startswith("bm25f") and "_ident" in base:
        step("L3b", f"{base}_sym5")
    else:
        log.append("L3b SKIP: needs a BM25F base with identifier tokens")
    if base.startswith("bm25f"):
        step("L4", f"{base}_cov")
    else:
        log.append("L4 SKIP: needs a BM25F base")
    return base, log


def page_gate(first_ranks: Sequence[bool]) -> tuple[bool, float]:
    """(passes, share) of tasks whose answer page ranks 1st."""
    share = sum(first_ranks) / len(first_ranks) if first_ranks else 0.0
    return share > PAGE_GATE, share


def confirm_general(
    cand: Mapping[int, Sequence[bool]],
    base: Mapping[int, Sequence[bool]],
    *,
    cand_per_1k: Mapping[int, float],
    strong_per_1k: Mapping[int, float],
    cand_ms: float,
    base_ms: float,
    paired: Callable[[Sequence[bool], Sequence[bool]], Mapping[str, float]],
) -> tuple[bool, list[str]]:
    """The general half-B rule for a retrieval change (DECISIONS 2026-10-16/17)."""
    lines: list[str] = []
    ok = True
    p = paired(cand[TARGET_BUDGET], base[TARGET_BUDGET])
    target_ok = p["lo"] > 0
    ok &= target_ok
    lines.append(
        f"- {'PASS' if target_ok else 'FAIL'} hit at {TARGET_BUDGET}: "
        f"{p['diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}]"
    )
    for budget in BUDGETS:
        diff = _rate(cand[budget]) - _rate(base[budget])
        drop_ok = diff >= -MAX_DROP
        eff_ok = cand_per_1k[budget] > strong_per_1k[budget]
        ok &= drop_ok and eff_ok
        lines.append(
            f"- {'PASS' if drop_ok else 'FAIL'} {budget}: hit {diff:+.3f} "
            f"(max drop {MAX_DROP}); {'PASS' if eff_ok else 'FAIL'} "
            f"coverage/1k {cand_per_1k[budget]:.3f} vs strong_rag "
            f"{strong_per_1k[budget]:.3f}"
        )
    lat_ok = base_ms <= 0 or cand_ms <= MAX_LATENCY_RATIO * base_ms
    ok &= lat_ok
    lines.append(
        f"- {'PASS' if lat_ok else 'FAIL'} median ms {cand_ms:,.0f} vs "
        f"{base_ms:,.0f} (max x{MAX_LATENCY_RATIO})"
    )
    return ok, lines


def confirm_noninferior(
    cand: Mapping[int, Sequence[bool]],
    base: Mapping[int, Sequence[bool]],
    *,
    paired: Callable[[Sequence[bool], Sequence[bool]], Mapping[str, float]],
) -> tuple[bool, list[str]]:
    """R1 on half B: CI lower bound at 4k and every estimate above the margin."""
    p = paired(cand[TARGET_BUDGET], base[TARGET_BUDGET])
    ok = p["lo"] > NONINFERIORITY
    lines = [
        f"- {'PASS' if ok else 'FAIL'} hit at {TARGET_BUDGET}: {p['diff']:+.3f} "
        f"[{p['lo']:+.3f}, {p['hi']:+.3f}] (lower bound > {NONINFERIORITY})"
    ]
    for budget in BUDGETS:
        diff = _rate(cand[budget]) - _rate(base[budget])
        b_ok = diff > NONINFERIORITY
        ok &= b_ok
        lines.append(f"- {'PASS' if b_ok else 'FAIL'} {budget}: {diff:+.3f}")
    return ok, lines


def rerank_rule(
    local: Mapping[int, Sequence[bool]],
    other: Mapping[int, Sequence[bool]],
    *,
    half: str,
    comparator: str,
    paired: Callable[[Sequence[bool], Sequence[bool]], Mapping[str, float]],
) -> tuple[bool, list[str]]:
    """R1: the local reranker against Jina (cache) or, as fallback, no rerank.

    Against Jina: half A needs hit at 4k >= Jina's and no budget more than 2 pts
    below; half B is non-inferiority (:func:`confirm_noninferior`). Against no
    rerank (the Jina cache cannot cover the pools): half A needs hit at 4k at
    least 2 pts above and no budget more than 2 pts below; half B needs the
    paired 95% CI at 4k above zero and no budget more than 2 pts below.
    """
    if half == "B" and comparator == "jina":
        return confirm_noninferior(local, other, paired=paired)
    lines: list[str] = []
    drops_ok = True
    for budget in BUDGETS:
        diff = _rate(local[budget]) - _rate(other[budget])
        b_ok = diff >= -MAX_DROP
        drops_ok &= b_ok
        lines.append(
            f"- {'PASS' if b_ok else 'FAIL'} {budget}: {diff:+.3f} "
            f"vs {comparator} (max drop {MAX_DROP})"
        )
    gain = _rate(local[TARGET_BUDGET]) - _rate(other[TARGET_BUDGET])
    if half == "A":
        need = 0.0 if comparator == "jina" else RERANK_MIN_GAIN_VS_NONE
        target_ok = gain >= need
        lines.append(
            f"- {'PASS' if target_ok else 'FAIL'} hit at {TARGET_BUDGET}: "
            f"{gain:+.3f} (needs >= {need:+.3f})"
        )
    else:
        p = paired(local[TARGET_BUDGET], other[TARGET_BUDGET])
        target_ok = p["lo"] > 0
        lines.append(
            f"- {'PASS' if target_ok else 'FAIL'} hit at {TARGET_BUDGET}: "
            f"{p['diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}] (CI > 0)"
        )
    return drops_ok and target_ok, lines


def embed_rule(
    local: PoolTally,
    openai: PoolTally,
    *,
    half: str,
    paired: Callable[[Sequence[bool], Sequence[bool]], Mapping[str, float]],
) -> tuple[bool, list[str]]:
    """E1: half A chunk >= OpenAI + 4 and set not lower; half B paired CI > 0."""
    if half == "A":
        ok = step_passes(
            local, openai, min_chunk=openai.n_chunk + MIN_GAIN, min_set=openai.n_set
        )
        return ok, [
            f"- chunk {local.n_chunk} vs {openai.n_chunk}+{MIN_GAIN}, "
            f"set {local.n_set} vs {openai.n_set}"
        ]
    p = paired(local.chunk, openai.chunk)
    ok = p["lo"] > 0 and local.n_set >= openai.n_set
    return ok, [
        f"- chunk {p['diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}] (CI > 0);"
        f" set {local.n_set} vs {openai.n_set}"
    ]


def _rate(hits: Sequence[bool]) -> float:
    return sum(hits) / len(hits) if hits else 0.0


def check_new_look(name: str, path: Path = _LOOKS) -> None:
    """Refuse a second half-B look at ``name`` (one look per change)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and json.loads(line).get("look") == name:
            raise SystemExit(
                f"half B was already looked at for {name!r} ({path}); "
                "a second look is not allowed (PREREG Amendment 6 discipline)"
            )


def record_look(name: str, verdict: bool, path: Path = _LOOKS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"look": name, "accepted": verdict}) + "\n")


def pool_table(tallies: Mapping[str, PoolTally], n: int) -> list[str]:
    lines = ["| variant | one chunk >= 0.5 | pool together >= 0.5 |", "|---|---|---|"]
    for name, t in tallies.items():
        lines.append(
            f"| {name} | {t.n_chunk} ({t.n_chunk / n:.3f}) "
            f"| {t.n_set} ({t.n_set / n:.3f}) |"
        )
    return lines


# --------------------------------------------------------------------------- #
# Providers: cache-only for paid APIs, local servers for open models
# --------------------------------------------------------------------------- #
class PaidCallRefused(RuntimeError):
    """A cache miss that would have called a paid API."""


class _RefusingEmbeddings:
    def __init__(self, model: str, dim: int) -> None:
        self.model = model
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise PaidCallRefused(
            f"{len(texts)} text(s) missing from the {self.model} embeddings cache"
        )


class _RefusingRerank:
    def __init__(self, model: str) -> None:
        self.model = model

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        raise PaidCallRefused(f"a pool missing from the {self.model} rerank cache")


def cached_openai() -> Any:
    from app.config import Settings
    from bench.provider_cache import CachingEmbeddingProvider

    model = str(Settings.model_fields["embedding_model"].default)
    dim = int(Settings.model_fields["embedding_dim"].default)
    return CachingEmbeddingProvider(_RefusingEmbeddings(model, dim))


def cached_jina() -> Any:
    from app.config import Settings
    from bench.provider_cache import CachingRerankProvider

    model = str(Settings.model_fields["reranker_model"].default)
    return CachingRerankProvider(_RefusingRerank(model))


def local_embeddings(args: argparse.Namespace) -> Any:
    from app.providers.embeddings.local import OpenAICompatibleEmbeddingProvider
    from bench.provider_cache import CachingEmbeddingProvider, SqliteStore

    if not (args.embeddings_url and args.embeddings_model and args.embeddings_dim):
        raise SystemExit("--embeddings-url, --embeddings-model and --embeddings-dim")
    provider = CachingEmbeddingProvider(
        OpenAICompatibleEmbeddingProvider(
            base_url=args.embeddings_url,
            model=args.embeddings_model,
            dim=args.embeddings_dim,
            query_prefix=args.query_prefix.replace("\\n", "\n"),
        ),
        store=SqliteStore(_LOCAL_CACHE / "embeddings.sqlite"),
    )
    provider._local = True  # type: ignore[attr-defined]
    return provider


def local_reranker(args: argparse.Namespace) -> Any:
    from app.providers.rerank.local import JinaCompatibleRerankProvider
    from bench.provider_cache import CachingRerankProvider, SqliteStore

    if not (args.reranker_url and args.reranker_model):
        raise SystemExit("--reranker-url and --reranker-model are required")
    provider = CachingRerankProvider(
        JinaCompatibleRerankProvider(
            base_url=args.reranker_url,
            model=args.reranker_model,
            score_transform=args.score_transform,
        ),
        store=SqliteStore(_LOCAL_CACHE / "rerank.sqlite"),
    )
    provider._local = True  # type: ignore[attr-defined]
    return provider


# --------------------------------------------------------------------------- #
# Real-corpus runs (the human's machine)
# --------------------------------------------------------------------------- #
def paid_line(*providers: Any) -> str:
    """The closing line: paid calls are always 0; local-server calls counted."""
    local = sum(
        getattr(p, "api_calls", 0)
        for p in providers
        if p is not None and getattr(p, "_local", False)
    )
    return f"paid calls: 0 (cache-only for OpenAI/Jina); local-server calls: {local}"


class _NoEmbeddings:
    """Indexing without vectors, for lexical-only work (no provider at all)."""

    model = "none"
    dim = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


@dataclass
class _Ctx:  # pragma: no cover - real corpus
    keys: list[dict[str, Any]]
    tasks: dict[str, Any]
    store: Any


@dataclass
class _TaskData:  # pragma: no cover - real corpus
    task: Any
    title: str
    indexed: list[Any]
    urls: dict[str, str]
    md_by_key: dict[Any, str]
    units: list[str]
    answer_doc: str


def _context(half: str) -> _Ctx:  # pragma: no cover - real corpus
    from bench.anchor_qrels import in_tune_half
    from bench.corpus_store import CorpusStore
    from bench.snapshot import DEFAULT_CORPUS_DIR
    from bench.tasks_schema import load_external_tasks

    qrels = Path("bench/results/m10a/qrels.jsonl")
    if not qrels.exists():
        raise SystemExit(f"{qrels} is missing: run `python -m bench.anchor_qrels`")
    keys = [
        k
        for k in map(json.loads, qrels.read_text(encoding="utf-8").splitlines())
        if in_tune_half(k["task_id"]) == (half == "A")
    ]
    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    return _Ctx(keys, tasks, CorpusStore(DEFAULT_CORPUS_DIR))


def _task_data(ctx: _Ctx, key: dict[str, Any]) -> _TaskData:  # pragma: no cover
    """Extract and chunk a task's pages once, without any embedding call."""
    from bench.m10a_baseline import bench_task
    from bench.runners.ours import OursRunner, _source_for
    from bench.section_recall import units

    external = ctx.tasks[key["task_id"]]
    task = bench_task(external)
    indexed, urls = OursRunner(embeddings=_NoEmbeddings())._index_task(task, ctx.store)
    answer_doc = _source_for(key["url"], ctx.store.content_type(key["url"]))[1]
    return _TaskData(
        task=task,
        title=external.question_title,
        indexed=indexed,
        urls=urls,
        md_by_key={item.key: item.chunk.md for item in indexed},
        units=units(key["section"]),
        answer_doc=answer_doc,
    )


def _vector(data: _TaskData, embeddings: Any) -> list[Any]:  # pragma: no cover
    """Top-50 keys by cosine to the query (as ``vector_search``); [] without."""
    from app.providers.embeddings.base import embed_query
    from app.retrieve.select import cosine
    from bench.runners.ours import _SEARCH_TOP_K

    if embeddings is None or not data.indexed:
        return []
    vectors = embeddings.embed([item.chunk.text for item in data.indexed])
    query = embed_query(embeddings, data.task.question)
    scored = [
        (cosine(query, vec), item.key)
        for item, vec in zip(data.indexed, vectors, strict=True)
    ]
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [key for _, key in scored[:_SEARCH_TOP_K]]


def _fused(lexical: list[Any], vector: list[Any]) -> list[Any]:  # pragma: no cover
    from app.retrieve.rrf import rrf_fuse
    from bench.runners.ours import _RRF_K

    return [k for k, _ in rrf_fuse([lexical, vector], k=_RRF_K, weights=[1.0, 1.0])]


def _vector_provider(args: argparse.Namespace) -> Any:  # pragma: no cover
    if args.vector == "cache":
        return cached_openai()
    if args.vector == "local":
        return local_embeddings(args)
    return None


def _reranker_for(args: argparse.Namespace) -> Any:  # pragma: no cover
    return local_reranker(args) if args.reranker == "local" else cached_jina()


def _write(name: str, lines: Sequence[str]) -> None:  # pragma: no cover
    _OUT.mkdir(parents=True, exist_ok=True)
    report = "\n".join(lines) + "\n"
    (_OUT / f"{name}.md").write_text(report, encoding="utf-8")
    print(report)


def run_lexical(args: argparse.Namespace) -> None:  # pragma: no cover - real corpus
    from bench.m10a_pool import bm25_ranks, pool_hits
    from bench.runners.ours import lexical_result
    from bench.section_recall import HIT, coverage

    if args.half == "B":
        _confirm(
            args,
            look=f"lexical:{args.confirm}",
            base=LexicalConfig(),
            cand=variant_config(_need_confirm(args)),
        )
        return
    ctx = _context(args.half)
    embeddings = _vector_provider(args)
    names = lexical_variants()
    tallies = {n: PoolTally() for n in names}
    ranks: dict[str, list[int | None]] = {n: [] for n in names}
    plain: list[bool] = []
    for i, key in enumerate(ctx.keys, 1):
        data = _task_data(ctx, key)
        vector = _vector(data, embeddings)
        question = data.task.question
        plain.append(not query_symbols(question))
        cache: TokenCache = {}
        answer = {
            k for k, md in data.md_by_key.items() if coverage(data.units, md) >= HIT
        }
        for name in names:
            if name == "bm25":
                lex = bm25_ranks(data.indexed, question)
            else:
                lex = lexical_result(
                    data.indexed, question, variant_config(name), data.urls, cache
                ).ranks
            tallies[name].add(
                *pool_hits(_fused(lex, vector), data.md_by_key, data.units)
            )
            if answer:
                ranks[name].append(first_hit_rank(lex, answer))
        if i % 25 == 0:
            print(f"ranked {i}/{len(ctx.keys)}", flush=True)
    if args.vector == "none":
        chosen, log = (
            "current",
            [
                "no decision: --vector none is diagnostic only "
                "(the rules read the hybrid pool)"
            ],
        )
    else:
        chosen, log = decide_lexical(tallies, plain)
    n = len(ctx.keys)
    lines = [f"# M10b lexical variants, half A ({n} tasks, vector: {args.vector})", ""]
    lines += pool_table(tallies, n)
    lines += [
        "",
        "## Lexical list alone (diagnostic)",
        "",
        "| variant | n | Recall@10 | Recall@50 | MRR |",
        "|---|---|---|---|---|",
    ]
    for name in names:
        d = rank_diagnostics(ranks[name])
        lines.append(
            f"| {name} | {d['n']:.0f} | {d['recall@10']:.3f} | "
            f"{d['recall@50']:.3f} | {d['mrr']:.3f} |"
        )
    lines += ["", "## Decision (DECISIONS 2026-10-29)", "", *[f"- {x}" for x in log]]
    lines += ["", f"**Chosen: {chosen}**"]
    if chosen != "current":
        lines.append(
            f"Next: `pages --half A --base {chosen}`, then "
            f"`lexical --half B --confirm {chosen}` (one look)."
        )
    _write(f"lexical_{args.vector}_A", lines)
    print(paid_line(embeddings))


def run_pages(args: argparse.Namespace) -> None:  # pragma: no cover - real corpus
    from app.retrieve.page_prior import apply_page_rrf
    from bench.m10a_pool import pool_hits
    from bench.runners.ours import _RRF_K, lexical_items, lexical_result

    base_cfg = variant_config(args.base)
    if args.half == "B":
        variant, _, words = _need_confirm(args).partition(":")
        _confirm(
            args,
            look=f"pages:{args.base}:{args.confirm}",
            base=base_cfg,
            cand=with_page_prior(base_cfg, variant, int(words or 32)),
        )
        return
    ctx = _context(args.half)
    embeddings = _vector_provider(args)
    labels = ["title", *[f"first{w}" for w in PAGE_QUERY_WORDS]]
    first: dict[str, list[bool]] = {label: [] for label in labels}
    per_task: list[tuple[_TaskData, list[Any]]] = []
    for key in ctx.keys:
        data = _task_data(ctx, key)
        items = lexical_items(data.indexed)
        queries = [("title", data.title, 10_000)]
        queries += [(f"first{w}", data.task.question, w) for w in PAGE_QUERY_WORDS]
        for label, query, words in queries:
            cfg = LexicalConfig(
                page_prior="rrf",
                page_prior_query_words=words,
                identifiers=base_cfg.identifiers,
            )
            ranks = page_ranks(page_scores(items, query, cfg, data.urls))
            first[label].append(ranks.get(data.answer_doc) == 1)
        per_task.append((data, _vector(data, embeddings)))
    best_words = max(PAGE_QUERY_WORDS, key=lambda w: (sum(first[f"first{w}"]), -w))
    ok, share = page_gate(first[f"first{best_words}"])
    n = len(ctx.keys)
    lines = [
        f"# M10b page prior, half A ({n} tasks, base {args.base})",
        "",
        "| page query | answer page ranked 1st |",
        "|---|---|",
    ]
    lines += [
        f"| {label} | {sum(v)} ({sum(v) / n:.3f}) |" for label, v in first.items()
    ]
    lines += [
        "",
        f"Gate (first{best_words}, > {PAGE_GATE:.0%}): "
        f"{'PASS' if ok else 'FAIL'} ({share:.3f})",
    ]
    if ok:
        tallies = {name: PoolTally() for name in ("base", *PAGE_VARIANTS)}
        for data, vector in per_task:
            cache: TokenCache = {}
            for name, tally in tallies.items():
                cfg = (
                    base_cfg
                    if name == "base"
                    else with_page_prior(base_cfg, name, best_words)
                )
                lex = lexical_result(
                    data.indexed, data.task.question, cfg, data.urls, cache
                )
                fused = apply_page_rrf(
                    _scored_fuse(lex.ranks, vector),
                    lex.page_rank,
                    weight=cfg.page_prior_weight,
                    k=_RRF_K,
                )
                keys = [k for k, _ in fused]
                tally.add(*pool_hits(keys, data.md_by_key, data.units))
        lines += ["", *pool_table(tallies, n)]
        best = max(PAGE_VARIANTS, key=lambda v: (tallies[v].n_chunk, tallies[v].n_set))
        b = tallies["base"]
        passed = step_passes(
            tallies[best], b, min_chunk=b.n_chunk + MIN_GAIN, min_set=b.n_set
        )
        lines += [
            "",
            f"**L2 {'PASS' if passed else 'FAIL'}: {best} (first{best_words})"
            f" chunk {tallies[best].n_chunk} vs {b.n_chunk}+{MIN_GAIN}, "
            f"set {tallies[best].n_set} vs {b.n_set}**",
        ]
        if passed:
            lines.append(
                f"Next: `pages --half B --base {args.base} --confirm "
                f"{best}:{best_words}` (one look)."
            )
    _write(f"pages_{args.base}_A", lines)
    print(paid_line(embeddings))


def _scored_fuse(
    lexical: list[Any], vector: list[Any]
) -> list[Any]:  # pragma: no cover
    from app.retrieve.rrf import rrf_fuse
    from bench.runners.ours import _RRF_K

    return rrf_fuse([lexical, vector], k=_RRF_K, weights=[1.0, 1.0])


def _need_confirm(args: argparse.Namespace) -> str:
    if not args.confirm:
        raise SystemExit("half B needs --confirm VARIANT (one look per change)")
    return str(args.confirm)


def _settings_runner(**kwargs: Any) -> Any:  # pragma: no cover
    from app.config import get_settings
    from bench.runners.ours import OursRunner

    settings = get_settings()
    return OursRunner(
        lambda_=settings.selection_lambda,
        min_value=settings.min_value,
        lexical_weight=settings.lexical_weight,
        **kwargs,
    )


def _arm(embeddings: Any, **kwargs: Any) -> Any:  # pragma: no cover
    """An ours runner with the frozen selection settings and a vector source."""
    if embeddings is None:
        return _settings_runner(use_vector=False, embeddings=_NoEmbeddings(), **kwargs)
    return _settings_runner(embeddings=embeddings, **kwargs)


def _full_pipeline(
    ctx: _Ctx, arms: Mapping[str, Any], strong: Any | None
) -> tuple[
    dict[str, dict[int, list[bool]]], dict[str, dict[int, float]], dict[str, float]
]:  # pragma: no cover
    """Section hits per arm and budget, coverage per 1k tokens, median ms."""
    from time import perf_counter

    from app.index.tokens import count_tokens
    from bench.m10a_baseline import bench_task
    from bench.section_recall import HIT, coverage, units

    names = [*arms, *(["strong_rag"] if strong is not None else [])]
    hits: dict[str, dict[int, list[bool]]] = {
        a: {b: [] for b in BUDGETS} for a in names
    }
    cov: dict[str, dict[int, list[float]]] = {
        a: {b: [] for b in BUDGETS} for a in names
    }
    toks: dict[str, dict[int, list[int]]] = {a: {b: [] for b in BUDGETS} for a in names}
    ms: dict[str, list[float]] = {a: [] for a in arms}

    def record(arm: str, budget: int, text: str, su: list[str]) -> None:
        c = coverage(su, text)
        hits[arm][budget].append(c >= HIT)
        cov[arm][budget].append(c)
        toks[arm][budget].append(count_tokens(text))

    for i, key in enumerate(ctx.keys, 1):
        task = bench_task(ctx.tasks[key["task_id"]])
        su = units(key["section"])
        for arm, runner in arms.items():
            start = perf_counter()
            prepared = runner.prepare_candidates(task, store=ctx.store)
            ms[arm].append((perf_counter() - start) * 1000)
            for budget in BUDGETS:
                result = runner.assemble_from_candidates(prepared, budget_tokens=budget)
                record(arm, budget, result.text, su)
        if strong is not None:
            for budget in BUDGETS:
                result = strong.build_context(
                    task, budget_tokens=budget, store=ctx.store
                )
                record("strong_rag", budget, result.text, su)
        if i % 25 == 0:
            print(f"{i}/{len(ctx.keys)} tasks", flush=True)
    per_1k = {
        arm: {b: per_thousand(cov[arm][b], toks[arm][b]) for b in BUDGETS}
        for arm in names
    }
    return hits, per_1k, {a: statistics.median(v) for a, v in ms.items() if v}


def per_thousand(coverages: Sequence[float], tokens: Sequence[int]) -> float:
    """Mean coverage per 1,000 mean tokens (0 when no tokens)."""
    if not coverages or not tokens or not statistics.mean(tokens):
        return 0.0
    return statistics.mean(coverages) / statistics.mean(tokens) * 1000


def _confirm(
    args: argparse.Namespace, *, look: str, base: LexicalConfig, cand: LexicalConfig
) -> None:  # pragma: no cover - real corpus
    from bench.m10a_diagnose import paired
    from bench.runners.strong_rag import StrongRagRunner

    check_new_look(look)
    ctx = _context("B")
    embeddings = _vector_provider(args)
    reranker = _reranker_for(args)
    arms = {
        "base": _arm(embeddings, reranker=reranker, lexical=base),
        "candidate": _arm(embeddings, reranker=reranker, lexical=cand),
    }
    strong = StrongRagRunner(embeddings=embeddings, reranker=reranker)
    hits, per_1k, ms = _full_pipeline(ctx, arms, strong)
    ok, lines = confirm_general(
        hits["candidate"],
        hits["base"],
        cand_per_1k=per_1k["candidate"],
        strong_per_1k=per_1k["strong_rag"],
        cand_ms=ms["candidate"],
        base_ms=ms["base"],
        paired=paired,
    )
    record_look(look, ok)
    _write(
        f"confirm_{look.replace(':', '_')}_B",
        [
            f"# M10b half B: {look} ({len(ctx.keys)} tasks, vector {args.vector}, "
            f"reranker {args.reranker})",
            "",
            *lines,
            "",
            f"**{'ACCEPTED' if ok else 'REJECTED'}**",
        ],
    )
    print(paid_line(embeddings, reranker))


def run_rerank(args: argparse.Namespace) -> None:  # pragma: no cover - real corpus
    from bench.m10a_diagnose import paired

    if not args.reranker_model:
        raise SystemExit("--reranker-model is required")
    look = f"rerank:{args.reranker_model}:{args.comparator}"
    if args.half == "B":
        check_new_look(look)
    ctx = _context(args.half)
    embeddings = _vector_provider(args)
    local = local_reranker(args)
    arms = {"local": _arm(embeddings, reranker=local)}
    jina = cached_jina() if args.comparator == "jina" else None
    if jina is not None:
        arms["jina"] = _arm(embeddings, reranker=jina)
    arms["none"] = _arm(embeddings, use_rerank=False)
    hits, _, ms = _full_pipeline(ctx, arms, None)
    n = len(ctx.keys)
    lines = [
        f"# M10b reranker swap, half {args.half} ({n} tasks, "
        f"{args.reranker_model} vs {args.comparator}, vector {args.vector})",
        "",
        "| arm | " + " | ".join(str(b) for b in BUDGETS) + " | median ms |",
        "|---|" + "---|" * (len(BUDGETS) + 1),
    ]
    for arm in arms:
        rates = " | ".join(f"{_rate(hits[arm][b]):.3f}" for b in BUDGETS)
        lines.append(f"| {arm} | {rates} | {ms[arm]:,.0f} |")
    ok, rule = rerank_rule(
        hits["local"],
        hits[args.comparator],
        half=args.half,
        comparator=args.comparator,
        paired=paired,
    )
    lines += ["", *rule, "", f"**R1 half {args.half}: {'PASS' if ok else 'FAIL'}**"]
    if args.half == "B":
        record_look(look, ok)
    if ok:
        lines.append(
            "Recalibrate coverage_low/high before any default changes: "
            "`coverage --reranker local`."
        )
    _write(f"rerank_{args.comparator}_{args.half}", lines)
    print(paid_line(embeddings, jina, local))


def run_embed(args: argparse.Namespace) -> None:  # pragma: no cover - real corpus
    from bench.m10a_diagnose import paired
    from bench.m10a_pool import pool_hits
    from bench.runners.ours import _lexical_ranks

    look = f"embed:{args.embeddings_model}"
    if args.half == "B":
        check_new_look(look)
    ctx = _context(args.half)
    openai, local = cached_openai(), local_embeddings(args)
    tallies = {"openai": PoolTally(), "local": PoolTally()}
    for key in ctx.keys:
        data = _task_data(ctx, key)
        lex = _lexical_ranks(data.indexed, data.task.question)
        for name, provider in (("openai", openai), ("local", local)):
            fused = _fused(lex, _vector(data, provider))
            tallies[name].add(*pool_hits(fused, data.md_by_key, data.units))
    n = len(ctx.keys)
    lines = [
        f"# M10b embedding swap, half {args.half} ({n} tasks, {args.embeddings_model})",
        "",
        *pool_table(tallies, n),
        "",
    ]
    ok, rule = embed_rule(
        tallies["local"], tallies["openai"], half=args.half, paired=paired
    )
    lines += [*rule, "", f"**E1 half {args.half}: {'PASS' if ok else 'FAIL'}**"]
    if args.half == "B":
        record_look(look, ok)
    _write(f"embed_{args.half}", lines)
    print(paid_line(openai, local))


def run_coverage(args: argparse.Namespace) -> None:  # pragma: no cover - real corpus
    """p33/p66 of the per-task max absolute rerank score: candidate thresholds."""
    from app.config import Settings
    from bench.calibrate import coverage_from_scores
    from bench.m10a_baseline import bench_task

    ctx = _context(args.half)
    embeddings = _vector_provider(args)
    reranker = _reranker_for(args)
    runner = _arm(embeddings, reranker=reranker)
    maxima: list[float] = []
    for key in ctx.keys:
        task = bench_task(ctx.tasks[key["task_id"]])
        prepared = runner.prepare_candidates(task, store=ctx.store)
        if prepared.max_rerank is not None:
            maxima.append(prepared.max_rerank)
    low, high = coverage_from_scores(maxima)
    cur_low = float(Settings.model_fields["coverage_low"].default)
    cur_high = float(Settings.model_fields["coverage_high"].default)
    model = getattr(reranker, "model", "?")
    _write(
        f"coverage_{args.reranker}_{args.half}",
        [
            f"# Coverage thresholds for {model}, half {args.half}",
            "",
            f"- tasks with a score: {len(maxima)}",
            f"- candidate COVERAGE_LOW = p33 = {low:.4f} (current {cur_low})",
            f"- candidate COVERAGE_HIGH = p66 = {high:.4f} (current {cur_high})",
            f"- shares with the candidates: {coverage_shares(maxima, low, high)}",
            f"- shares with the current: {coverage_shares(maxima, cur_low, cur_high)}",
            "",
            "Plan (DECISIONS 2026-10-29): derive on half A; on half B each class",
            "must hold 23-43% of the tasks with the half-A thresholds; no default",
            "changes before a reranker swap passes R1.",
        ],
    )
    print(paid_line(embeddings, reranker))


def coverage_shares(
    maxima: Sequence[float], low: float, high: float
) -> dict[str, float]:
    """Share of tasks classed insufficient/partial/sufficient by ``low``/``high``."""
    n = len(maxima) or 1
    return {
        "insufficient": round(sum(1 for m in maxima if m < low) / n, 3),
        "partial": round(sum(1 for m in maxima if low <= m < high) / n, 3),
        "sufficient": round(sum(1 for m in maxima if m >= high) / n, 3),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command", choices=["lexical", "pages", "rerank", "embed", "coverage"]
    )
    parser.add_argument("--half", choices=["A", "B"], default="A")
    parser.add_argument("--confirm", help="half B: the variant to confirm (one look)")
    parser.add_argument("--base", default="current", help="pages: lexical base")
    parser.add_argument(
        "--vector",
        choices=["cache", "local", "none"],
        default="cache",
        help="vector list (OpenAI cache, local server or none)",
    )
    parser.add_argument(
        "--reranker",
        choices=["jina", "local"],
        default="local",
        help="half B / coverage: the reranker of every arm",
    )
    parser.add_argument(
        "--comparator",
        choices=["jina", "none"],
        default="jina",
        help="rerank: what the local reranker is compared with",
    )
    parser.add_argument("--reranker-url")
    parser.add_argument("--reranker-model")
    parser.add_argument(
        "--score-transform", choices=["none", "sigmoid"], default="none"
    )
    parser.add_argument("--embeddings-url")
    parser.add_argument("--embeddings-model")
    parser.add_argument("--embeddings-dim", type=int)
    parser.add_argument(
        "--query-prefix", default="", help=r"query instruction; '\n' becomes a newline"
    )
    return parser


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real corpus
    args = _parser().parse_args(argv)
    if args.base not in lexical_variants():
        raise SystemExit(f"--base must be one of {lexical_variants()}")
    commands = {
        "lexical": run_lexical,
        "pages": run_pages,
        "rerank": run_rerank,
        "embed": run_embed,
        "coverage": run_coverage,
    }
    try:
        commands[args.command](args)
    except PaidCallRefused as exc:
        raise SystemExit(
            f"stopped: {exc}. paid calls: 0 (refused). The OpenAI/Jina cache does "
            "not cover this run (chunks changed with the M10b extraction): use "
            "the local servers (--vector local, --reranker local, --comparator "
            "none) or fill the cache with `make m10a-pending`, which costs money."
        ) from exc


if __name__ == "__main__":
    main()
