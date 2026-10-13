"""Make the standalone ``nina-client`` SDK importable in this test package.

The SDK (``sdk/python/nina_client``) is a SEPARATE installable distribution, not
part of the ``nina`` application package, so it is not on ``sys.path`` by
default. Rather than install it (which would couple the main build to the SDK),
this conftest prepends ``sdk/python`` to ``sys.path`` for the duration of the
offline SDK unit tests — the lightest option the project supports without
touching the main ``pyproject.toml``. Scoped to ``tests/unit/sdk`` so it cannot
affect any other suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SDK_SRC = Path(__file__).resolve().parents[3] / "sdk" / "python"
if str(_SDK_SRC) not in sys.path:
    sys.path.insert(0, str(_SDK_SRC))
