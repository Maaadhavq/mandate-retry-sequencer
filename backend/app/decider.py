"""F5 — the decider agent. SPEC §4.

The agent decides only inside the ambiguous band, and only on records where no hard rule
fired. Everything it proposes goes back through `guardrails.validate_proposal` before it can
execute, so the model's judgment is bounded by the same rules a score is.

Three layers of reproducibility (SPEC §4.3), tried in order:

1. the committed response cache — a clone with no key replays the run byte for byte
2. a live API call, if a key is present
3. `decide_fallback` — pure, tested, no network

A miss with no key is layer 3, logged as `agent_source: "fallback"`. It is never a crash and
never a silent substitution: every ledger row records which layer decided it.

Prompting notes that are load-bearing rather than stylistic:

- The policy prompt is byte-identical on every call and carries the `cache_control`
  breakpoint. Per-record data goes in the user message, after the breakpoint. Anything
  volatile in the prefix — a timestamp, a row_id, a run counter — silently drops the cache
  hit rate to zero, so `usage.cache_read_input_tokens` is asserted in the tests.
- No `temperature`. It is removed on current models and returns a 400; determinism here
  comes from the cache, not from sampling parameters.
- No thinking configuration, and `max_tokens` is small. This is a bounded classification
  with a one-sentence justification, not a reasoning task.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Final

from backend.app import guardrails
from backend.app.env import load_env
from backend.app.llm_cache import AgentDecision, ResponseCache, cache_key, canonical_record
from backend.app.models import MandateRecord
from backend.app.policy import (
    BAND_AGENT_MAX,
    BAND_AGENT_MIN,
    COOLING_PERIOD_HOURS,
    MAX_ATTEMPTS,
    RETRY_WINDOWS_HOURS,
    Action,
    AgentSource,
)

MODEL: Final[str] = "claude-haiku-4-5"
MAX_TOKENS: Final[int] = 256
MAX_REASONING_CHARS: Final[int] = 200

#: Strict schema. `additionalProperties: false` plus a full `required` list is what makes
#: the response validate exactly rather than approximately.
DECISION_SCHEMA: Final[dict] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "retry_delay_hours", "confidence", "reasoning"],
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                Action.RETRY_NOW.value,
                Action.RETRY_SCHEDULED.value,
                Action.DUNNING_P2P.value,
                Action.STOP.value,
            ],
        },
        "retry_delay_hours": {
            "type": ["integer", "null"],
            "enum": [*RETRY_WINDOWS_HOURS, None],
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string", "maxLength": MAX_REASONING_CHARS},
    },
}

#: Byte-identical on every call. Nothing per-record, per-run, or time-dependent may appear
#: here — see the module docstring on why that would cost the cache.
SYSTEM_PROMPT: Final[str] = f"""\
You are the intervention decider for a UPI Autopay mandate recovery system in India.

You are consulted ONLY for records whose recovery score is genuinely ambiguous \
({BAND_AGENT_MIN} <= score < {BAND_AGENT_MAX}) and where no hard compliance rule has fired. \
Clear-cut records never reach you.

Choose ONE action:

- RETRY_NOW - attempt the debit immediately. Best when the balance is likely present now, \
typically at or just after payday.
- RETRY_SCHEDULED - book a retry at 24, 72, or 168 hours. Best when waiting materially \
improves the odds, most often when payday is a few days out. Set retry_delay_hours.
- DUNNING_P2P - stop debiting and capture a promise to pay. Best for larger tickets where \
another failed attempt costs money and goodwill, or where the mandate looks dormant.
- STOP - write the record off. Best when further spend is unlikely to be recovered.

What matters:

- Ticket size changes the calculus. A small renewal is worth a cheap immediate retry; a \
large invoice is worth a promise rather than a third failed debit.
- days_to_payday matters most for insufficient_balance and barely at all for \
technical_decline, which is usually transient and worth retrying sooner.
- A high attempt_number means the mandate has already resisted several attempts. The cap is \
{MAX_ATTEMPTS} total; attempts are not free.
- A long days_since_last_success suggests a dormant mandate rather than a temporary shortfall.

Constraints:

