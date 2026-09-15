import { count, percent, rupees } from "../api";
import { COHORT, label } from "../labels";

function CohortList({ title, rows, note }) {
  return (
    <div className="cohort">
      <h3>{title}</h3>
      <ul>
        {rows.map((r) => {
          const rate = r.at_risk_paise ? r.recovered_paise / r.at_risk_paise : 0;
          const flagged = note?.key === r.key;
          return (
            <li key={r.key} className={flagged ? "flagged" : ""}>
              <span className="cohort-name">
                {label(COHORT, r.key)}
                {flagged && <em title={note.text}> · known blind spot</em>}
              </span>
              <span className="cohort-bar" title={`${rupees(r.recovered_paise)} of ${rupees(r.at_risk_paise)} · n=${r.n}`}>
                <i style={{ width: `${rate * 100}%` }} />
              </span>
              <span className="cohort-rate">{percent(rate, 0)}</span>
              <span className="cohort-n">n {count(r.n)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * Recovery rate by cohort. Edtech is flagged on purpose: the scorer over-predicts it on
 * this batch because the fee-cycle drift is not a feature (ARCHITECTURE.md, SPEC §2.2).
 */
export default function Cohorts({ cohorts }) {
  return (
    <section className="panel cohorts" aria-label="recovery by cohort">
      <header className="panel-head">
        <h2>By cohort</h2>
        <p>share of ₹ at risk recovered</p>
      </header>
      <CohortList title="Failure reason" rows={cohorts.by_failure_reason} />
      <CohortList
        title="Merchant category"
        rows={cohorts.by_merchant_category}
        note={{
          key: "edtech",
          text: "The scorer over-predicts edtech on this batch: the academic fee cycle drifts and is not a feature. Documented, not hidden.",
        }}
      />
    </section>
  );
}
