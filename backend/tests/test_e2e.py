"""SPEC §8.2 — the whole-pipeline gates, and Gate B.

Gate 4 (an agent proposal vetoed by a hard rule) needs F5 and lives in `test_decider.py`.
The guardrail half of it is already proven in `test_guardrails.py`; what is missing here is
only the live agent, which is by design — this file demonstrates that the pipeline closes
on a real rupee figure with no LLM in the loop at all (SPEC §10.3).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.guardrails import HARD_RULES
from backend.app.main import app
from backend.app.policy import MAX_ATTEMPTS, Action, FailureReason, TerminalState
from backend.app.runner import load_batch, run_campaign

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "data"

needs_artifacts = pytest.mark.skipif(
    not (DATA / "batch.csv").exists()
    or not (REPO_ROOT / "models" / "scorer.txt").exists(),
    reason="run the data commands in README.md first",
)

pytestmark = needs_artifacts


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    ledger = tmp_path_factory.mktemp("e2e") / "ledger.jsonl"
    payload = run_campaign(seed=42, use_llm=False, ledger_path=ledger)
    payload["_ledger_path"] = str(ledger)
    return payload


@pytest.fixture(scope="module")
def ledger_rows(run: dict) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(run["_ledger_path"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --------------------------------------------------------------------------------------
# Gate 1 — every record accounted for
# --------------------------------------------------------------------------------------


def test_every_input_record_has_at_least_one_ledger_row(ledger_rows: list[dict]) -> None:
    batch = load_batch(DATA / "batch.csv")
    seen = {r["row_id"] for r in ledger_rows}
    missing = {r.row_id for r in batch} - seen

    assert not missing, f"{len(missing)} records produced no ledger row at all"


def test_every_record_reaches_a_terminal_state(run: dict, ledger_rows: list[dict]) -> None:
    """Nothing may be left in limbo when the horizon closes.

    Asserted through the response rather than by reaching into the clock: every record is
    either recovered or listed in the failures panel, and never both. That partition is
    the observable form of "reached a terminal state", and it is what the dashboard shows.
    """
    batch = load_batch(DATA / "batch.csv")
    failed_ids = {f["row_id"] for f in run["failures"]}
    recovered_ids = {r["row_id"] for r in ledger_rows if int(r["recovered_paise"]) > 0}

    assert not (failed_ids & recovered_ids), "a record is both recovered and failed"
    assert len(failed_ids | recovered_ids) == len(batch), (
        f"{len(batch) - len(failed_ids | recovered_ids)} records ended in neither state"
    )


# --------------------------------------------------------------------------------------
# Gate 2 — the total is independently reproducible
# --------------------------------------------------------------------------------------


def test_reported_total_equals_a_manual_sum_over_the_ledger(
    run: dict, ledger_rows: list[dict]
) -> None:
    manual = sum(int(r["recovered_paise"]) for r in ledger_rows)
    assert run["totals"]["recovered_paise"] == manual


def test_verify_totals_script_agrees_and_does_not_import_the_ledger(run: dict) -> None:
    """SPEC §8.2 gate 2. Shared aggregation code would make agreement a tautology."""
    source = (REPO_ROOT / "backend" / "scripts" / "verify_totals.py").read_text(
        encoding="utf-8"
    )
    assert "import" in source
    assert "from backend.app.ledger" not in source
    assert "import ledger" not in source

    result = subprocess.run(
        [
            sys.executable, "-m", "backend.scripts.verify_totals",
            "--ledger", run["_ledger_path"],
            "--expect-recovered", str(run["totals"]["recovered_paise"]),
        ],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ledger agrees with the API" in result.stdout


# --------------------------------------------------------------------------------------
# Gate 3 — every hard rule actually fires
# --------------------------------------------------------------------------------------


def test_each_hard_rule_fires_at_least_once(ledger_rows: list[dict]) -> None:
    """If one never fires the generator is not producing that case. Fix it, don't skip it."""
    fired = {rule for row in ledger_rows for rule in row["rules_fired"]}

    for rule in ("hard_revoked_mandate", "hard_max_attempts", "hard_cooling_period"):
        assert rule in fired, (
            f"{rule} never fired on a 500-record batch — the generator is not producing "
            "that case (SPEC §8.2 gate 3)"
        )


def test_an_example_row_exists_for_each_hard_rule(ledger_rows: list[dict]) -> None:
    for rule in ("hard_revoked_mandate", "hard_max_attempts", "hard_cooling_period"):
        example = next(r for r in ledger_rows if rule in r["rules_fired"])
        assert example["recovered_paise"] == 0 or rule == "hard_cooling_period"


