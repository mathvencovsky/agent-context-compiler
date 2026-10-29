"""OpenAI-compatible embeddings from a LOCAL server (llama.cpp, TEI).

``POST {base_url}/v1/embeddings`` with ``{"model", "input": [...]}`` and reads
``data[i].embedding`` by ``index`` — the OpenAI wire format that llama.cpp's
``llama-server --embedding`` and Hugging Face TEI both serve. The base URL must
be a loopback IP literal (:mod:`app.providers.local_endpoint`); the call uses
its own ``httpx`` client, never the fetch guard (DECISIONS 2026-10-13 and
2026-10-29). Inputs go in batches of :data:`_BATCH` to keep a CPU server
responsive. Every vector must have ``dim`` components, or the call fails loudly.
With ``pad_to``, each vector is then zero-padded to that many components, so a
1024-d model fills the ``vector(1536)`` column: zeros change neither the dot
product nor the norm, so cosine distances are exactly the model's.

Asymmetric models (Qwen3-Embedding and its fine-tunes) want an instruction on
the query only: :meth:`embed_query` prepends ``query_prefix``; :meth:`embed`
(documents) never does. Tests inject an ``httpx.MockTransport``; nothing here
touches the network in tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.providers.local_endpoint import local_client, validate_local_base_url

if TYPE_CHECKING:
    import httpx

#: Texts per request.
_BATCH = 32


class LocalProviderError(RuntimeError):
    """The local model server answered with something unusable."""


class OpenAICompatibleEmbeddingProvider:
    """Embeddings from a local OpenAI-compatible ``/v1/embeddings`` server."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dim: int,
        query_prefix: str = "",
        pad_to: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = validate_local_base_url(base_url)
        self.model = model
        self.dim = dim
        self.query_prefix = query_prefix
        if pad_to is not None and pad_to < dim:
            raise ValueError(f"pad_to={pad_to} is smaller than dim={dim}")
        self.pad_to = pad_to
        self._transport = transport

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed documents as they are (no instruction)."""
        if not texts:
            return []
        out: list[list[float]] = []
        with local_client(self._transport) as client:
            for start in range(0, len(texts), _BATCH):
                out.extend(self._embed_batch(client, texts[start : start + _BATCH]))
        return out

    def embed_query(self, text: str) -> list[float]:
        """Embed a query, with ``query_prefix`` in front of it."""
        return self.embed([self.query_prefix + text])[0]

    def _embed_batch(self, client: httpx.Client, batch: list[str]) -> list[list[float]]:
        response = client.post(
            f"{self.base_url}/v1/embeddings",
            json={"model": self.model, "input": batch},
        )
        response.raise_for_status()
        data = response.json().get("data")
        if not isinstance(data, list) or len(data) != len(batch):
            got = len(data) if isinstance(data, list) else "no"
            raise LocalProviderError(
                f"embeddings server returned {got} vectors for {len(batch)} inputs"
            )
        vectors: list[list[float] | None] = [None] * len(batch)
        for position, item in enumerate(data):
            index = item.get("index", position)
            vector = [float(x) for x in item["embedding"]]
            if len(vector) != self.dim:
                raise LocalProviderError(
                    f"embedding has {len(vector)} dimensions, expected {self.dim} "
                    "(set EMBEDDING_DIM to the model's dimension)"
                )
            if self.pad_to is not None:
                vector.extend([0.0] * (self.pad_to - self.dim))
            vectors[index] = vector
        if any(v is None for v in vectors):
            raise LocalProviderError("embeddings server skipped an input index")
        return [v for v in vectors if v is not None]
