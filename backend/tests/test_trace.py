"""`/batch/trace` — the second read of the ledger must agree with the first.

Every figure the pipeline view shows is re-derived here from the same rows the headline
came from. If the two ever disagree, the ledger is wrong, not the dashboard.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.policy import HORIZON_DAYS
from backend.app.runner import run_campaign
from backend.app.trace import STOPPING_RULES, load_rows, summarise

client = TestClient(app)


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> tuple[dict, dict]:
    ledger = tmp_path_factory.mktemp("trace") / "ledger.jsonl"
    payload = run_campaign(seed=42, use_llm=False, ledger_path=ledger)
    return payload, summarise(load_rows(ledger))


def test_every_record_lands_in_exactly_one_entry_bucket(run):
    _, trace = run
    flow = trace["flow"]
    partitioned = (
        sum(flow["stopped_at_entry"].values())
        + flow["band_high"]
        + flow["band_low"]
        + flow["agent_band"]
    )
    assert flow["input"] == 500
    assert partitioned == flow["input"]
    assert flow["recovered_records"] + flow["unrecovered_records"] == flow["input"]


def test_hard_stops_agree_with_the_headline(run):
    payload, trace = run
    flow = trace["flow"]
    stopped = sum(flow["stopped_at_entry"].values()) + sum(flow["stopped_later"].values())
    assert stopped == payload["totals"]["stopped_by_hard_rule"]
    assert set(flow["stopped_at_entry"]) == set(STOPPING_RULES)


def test_daily_curve_sums_to_the_headline_rupees(run):
    payload, trace = run
    assert len(trace["daily"]) == HORIZON_DAYS + 1
    assert sum(d["recovered_paise"] for d in trace["daily"]) == payload["totals"]["recovered_paise"]
    assert all(isinstance(d["recovered_paise"], int) for d in trace["daily"])


def test_no_debit_ran_inside_a_peak_window(run):
    _, trace = run
    assert trace["peak_violations"] == 0
    assert trace["peak_deferrals"] > 0, "rule 5 never fired — cannot claim it works"
    # Hours 10-12 and 17-21 are blocked outright; the clock must show nothing there.
    for hour in (10, 11, 12, 17, 18, 19, 20):
        assert trace["debits_by_hour"][hour] == 0


def test_hour_clock_counts_every_billable_debit(run):
    _, trace = run
    rows = trace["daily"]
    assert sum(trace["debits_by_hour"]) == sum(d["attempts"] for d in rows)
    assert len(trace["debits_by_hour"]) == 24


def test_vetoes_agree_with_the_failures_list(run):
    payload, trace = run
    vetoed_failures = {
        f["row_id"] for f in payload["failures"] if "vetoed_agent_proposal" in f["rules_fired"]
    }
    assert trace["flow"]["agent_vetoed"] >= len(vetoed_failures)
    assert trace["rules_fired"]["vetoed_agent_proposal"] == trace["flow"]["agent_vetoed"]


def test_transitions_carry_every_record_exactly_once(run):
    payload, trace = run
    edges = trace["transitions"]
    assert sum(e["n"] for e in edges) == trace["flow"]["input"]
    recovered = sum(e["n"] for e in edges if e["target"] == "recovered")
    assert recovered == trace["flow"]["recovered_records"]
    # The failures list is the authority on what stopped each unrecovered record.
    from collections import Counter

    by_label = Counter(f["stopped_by"] for f in payload["failures"])
    for label in ("score_below_band", "hard_revoked_mandate", "hard_max_attempts"):
        assert sum(e["n"] for e in edges if e["target"] == label) == by_label[label]


def test_score_histogram_covers_every_record(run):
    _, trace = run
    assert sum(trace["score_histogram"]) == trace["flow"]["input"]


def test_endpoint_serves_the_schema():
    client.post("/batch/run", json={"seed": 42, "n": 500, "use_llm": False})
    response = client.get("/batch/trace")
    assert response.status_code == 200
    body = response.json()
    assert body["flow"]["input"] == 500
    assert body["peak_violations"] == 0
    assert len(body["debits_by_hour"]) == 24
