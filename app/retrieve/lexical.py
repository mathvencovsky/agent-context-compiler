"""Lexical search (SPEC §6 step 2, §5).

``lexical_search`` loads the chunks of the requested versions and ranks them
with :func:`rank_lexical`. The default scorer is ``overlap_ranks``: each chunk
scores the number of its tokens that are query words (lowercase ``[a-z0-9]+``
tokens), zero-score chunks are dropped, and ties break on the ``(document_id,
ord)`` key — the same key the RRF fusion uses. Any query word counts, so a long
natural-language question still matches (``websearch_to_tsquery`` required
every word; DECISIONS 2026-10-16).

M10b adds opt-in scorers, all OFF by default (``LexicalConfig()`` reproduces
``overlap_ranks`` exactly; DECISIONS 2026-10-29, research round 4 front H):

* ``bm25f``: BM25F over four fields of each chunk — its own heading (``leaf``),
  the headings above it (``ancestors``), prose and code — with IDF computed over
  the chunks of the request, ``k1 = 1.2``, ``b = 0.75`` on the body length, and
  a heading weight (``title_weight``; ancestors get half of it);
* ``identifiers``: identifier-aware tokens — ``os.path.join`` and ``useState``
  also index as the whole identifier and its parts (split before lowercasing);
* ``symbol_bonus``: a query symbol found whole in a chunk counts ``symbol_bonus``
  times in the term frequency, its last dotted part half of that (Zoekt, Sphinx);
* ``coverage``: multiply by ``1 + c²``, ``c`` the IDF share of the distinct
  query terms the chunk holds (the "words" criterion of DocSearch);
* a page prior (:mod:`app.retrieve.page_prior`), as a multiplier here or as a
  third RRF list in the callers.

Everything is shared with the benchmark runner (``bench/runners/ours``), so the
product ranks exactly as the benchmark measures. Results are capped at
``limit`` (top-50) and returned as ``(chunk, rank_position)`` with 1-based
positions. A query with no tokens yields an empty result.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select

from app.store.models import Chunk, DocumentVersion, Source

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.config import Settings

#: SPEC §6 step 2: top-50 by lexical rank.
_DEFAULT_LIMIT = 50

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: (document_id, ord), the fusion key.
Key = tuple[str, int]

Tokenizer = Callable[[str], list[str]]

#: BM25 constants (Lucene defaults; research H, Zoekt ``score.go``).
BM25_K1 = 1.2
BM25_B = 0.75


def tokenize(text: str) -> list[str]:
    """Return lowercase alphanumeric tokens for ``text``."""
    return _TOKEN_RE.findall(text.lower())


def overlap_ranks(
    items: Iterable[tuple[Key, str]], query: str, limit: int = _DEFAULT_LIMIT
) -> list[Key]:
    """Rank ``(key, text)`` items by how many of their tokens are query words."""
    terms = set(tokenize(query))
    scored: list[tuple[int, Key]] = []
    for key, text in items:
        overlap = sum(1 for t in tokenize(text) if t in terms)
        if overlap > 0:
            scored.append((overlap, key))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [key for _, key in scored[:limit]]


# --------------------------------------------------------------------------- #
# Identifier-aware tokenization (research H, L3)
# --------------------------------------------------------------------------- #
_WORD_RE = re.compile(r"\S+")
#: Leading/trailing punctuation around a word: quotes, brackets, ``()``, ``.``.
_EDGE_RE = re.compile(r"^[^\w]+|[^\w]+$")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]+")
#: camelCase / PascalCase / acronym parts: ``HTTPServer`` -> HTTP, Server.
_CASE_PART_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
#: Characters a whole identifier may hold (``os.path.join``, ``std::vec``).
_IDENT_CHARS_RE = re.compile(r"^[A-Za-z0-9_.:/\->]+$")
_IDENT_SEPARATORS = ("_", ".", "::", "->", "-", "/")
#: Query symbols for the exact-symbol bonus: dotted, snake_case or camelCase.
_DOTTED_RE = re.compile(r"^[A-Za-z_]\w*(?:(?:\.|::|->)[A-Za-z_]\w*)+$")
_SNAKE_RE = re.compile(r"^_*[A-Za-z][A-Za-z0-9]*(?:_+[A-Za-z0-9]+)+_*$")
_CAMEL_RE = re.compile(r"^(?:[a-z][a-z0-9]*[A-Z]|[A-Z][a-z0-9]+[A-Z])[A-Za-z0-9]*$")
_SYMBOL_MIN_CHARS = 4
#: Language keywords that look like symbols but say nothing about the page.
_SYMBOL_STOPWORDS = frozenset(
    {
        "import",
        "return",
        "function",
        "const",
        "class",
        "self",
        "this",
        "none",
        "null",
        "true",
        "false",
        "print",
        "string",
        "async",
        "await",
    }  # fmt: skip
)


def _word_core(word: str) -> str:
    """``word`` without wrapping punctuation (`` `os.path.join()`, `` -> name)."""
    return _EDGE_RE.sub("", word)


def identifier_tokenize(text: str) -> list[str]:
    """Tokens for ``text`` that keep identifiers whole AND split into parts.

    For each whitespace-separated word: every alphanumeric run, lowercased (the
    tokens :func:`tokenize` gives); the camelCase parts of a run, split before
    lowercasing (``useState`` -> ``use``, ``state``); and, when the word is an
    identifier (holds ``_ . :: -> - /`` inside or has case parts), the whole
    identifier lowercased (``os.path.join``, ``usestate``). Plain prose yields
    exactly the :func:`tokenize` tokens.
    """
    tokens: list[str] = []
    for word in _WORD_RE.findall(text):
        core = _word_core(word)
        if not core:
            continue
        split_case = False
        for run in _ALNUM_RE.findall(core):
            tokens.append(run.lower())
            parts = _CASE_PART_RE.findall(run)
            if len(parts) > 1:
                split_case = True
                tokens.extend(p.lower() for p in parts)
        has_separator = any(sep in core for sep in _IDENT_SEPARATORS)
        if (has_separator or split_case) and _IDENT_CHARS_RE.match(core):
            whole = core.lower()
            if whole not in (run.lower() for run in _ALNUM_RE.findall(core)):
                tokens.append(whole)
    return tokens


def query_symbols(query: str) -> list[str]:
    """Distinct code symbols named in ``query``, lowercased, in first-seen order.

    A symbol is a dotted (``os.path.join``, ``a::b``), snake_case or camelCase
    word of at least four characters that is not a language keyword.
    """
    seen: dict[str, None] = {}
    for word in _WORD_RE.findall(query):
        core = _word_core(word)
        if len(core) < _SYMBOL_MIN_CHARS or core.lower() in _SYMBOL_STOPWORDS:
            continue
        if _DOTTED_RE.match(core) or _SNAKE_RE.match(core) or _CAMEL_RE.match(core):
            seen.setdefault(core.lower(), None)
    return list(seen)


def _symbol_multipliers(symbols: Sequence[str], bonus: float) -> dict[str, float]:
    """Term-frequency multipliers: whole symbol ``bonus``, last dotted part half."""
    mult: dict[str, float] = {}
    for symbol in symbols:
        mult[symbol] = max(mult.get(symbol, 1.0), bonus)
        last = re.split(r"\.|::|->", symbol)[-1]
        if last != symbol and len(last) >= _SYMBOL_MIN_CHARS:
            mult[last] = max(mult.get(last, 1.0), bonus / 2)
    return mult


# --------------------------------------------------------------------------- #
# Configuration and fields
# --------------------------------------------------------------------------- #
LexicalScorer = Literal["overlap", "bm25f"]
PagePrior = Literal["off", "rrf", "multiply"]


@dataclass(frozen=True)
class LexicalConfig:
    """Lexical-stage knobs; the default reproduces ``overlap_ranks`` exactly.

    ``title_weight`` is the BM25F weight of the chunk's own heading (ancestors
    get half, prose and code 1). ``symbol_bonus`` 0 disables the exact-symbol
    bonus. ``page_prior`` selects how :mod:`app.retrieve.page_prior` is used:
    ``"multiply"`` scales each chunk's lexical score by ``1 + page_prior_alpha
    · page_norm``; ``"rrf"`` adds ``page_prior_weight / (k + page_rank)`` after
    the fusion. The page query is the first ``page_prior_query_words`` words of
    the query.
    """

    scorer: LexicalScorer = "overlap"
    title_weight: float = 3.0
    identifiers: bool = False
    symbol_bonus: float = 0.0
    coverage: bool = False
    page_prior: PagePrior = "off"
    page_prior_weight: float = 0.5
    page_prior_alpha: float = 0.5
    page_prior_query_words: int = 32

    def __post_init__(self) -> None:
        if self.symbol_bonus and not (self.identifiers and self.scorer == "bm25f"):
            raise ValueError("symbol_bonus needs identifiers=True and scorer='bm25f'")
        if self.coverage and self.scorer != "bm25f":
            raise ValueError("coverage needs scorer='bm25f'")
        if self.title_weight <= 0 or self.symbol_bonus < 0:
            raise ValueError("title_weight must be > 0 and symbol_bonus >= 0")
        if self.page_prior_query_words < 1:
            raise ValueError("page_prior_query_words must be >= 1")

    @property
    def is_default(self) -> bool:
        """True when ranking is exactly today's ``overlap_ranks`` (no prior)."""
        return (
            self.scorer == "overlap"
            and not self.identifiers
            and self.page_prior == "off"
        )

    def tokenizer(self) -> Tokenizer:
        return identifier_tokenize if self.identifiers else tokenize

    @classmethod
    def from_settings(cls, settings: Settings) -> LexicalConfig:
        return cls(
            scorer=settings.lexical_scorer,
            title_weight=settings.lexical_title_weight,
            identifiers=settings.lexical_identifiers,
            symbol_bonus=settings.lexical_symbol_bonus,
            coverage=settings.lexical_coverage,
            page_prior=settings.page_prior,
            page_prior_weight=settings.page_prior_weight,
            page_prior_alpha=settings.page_prior_alpha,
            page_prior_query_words=settings.page_prior_query_words,
        )


