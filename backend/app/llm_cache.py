"""F5 layer 1 — the committed response cache. SPEC §4.3.

This is what reconciles an LLM in the money path with the rule that every ₹ figure be
reproducible by running a script. Each decision is stored under
`sha256(model + policy_version + canonical_record_json)` and **committed to the repo**, so a
judge who clones with no API key replays every decision and reproduces the video's totals
byte for byte.

Two properties make that work:

- The key is content-addressed, so it cannot drift from what produced it. `POLICY_VERSION`
  is part of the hash, which means editing the prompt or the schema invalidates every stale
  entry instead of silently replaying decisions made under different rules.
- The canonical JSON is sorted and separator-normalised. An unsorted `json.dumps` would
  make the key depend on dict insertion order, and the cache would miss on a rerun of the
  same batch for no visible reason.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from backend.app.models import MandateRecord
from backend.app.policy import POLICY_VERSION, Action

DEFAULT_CACHE_DIR: Final[Path] = Path("cache/llm")


@dataclass(frozen=True, slots=True)
class AgentDecision:
    """What the agent proposed. Not yet validated against the guardrails."""

    action: Action
    retry_delay_hours: int | None
    confidence: float
    reasoning: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["action"] = self.action.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "AgentDecision":
        return cls(
            action=Action(payload["action"]),
            retry_delay_hours=payload.get("retry_delay_hours"),
            confidence=float(payload["confidence"]),
            reasoning=str(payload["reasoning"]),
        )


def canonical_record(record: MandateRecord, score: float) -> str:
    """The exact bytes that go into the cache key.

    `score` is rounded to 4 decimals on purpose. A float that differs in the fifteenth
    significant digit between two runs is the same decision to any human, but would be a
    cache miss and a fresh API call — turning a reproducible replay into a live one.
    """
    return json.dumps(
        {
            "row_id": record.row_id,
            "failure_reason": record.failure_reason.value,
            "merchant_category": record.merchant_category.value,
            "days_to_payday": record.days_to_payday,
            "attempt_number": record.attempt_number,
            "ticket_size_paise": record.ticket_size_paise,
            "days_since_last_success": record.days_since_last_success,
            "mandate_age_days": record.mandate_age_days,
            "score": round(float(score), 4),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def cache_key(record: MandateRecord, score: float, *, model: str) -> str:
    material = f"{model}|{POLICY_VERSION}|{canonical_record(record, score)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ResponseCache:
    """Content-addressed store for agent decisions.

    Reads never fail the pipeline: a corrupt or hand-edited entry is treated as a miss, so
    a bad file in `cache/llm/` degrades to a live call or the fallback rather than taking
    the batch down.
    """

    def __init__(self, directory: Path | str = DEFAULT_CACHE_DIR) -> None:
        self.directory = Path(directory)
        self.hits = 0
        self.misses = 0
        self.writes = 0

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> AgentDecision | None:
        path = self.path_for(key)
        if not path.exists():
            self.misses += 1
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            decision = AgentDecision.from_dict(payload["decision"])
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            # A damaged entry is a miss, never a crash. See the class docstring.
            self.misses += 1
            return None
        self.hits += 1
        return decision

    def put(self, key: str, decision: AgentDecision, *, model: str, record_json: str) -> None:
        """Write an entry. Sorted keys and a trailing newline keep diffs reviewable."""
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "key": key,
            "model": model,
            "policy_version": POLICY_VERSION,
            "record": json.loads(record_json),
            "decision": decision.to_dict(),
        }
        with self.path_for(key).open("w", encoding="utf-8", newline="") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        self.writes += 1
