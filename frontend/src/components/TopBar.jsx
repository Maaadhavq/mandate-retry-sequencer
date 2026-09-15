const STATUS = {
  waking: "waking the rail",
  running: "running 14 days",
  settled: "settled",
  error: "unreachable",
};

export default function TopBar({ status, busy, useLlm, onToggleLlm, onRun, config, seed }) {
  return (
    <header className="topbar">
      <div className="brand">
        <h1>Mandate Retry Sequencer</h1>
        <p className="tagline">
          Failed UPI Autopay debits, recovered under five rules no score or agent can override.
        </p>
      </div>

      <div className="topbar-right">
        {config && (
          <ul className="chips" aria-label="run configuration">
            <li>seed <b>{seed}</b></li>
            <li>n <b>{config.n}</b></li>
            <li>horizon <b>{config.horizon_days}d</b></li>
          </ul>
        )}
        <label className="switch">
          <input
            type="checkbox"
            checked={useLlm}
            disabled={busy}
            onChange={(e) => onToggleLlm(e.target.checked)}
          />
          <span className="switch-track" aria-hidden="true" />
          <span>Agent on the ambiguous band</span>
        </label>
        <button className="run" onClick={onRun} disabled={busy}>
          {busy ? "Running…" : config ? "Run again" : "Run batch"}
        </button>
        <span className={`led led-${status}`} role="status">
          <i aria-hidden="true" />
          {STATUS[status]}
        </span>
      </div>
    </header>
  );
}
