import { useCallback, useEffect, useRef, useState } from "react";
import { fetchTrace, runBatch } from "./api";
import TopBar from "./components/TopBar";
import Rail from "./components/Rail";
import Flow from "./components/Flow";
import MoneyStrip from "./components/MoneyStrip";
import Rulebook from "./components/Rulebook";
import Decider from "./components/Decider";
import Timeline from "./components/Timeline";
import Cohorts from "./components/Cohorts";
import Promises from "./components/Promises";
import Ledger from "./components/Ledger";
import Skeleton from "./components/Skeleton";

/**
 * The dashboard. SPEC §2.5, rebuilt as the pipeline itself: the rail on top, the money
 * beneath it, the rulebook, the decider, the clock, and — never collapsed — every record
 * the system failed to recover.
 *
 * The page runs the batch on load. A reviewer opening the link should see the system
 * working, not a button.
 */
export default function App() {
  const [data, setData] = useState(null);
  const [trace, setTrace] = useState(null);
  const [status, setStatus] = useState("waking"); // waking | running | settled | error
  const [error, setError] = useState(null);
  const [slow, setSlow] = useState(false);
  const [useLlm, setUseLlm] = useState(true);
  const runs = useRef(0);
  const started = useRef(false);

  const run = useCallback(
    async (opts, attempt = 0) => {
      const id = ++runs.current;
      setStatus(attempt === 0 && runs.current === 1 ? "waking" : "running");
      setError(null);
      setSlow(false);
      const slowTimer = setTimeout(() => setSlow(true), 8000);
      try {
        const batch = await runBatch(opts);
        const t = await fetchTrace();
        if (id !== runs.current) return;
        setData(batch);
        setTrace(t);
        setStatus("settled");
      } catch (e) {
        if (id !== runs.current) return;
        // A free-tier backend may still be waking: one silent retry before showing it.
        if (attempt === 0 && /Failed to fetch|NetworkError|Load failed/i.test(e.message)) {
          await new Promise((r) => setTimeout(r, 4000));
          runs.current = id - 1;
          return run(opts, 1);
        }
        setError(e.message);
        setStatus("error");
      } finally {
        clearTimeout(slowTimer);
      }
    },
    [],
  );

  useEffect(() => {
    // StrictMode mounts twice in development; the batch must only be requested once.
    if (started.current) return;
    started.current = true;
    run({ useLlm: true });
  }, [run]);

  const busy = status === "waking" || status === "running";

  return (
    <div className="page">
      <TopBar
        status={status}
        busy={busy}
        useLlm={useLlm}
        onToggleLlm={setUseLlm}
        onRun={() => run({ useLlm })}
        config={data?.config}
        seed={data?.seed}
      />

      {error && (
        <div className="notice notice-error" role="alert">
          <strong>The rail did not answer.</strong> {error}
          <br />
          The backend runs on a free tier and sleeps when idle; it takes up to a minute to
          wake. <button className="link" onClick={() => run({ useLlm })}>Run again</button>
        </div>
      )}

      {!data && busy && <Skeleton slow={slow} />}

      {data && (
        <main className={busy ? "settled dimmed" : "settled"}>
          <Rail data={data} trace={trace} />
          <Flow trace={trace} />
          <MoneyStrip data={data} trace={trace} />
          <Rulebook trace={trace} data={data} />
          <Decider data={data} trace={trace} />
          <section className="triptych">
            <Timeline trace={trace} totals={data.totals} />
            <Cohorts cohorts={data.cohorts} />
            <Promises promises={data.promises} />
          </section>
          <Ledger failures={data.failures} atRisk={data.totals.at_risk_paise} />
          <footer className="foot">
            <p>
              Every figure on this page is a sum over rows in an append-only ledger.{" "}
              <code>backend/scripts/verify_totals.py</code> re-derives the headline without
              importing the code that produced it.
            </p>
            <p>
              <a href="https://github.com/Maaadhavq/mandate-retry-sequencer">Source</a>
              <a href="https://github.com/Maaadhavq/mandate-retry-sequencer/blob/main/ARCHITECTURE.md">
                Architecture
              </a>
              <a href="https://github.com/Maaadhavq/mandate-retry-sequencer/blob/main/SOURCES.md">
                Sources &amp; what would change my mind
              </a>
            </p>
          </footer>
        </main>
      )}
    </div>
  );
}