- retry_delay_hours must be null unless the action is RETRY_SCHEDULED, and otherwise one of \
{list(RETRY_WINDOWS_HOURS)}.
- Attempts are separated by at least {COOLING_PERIOD_HOURS:.0f} hours regardless of what you choose.
- reasoning must be ONE sentence under {MAX_REASONING_CHARS} characters, stating the specific \
feature that drove the call. It is written to an audit ledger a payments team reads.

Your proposal is re-validated against the compliance rules before it executes. A proposal \
that violates one is discarded and recorded as vetoed, so answer on the merits.

Worked examples. These are the boundaries, not a lookup table - reason from the same \
tradeoffs when a record sits between them.

1. score 0.44, insufficient_balance, ott, 4,900 paise, days_to_payday 1, attempt 1 of 4, \
days_since_last_success 12.
   -> RETRY_NOW. A tiny renewal one day from payday: the balance is about to arrive, and an \
attempt costs almost nothing relative to the ticket. Waiting buys nothing here.

2. score 0.44, insufficient_balance, saas, 3,900,000 paise, days_to_payday 6, attempt 2 of 4, \
days_since_last_success 40.
   -> RETRY_SCHEDULED, retry_delay_hours 168. Same score, opposite call. The ticket is large \
enough that a second failed debit is real money and a real signal to the customer, and payday \
is far enough out that landing after it is worth the wait.

3. score 0.29, technical_decline, fitness, 120,000 paise, days_to_payday 22, attempt 1 of 4, \
days_since_last_success 8.
   -> RETRY_NOW. technical_decline is usually transient and mostly indifferent to payday, so \
the 22 days are close to irrelevant. Retry while the mandate is clearly still active.

4. score 0.31, insufficient_balance, edtech, 2,400,000 paise, days_to_payday 9, attempt 3 of 4, \
days_since_last_success 95.
   -> DUNNING_P2P. Third attempt, dormant for three months, large ticket. One attempt remains \
and spending it blind is worse than asking for a commitment; a promise converts a likely \
write-off into a dated obligation.

5. score 0.18, revoked_mandate is impossible here (a hard rule would have caught it), but \
score 0.18, insufficient_balance, utilities, 890,000 paise, attempt 4 of 4, \
days_since_last_success 150.
   -> STOP. The mandate has resisted every attempt, has not succeeded in five months, and the \
score is at the floor of your range. Further spend is not recoverable; write it off cleanly \
so the ledger says so.

Writing the reasoning field. It is not a label - it is the audit trail. A payments operations \
team reads these rows when a merchant asks why a particular mandate was handled the way it \
was, and the same text is rendered on the recovery dashboard.

- Name the specific feature and its value, not the score. "payday in 1 day on a 49 rupee \
renewal" is useful; "score suggests retry" is not, because the score is already on the row.
- Say what the alternative would have cost when that is the actual reason. "third attempt on a \
dormant mandate - a promise beats spending the last attempt blind" explains a choice; \
"DUNNING_P2P selected" restates it.
- One sentence, no hedging, no restating the inputs back in full. Under \
{MAX_REASONING_CHARS} characters.
- Do not claim certainty you do not have. These are ambiguous records by construction; \
"likely" and "worth" are honest words here.