@dataclass(frozen=True)
class LexicalItem:
    """What the lexical stage reads from one chunk."""

    key: Key
    text: str
    heading_path: Sequence[str]
    md: str


@dataclass(frozen=True)
class ChunkFields:
    """The four BM25F fields of a chunk."""

    leaf: str
    ancestors: str
    prose: str
    code: str


def chunk_fields(heading_path: Sequence[str], md: str) -> ChunkFields:
    """Split a chunk into its own heading, ancestor headings, prose and code.

    Code is the content of fenced blocks (```` ``` ```` / ``~~~`` lines, as
    ``to_markdown`` writes them); the fence lines themselves are dropped.
    """
    prose: list[str] = []
    code: list[str] = []
    fence: str | None = None
    for line in md.split("\n"):
        stripped = line.lstrip()
        if fence is None and stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            continue
        if fence is not None and stripped.startswith(fence):
            fence = None
            continue
        (code if fence is not None else prose).append(line)
    path = list(heading_path)
    return ChunkFields(
        leaf=path[-1] if path else "",
        ancestors=" ".join(path[:-1]),
        prose="\n".join(prose),
        code="\n".join(code),
    )


# --------------------------------------------------------------------------- #
# Scorers
# --------------------------------------------------------------------------- #
def bm25_idf(n: int, df: int) -> float:
    """BM25 IDF with the +1 inside the log (never negative)."""
    return math.log(1 + (n - df + 0.5) / (df + 0.5))


