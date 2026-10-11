"""Token counting (tiktoken cl100k_base) — M5.

A single deterministic token counter used to populate ``stats.tokens_markdown``
and ``stats.tokens_raw_html`` on the resolve response (SPEC §8). The
``cl100k_base`` encoding ships embedded in the tiktoken package, so counting is
pure and never touches the network (CLAUDE.md). The encoder is loaded once and
cached for the process lifetime.
"""

from __future__ import annotations

from functools import lru_cache

import tiktoken


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    """Return the process-wide cached ``cl100k_base`` encoding."""
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Return the number of ``cl100k_base`` tokens in ``text``.

    An empty string counts as zero. The count is deterministic across calls.
    """
    if not text:
        return 0
    return len(_encoding().encode(text))
