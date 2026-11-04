"""The public pre-registration stamp (bench/prereg_stamp.py, front F L4)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from bench.prereg_stamp import stamp


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for rel, text in {
        "bench/PREREG.md": "# prereg\n",
        "app/main.py": "x = 1\n",
        "migrations/env.py": "y = 2\n",
    }.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(
        tmp_path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        "init",
    )
    return tmp_path


def test_stamp_gives_the_hash_trees_and_head_as_committed(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    app_tree = _git(repo, "rev-parse", "HEAD:app")
    text = stamp(repo, frozen={"app": app_tree, "migrations": "0" * 40})
    sha = hashlib.sha256(b"# prereg\n").hexdigest()
    assert f"bench/PREREG.md sha256: {sha}" in text
    assert f"commit: {head}" in text
    assert f"app/ tree: {app_tree}" in text
    assert f"migrations/ tree: {_git(repo, 'rev-parse', 'HEAD:migrations')}" in text
    assert "app/ matches the frozen tree: yes" in text
    assert "migrations/ matches the frozen tree: NO" in text
    # how a third party checks it, with nothing but git and sha256sum
    assert f"git show {head}:bench/PREREG.md | sha256sum" in text
    assert "WARNING" not in text


def test_stamp_warns_when_the_working_prereg_is_not_committed(repo: Path) -> None:
    (repo / "bench/PREREG.md").write_text("# edited\n", encoding="utf-8")
    text = stamp(repo, frozen={})
    # the stamp is of the committed file, and it says the working copy differs
    assert hashlib.sha256(b"# prereg\n").hexdigest() in text
    assert "WARNING" in text
