import { useState } from "react";
import { runBatch, rupees, percent } from "./api";

/**
 * Gate A shell (SPEC §10.2): headline + six stat cards, rendered off the frozen
 * /batch/run contract. Panels 1-4 from SPEC §2.5 land here once Gate B produces
 * real ledger data.
 */
export default function App() {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState(null);

  async function handleRun() {
    setStatus("running");
    setError(null);
    try {
      setData(await runBatch());
      setStatus("done");
    } catch (e) {
      setError(e.message);
      setStatus("error");
    }
  }

  const t = data?.totals;
  const isStub = data?.failures?.[0]?.stopped_by === "STUB";

  return (
    <div className="page">
      <header className="masthead">
        <div>
          <p className="eyebrow">Razorpay AI Buildathon · Track 03</p>
          <h1>Mandate Retry Sequencer</h1>
          <p className="sub">
            Bounded recovery workflow for failed UPI Autopay mandate debits.
          </p>
        </div>
        <button className="run" onClick={handleRun} disabled={status === "running"}>
          {status === "running" ? "Running…" : "Run batch"}
        </button>
      </header>

      {status === "idle" && (
        <p className="empty">Run a batch to see recovery figures.</p>
      )}

      {error && (
        <div className="error">
          <strong>Could not reach the API.</strong> {error}
          <br />
          Is the backend running on :8000?
        </div>
      )}

      {data && (
        <>
          {isStub && (
            <div className="stub-banner">
              Stub data — the pipeline is not wired yet (Gate B). These numbers are fake
              on purpose.
            </div>
          )}

          <section className="headline">
            <p className="eyebrow">Recovered against at risk</p>
            <div className="headline-figures">
              <span className="big">{rupees(t.recovered_paise)}</span>
              <span className="of">of {rupees(t.at_risk_paise)}</span>
            </div>
            <div className="bar">
              <div
                className="fill"
                style={{ width: `${Math.min(100, t.recovery_rate * 100)}%` }}
              />
            </div>
          </section>

          <section className="cards">
            <Stat label="At risk" value={rupees(t.at_risk_paise, { compact: true })} />
            <Stat label="Recovered" value={rupees(t.recovered_paise, { compact: true })} accent />
            <Stat label="Recovery rate" value={percent(t.recovery_rate)} />
            <Stat label="Attempts per recovery" value={t.attempts_per_recovery.toFixed(2)} />
            <Stat label="Stopped by a hard rule" value={t.stopped_by_hard_rule} />
            <Stat
              label="Spent on failed retries"
              value={rupees(t.false_positive_cost_paise, { compact: true })}
              warn
            />
          </section>

          <section className="agent-strip">
            <span className="eyebrow">Agent</span>
            <span>{data.agent.records_routed} records routed</span>
            {Object.entries(data.agent.sources).map(([k, v]) => (
              <span key={k} className="pill">
                {k} {v}
              </span>
            ))}
          </section>

          <p className="todo">
            Panels 1–4 (cohorts, attempts histogram, honest failures, false-positive cost)
            render here once Gate B lands. SPEC §2.5.
          </p>
        </>
      )}
    </div>
  );
}

function Stat({ label, value, accent, warn }) {
  return (
    <div className={`card${accent ? " accent" : ""}${warn ? " warn" : ""}`}>
      <p className="eyebrow">{label}</p>
      <p className="value">{value}</p>
    </div>
  );
}
