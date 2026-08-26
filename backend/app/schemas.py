"""The `/batch/run` response contract, SPEC §7.2.

This shape is FROZEN. The frontend is built against it and the batch runner replaces the
stub beneath it without changing it. Adding a field is cheap; renaming or removing one
costs a frontend rewrite, so do neither without editing SPEC §7.2 first.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RunConfig(BaseModel):
    n: int
    horizon_days: int
    use_llm: bool


class Totals(BaseModel):
    at_risk_paise: int
    recovered_paise: int
    recovery_rate: float
    attempts_per_recovery: float
    false_positive_cost_paise: int
    stopped_by_hard_rule: int


class CohortSlice(BaseModel):
    key: str
    at_risk_paise: int
    recovered_paise: int
    n: int


class Cohorts(BaseModel):
    by_failure_reason: list[CohortSlice]
    by_merchant_category: list[CohortSlice]


class AttemptsBucket(BaseModel):
    attempts: int
    count: int


class Promises(BaseModel):
    made: int
    kept: int
    broken: int
    recovered_paise: int


class FailureRow(BaseModel):
    """One record the pipeline did not recover. SPEC §2.5 panel 3 renders every one of these."""

    row_id: str
    amount_paise: int
    stopped_by: str
    rules_fired: list[str]
    score: float
    agent_reasoning: str


class AgentSources(BaseModel):
    live: int
    cache: int
    fallback: int
    deterministic: int


class AgentStats(BaseModel):
    records_routed: int
    sources: AgentSources


class BatchRunResponse(BaseModel):
    run_id: str
    seed: int
    config: RunConfig
    totals: Totals
    cohorts: Cohorts
    attempts_histogram: list[AttemptsBucket]
    promises: Promises
    failures: list[FailureRow]
    agent: AgentStats


class BatchRunRequest(BaseModel):
    #: Non-negative: numpy's Generator rejects a negative seed with a bare ValueError,
    #: which surfaced as a 500 and a stack trace rather than a message. Validation belongs
    #: at the boundary, so a bad seed is a 422 that says what was wrong.
    seed: int = Field(default=42, ge=0)
    n: int = Field(default=500, ge=1, le=100_000)
    use_llm: bool = True


class HealthResponse(BaseModel):
    status: str
    version: str