Read the whole record before deciding. The score got the case to you; it is the features that \
tell you what to do with it."""


def _user_prompt(record: MandateRecord, score: float) -> str:
    """Per-record data only. Everything here sits after the cache breakpoint."""
    return (
        f"score: {score:.4f}\n"
        f"failure_reason: {record.failure_reason.value}\n"
        f"merchant_category: {record.merchant_category.value}\n"
        f"ticket_size_paise: {record.ticket_size_paise}\n"
        f"days_to_payday: {record.days_to_payday}\n"
        f"attempt_number: {record.attempt_number} of {MAX_ATTEMPTS}\n"
        f"days_since_last_success: {record.days_since_last_success}\n"
        f"mandate_age_days: {record.mandate_age_days}\n"
    )


class Decider:
    """Cache → live call → deterministic fallback, in that order.

    `use_llm=False` or a missing key skips straight to the cache and then the fallback, which
    is what makes `--no-llm` a real ablation rather than a different code path.
    """

    def __init__(
        self,
        *,
        cache: ResponseCache | None = None,
        client=None,
        use_llm: bool = True,
        model: str = MODEL,
    ) -> None:
        self.cache = cache or ResponseCache()
        self.model = model
        self.use_llm = use_llm
        self._client = client
        self._client_failed = False
        self.last_usage: dict | None = None
        self.cache_read_tokens_total = 0

    # -- client ------------------------------------------------------------------------

    @property
    def client(self):
        """Constructed lazily so importing this module never needs a key."""
        if self._client is None and not self._client_failed:
            # A `.env` is the documented way to supply the key (`.env.example`). Loaded
            # here rather than at import so the module still needs nothing to be importable.
            load_env()
            if not os.environ.get("ANTHROPIC_API_KEY"):
                self._client_failed = True
                return None
            try:
                import anthropic

                self._client = anthropic.Anthropic()
            except Exception:
                self._client_failed = True
                return None
        return self._client

    @property
    def can_call_api(self) -> bool:
        return self.use_llm and self.client is not None

    # -- the three layers ---------------------------------------------------------------

    def decide(
        self, record: MandateRecord, score: float, now: datetime
    ) -> tuple[Action, int | None, str, AgentSource]:
        """Return `(action, retry_delay_hours, reasoning, source)`.

        The action is a *proposal*. The caller must pass it through
        `guardrails.validate_proposal` before executing it (SPEC §4.1, §8.2 gate 4).
        """
        key = cache_key(record, score, model=self.model)

        cached = self.cache.get(key)
        if cached is not None:
            return cached.action, cached.retry_delay_hours, cached.reasoning, AgentSource.CACHE

        if self.can_call_api:
            live = self._call_api(record, score)
            if live is not None:
                self.cache.put(
                    key,
                    live,
                    model=self.model,
                    record_json=canonical_record(record, score),
                )
                return live.action, live.retry_delay_hours, live.reasoning, AgentSource.LIVE

        fallback = guardrails.decide_fallback(record, score)
        return (
            fallback.action,
            fallback.retry_delay_hours,
            "deterministic fallback: no cache entry and no live agent available",
            AgentSource.FALLBACK,
        )

    def _call_api(self, record: MandateRecord, score: float) -> AgentDecision | None:
        """One live call, retried once on a schema-invalid response. SPEC §4.4.

        Any API error falls through to the caller's fallback immediately — the pipeline
        never blocks on the API and never fails a batch because of it.
        """
        for attempt in (1, 2):
            try:
                response = self.client.messages.parse(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=[
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": _user_prompt(record, score)}],
                    output_config={
                        "format": {
                            "type": "json_schema",
                            "schema": DECISION_SCHEMA,
                        }
                    },
                )
            except Exception:
                # Network error, rate limit, timeout, auth — all the same to the pipeline.
                return None

            self._record_usage(response)
            parsed = self._extract(response)
            if parsed is not None:
                return parsed
            if attempt == 2:
                return None
        return None

    def _record_usage(self, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        self.cache_read_tokens_total += read
        self.last_usage = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_creation_input_tokens": int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            ),
            "cache_read_input_tokens": read,
        }

    @staticmethod
    def _extract(response) -> AgentDecision | None:
        """Pull the parsed object out, tolerating either shape the SDK may return."""
        payload = getattr(response, "parsed_output", None)
        if payload is None:
            payload = getattr(response, "parsed", None)
        if payload is None:
            # Fall back to the raw text block; still validated by from_dict below.
            import json

            text = next(
                (b.text for b in getattr(response, "content", []) if getattr(b, "type", "") == "text"),
                None,
            )
            if not text:
                return None
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return None

        if not isinstance(payload, dict):
            payload = getattr(payload, "__dict__", None) or None
            if payload is None:
                return None

        try:
            decision = AgentDecision.from_dict(payload)
        except (KeyError, ValueError, TypeError):
            return None

        if decision.action is Action.RETRY_SCHEDULED:
            if decision.retry_delay_hours not in RETRY_WINDOWS_HOURS:
                return None
        elif decision.retry_delay_hours is not None:
            return None
        if not 0.0 <= decision.confidence <= 1.0:
            return None
        if len(decision.reasoning) > MAX_REASONING_CHARS:
            return None
        return decision
