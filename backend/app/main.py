"""FastAPI surface for the mandate retry sequencer.

The `/batch/run` response shape (SPEC §7.2) was frozen before the pipeline existed, so the
frontend could be built against a real contract. `run_batch()` was the one function replaced
when the real pipeline landed — the response model never changed.
"""

from __future__ import annotations

import os
import threading
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
    TraceResponse,
)

VERSION = "0.1.0"

#: One campaign at a time. `run_campaign` truncates and rewrites `data/ledger.jsonl`; two
#: concurrent runs would interleave their rows and every downstream figure would double.
#: Sync endpoints run on a threadpool, so this is a real race on a shared host.
_RUN_LOCK = threading.Lock()

app = FastAPI(
    title="Mandate Retry Sequencer",
    version=VERSION,
    description="Bounded recovery workflow for failed UPI Autopay mandate debits.",
)

# The dashboard runs on the Vite dev server during development and on Render when deployed.
# `CORS_ORIGINS` (comma-separated) adds any other origin without a code change.
_extra_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", *_extra_origins],
    allow_origin_regex=r"https://.*\.onrender\.com",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=VERSION)


def _stub_response(req: BatchRunRequest) -> BatchRunResponse:
    """Skeleton-phase placeholder. Retained only as the frozen-shape reference for tests.

    No longer served: `/batch/run` runs the real pipeline. Kept because a
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
                agent_reasoning="Stub row. Replaced in production by a real ledger read.",
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
    On a clone with no API key — the default configuration — this is the whole
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


@app.get("/batch/trace", response_model=TraceResponse)
def trace() -> TraceResponse:
    """Where the debits went. Derived from the ledger the last `/batch/run` wrote.

    The dashboard's pipeline view — rule fire-counts, the hour-of-day clock, the 14-day
    curve — comes from here. It is a second read of the same append-only rows, so nothing on
    it can disagree with the headline without the ledger itself being wrong.
    """
    from backend.app.trace import load_rows, summarise

    try:
        with _RUN_LOCK:
            rows = load_rows()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return TraceResponse.model_validate(summarise(rows))


@app.post("/batch/run", response_model=BatchRunResponse)
def run_batch(req: BatchRunRequest) -> BatchRunResponse:
    """Run a recovery campaign over a batch of failed mandate debits.

    The stub beneath this is gone and the shape above it did not change, which was
    the point of freezing §7.2 before any of it existed.

    Missing artefacts surface as a 503 carrying the command that fixes them. Anyone who
    clones the repo and calls this before generating data should get a sentence, not a
    stack trace (SPEC §8.4).
    """
    from backend.app.runner import run_campaign

    try:
        with _RUN_LOCK:
            payload = run_campaign(seed=req.seed, n=req.n, use_llm=req.use_llm)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return BatchRunResponse.model_validate(payload)
