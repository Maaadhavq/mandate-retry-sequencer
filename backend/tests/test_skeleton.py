"""Gate A (SPEC §10.2): the skeleton runs and the frozen contract holds.

These tests outlive the stub. When `run_batch` is replaced at Gate B they must still pass
unchanged — that is what "frozen shape" means in practice.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import policy
from backend.app.main import app

client = TestClient(app)


def test_health_returns_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_batch_run_returns_frozen_shape():
    r = client.post("/batch/run", json={"seed": 42, "n": 500, "use_llm": True})
    assert r.status_code == 200
    body = r.json()

    assert set(body) == {
        "run_id", "seed", "config", "totals", "cohorts",
        "attempts_histogram", "promises", "failures", "agent",
    }
    assert set(body["totals"]) == {
        "at_risk_paise", "recovered_paise", "recovery_rate", "attempts_per_recovery",
        "false_positive_cost_paise", "stopped_by_hard_rule",
    }
    assert set(body["cohorts"]) == {"by_failure_reason", "by_merchant_category"}
    assert set(body["agent"]["sources"]) == {"live", "cache", "fallback", "deterministic"}
    assert set(body["promises"]) == {"made", "kept", "broken", "recovered_paise"}


def test_batch_run_echoes_config():
    r = client.post("/batch/run", json={"seed": 7, "n": 250, "use_llm": False})
    body = r.json()
    assert body["seed"] == 7
    assert body["config"] == {"n": 250, "horizon_days": policy.HORIZON_DAYS, "use_llm": False}


def test_money_fields_are_integers():
    """SPEC §11: money is integer paise, never a float. Guarded from the first commit."""
    body = client.post("/batch/run", json={}).json()
    for field in ("at_risk_paise", "recovered_paise", "false_positive_cost_paise"):
        assert isinstance(body["totals"][field], int), f"{field} must be int paise"
    for slice_ in body["cohorts"]["by_failure_reason"] + body["cohorts"]["by_merchant_category"]:
        assert isinstance(slice_["at_risk_paise"], int)
        assert isinstance(slice_["recovered_paise"], int)


def test_score_bands_are_exhaustive_and_non_overlapping():
    """SPEC §3.2. Asserted here so a careless edit to policy.py fails immediately."""
    assert 0.0 < policy.BAND_LOW < policy.BAND_HIGH < 1.0
    assert policy.BAND_AGENT_MIN == policy.BAND_LOW
    assert policy.BAND_AGENT_MAX == policy.BAND_HIGH


def test_npci_constants_are_present_and_sane():
    """SPEC §3.3: these live in exactly one place."""
    assert policy.MAX_ATTEMPTS == 4
    assert policy.COOLING_PERIOD_HOURS == 24.0
    assert policy.RETRY_WINDOWS_HOURS == (24, 72, 168)
    assert policy.HORIZON_DAYS == 14
