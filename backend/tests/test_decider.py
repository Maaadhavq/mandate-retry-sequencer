"""SPEC §8.1 F5 and §8.2 gate 4 — the decider agent.

No test here touches the network. The live path is exercised through a stub client that
returns whatever the test wants, which is the only way to assert the failure handling in
SPEC §4.4: a schema-invalid response must retry once and then fall back, and an API error
must fall back immediately, without either taking the batch down.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app import guardrails
from backend.app.decider import (
    DECISION_SCHEMA,
    MAX_REASONING_CHARS,
    MODEL,
    SYSTEM_PROMPT,
    Decider,
)
from backend.app.guardrails import SIM_START
from backend.app.llm_cache import AgentDecision, ResponseCache, cache_key, canonical_record
from backend.app.models import MandateRecord
from backend.app.policy import (
    BAND_AGENT_MAX,
    BAND_AGENT_MIN,
    POLICY_VERSION,
    RETRY_WINDOWS_HOURS,
    Action,
    AgentSource,
    FailureReason,
    MerchantCategory,
)

NOW = SIM_START + timedelta(days=2)


def rec(**overrides) -> MandateRecord:
    base = dict(
        row_id="mrs_0000a1",
        failure_reason=FailureReason.INSUFFICIENT_BALANCE,
        days_to_payday=4,
        attempt_number=2,
        ticket_size_paise=1_200_000,
        merchant_category=MerchantCategory.SAAS,
        days_since_last_success=40,
        mandate_age_days=400,
        last_attempt_at=NOW - timedelta(hours=48),
    )
    base.update(overrides)
    return MandateRecord(**base)


class StubClient:
    """Minimal stand-in for `anthropic.Anthropic`. Records what it was called with."""

    def __init__(self, *responses) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        result = self._responses.pop(0) if self._responses else None
        if isinstance(result, Exception):
            raise result
        return result


def response(payload, *, cache_read: int = 0, cache_create: int = 0):
    return SimpleNamespace(
        parsed_output=payload,
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        usage=SimpleNamespace(
            input_tokens=1400,
            output_tokens=60,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_create,
        ),
    )


VALID = {
    "action": "RETRY_SCHEDULED",
    "retry_delay_hours": 72,
    "confidence": 0.62,
    "reasoning": "payday is four days out on a mid-size SaaS ticket, so waiting beats retrying now",
}


# --------------------------------------------------------------------------------------
# The cache — SPEC §4.3 layer 1
# --------------------------------------------------------------------------------------


def test_cache_key_is_stable_across_runs() -> None:
    record, score = rec(), 0.44
    assert cache_key(record, score, model=MODEL) == cache_key(record, score, model=MODEL)


def test_cache_key_changes_with_policy_version(monkeypatch) -> None:
    """A prompt or schema edit must invalidate stale entries, not silently replay them."""
    record = rec()
    before = cache_key(record, 0.44, model=MODEL)

    monkeypatch.setattr("backend.app.llm_cache.POLICY_VERSION", "v99")
    after = cache_key(record, 0.44, model=MODEL)

    assert before != after


def test_cache_key_changes_with_the_model() -> None:
    record = rec()
    assert cache_key(record, 0.44, model=MODEL) != cache_key(
        record, 0.44, model="claude-opus-5"
    )


def test_canonical_record_is_key_order_independent() -> None:
    """Unsorted JSON would make the key depend on dict insertion order."""
    text = canonical_record(rec(), 0.44)
    assert text == canonical_record(rec(), 0.44)
    keys = [k.split('"')[1] for k in text.split(",") if '":' in k]
    assert keys == sorted(keys), "canonical JSON is not sorted"


def test_score_is_rounded_so_float_noise_is_not_a_cache_miss() -> None:
    record = rec()
    assert cache_key(record, 0.44, model=MODEL) == cache_key(
        record, 0.4400000000000001, model=MODEL
    )


def test_a_cache_hit_returns_byte_identical_output(tmp_path: Path) -> None:
    """SPEC §8.1 F5. This is what lets a clone with no key reproduce the totals."""
    cache = ResponseCache(tmp_path)
    record, score = rec(), 0.44
    key = cache_key(record, score, model=MODEL)
    original = AgentDecision.from_dict(VALID)
    cache.put(key, original, model=MODEL, record_json=canonical_record(record, score))

    replayed = ResponseCache(tmp_path).get(key)

    assert replayed == original
    assert replayed.to_dict() == original.to_dict()


def test_a_corrupt_cache_entry_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    key = "0" * 64
    cache.path_for(key).parent.mkdir(parents=True, exist_ok=True)
    cache.path_for(key).write_text("{not json", encoding="utf-8")

    assert cache.get(key) is None
    assert cache.misses == 1


def test_cache_entries_record_their_provenance(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    record, score = rec(), 0.44
    key = cache_key(record, score, model=MODEL)
    cache.put(
        key,
        AgentDecision.from_dict(VALID),
        model=MODEL,
        record_json=canonical_record(record, score),
    )

    payload = json.loads(cache.path_for(key).read_text(encoding="utf-8"))
    assert payload["model"] == MODEL
    assert payload["policy_version"] == POLICY_VERSION
    assert payload["key"] == key
    assert payload["record"]["row_id"] == record.row_id


# --------------------------------------------------------------------------------------
# The prompt — SPEC §4.2
# --------------------------------------------------------------------------------------


def test_system_prompt_is_byte_identical_across_calls() -> None:
    """Anything volatile in the prefix drops the cache hit rate to zero."""
    from backend.app.decider import SYSTEM_PROMPT as again

    assert SYSTEM_PROMPT is again
    for volatile in ("2026-", "row_id", "mrs_", "run_"):
        assert volatile not in SYSTEM_PROMPT, f"{volatile!r} leaked into the cached prefix"


def test_system_prompt_clears_the_prompt_cache_minimum() -> None:
    """Prefixes under ~1024 tokens silently do not cache, which would fail §8.1 F5.

    Measured in characters because counting tokens needs the network. The ratio is
    conservative: ~3.7 chars per token understates the count for prose like this.
    """
    approx_tokens = len(SYSTEM_PROMPT) / 3.7
    assert approx_tokens > 1024, (
        f"system prompt is ~{approx_tokens:.0f} tokens, under the ~1024 minimum — "
        "prompt caching would silently never engage"
    )


def test_schema_matches_the_actions_the_agent_may_propose() -> None:
    from backend.app.policy import AGENT_PROPOSABLE

    assert set(DECISION_SCHEMA["properties"]["action"]["enum"]) == {
        a.value for a in AGENT_PROPOSABLE
    }
    assert DECISION_SCHEMA["additionalProperties"] is False
    assert set(DECISION_SCHEMA["required"]) == set(DECISION_SCHEMA["properties"])


def test_schema_allows_only_the_policy_retry_windows() -> None:
    allowed = DECISION_SCHEMA["properties"]["retry_delay_hours"]["enum"]
    assert set(allowed) == {*RETRY_WINDOWS_HOURS, None}


# --------------------------------------------------------------------------------------
# The live path and its failure handling — SPEC §4.4
# --------------------------------------------------------------------------------------


def test_a_valid_response_is_used_and_cached(tmp_path: Path) -> None:
    client = StubClient(response(VALID, cache_create=1300))
    decider = Decider(cache=ResponseCache(tmp_path), client=client)

    action, delay, reasoning, source = decider.decide(rec(), 0.44, NOW)

    assert action is Action.RETRY_SCHEDULED
    assert delay == 72
    assert source is AgentSource.LIVE
    assert reasoning == VALID["reasoning"]
    assert decider.cache.writes == 1


def test_the_second_call_for_the_same_record_hits_the_cache(tmp_path: Path) -> None:
    client = StubClient(response(VALID))
    decider = Decider(cache=ResponseCache(tmp_path), client=client)

    decider.decide(rec(), 0.44, NOW)
    _, _, _, source = decider.decide(rec(), 0.44, NOW)

    assert source is AgentSource.CACHE
    assert len(client.calls) == 1, "a cached record should not reach the API again"


def test_the_policy_prompt_carries_the_cache_breakpoint(tmp_path: Path) -> None:
    client = StubClient(response(VALID))
    Decider(cache=ResponseCache(tmp_path), client=client).decide(rec(), 0.44, NOW)

    system = client.calls[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[0]["text"] == SYSTEM_PROMPT


def test_per_record_data_goes_after_the_breakpoint(tmp_path: Path) -> None:
    """Per-record content in `system` would invalidate the cache on every call."""
    client = StubClient(response(VALID))
    record = rec(row_id="mrs_beef01", ticket_size_paise=4_242_424)
    Decider(cache=ResponseCache(tmp_path), client=client).decide(record, 0.44, NOW)

    call = client.calls[0]
    user = call["messages"][0]["content"]
    assert "4242424" in user
    assert "4242424" not in call["system"][0]["text"]


def test_cache_read_tokens_are_tracked(tmp_path: Path) -> None:
    """SPEC §8.1 F5: a zero here after the first call means the prefix is not stable."""
    client = StubClient(
        response(VALID, cache_create=1300),
        response(dict(VALID, reasoning="second"), cache_read=1300),
    )
    decider = Decider(cache=ResponseCache(tmp_path), client=client)

    decider.decide(rec(row_id="mrs_00aa01"), 0.44, NOW)
    decider.decide(rec(row_id="mrs_00aa02"), 0.44, NOW)

    assert decider.cache_read_tokens_total == 1300
    assert decider.last_usage["cache_read_input_tokens"] == 1300


def test_temperature_is_never_sent(tmp_path: Path) -> None:
    """Removed on current models — sending it returns a 400 (SPEC §4.2)."""
    client = StubClient(response(VALID))
    Decider(cache=ResponseCache(tmp_path), client=client).decide(rec(), 0.44, NOW)

    assert "temperature" not in client.calls[0]
    assert "thinking" not in client.calls[0]


def test_schema_invalid_response_retries_once_then_falls_back(tmp_path: Path) -> None:
    """SPEC §4.4."""
    bad = {"action": "TELEPORT", "retry_delay_hours": None, "confidence": 0.5, "reasoning": "x"}
    client = StubClient(response(bad), response(bad))
    decider = Decider(cache=ResponseCache(tmp_path), client=client)

    _, _, _, source = decider.decide(rec(), 0.44, NOW)

    assert source is AgentSource.FALLBACK
    assert len(client.calls) == 2, "an invalid response should be retried exactly once"
    assert decider.cache.writes == 0, "a rejected response must never be cached"


def test_a_retry_that_succeeds_is_used(tmp_path: Path) -> None:
    bad = {"action": "NOPE", "retry_delay_hours": None, "confidence": 0.5, "reasoning": "x"}
    client = StubClient(response(bad), response(VALID))
    decider = Decider(cache=ResponseCache(tmp_path), client=client)

    _, _, _, source = decider.decide(rec(), 0.44, NOW)

    assert source is AgentSource.LIVE
    assert len(client.calls) == 2


def test_an_api_error_falls_back_immediately(tmp_path: Path) -> None:
    client = StubClient(RuntimeError("connection reset"))
    decider = Decider(cache=ResponseCache(tmp_path), client=client)

    _, _, _, source = decider.decide(rec(), 0.44, NOW)

    assert source is AgentSource.FALLBACK
    assert len(client.calls) == 1, "an API error should not be retried by the decider"


def test_a_delay_on_a_non_scheduled_action_is_rejected(tmp_path: Path) -> None:
    bad = dict(VALID, action="RETRY_NOW", retry_delay_hours=72)
    client = StubClient(response(bad), response(bad))
    decider = Decider(cache=ResponseCache(tmp_path), client=client)

    _, _, _, source = decider.decide(rec(), 0.44, NOW)
    assert source is AgentSource.FALLBACK


def test_a_scheduled_action_without_a_delay_is_rejected(tmp_path: Path) -> None:
    bad = dict(VALID, retry_delay_hours=None)
    client = StubClient(response(bad), response(bad))
    decider = Decider(cache=ResponseCache(tmp_path), client=client)

    _, _, _, source = decider.decide(rec(), 0.44, NOW)
    assert source is AgentSource.FALLBACK


def test_overlong_reasoning_is_rejected(tmp_path: Path) -> None:
    bad = dict(VALID, reasoning="x" * (MAX_REASONING_CHARS + 1))
    client = StubClient(response(bad), response(bad))
    decider = Decider(cache=ResponseCache(tmp_path), client=client)

    _, _, _, source = decider.decide(rec(), 0.44, NOW)
    assert source is AgentSource.FALLBACK


# --------------------------------------------------------------------------------------
# No key at all — SPEC §4.3 layer 2
# --------------------------------------------------------------------------------------


def test_no_key_falls_back_without_touching_the_network(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    decider = Decider(cache=ResponseCache(tmp_path))

    assert decider.can_call_api is False
    _, _, _, source = decider.decide(rec(), 0.44, NOW)
    assert source is AgentSource.FALLBACK


def test_use_llm_false_never_calls_the_api(tmp_path: Path) -> None:
    """`--no-llm` must be the same pipeline with the agent switched off, not a new path."""
    client = StubClient(response(VALID))
    decider = Decider(cache=ResponseCache(tmp_path), client=client, use_llm=False)

    _, _, _, source = decider.decide(rec(), 0.44, NOW)

    assert source is AgentSource.FALLBACK
    assert client.calls == []


def test_the_cache_is_still_read_when_use_llm_is_false(tmp_path: Path) -> None:
    """A committed cache reproduces the run even in ablation mode."""
    cache = ResponseCache(tmp_path)
    record, score = rec(), 0.44
    cache.put(
        cache_key(record, score, model=MODEL),
        AgentDecision.from_dict(VALID),
        model=MODEL,
        record_json=canonical_record(record, score),
    )
    decider = Decider(cache=cache, client=StubClient(), use_llm=False)

    _, _, _, source = decider.decide(record, score, NOW)
    assert source is AgentSource.CACHE


# --------------------------------------------------------------------------------------
# SPEC §8.2 gate 4 — a hard rule vetoing an agent proposal
# --------------------------------------------------------------------------------------


def test_a_hard_rule_vetoes_an_agent_retry() -> None:
    """The demo's centrepiece, at unit level."""
    revoked = rec(failure_reason=FailureReason.REVOKED_MANDATE)

    decision = guardrails.validate_proposal(revoked, 0.71, NOW, Action.RETRY_NOW, None)

    assert decision.action is Action.STOP
    assert decision.vetoed_proposal is Action.RETRY_NOW
    assert "vetoed_agent_proposal" in decision.rules_fired


def test_the_agent_is_only_consulted_inside_its_band() -> None:
    """Outside BAND_AGENT_MIN..MAX the pipeline is deterministic (SPEC §2.3)."""
    for score in (0.0, BAND_AGENT_MIN - 0.01, BAND_AGENT_MAX, 0.99):
        decision = guardrails.evaluate(rec(), score, NOW)
        assert not decision.needs_agent, f"score {score} should not reach the agent"

    for score in (BAND_AGENT_MIN, 0.44, BAND_AGENT_MAX - 0.01):
        decision = guardrails.evaluate(rec(), score, NOW)
        assert decision.needs_agent, f"score {score} should reach the agent"


def test_a_hard_rule_record_never_reaches_the_agent() -> None:
    """Cheaper and safer: no API call is spent on a record a rule already settled."""
    for record in (
        rec(failure_reason=FailureReason.REVOKED_MANDATE),
        rec(attempt_number=4),
        rec(last_attempt_at=NOW - timedelta(hours=1)),
    ):
        assert guardrails.evaluate(record, 0.44, NOW).needs_agent is False
