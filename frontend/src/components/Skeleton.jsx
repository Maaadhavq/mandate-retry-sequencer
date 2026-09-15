const STATIONS = ["Scorer", "Guardrails", "Decider", "Executor", "Ledger"];

/** The rail, unlit, while the batch runs. */
export default function Skeleton({ slow }) {
  return (
    <section className="rail rail-skeleton" aria-busy="true" aria-label="running the batch">
      <ol>
        {STATIONS.map((s, i) => (
          <li key={s} className="station" style={{ "--i": i }}>
            <span className="node" aria-hidden="true" />
            <span className="station-name">{s}</span>
            <span className="station-figure">&nbsp;</span>
            <span className="station-unit">&nbsp;</span>
          </li>
        ))}
      </ol>
      <p className="rail-thesis">
        <span>
          Scoring 500 failed debits, running the rules, stepping a 14-day clock in one-hour ticks…
          {slow && (
            <em className="slow">
              {" "}
              The backend runs on a free tier and sleeps when idle. First wake takes up to a minute.
            </em>
          )}
        </span>
      </p>
    </section>
  );
}
