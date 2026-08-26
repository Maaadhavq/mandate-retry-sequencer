"""FastAPI surface for the mandate retry sequencer.

Gate A (SPEC §10.2): `/batch/run` returns the frozen §7.2 shape as a stub, so the frontend
can be built against a real contract before the pipeline exists. `run_batch()` is the one
function that gets replaced at Gate B — the response model does not change.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app import policy
from backend.app.schemas import (
    AgentSources,
    AgentStats,
    AttemptsBucket,
    BatchRunRequest,
    BatchRunResponse,
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
    """Placeholder with the correct shape and obviously-fake numbers.

    Deliberately not zeros: a dashboard built against all-zeros hides layout bugs that
    only appear once real figures arrive. Deliberately not plausible either — nobody
    should ever mistake a stub run for a real one.
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


@app.post("/batch/run", response_model=BatchRunResponse)
def run_batch(req: BatchRunRequest) -> BatchRunResponse:
    """Run a recovery campaign over a batch of failed mandate debits.

    STUB until Gate B. See SPEC §10.2.
    """
    return _stub_response(req)
