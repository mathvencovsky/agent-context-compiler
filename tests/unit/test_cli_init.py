"""``nina init`` writes a minimal managed block and nothing else (research J1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.cli import BLOCK, CLAUDE_BLOCK, END, START, MarkerError, apply_block, main


def _read(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def test_creates_agents_md_with_only_the_block(tmp_path: Path) -> None:
    assert main(["init", "--path", str(tmp_path)]) == 0
    text = _read(tmp_path / "AGENTS.md")
    assert text == BLOCK + "\n"
    assert not (tmp_path / "CLAUDE.md").exists()  # never created


def test_block_is_short_and_names_the_tools() -> None:
    lines = BLOCK.splitlines()
    assert lines[0] == START and lines[-1] == END
    assert len(lines) <= 8  # long context files hurt (AGENTbench)
    assert "assemble_context" in BLOCK and "untrusted" in BLOCK
    assert 'format="index"' in BLOCK and "`version`" in BLOCK


def test_appends_and_keeps_existing_text_byte_for_byte(tmp_path: Path) -> None:
    original = "# Project\n\nUse tabs.\n"
    (tmp_path / "AGENTS.md").write_text(original, encoding="utf-8")
    main(["init", "--path", str(tmp_path)])
    text = _read(tmp_path / "AGENTS.md")
    assert text.startswith(original)
    assert text == original + "\n" + BLOCK + "\n"


def test_second_run_is_a_no_op(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "AGENTS.md").write_text("intro\n", encoding="utf-8")
    main(["init", "--path", str(tmp_path)])
    first = _read(tmp_path / "AGENTS.md")
    mtime = (tmp_path / "AGENTS.md").stat().st_mtime_ns
    capsys.readouterr()
    main(["init", "--path", str(tmp_path)])
    assert _read(tmp_path / "AGENTS.md") == first
    assert (tmp_path / "AGENTS.md").stat().st_mtime_ns == mtime
    assert "nothing written" in capsys.readouterr().out


def test_replaces_only_between_markers(tmp_path: Path) -> None:
    before = f"top\n{START}\nold NINA text\n{END}\nbottom\n"
    (tmp_path / "AGENTS.md").write_text(before, encoding="utf-8")
    main(["init", "--path", str(tmp_path)])
    assert _read(tmp_path / "AGENTS.md") == f"top\n{BLOCK}\nbottom\n"


def test_keeps_crlf_line_endings() -> None:
    text = "a\r\nb\r\n"
    out = apply_block(text, BLOCK)
    assert out.startswith("a\r\nb\r\n\r\n")
    assert "\n" not in out.replace("\r\n", "")


@pytest.mark.parametrize(
    "broken",
    [f"{START}\nno end\n", f"{END}\n{START}\n", f"{START}\n{END}\n{START}\n{END}\n"],
)
def test_broken_markers_leave_the_file_untouched(tmp_path: Path, broken: str) -> None:
    with pytest.raises(MarkerError):
        apply_block(broken, BLOCK)
    (tmp_path / "AGENTS.md").write_text(broken, encoding="utf-8")
    assert main(["init", "--path", str(tmp_path)]) == 2
    assert _read(tmp_path / "AGENTS.md") == broken


def test_dry_run_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "CLAUDE.md").write_text("rules\n", encoding="utf-8")
    assert main(["init", "--path", str(tmp_path), "--dry-run"]) == 0
    assert not (tmp_path / "AGENTS.md").exists()
    assert _read(tmp_path / "CLAUDE.md") == "rules\n"
    out = capsys.readouterr().out
    assert "would create" in out and "would update" in out


def test_existing_claude_md_gets_the_import(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# Rules\n", encoding="utf-8")
    main(["init", "--path", str(tmp_path)])
    assert _read(tmp_path / "CLAUDE.md") == "# Rules\n\n" + CLAUDE_BLOCK + "\n"
    main(["init", "--path", str(tmp_path)])  # idempotent
    assert _read(tmp_path / "CLAUDE.md").count("@AGENTS.md") == 1


def test_claude_md_that_already_imports_agents_md_is_untouched(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    main(["init", "--path", str(tmp_path)])
    assert _read(tmp_path / "CLAUDE.md") == "@AGENTS.md\n"


def test_claude_md_symlinked_to_agents_md_is_written_once(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("shared\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")
    main(["init", "--path", str(tmp_path)])
    assert (tmp_path / "CLAUDE.md").is_symlink()  # the link survives
    text = _read(tmp_path / "AGENTS.md")
    assert text == "shared\n\n" + BLOCK + "\n"
    assert "@AGENTS.md" not in text


def test_api_url_prints_registration_without_a_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["init", "--path", str(tmp_path), "--api-url", "http://localhost:8000"])
    out = capsys.readouterr().out
    assert "API_URL=http://localhost:8000" in out and "$API_KEY" in out
    assert "API_KEY" not in _read(tmp_path / "AGENTS.md")
    assert "localhost" not in _read(tmp_path / "AGENTS.md")


def test_missing_directory_fails(tmp_path: Path) -> None:
    assert main(["init", "--path", str(tmp_path / "nope")]) == 2