def test_revoked_mandates_never_recover_anywhere_in_the_run(
    ledger_rows: list[dict],
) -> None:
    """Rule 1 is absolute. This is the claim the whole submission rests on."""
    revoked = [
        r for r in ledger_rows
        if r["failure_reason"] == FailureReason.REVOKED_MANDATE.value
    ]
    assert revoked, "no revoked mandates in the batch"
    assert all(int(r["recovered_paise"]) == 0 for r in revoked)


def test_no_record_exceeds_the_attempt_cap(ledger_rows: list[dict]) -> None:
    from collections import Counter

    debits: Counter[str] = Counter()
    for row in ledger_rows:
        if int(row["attempt_cost_paise"]) > 0:
            debits[row["row_id"]] += 1

    worst = max(debits.values()) if debits else 0
    assert worst <= MAX_ATTEMPTS, f"a record was debited {worst} times"


def test_cooling_period_is_respected_between_real_attempts(
    ledger_rows: list[dict],
) -> None:
    """No two billable attempts on the same record inside the cooling window."""
    from collections import defaultdict
    from datetime import datetime

    from backend.app.policy import COOLING_PERIOD_HOURS

    per_record: dict[str, list[datetime]] = defaultdict(list)
    for row in ledger_rows:
        if int(row["attempt_cost_paise"]) > 0:
            per_record[row["row_id"]].append(datetime.fromisoformat(row["sim_ts"]))

    for row_id, stamps in per_record.items():
        stamps.sort()
        for earlier, later in zip(stamps, stamps[1:]):
            gap = (later - earlier).total_seconds() / 3600.0
            assert gap >= COOLING_PERIOD_HOURS - 1e-6, (
                f"{row_id} was debited twice {gap:.2f}h apart, inside the "
                f"{COOLING_PERIOD_HOURS}h cooling period"
            )


# --------------------------------------------------------------------------------------
# Gate 5 — reproducibility
# --------------------------------------------------------------------------------------


def test_same_seed_reproduces_identical_totals(tmp_path: Path) -> None:
    a = run_campaign(seed=42, use_llm=False, ledger_path=tmp_path / "a.jsonl")
    b = run_campaign(seed=42, use_llm=False, ledger_path=tmp_path / "b.jsonl")

    assert a["totals"] == b["totals"]
    assert a["cohorts"] == b["cohorts"]
    assert a["attempts_histogram"] == b["attempts_histogram"]
    assert a["promises"] == b["promises"]
    # run_id is a fresh uuid per run by design, so the ledgers are compared instead.
    assert (tmp_path / "a.jsonl").read_bytes() == (tmp_path / "b.jsonl").read_bytes()


def test_a_different_seed_changes_the_outcome(tmp_path: Path) -> None:
    a = run_campaign(seed=42, use_llm=False, ledger_path=tmp_path / "a.jsonl")
    b = run_campaign(seed=99, use_llm=False, ledger_path=tmp_path / "b.jsonl")

    assert a["totals"]["recovered_paise"] != b["totals"]["recovered_paise"]


# --------------------------------------------------------------------------------------
# Gate B — a real figure through the frozen shape
# --------------------------------------------------------------------------------------


def test_batch_run_returns_a_real_recovered_figure() -> None:
    client = TestClient(app)
    response = client.post("/batch/run", json={"seed": 42, "n": 500, "use_llm": False})

    assert response.status_code == 200
    totals = response.json()["totals"]
    assert totals["recovered_paise"] > 0, "Gate B needs one real ₹ figure end to end"
    assert totals["at_risk_paise"] > totals["recovered_paise"]
    assert 0.0 < totals["recovery_rate"] < 1.0


def test_response_still_matches_the_frozen_shape() -> None:
    """SPEC §7.2 is frozen. The stub is kept as the reference to compare against."""
    from backend.app.main import _stub_response
    from backend.app.schemas import BatchRunRequest

    client = TestClient(app)
    live = client.post("/batch/run", json={"seed": 42, "n": 500, "use_llm": False}).json()
    stub = _stub_response(BatchRunRequest(seed=42, n=500, use_llm=False)).model_dump()

    def shape(node):
        if isinstance(node, dict):
            return {k: shape(v) for k, v in sorted(node.items())}
        if isinstance(node, list):
            return [shape(node[0])] if node else []
        return type(node).__name__

    assert shape(live) == shape(stub), "the live response drifted from the frozen §7.2 shape"


