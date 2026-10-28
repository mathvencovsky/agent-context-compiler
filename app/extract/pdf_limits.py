"""Decoded-size budget for pdfminer stream filters (audit A2, 2026-10-28).

pdfminer.six decodes every ``FlateDecode``/``LZWDecode``/``RunLengthDecode``
stream in full (``zlib.decompress(data)``), so a PDF under the 15 MB body cap
can inflate to tens of GiB (a 204 KB PDF reached 438 MiB in the audit). This
module puts a DETERMINISTIC budget on that decoding while ``extract_pdf`` runs:

* each stream may decode to at most ``per_stream`` bytes, and the whole
  document to at most ``total`` bytes;
* a stream over its budget is TRUNCATED to what fits (never inflated in full)
  and counted, so ``extract_pdf`` can say so in ``warnings`` (explicit failure,
  CLAUDE.md) instead of failing silently;
* a stream within budget is decoded by pdfminer's own original code path,
  byte for byte, so normal documentation PDFs extract exactly as before.

The budget lives in a :class:`contextvars.ContextVar`, set only inside
:func:`decode_budget`. Outside it (any other pdfminer use) the wrappers call the
original functions unchanged. The wrappers are installed once into
``pdfminer.pdftypes`` (the module whose ``PDFStream.decode`` calls them).
"""

from __future__ import annotations

import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pdfminer.pdftypes as _pdftypes
from pdfminer.lzw import LZWDecoder
from pdfminer.lzw import lzwdecode as _orig_lzwdecode
from pdfminer.runlength import rldecode as _orig_rldecode


@dataclass
class DecodeBudget:
    """Per-extraction decoding budget and the truncation tally."""

    per_stream: int
    total_left: int
    truncated_streams: int = 0

    def room(self) -> int:
        return max(0, min(self.per_stream, self.total_left))

    def spend(self, n: int) -> None:
        self.total_left = max(0, self.total_left - n)


_BUDGET: ContextVar[DecodeBudget | None] = ContextVar("pdf_decode_budget", default=None)


def _truncate(budget: DecodeBudget, out: bytes, room: int) -> bytes:
    budget.truncated_streams += 1
    budget.spend(room)
    return out[:room]


def _capped_zlib_decompress(data: bytes, *args: Any, **kwargs: Any) -> bytes:
    budget = _BUDGET.get()
    if budget is None or args or kwargs:
        return zlib.decompress(data, *args, **kwargs)
    room = budget.room()
    probe = zlib.decompressobj()
    try:
        out = probe.decompress(data, room + 1)
    except zlib.error:
        # Corrupt data: let pdfminer see the very same error it saw before
        # (it then tries its own recovery path).
        return zlib.decompress(data)
    if len(out) > room or probe.unconsumed_tail:
        return _truncate(budget, out, room)
    result = zlib.decompress(data)  # Within budget: the original decode.
    budget.spend(len(result))
    return result


def _capped_lzwdecode(data: bytes) -> bytes:
    budget = _BUDGET.get()
    if budget is None:
        return _orig_lzwdecode(data)
    room = budget.room()
    out = bytearray()
    for piece in LZWDecoder(BytesIO(data)).run():
        out += piece
        if len(out) > room:
            return _truncate(budget, bytes(out), room)
    budget.spend(len(out))
    return bytes(out)


def _rl_decoded_length(data: bytes) -> int:
    """Length ``rldecode`` would produce (cheap scan, no allocation)."""
    total = i = 0
    n = len(data)
    while i < n:
        length = data[i]
        if length == 128:
            break
        if length < 128:
            total += length + 1
            i += length + 2
        else:
            total += 257 - length
            i += 2
    return total


def _capped_rldecode(data: bytes) -> bytes:
    budget = _BUDGET.get()
    if budget is None:
        return _orig_rldecode(data)
    room = budget.room()
    if _rl_decoded_length(data) <= room:
        result = _orig_rldecode(data)
        budget.spend(len(result))
        return result
    # Over budget: decode run by run and stop at the budget.
    out = bytearray()
    i, n = 0, len(data)
    while i < n and len(out) <= room:
        length = data[i]
        if length == 128:
            break
        if length < 128:
            out += data[i + 1 : i + length + 2]
            i += length + 2
        else:
            out += data[i + 1 : i + 2] * (257 - length)
            i += 2
    return _truncate(budget, bytes(out), room)


# pdfminer.pdftypes calls ``zlib.decompress``, ``lzwdecode`` and ``rldecode``
# through its module globals; swap in the budget-aware versions. ``zlib`` keeps
# every other attribute (``decompressobj`` for pdfminer's corrupt-data path,
# ``error``) from the real module.
_zlib_shim = SimpleNamespace(
    **{k: getattr(zlib, k) for k in dir(zlib) if not k.startswith("__")}
)
_zlib_shim.decompress = _capped_zlib_decompress
_pdftypes.zlib = _zlib_shim  # type: ignore[assignment,attr-defined]
_pdftypes.lzwdecode = _capped_lzwdecode  # type: ignore[attr-defined]
_pdftypes.rldecode = _capped_rldecode  # type: ignore[attr-defined]


@contextmanager
def decode_budget(per_stream: int, total: int) -> Iterator[DecodeBudget]:
    """Apply a decoding budget to every pdfminer stream decoded in this block."""
    budget = DecodeBudget(per_stream=per_stream, total_left=total)
    token = _BUDGET.set(budget)
    try:
        yield budget
    finally:
        _BUDGET.reset(token)
