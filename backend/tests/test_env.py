"""`.env` loading. SPEC §4.3 — the documented way to supply an API key."""

from __future__ import annotations

import os
from pathlib import Path

import backend.app.env as env_module
from backend.app.env import load_env


def _reset() -> None:
    env_module._loaded = False


def test_loads_key_value_pairs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEMO_KEY", raising=False)
    _reset()
    path = tmp_path / ".env"
    path.write_text("DEMO_KEY=abc123\n", encoding="utf-8")

    assert load_env(path, force=True) == 1
    assert os.environ["DEMO_KEY"] == "abc123"
    monkeypatch.delenv("DEMO_KEY", raising=False)


def test_a_real_env_var_always_wins(tmp_path: Path, monkeypatch) -> None:
    """A stale file must never silently replace a key someone deliberately exported."""
    monkeypatch.setenv("DEMO_KEY", "from-the-shell")
    _reset()
    path = tmp_path / ".env"
    path.write_text("DEMO_KEY=from-the-file\n", encoding="utf-8")

    load_env(path, force=True)
    assert os.environ["DEMO_KEY"] == "from-the-shell"


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    """Running with no key is a supported mode, not a failure."""
    _reset()
    assert load_env(tmp_path / "nope.env", force=True) == 0


def test_comments_blanks_and_quotes_are_handled(tmp_path: Path, monkeypatch) -> None:
    for k in ("Q1", "Q2", "Q3"):
        monkeypatch.delenv(k, raising=False)
    _reset()
    path = tmp_path / ".env"
    path.write_text(
        '# a comment\n\nQ1="double"\nQ2=\'single\'\nQ3=\nnot_a_pair\n', encoding="utf-8"
    )

    load_env(path, force=True)
    assert os.environ["Q1"] == "double"
    assert os.environ["Q2"] == "single"
    assert "Q3" not in os.environ, "an empty value should not be set"
    for k in ("Q1", "Q2"):
        monkeypatch.delenv(k, raising=False)


def test_the_example_file_documents_the_real_variable() -> None:
    text = Path(__file__).resolve().parents[2].joinpath(".env.example").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" in text