def test_no_debit_ever_executes_inside_an_npci_peak_window(
    ledger_rows: list[dict],
) -> None:
    """Rule 5, SPEC §3.3 — the one constraint here checkable against a dated public source.

    NPCI restricts autopay execution to non-peak hours from 1 August 2025. Roughly 40% of
    the day is closed. This asserts the guarantee over every real debit in the run, not
    over a sampled few: a single execution at 11:00 or 19:00 IST would be non-compliant.
    """
    from datetime import datetime

    from backend.app.guardrails import in_peak_window

    debits = [r for r in ledger_rows if int(r["attempt_cost_paise"]) > 0]
    assert debits, "no debits in the run — this test would pass vacuously"

    illegal = [
        r for r in debits if in_peak_window(datetime.fromisoformat(r["sim_ts"]))
    ]
    assert not illegal, (
        f"{len(illegal)} of {len(debits)} debits executed inside an NPCI peak window, "
        f"e.g. {illegal[0]['row_id']} at {illegal[0]['sim_ts']}"
    )


def test_peak_deferrals_actually_happen(ledger_rows: list[dict]) -> None:
    """The rule must bite. If nothing defers, the test above passes for the wrong reason."""
    deferred = [r for r in ledger_rows if "hard_peak_window" in r["rules_fired"]]

    assert deferred, "rule 5 never fired — either the clock or the rule is not wired in"
    assert all(r["outcome"] == "NOT_ATTEMPTED" for r in deferred)
    assert all(int(r["recovered_paise"]) == 0 for r in deferred)


def test_a_deferred_record_comes_back_and_is_not_dropped(ledger_rows: list[dict]) -> None:
    """A peak deferral postpones. It must never be a silent write-off."""
    deferred_ids = {
        r["row_id"] for r in ledger_rows if "hard_peak_window" in r["rules_fired"]
    }
    assert deferred_ids

    for row_id in deferred_ids:
        rows = [r for r in ledger_rows if r["row_id"] == row_id]
        assert len(rows) > 1, f"{row_id} was deferred and then never woken again"


def test_bad_input_is_a_422_not_a_stack_trace() -> None:
    """SPEC §8.4: nothing degrades to a traceback on screen.

    A negative seed used to reach numpy and raise a bare ValueError, surfacing as a 500.
    """
    client = TestClient(app)

    for body in (
        {"seed": -1, "n": 10, "use_llm": False},
        {"seed": 42, "n": 0, "use_llm": False},
        {"seed": 42, "n": 10_000_000, "use_llm": False},
    ):
        response = client.post("/batch/run", json=body)
        assert response.status_code == 422, f"{body} returned {response.status_code}"
        assert "detail" in response.json()


def test_the_agent_has_no_free_text_injection_surface() -> None:
    """Every record field reaching the prompt is an enum or an int.

    Nothing attacker-controlled is interpolated into it, which is why the decider needs no
    input sanitisation of its own. If a free-text field is ever added to MandateRecord,
    this test should fail and the prompt should be revisited before it ships.
    """
    from dataclasses import fields

    from backend.app.decider import _user_prompt
    from backend.app.models import MandateRecord

    free_text = {
        f.name
        for f in fields(MandateRecord)
        if f.type in ("str", str) and f.name != "row_id"
    }
    assert not free_text, f"free-text fields on the record: {free_text}"

    batch = load_batch(DATA / "batch.csv")
    prompt = _user_prompt(batch[0], 0.44)
    assert batch[0].row_id not in prompt, "row_id should not reach the prompt at all"


def test_pipeline_closes_with_no_llm_in_the_loop(run: dict) -> None:
    """SPEC §10.3: the agent is an upgrade to a working system, not a dependency."""
    assert run["config"]["use_llm"] is False
    assert run["agent"]["records_routed"] == 0
    assert run["agent"]["sources"]["live"] == 0
    assert run["agent"]["sources"]["cache"] == 0
    assert run["totals"]["recovered_paise"] > 0


def test_failures_panel_lists_every_unrecovered_record(
    run: dict, ledger_rows: list[dict]
) -> None:
    """SPEC §2.5 panel 3 — never paginated away, never collapsed."""
    recovered_ids = {r["row_id"] for r in ledger_rows if int(r["recovered_paise"]) > 0}
    all_ids = {r["row_id"] for r in ledger_rows}
    failures = run["failures"]

    assert failures, "a run with unrecovered records must list them"
    assert {f["row_id"] for f in failures} == all_ids - recovered_ids, (
        "the failures panel is not the exact complement of what was recovered"
    )
    assert failures == sorted(
        failures, key=lambda f: (-f["amount_paise"], f["row_id"])
    ), "failures must be sorted by ₹ descending"
    assert all(f["stopped_by"] for f in failures), "every failure needs a reason"
