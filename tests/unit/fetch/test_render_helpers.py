"""Browser-free checks of the render helpers (M10b): no Chromium needed."""

from __future__ import annotations

import threading
import time

import pytest

from app.config import Settings
from app.fetch import render
from app.fetch.errors import Timeout


def test_render_slots_bound_concurrent_renders() -> None:
    settings = Settings(render_concurrency=1, render_total_timeout_s=60)
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with render._render_slot(settings, time.monotonic() + 5, "http://a/"):
            held.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert held.wait(5)
        started = time.monotonic()
        with pytest.raises(Timeout):
            with render._render_slot(settings, time.monotonic() + 0.2, "http://b/"):
                pass
        assert time.monotonic() - started < 2
    finally:
        release.set()
        thread.join()
    # Released: the slot is free again.
    with render._render_slot(settings, time.monotonic() + 0.2, "http://c/"):
        pass
