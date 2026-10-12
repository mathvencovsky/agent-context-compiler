"""JinaRerankProvider: imports without a key and fails loudly when unused.

No network test (CLAUDE.md): the real adapter is never exercised against the
vendor API. These tests only assert the module imports with no key configured
and that calling it without a key raises :class:`MissingAPIKey`.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.providers.rerank.jina import JinaRerankProvider, MissingAPIKey


def test_imports_and_constructs_without_key() -> None:
    provider = JinaRerankProvider(settings=Settings(reranker_api_key=None))
    assert provider.model == "jina-reranker-v2-base-multilingual"


def test_rerank_without_key_raises() -> None:
    provider = JinaRerankProvider(settings=Settings(reranker_api_key=None))
    with pytest.raises(MissingAPIKey):
        provider.rerank("query", ["a document"])
