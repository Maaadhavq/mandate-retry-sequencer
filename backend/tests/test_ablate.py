"""The ablation harness. SPEC §4.3 layer 3.

The property that matters here is not arithmetic — it is that a ₹0 delta produced by an empty
cache is reported as *not a finding*, rather than printed as a number that looks like one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from backend.scripts.ablate import cache_size, rupees

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "data" / "batch.csv").exists()
    or not (REPO_ROOT / "models" / "scorer.txt").exists(),
    reason="run the data commands in README.md first",
)


def test_rupees_formats_paise_without_float_arithmetic_on_the_input() -> None:
    assert rupees(442_508_967) == "₹4,425,089.67"
    assert rupees(0) == "₹0.00"
    assert rupees(1) == "₹0.01"


def test_cache_size_counts_only_json_entries(tmp_path: Path) -> None:
    assert cache_size(tmp_path) == 0
    (tmp_path / "README.md").write_text("not an entry", encoding="utf-8")
    assert cache_size(tmp_path) == 0, "the explainer README must not count as a cached decision"

    (tmp_path / ("a" * 64 + ".json")).write_text("{}", encoding="utf-8")
    assert cache_size(tmp_path) == 1


def test_cache_size_of_a_missing_directory_is_zero(tmp_path: Path) -> None:
    assert cache_size(tmp_path / "nope") == 0


def test_an_empty_cache_delta_is_reported_as_not_a_finding(tmp_path: Path) -> None:
    """The guard that keeps an artefact of the setup from being read as a result."""
    result = subprocess.run(
        [
            sys.executable, "-m", "backend.scripts.ablate",
            "--cache-dir", str(tmp_path / "empty"),
        ],
        cwd=REPO_ROOT, capture_output=True, text=True,
        env={**__import__("os").environ, "ANTHROPIC_API_KEY": "", "PYTHONIOENCODING": "utf-8"},
    )

    assert result.returncode == 0, result.stderr
    assert "NOT A FINDING" in result.stdout
    assert "measured contribution" in result.stdout


def test_populate_without_a_key_refuses_rather_than_reporting_zero(tmp_path: Path) -> None:
    """--populate with no key would silently produce an empty cache and a meaningless ₹0."""
    result = subprocess.run(
        [
            sys.executable, "-m", "backend.scripts.ablate",
            "--populate", "--cache-dir", str(tmp_path / "empty"),
        ],
        cwd=REPO_ROOT, capture_output=True, text=True,
        env={**__import__("os").environ, "ANTHROPIC_API_KEY": "", "PYTHONIOENCODING": "utf-8"},
    )

    assert result.returncode != 0
    assert "needs ANTHROPIC_API_KEY" in result.stdout + result.stderr
