import { useMemo } from "react";
import { count, rupees } from "../api";
import { OUTCOME, SOURCE, label } from "../labels";

// Score bands, SPEC §2.3. Mirrored from backend/app/policy.py BAND_LOW / BAND_HIGH.
const BAND_LOW = 0.15;
const BAND_HIGH = 0.65;

/**
 * The narrow segment where a model gets a say. The histogram is every record's first
 * score; the shaded band is the only region the agent is ever called for, and even there
 * its answer is re-validated against the rulebook before it executes.
 */
export default function Decider({ data, trace }) {
  const bins = trace?.score_histogram ?? [];
  const max = Math.max(...bins, 1);
  const sources = data.agent.sources;
  const agentRan = sources.live + sources.cache > 0;

  const vetoed = useMemo(
    () => data.failures.filter((f) => f.rules_fired.includes("vetoed_agent_proposal")),
    [data.failures],
  );
  const worst = vetoed.length
    ? vetoed.reduce((a, b) => (b.amount_paise > a.amount_paise ? b : a))
    : null;

  return (
    <section className="decider" aria-label="the decider">
      <header className="section-head">
        <h2>The decider</h2>
        <p>
          {count(data.agent.records_routed)} of {count(trace?.flow.input ?? data.config.n)}{" "}
          records scored inside the ambiguous band. Only those reach the agent.
        </p>
      </header>

      <div className="decider-grid">
        <figure className="scoreband">
          <div className="scoreband-plot" role="img" aria-label="distribution of recovery scores">
            <span className="band-shade" style={{ left: `${BAND_LOW * 100}%`, width: `${(BAND_HIGH - BAND_LOW) * 100}%` }} />
            {bins.map((n, i) => (
              <span
                key={i}
                className={`bin ${i / bins.length < BAND_LOW ? "bin-low" : i / bins.length < BAND_HIGH ? "bin-agent" : "bin-high"}`}
                style={{ height: `${(n / max) * 100}%` }}
                title={`${count(n)} records scored ${(i / bins.length).toFixed(2)}–${((i + 1) / bins.length).toFixed(2)}`}
              />
            ))}
          </div>
          <div className="scoreband-axis">
            <span style={{ left: 0 }}>0</span>
            <span style={{ left: `${BAND_LOW * 100}%` }}>{BAND_LOW}</span>
            <span style={{ left: `${BAND_HIGH * 100}%` }}>{BAND_HIGH}</span>
            <span style={{ left: "100%" }}>1</span>
          </div>
          <figcaption className="scoreband-bands">
            <span className="tone-slate">
              <b>{count(trace?.flow.band_low ?? 0)}</b> below {BAND_LOW} · stop
            </span>
            <span className="tone-blue">
              <b>{count(trace?.flow.agent_band ?? 0)}</b> in the band · agent decides
            </span>
            <span className="tone-blue-soft">
              <b>{count(trace?.flow.band_high ?? 0)}</b> at {BAND_HIGH} or above · retry now
            </span>
          </figcaption>
        </figure>

        <div className="decider-side">
          <ul className="sources" aria-label="how each decision was made">
            {Object.entries(sources).map(([k, v]) => (
              <li key={k} className={v ? "" : "zero"}>
                <b>{count(v)}</b> {SOURCE[k] ?? k}
              </li>
            ))}
          </ul>
          <p className="decider-note">
            {agentRan
              ? "Agent decisions replay byte-for-byte from the committed cache, so the run is reproducible without a key."
              : "No API key and an empty cache on this host: the deterministic fallback stood in for the agent on every routed record. The ₹ delta between the two is zero by construction — the ablation script says so rather than printing a zero that looks like a result."}
          </p>
        </div>
      </div>

      {worst && (
        <div className="veto">
          <p className="veto-eyebrow">A rule overrode a proposed retry</p>
          <p className="veto-lead">
            {count(vetoed.length)} {vetoed.length === 1 ? "record" : "records"} had a retry
            proposed and refused. The rule wins regardless of score.
          </p>
          <div className="veto-row">
            <code>{worst.row_id}</code>
            <span className="veto-amount">{rupees(worst.amount_paise)}</span>
            <span className="tag">score {worst.score.toFixed(3)}</span>
            <span className="tag tag-rose">{label(OUTCOME, worst.stopped_by)}</span>
          </div>
          {worst.agent_reasoning && <p className="veto-reason">“{worst.agent_reasoning}”</p>}
        </div>
      )}
    </section>
  );
}
