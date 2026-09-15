import { useMemo, useState } from "react";
import { count, explain, percent, rupees } from "../api";
import { OUTCOME, label } from "../labels";

/**
 * SPEC §2.5 panel 3. Every unrecovered record, largest rupees first, never paginated and
 * never collapsed — that is the point of it. A row expands to the SHAP explanation of its
 * score, which is the explanation layer that needs no API key.
 */
export default function Ledger({ failures, atRisk }) {
  const [filter, setFilter] = useState("all");

  const reasons = useMemo(() => {
    const counts = new Map();
    for (const f of failures) counts.set(f.stopped_by, (counts.get(f.stopped_by) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [failures]);

  const shown = filter === "all" ? failures : failures.filter((f) => f.stopped_by === filter);
  const left = shown.reduce((sum, f) => sum + f.amount_paise, 0);

  return (
    <section className="ledger" aria-label="honest failures">
      <header className="section-head">
        <h2>What it did not recover</h2>
        <p>
          {count(shown.length)} records · {rupees(left)} left on the table ·{" "}
          {percent(left / atRisk)} of everything at risk. Listed in full.
        </p>
      </header>

      <div className="filters" role="group" aria-label="filter by what stopped the record">
        <button className={filter === "all" ? "chip on" : "chip"} onClick={() => setFilter("all")}>
          all <b>{count(failures.length)}</b>
        </button>
        {reasons.map(([reason, n]) => (
          <button
            key={reason}
            className={`chip${filter === reason ? " on" : ""}${reason.startsWith("hard_") ? " chip-rose" : ""}`}
            onClick={() => setFilter(reason)}
          >
            {label(OUTCOME, reason)} <b>{count(n)}</b>
          </button>
        ))}
      </div>

      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>record</th>
              <th className="num">left on the table</th>
              <th className="num">score</th>
              <th>stopped by</th>
              <th className="fired">rules fired</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((f) => (
              <Row key={f.row_id} failure={f} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="ledger-hint">Select a row for the SHAP explanation of its score.</p>
    </section>
  );
}

function Row({ failure: f }) {
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
      <tr
        className={`row${vetoed ? " row-vetoed" : ""}${open ? " row-open" : ""}`}
        onClick={toggle}
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), toggle())}
        aria-expanded={open}
      >
        <td>
          <span className="caret" aria-hidden="true">▸</span>
          <code>{f.row_id}</code>
        </td>
        <td className="num">{rupees(f.amount_paise)}</td>
        <td className="num">{f.score.toFixed(3)}</td>
        <td className={f.stopped_by.startsWith("hard_") ? "tone-rose" : ""}>{label(OUTCOME, f.stopped_by)}</td>
        <td className="fired">
          <span className="tags">
          {f.rules_fired.map((r) => (
            <span
              key={r}
              className={`tag${r.startsWith("hard_") ? " tag-rose" : ""}${r === "vetoed_agent_proposal" ? " tag-veto" : ""}`}
            >
              {r}
            </span>
          ))}
          </span>
        </td>
      </tr>
      {open && (
        <tr className="detail">
          <td colSpan={5}>
            {failed && <span className="detail-msg tone-rose">Could not explain: {failed}</span>}
            {!failed && !detail && <span className="detail-msg">Explaining… the first one on a cold host builds the explainer.</span>}
            {detail && (
              <div className="explain">
                <p className="explain-summary">{detail.summary}</p>
                <ul className="explain-bars">
                  {detail.contributions.map((c) => (
                    <li key={c.feature}>
                      <span className="explain-label">{c.label}</span>
                      <span className="explain-value">{c.value}</span>
                      <span className="explain-track">
                        <i
                          className={c.contribution >= 0 ? "up" : "down"}
                          style={{ width: `${Math.min(50, Math.abs(c.contribution) * 22)}%` }}
                        />
                      </span>
                      <span className={`explain-num ${c.contribution >= 0 ? "up" : "down"}`}>
                        {c.contribution >= 0 ? "+" : ""}
                        {c.contribution.toFixed(3)}
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="explain-note">
                  Contributions in log-odds from a base of {detail.base_value.toFixed(3)}, not
                  probability. A rule can still refuse this record regardless of what the model thinks.
                </p>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