def _top(scored: list[tuple[float, Key]], limit: int) -> list[Key]:
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [key for _, key in scored[:limit]]


def _boost(key: Key, page_boost: Mapping[str, float] | None, alpha: float) -> float:
    if not page_boost:
        return 1.0
    return 1.0 + alpha * page_boost.get(key[0], 0.0)


def _overlap_scores(
    items: Sequence[LexicalItem],
    query: str,
    tokenizer: Tokenizer,
    page_boost: Mapping[str, float] | None,
    alpha: float,
) -> list[tuple[float, Key]]:
    terms = set(tokenizer(query))
    scored: list[tuple[float, Key]] = []
    for item in items:
        overlap = sum(1 for t in tokenizer(item.text) if t in terms)
        if overlap > 0:
            scored.append((overlap * _boost(item.key, page_boost, alpha), item.key))
    return scored


#: Per-chunk field term frequencies (leaf, ancestors, prose, code) and body
#: length; :data:`TokenCache` keys them by (identifiers?, heading path, md).
FieldStats = tuple[tuple[Counter[str], Counter[str], Counter[str], Counter[str]], int]
TokenCache = dict[tuple[bool, tuple[str, ...], str], FieldStats]


def field_stats(item: LexicalItem, config: LexicalConfig) -> FieldStats:
    """Term frequencies of the four fields of ``item`` and its body length."""
    tok = config.tokenizer()
    fields = chunk_fields(item.heading_path, item.md)
    prose, code = tok(fields.prose), tok(fields.code)
    counts = (
        Counter(tok(fields.leaf)),
        Counter(tok(fields.ancestors)),
        Counter(prose),
        Counter(code),
    )
    return counts, len(prose) + len(code)


