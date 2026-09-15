import { count, rupees } from "../api";

export default function Promises({ promises }) {
  const made = promises.made || 1;
  return (
    <section className="panel promises" aria-label="promise to pay">
      <header className="panel-head">
        <h2>Promises to pay</h2>
        <p>{rupees(promises.recovered_paise)} recovered through kept promises</p>
      </header>
      <dl className="promise-figures">
        <div>
          <dd>{count(promises.made)}</dd>
          <dt>captured</dt>
        </div>
        <div className="tone-brass">
          <dd>{count(promises.kept)}</dd>
          <dt>kept</dt>
        </div>
        <div className="tone-rose">
          <dd>{count(promises.broken)}</dd>
          <dt>broken</dt>
        </div>
      </dl>
      <div className="segments thin" role="img" aria-label="promises kept against broken">
        <span className="segment tone-brass" style={{ flexGrow: promises.kept }} />
        <span className="segment tone-rose" style={{ flexGrow: promises.broken }} />
        <span className="segment tone-slate-soft" style={{ flexGrow: Math.max(0, made - promises.kept - promises.broken) }} />
      </div>
      <p className="panel-note">
        A broken promise re-enters the pipeline with its attempt counter incremented. It cannot
        be used to walk around the cap.
      </p>
    </section>
  );
}
