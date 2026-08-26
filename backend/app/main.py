"""FastAPI surface for the mandate retry sequencer.

Gate A (SPEC §10.2): `/batch/run` returns the frozen §7.2 shape as a stub, so the frontend
can be built against a real contract before the pipeline exists. `run_batch()` is the one
function that gets replaced at Gate B — the response model does not change.
"""

from __future__ import annotations

import uuid
from functools import lru_cache

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.app import policy
from backend.app.schemas import (
    AgentSources,
    AgentStats,
    AttemptsBucket,
    BatchRunRequest,
    BatchRunResponse,
    ExplainResponse,
    CohortSlice,
    Cohorts,
    FailureRow,
    HealthResponse,
    Promises,
    RunConfig,
    Totals,
)

VERSION = "0.1.0"

app = FastAPI(
    title="Mandate Retry Sequencer",
    version=VERSION,
    description="Bounded recovery workflow for failed UPI Autopay mandate debits.",
)

# The dashboard runs on the Vite dev server during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=VERSION)


def _stub_response(req: BatchRunRequest) -> BatchRunResponse:
    """Gate-A placeholder. Retained only as the frozen-shape reference for tests.

    No longer served: `/batch/run` runs the real pipeline as of Gate B. Kept because a
    test asserts the live response still matches this shape field for field, which is how
    the §7.2 freeze stays enforced rather than merely promised.
    """
    return BatchRunResponse(
        run_id=f"run_{uuid.uuid4().hex[:8]}",
        seed=req.seed,
        config=RunConfig(n=req.n, horizon_days=policy.HORIZON_DAYS, use_llm=req.use_llm),
        totals=Totals(
            at_risk_paise=11_111_100,
            recovered_paise=4_444_400,
            recovery_rate=0.4,
            attempts_per_recovery=1.5,
            false_positive_cost_paise=11_100,
            stopped_by_hard_rule=11,
        ),
        cohorts=Cohorts(
            by_failure_reason=[
                CohortSlice(key=r.value, at_risk_paise=3_703_700, recovered_paise=1_481_400, n=111)
                for r in policy.FailureReason
            ],
            by_merchant_category=[
                CohortSlice(key=c.value, at_risk_paise=2_222_220, recovered_paise=888_880, n=66)
                for c in policy.MerchantCategory
            ],
        ),
        attempts_histogram=[AttemptsBucket(attempts=i, count=11) for i in range(1, 5)],
        promises=Promises(made=11, kept=6, broken=5, recovered_paise=111_100),
        failures=[
            FailureRow(
                row_id="mrs_stub01",
                amount_paise=111_100,
                stopped_by="STUB",
                rules_fired=["stub_no_pipeline_yet"],
                score=0.11,
                agent_reasoning="Stub row. Replaced at Gate B by a real ledger read.",
            )
        ],
        agent=AgentStats(
            records_routed=0,
            sources=AgentSources(live=0, cache=0, fallback=0, deterministic=0),
        ),
    )


@lru_cache(maxsize=1)
def _explainer():
    """Built once. Constructing a TreeExplainer walks the whole forest."""
    from backend.app.explain import Explainer

    return Explainer()


@lru_cache(maxsize=1)
def _batch_by_id() -> dict:
    from backend.app.runner import load_batch

    return {r.row_id: r for r in load_batch()}


@app.get("/explain/{row_id}", response_model=ExplainResponse)
def explain(row_id: str) -> ExplainResponse:
    """Why the scorer gave this record the score it did. SPEC §7, F9.

    Exists because the agent's own reasoning is only populated when the agent actually runs.
    On a clone with no API key — the configuration a judge will use — this is the whole
    explanation layer, and it needs no network.
    """
    try:
        records = _batch_by_id()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    record = records.get(row_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"{row_id} is not in the current batch ({len(records)} records).",
        )

    return ExplainResponse.model_validate(_explainer().explain(record).to_dict())


@app.post("/batch/run", response_model=BatchRunResponse)
def run_batch(req: BatchRunRequest) -> BatchRunResponse:
    """Run a recovery campaign over a batch of failed mandate debits.

    Gate B: the stub beneath this is gone and the shape above it did not change, which was
    the point of freezing §7.2 before any of it existed.

    Missing artefacts surface as a 503 carrying the command that fixes them. A judge who
    clones the repo and calls this before generating data should get a sentence, not a
    stack trace (SPEC §8.4).
    """
    from backend.app.runner import run_campaign

    try:
        payload = run_campaign(seed=req.seed, n=req.n, use_llm=req.use_llm)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return BatchRunResponse.model_validate(payload)
