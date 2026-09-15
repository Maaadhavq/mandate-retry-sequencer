import { count } from "../api";
import { RULES } from "../labels";
import NpciClock from "./NpciClock";

/**
 * The five hard rules, numbered because SPEC §3.1 numbers them and evaluates them in
 * that order. Each card carries how often it fired on this run and how many times it was
 * overridden — the second figure is zero by construction and shown anyway, because that
 * is the claim the whole system makes.
 */
export default function Rulebook({ trace, data }) {
  const fired = trace?.rules_fired ?? {};

  return (
    <section className="rulebook" aria-label="the five hard rules">
      <header className="section-head">
        <h2>The rulebook</h2>
        <p>
          Run before the score is read, and again on every agent proposal. Each constant is
          graded by what it is: regulation, an assumption, or a design choice.
        </p>
      </header>

      <ol className="rules">
        {RULES.map((r) => (
          <li key={r.id} className={`rule rule-${r.n} effect-${r.effect}`}>
            <div className="rule-head">
              <span className="rule-n">{r.n}</span>
              <h3>{r.name}</h3>
            </div>
            <p className="rule-text">{r.rule}</p>
            <p className="rule-provenance">
              <span className={`tier tier-${r.tier.replace(" ", "-")}`}>{r.tier}</span>
              {r.source && <span className="rule-source">{r.source}</span>}
            </p>
            <dl className="rule-stats">
              <div>
                <dt>{r.effect === "defers" ? "deferred" : "fired"}</dt>
                <dd>{count(fired[r.id] ?? 0)}</dd>
              </div>
              <div>
                <dt>overridden</dt>
                <dd>0</dd>
              </div>
            </dl>
            {r.n === 5 && (
              <table className="windows">
                <tbody>
                  <tr>
                    <th>Autopay permitted</th>
                    <td>before 10:00 · 13:00–17:00 · after 21:30</td>
                  </tr>
                  <tr className="blocked">
                    <th>Blocked — NPCI peak</th>
                    <td>10:00–13:00 · 17:00–21:30</td>
                  </tr>
                </tbody>
              </table>
            )}
            {r.n === 5 && trace && (
              <NpciClock
                hours={trace.debits_by_hour}
                violations={trace.peak_violations}
                deferrals={trace.peak_deferrals}
              />
            )}
          </li>
        ))}
      </ol>
      <p className="rule-foot">
        {count(data.totals.stopped_by_hard_rule)} records were ended by rules 1, 2 or 4. Rules
        3 and 5 only postpone — a deferred retry is still owed to the customer's balance, not
        written off.
      </p>
    </section>
  );
}