def bm25f_scores(
    items: Sequence[LexicalItem],
    query: str,
    config: LexicalConfig,
    page_boost: Mapping[str, float] | None = None,
    token_cache: TokenCache | None = None,
) -> list[tuple[float, Key]]:
    """BM25F score of every item with a positive score, in input order.

    ``tf~ = Σ_field w_field · tf_field`` (times the symbol multiplier of the
    term), saturated once: ``idf · tf~ · (k1 + 1) / (tf~ + k1 · (1 - b + b ·
    dl / avgdl))`` with ``dl`` the prose + code length in tokens. IDF and
    ``avgdl`` come from ``items`` (the chunks of the request). Weights: leaf
    ``title_weight``, ancestors ``title_weight / 2``, prose and code 1.
    ``token_cache`` (optional, caller-owned) reuses field statistics across
    calls on the same chunks; it never changes the result.
    """
    terms = sorted(set(config.tokenizer()(query)))
    if not items or not terms:
        return []
    w_leaf = config.title_weight
    weights = (w_leaf, w_leaf / 2, 1.0, 1.0)
    mult = (
        _symbol_multipliers(query_symbols(query), config.symbol_bonus)
        if config.symbol_bonus
        else {}
    )
    per_item: list[tuple[Key, dict[str, float], int]] = []
    df: Counter[str] = Counter()
    for item in items:
        cache_key = (config.identifiers, tuple(item.heading_path), item.md)
        stats = token_cache.get(cache_key) if token_cache is not None else None
        if stats is None:
            stats = field_stats(item, config)
            if token_cache is not None:
                token_cache[cache_key] = stats
        counts, dl = stats
        tf: dict[str, float] = {}
        for term in terms:
            raw = sum(w * c[term] for w, c in zip(weights, counts, strict=True))
            if raw > 0:
                tf[term] = raw * mult.get(term, 1.0)
        df.update(tf.keys())
        per_item.append((item.key, tf, dl))
    n = len(per_item)
    avgdl = sum(dl for _, _, dl in per_item) / n or 1.0
    idf = {t: bm25_idf(n, df[t]) for t in terms if df[t]}
    total_idf = sum(idf.values())
    scored: list[tuple[float, Key]] = []
    for key, tf, dl in per_item:
        if not tf:
            continue
        norm = BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl)
        score = 0.0
        matched_idf = 0.0
        for term in terms:
            weighted = tf.get(term)
            if weighted is None:
                continue
            score += idf[term] * weighted * (BM25_K1 + 1) / (weighted + norm)
            matched_idf += idf[term]
        if config.coverage and total_idf > 0:
            share = matched_idf / total_idf
            score *= 1 + share * share
        score *= _boost(key, page_boost, config.page_prior_alpha)
        scored.append((score, key))
    return scored


