"""Print a public timestamp record of the M10 pre-registration (front F, L4).

The repository is private, and a commit in a private repository can be
rewritten, so the commit date proves nothing to an outside reader. Pasting this
record into a public, dated place (a public gist, OSF Registries, AsPredicted,
optionally OpenTimestamps) before the pilot, and again after every amendment,
lets anyone check later that the published PREREG and frozen code are the ones
fixed before the data: the record holds the sha256 of ``bench/PREREG.md`` as
committed at HEAD, the git tree ids of ``app/`` and ``migrations/``, HEAD and
its date, and the commands that recompute them. No network; it only reads git.

Usage::

    uv run python -m bench.prereg_stamp
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PREREG = "bench/PREREG.md"
TREES = ("app", "migrations")


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, check=True
    ).stdout


def stamp(repo: Path, frozen: Mapping[str, str]) -> str:
    """The record to paste, for HEAD of ``repo``; ``frozen``: expected tree ids."""
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    date = _git(repo, "show", "-s", "--format=%cI", head).decode().strip()
    committed = _git(repo, "show", f"{head}:{PREREG}")
    lines = [
        "NINA M10 pre-registration stamp",
        f"stamped_at_utc: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"commit: {head}",
        f"commit_date: {date}",
        f"{PREREG} sha256: {hashlib.sha256(committed).hexdigest()}",
    ]
    for name in TREES:
        tree = _git(repo, "rev-parse", f"{head}:{name}").decode().strip()
        lines.append(f"{name}/ tree: {tree}")
        if name in frozen:
            match = "yes" if frozen[name] == tree else "NO"
            lines.append(f"{name}/ matches the frozen tree: {match}")
    lines += [
        "verify:",
        f"  git show {head}:{PREREG} | sha256sum",
        f"  git rev-parse {head}:app {head}:migrations",
    ]
    working = repo / PREREG
    if working.exists() and working.read_bytes() != committed:
        lines.append(
            f"WARNING: the working {PREREG} differs from HEAD; commit it and "
            "stamp again before publishing"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - prints git
    from bench.m10_agent import FROZEN_TREES

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    print(stamp(args.repo, FROZEN_TREES), end="")


if __name__ == "__main__":
    main()
