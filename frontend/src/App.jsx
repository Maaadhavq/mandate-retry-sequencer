import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { explain, runBatch, rupees, percent } from "./api";

/**
 * The dashboard. SPEC §2.5: headline, six stat cards, four panels.
 *
 * The honest-failures panel renders every unrecovered record, sorted by rupees
 * descending, and is deliberately never paginated or collapsed by default. It is the
 * part of the submission that says what the system did not manage to do.
 */
export default function App() {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState(null);
  const [useLlm, setUseLlm] = useState(true);

  async function handleRun() {
    setStatus("running");
    setError(null);
    try {
      setData(await runBatch({ useLlm }));
      setStatus("done");
    } catch (e) {
      setError(e.message);
      setStatus("error");
    }
  }

  const t = data?.totals;

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
        <div className="controls">
          <label className="toggle">
            <input
              type="checkbox"
              checked={useLlm}
              onChange={(e) => setUseLlm(e.target.checked)}
            />
            <span>Agent on ambiguous band</span>
          </label>
          <button className="run" onClick={handleRun} disabled={status === "running"}>
            {status === "running" ? "Running…" : "Run batch"}
          </button>
        </div>
      </header>

      {status === "idle" && <p className="empty">Run a batch to see recovery figures.</p>}

      {error && (
        <div className="error">
          <strong>Could not reach the API.</strong> {error}
          <br />
          Is the backend running on :8000?
        </div>
      )}

      {data && (
        <>
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
            <Stat
              label="Recovered"
              value={rupees(t.recovered_paise, { compact: true })}
              accent
            />
            <Stat label="Recovery rate" value={percent(t.recovery_rate)} />
            <Stat
              label="Attempts per recovery"
              value={t.attempts_per_recovery.toFixed(2)}
            />
            <Stat label="Stopped by a hard rule" value={t.stopped_by_hard_rule} />
            <Stat
              label="Spent on failed retries"
              value={rupees(t.false_positive_cost_paise, { compact: true })}
              warn
            />
          </section>

          <AgentStrip agent={data.agent} config={data.config} />
          <VetoPanel failures={data.failures} />

          <div className="panels">
            <CohortPanel
              title="Recovery by failure reason"
              rows={data.cohorts.by_failure_reason}
            />
            <CohortPanel
              title="Recovery by merchant category"
              rows={data.cohorts.by_merchant_category}
            />
          </div>

          <div className="panels">
            <AttemptsPanel buckets={data.attempts_histogram} />
            <PromisesPanel promises={data.promises} />
          </div>

          <FailuresPanel failures={data.failures} atRisk={t.at_risk_paise} />
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

function AgentStrip({ agent, config }) {
  return (
    <section className="agent-strip">
      <span className="eyebrow">Agent</span>
      <span>{agent.records_routed} records routed</span>
      {Object.entries(agent.sources).map(([k, v]) => (
        <span key={k} className="pill">
          {k} {v}
        </span>
      ))}
      <span className="pill">{config.use_llm ? "use_llm on" : "use_llm off"}</span>
      <span className="pill">horizon {config.horizon_days}d</span>
    </section>
  );
}

/**
 * The moment the whole submission rests on: a rule overriding a proposed retry.
 * Surfaced above the fold rather than buried in the failures table, because a judge
 * asking "show me where a rule beat the model" should not have to scroll for it.
 */
function VetoPanel({ failures }) {
  const vetoed = useMemo(
    () => failures.filter((f) => f.rules_fired.includes("vetoed_agent_proposal")),
    [failures],
  );
  if (vetoed.length === 0) return null;

  const worst = vetoed.reduce((a, b) => (b.amount_paise > a.amount_paise ? b : a));

  return (
    <section className="veto">
      <p className="eyebrow">A hard rule overrode a proposed retry</p>
      <p className="veto-lead">
        {vetoed.length} {vetoed.length === 1 ? "record" : "records"} had a retry proposed
        and refused by a compliance rule. The rule wins regardless of score.
      </p>
      <div className="veto-row">
        <code>{worst.row_id}</code>
        <span className="veto-amount">{rupees(worst.amount_paise)}</span>
        <span className="pill">score {worst.score.toFixed(3)}</span>
        <span className="pill crit">{worst.stopped_by}</span>
      </div>
      {worst.agent_reasoning && <p className="reasoning">“{worst.agent_reasoning}”</p>}
    </section>
  );
}

function CohortPanel({ title, rows }) {
  const chart = rows.map((r) => ({
    key: r.key,
    rate: r.at_risk_paise ? (r.recovered_paise / r.at_risk_paise) * 100 : 0,
    recovered: r.recovered_paise / 100,
    atRisk: r.at_risk_paise / 100,
    n: r.n,
  }));

  return (
    <section className="panel">
      <p className="eyebrow">{title}</p>
      <ResponsiveContainer width="100%" height={230}>
        <BarChart data={chart} margin={{ top: 16, right: 8, bottom: 4, left: 8 }}>
          <CartesianGrid stroke="#26333a" vertical={false} />
          <XAxis
            dataKey="key"
            tick={{ fill: "#8fa3a9", fontSize: 11 }}
            axisLine={{ stroke: "#26333a" }}
            tickLine={false}
          />
          <YAxis
            unit="%"
            tick={{ fill: "#8fa3a9", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            cursor={{ fill: "#1d272c" }}
            contentStyle={{
              background: "#171f23",
              border: "1px solid #26333a",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(v, _n, p) => [
              `${v.toFixed(1)}%  ·  ₹${p.payload.recovered.toLocaleString("en-IN", {
                maximumFractionDigits: 0,
              })} of ₹${p.payload.atRisk.toLocaleString("en-IN", {
                maximumFractionDigits: 0,
              })}  ·  n=${p.payload.n}`,
              "recovery rate",
            ]}
          />
          <Bar dataKey="rate" radius={[4, 4, 0, 0]}>
            {chart.map((row) => (
              <Cell
                key={row.key}
                fill={row.key === "revoked_mandate" ? "#e08c76" : "#5fbb90"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}

function AttemptsPanel({ buckets }) {
  return (
    <section className="panel">
      <p className="eyebrow">Debit attempts per recovered record</p>
      <ResponsiveContainer width="100%" height={230}>
        <BarChart data={buckets} margin={{ top: 16, right: 8, bottom: 4, left: 8 }}>
          <CartesianGrid stroke="#26333a" vertical={false} />
          <XAxis
            dataKey="attempts"
            tick={{ fill: "#8fa3a9", fontSize: 11 }}
            axisLine={{ stroke: "#26333a" }}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "#8fa3a9", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            cursor={{ fill: "#1d272c" }}
            contentStyle={{
              background: "#171f23",
              border: "1px solid #26333a",
              borderRadius: 8,
              fontSize: 12,
            }}
          />
          <Bar dataKey="count" fill="#5fbb90" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}

function PromisesPanel({ promises }) {
  const total = promises.made || 1;
  return (
    <section className="panel">
      <p className="eyebrow">Promise to pay</p>
      <div className="promise-figures">
        <div>
          <span className="promise-big">{promises.made}</span>
          <span className="promise-label">captured</span>
        </div>
        <div>
          <span className="promise-big accent-text">{promises.kept}</span>
          <span className="promise-label">kept</span>
        </div>
        <div>
          <span className="promise-big crit-text">{promises.broken}</span>
          <span className="promise-label">broken</span>
        </div>
      </div>
      <div className="bar promise-bar">
        <div className="fill" style={{ width: `${(promises.kept / total) * 100}%` }} />
      </div>
      <p className="promise-note">
        {rupees(promises.recovered_paise)} recovered via promises. A broken promise
        re-enters the pipeline with the attempt counter incremented, so it cannot be used
        to walk around the cap.
      </p>
    </section>
  );
}

/**
 * SPEC §2.5 panel 3. Every unrecovered record, largest rupees first.
 * Not paginated, not collapsed — that is the point of it.
 */
function FailuresPanel({ failures, atRisk }) {
  const [filter, setFilter] = useState("all");

  const reasons = useMemo(() => {
    const counts = new Map();
    for (const f of failures) counts.set(f.stopped_by, (counts.get(f.stopped_by) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [failures]);

  const shown = filter === "all" ? failures : failures.filter((f) => f.stopped_by === filter);
  const leftOnTable = shown.reduce((sum, f) => sum + f.amount_paise, 0);

  return (
    <section className="panel failures">
      <div className="failures-head">
        <div>
          <p className="eyebrow">Honest failures</p>
          <p className="failures-sub">
            {shown.length} unrecovered · {rupees(leftOnTable)} left on the table ·{" "}
            {percent(leftOnTable / atRisk)} of everything at risk
          </p>
        </div>
        <div className="filters">
          <button
            className={filter === "all" ? "chip on" : "chip"}
            onClick={() => setFilter("all")}
          >
            all {failures.length}
          </button>
          {reasons.map(([reason, count]) => (
            <button
              key={reason}
              className={filter === reason ? "chip on" : "chip"}
              onClick={() => setFilter(reason)}
            >
              {reason} {count}
            </button>
          ))}
        </div>
      </div>

      <p className="failures-hint">
        Click any row for the SHAP explanation of its score. No API key needed.
      </p>

      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Record</th>
              <th className="num">Left on the table</th>
              <th className="num">Score</th>
              <th>Stopped by</th>
              <th>Rules fired</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((f) => (
              <FailureRow key={f.row_id} failure={f} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/**
 * One failure, expanding to its SHAP explanation on click.
 *
 * This is the explanation layer that works with no API key — the agent's own reasoning is
 * only populated once it has actually run, so on a fresh clone this is all there is.
 */
function FailureRow({ failure: f }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [failed, setFailed] = useState(null);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !detail && !failed) {
      try {
        setDetail(await explain(f.row_id));
      } catch (e) {
        setFailed(e.message);
      }
    }
  }

  const vetoed = f.rules_fired.includes("vetoed_agent_proposal");

  return (
    <>
      <tr className={`clickable${vetoed ? " vetoed" : ""}`} onClick={toggle}>
        <td>
          <span className={`caret${open ? " open" : ""}`}>▸</span>
          <code>{f.row_id}</code>
        </td>
        <td className="num">{rupees(f.amount_paise)}</td>
        <td className="num">{f.score.toFixed(3)}</td>
        <td>{f.stopped_by}</td>
        <td className="rules">
          {f.rules_fired.map((r) => (
            <span
              key={r}
              className={`tag${r.startsWith("hard_") ? " hard" : ""}${
                r === "vetoed_agent_proposal" ? " veto" : ""
              }`}
            >
              {r}
            </span>
          ))}
        </td>
      </tr>
      {open && (
        <tr className="detail-row">
          <td colSpan={5}>
            {failed && <span className="detail-error">Could not explain: {failed}</span>}
            {!failed && !detail && <span className="detail-loading">Explaining…</span>}
            {detail && (
              <div className="explain">
                <p className="explain-summary">{detail.summary}</p>
                <div className="explain-bars">
                  {detail.contributions.map((c) => (
                    <div className="explain-row" key={c.feature}>
                      <span className="explain-label">{c.label}</span>
                      <span className="explain-value">{c.value}</span>
                      <span className="explain-track">
                        <span
                          className={`explain-fill ${c.contribution >= 0 ? "up" : "down"}`}
                          style={{
                            width: `${Math.min(100, Math.abs(c.contribution) * 45)}%`,
                          }}
                        />
                      </span>
                      <span
                        className={`explain-num ${c.contribution >= 0 ? "up" : "down"}`}
                      >
                        {c.contribution >= 0 ? "+" : ""}
                        {c.contribution.toFixed(3)}
                      </span>
                    </div>
                  ))}
                </div>
                <p className="explain-note">
                  Contributions are in log-odds from a base of{" "}
                  {detail.base_value.toFixed(3)}, not probability. A rule can still refuse
                  this record regardless of what the model thinks.
                </p>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