def lexical_ranks(
    items: Sequence[LexicalItem],
    query: str,
    config: LexicalConfig | None = None,
    *,
    limit: int = _DEFAULT_LIMIT,
    page_boost: Mapping[str, float] | None = None,
    token_cache: TokenCache | None = None,
) -> list[Key]:
    """Top-``limit`` keys of ``items`` under ``config`` (ties: key ascending).

    The default config calls :func:`overlap_ranks` itself, so today's ranking is
    unchanged byte for byte. ``page_boost`` maps ``document_id`` to a page prior
    in ``[0, 1]``; it is used only with ``config.page_prior == "multiply"``.
    """
    config = config or LexicalConfig()
    boost = page_boost if config.page_prior == "multiply" else None
    if config.scorer == "overlap" and not config.identifiers and boost is None:
        return overlap_ranks(((i.key, i.text) for i in items), query, limit)
    if config.scorer == "overlap":
        scored = _overlap_scores(
            items, query, config.tokenizer(), boost, config.page_prior_alpha
        )
    else:
        scored = bm25f_scores(items, query, config, boost, token_cache)
    return _top(scored, limit)


@dataclass(frozen=True)
class LexicalResult:
    """Lexical ranking plus the page ranks the ``rrf`` page prior adds later."""

    ranks: list[Key]
    page_rank: dict[str, int]


def rank_lexical(
    items: Sequence[LexicalItem],
    query: str,
    config: LexicalConfig | None = None,
    *,
    url_by_document: Mapping[str, str] | None = None,
    limit: int = _DEFAULT_LIMIT,
    token_cache: TokenCache | None = None,
) -> LexicalResult:
    """The lexical stage shared by ``assemble`` and the benchmark runner.

    Computes the page prior when ``config.page_prior`` is on (pages are the
    documents of ``items``; their URLs come from ``url_by_document``), then the
    lexical ranking. ``page_rank`` is empty unless the prior is ``"rrf"``.
    """
    from app.retrieve.page_prior import page_norm, page_ranks, page_scores

    config = config or LexicalConfig()
    boost: dict[str, float] | None = None
    page_rank: dict[str, int] = {}
    if config.page_prior != "off":
        scores = page_scores(items, query, config, url_by_document or {})
        if config.page_prior == "multiply":
            boost = page_norm(scores)
        else:
            page_rank = page_ranks(scores)
    ranks = lexical_ranks(
        items, query, config, limit=limit, page_boost=boost, token_cache=token_cache
    )
    return LexicalResult(ranks, page_rank)


def _load_items(
    session: Session, version_ids: list[str]
) -> tuple[dict[Key, Chunk], list[LexicalItem]]:
    # shortcut: scores every chunk of the requested versions in Python, no index;
    # fine for a few URLs per request, revisit if requests grow to many large docs.
    rows = session.execute(
        select(Chunk, Source.id)
        .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
        .join(Source, Source.id == DocumentVersion.source_id)
        .where(Chunk.version_id.in_(version_ids))
    ).all()
    chunk_by_key: dict[Key, Chunk] = {
        (document_id, chunk.ord): chunk for chunk, document_id in rows
    }
    items = [
        LexicalItem(
            key=key, text=chunk.text, heading_path=list(chunk.heading_path), md=chunk.md
        )
        for key, chunk in chunk_by_key.items()
    ]
    return chunk_by_key, items


def lexical_search_ranked(
    session: Session,
    *,
    query: str,
    version_ids: list[str],
    config: LexicalConfig | None = None,
    url_by_document: Mapping[str, str] | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> tuple[list[tuple[Chunk, int]], dict[str, int]]:
    """:func:`lexical_search` plus the page ranks of an ``rrf`` page prior."""
    if not version_ids:
        return [], {}
    chunk_by_key, items = _load_items(session, version_ids)
    result = rank_lexical(
        items, query, config, url_by_document=url_by_document, limit=limit
    )
    ranked = [(chunk_by_key[key], pos) for pos, key in enumerate(result.ranks, 1)]
    return ranked, result.page_rank


def lexical_search(
    session: Session,
    *,
    query: str,
    version_ids: list[str],
    limit: int = _DEFAULT_LIMIT,
    config: LexicalConfig | None = None,
) -> list[tuple[Chunk, int]]:
    """Return the top-``limit`` chunks of ``version_ids`` by the lexical scorer.

    Each result is ``(chunk, rank_position)`` with a 1-based position. An empty
    ``version_ids`` or a query without tokens yields ``[]``. ``config`` defaults
    to ``overlap_ranks`` (today's ranking).
    """
    ranked, _ = lexical_search_ranked(
        session, query=query, version_ids=version_ids, config=config, limit=limit
    )
    return ranked
