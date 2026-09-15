import { count, rupees } from "../api";

/**
 * The hero: the pipeline as a rail with five stations, each carrying the figure that
 * happened there on this run. The order is the order of evaluation (SPEC §3.1) — the
 * guardrails sit before the decider because they run before it, always.
 */
export default function Rail({ data, trace }) {
  const t = data.totals;
  const f = trace?.flow;
  const deferred =
    (trace?.rules_fired.hard_cooling_period ?? 0) + (trace?.rules_fired.hard_peak_window ?? 0);
  const debits = trace ? trace.debits_by_hour.reduce((a, b) => a + b, 0) : null;

  const stations = [
    {
      name: "Scorer",
      figure: f ? count(f.input) : count(data.config.n),
      unit: "debits in",
      note: "LightGBM · P(recover | features)",
      tone: "neutral",
    },
    {
      name: "Guardrails",
      figure: count(t.stopped_by_hard_rule),
      unit: "stopped by a rule",
      note: `${count(deferred)} deferred · 0 overridden`,
      tone: "rose",
    },
    {
      name: "Decider",
      figure: count(data.agent.records_routed),
      unit: "routed to the agent",
      note: `${count(f?.agent_vetoed ?? 0)} proposals vetoed by a rule`,
      tone: "blue",
    },
    {
      name: "Executor",
      figure: debits === null ? "—" : count(debits),
      unit: "debits on the rail",
      note: `${trace?.peak_violations ?? 0} inside an NPCI peak window`,
      tone: "neutral",
    },
    {
      name: "Ledger",
      figure: rupees(t.recovered_paise, { compact: true }),
      unit: "recovered",
      note: `${count(f?.recovered_records ?? 0)} recovered · ${count(
        f?.unrecovered_records ?? data.failures.length,
      )} listed as failures`,
      tone: "brass",
    },
  ];

  return (
    <section className="rail" aria-label="the pipeline">
      <ol>
        {stations.map((s, i) => (
          <li key={s.name} className={`station tone-${s.tone}`} style={{ "--i": i }}>
            <span className="node" aria-hidden="true" />
            <span className="station-name">{s.name}</span>
            <span className="station-figure">{s.figure}</span>
            <span className="station-unit">{s.unit}</span>
            <span className="station-note">{s.note}</span>
          </li>
        ))}
      </ol>
      <p className="rail-thesis">
        An agent proposal is a request, not an authority. The guardrails run <em>before</em> the
        score is read and <em>again</em> on every proposal before anything executes.
      </p>
    </section>
  );
}
