import { useMemo } from "react";
import { count, percent, rupees } from "../api";

/**
 * The money, split the way the rail split it. Paise are summed as integers and only
 * formatted at the edge (CLAUDE.md); the segments are the failures list re-bucketed.
 */
export default function MoneyStrip({ data, trace }) {
  const t = data.totals;
  const rederived = trace ? trace.daily.reduce((sum, d) => sum + d.recovered_paise, 0) : null;
  const agrees = rederived === t.recovered_paise;

  const segments = useMemo(() => {
    const acc = { rule: 0, score: 0, expired: 0 };
    for (const f of data.failures) {
      if (f.stopped_by.startsWith("hard_")) acc.rule += f.amount_paise;
      else if (f.stopped_by === "score_below_band") acc.score += f.amount_paise;
      else acc.expired += f.amount_paise;
    }
    return [
      { key: "recovered", label: "recovered", paise: t.recovered_paise, tone: "brass" },
      { key: "rule", label: "refused by a rule", paise: acc.rule, tone: "rose" },
      { key: "score", label: "written off by score", paise: acc.score, tone: "slate" },
      { key: "expired", label: "expired at day 14", paise: acc.expired, tone: "slate-soft" },
    ].filter((s) => s.paise > 0);
  }, [data, t.recovered_paise]);

  return (
    <section className="money" aria-label="money recovered against money at risk">
      <div className="money-head">
        <p className="money-figure">
          <span className="money-big">{rupees(t.recovered_paise)}</span>
          <span className="money-of">
            of {rupees(t.at_risk_paise)} at risk · {percent(t.recovery_rate)}
          </span>
        </p>
        <dl className="money-stats">
          <div>
            <dt>attempts per recovery</dt>
            <dd>{t.attempts_per_recovery.toFixed(2)}</dd>
          </div>
          <div>
            <dt>spent on debits that never recovered</dt>
            <dd>{rupees(t.false_positive_cost_paise)}</dd>
          </div>
          <div>
            <dt>recovered through a kept promise</dt>
            <dd>{rupees(data.promises.recovered_paise)}</dd>
          </div>
          <div>
            <dt>records a hard rule ended</dt>
            <dd>{count(t.stopped_by_hard_rule)}</dd>
          </div>
        </dl>
      </div>

      {rederived !== null && (
        <p className={`agree${agrees ? "" : " disagree"}`}>
          <i aria-hidden="true">{agrees ? "✓" : "!"}</i>
          {agrees ? (
            <>
              A second, independent read of the ledger sums to <b>{rupees(rederived)}</b> — the
              headline and the trace agree to the paisa.
            </>
          ) : (
            <>
              The trace re-derives <b>{rupees(rederived)}</b>, which does not match the headline.
              The ledger is wrong; nothing on this page should be trusted until it is fixed.
            </>
          )}
        </p>
      )}

      <div className="segments" role="img" aria-label="at-risk money by outcome">
        {segments.map((s) => (
          <span
            key={s.key}
            className={`segment tone-${s.tone}`}
            style={{ flexGrow: s.paise }}
            title={`${s.label}: ${rupees(s.paise)}`}
          />
        ))}
      </div>
      <ul className="segment-legend">
        {segments.map((s) => (
          <li key={s.key} className={`tone-${s.tone}`}>
            <i aria-hidden="true" />
            <span>{s.label}</span>
            <b>{rupees(s.paise, { compact: true })}</b>
            <small>{percent(s.paise / t.at_risk_paise)}</small>
          </li>
        ))}
      </ul>
    </section>
  );
}
