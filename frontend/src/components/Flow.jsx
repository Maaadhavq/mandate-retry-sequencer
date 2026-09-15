import { useEffect, useMemo, useRef, useState } from "react";
import { sankey, sankeyLinkHorizontal, sankeyLeft } from "d3-sankey";
import { count } from "../api";
import { ENTRY, OUTCOME } from "../labels";

const ENTRY_ORDER = [
  "hard_revoked_mandate",
  "hard_max_attempts",
  "hard_horizon_exhausted",
  "band_high",
  "agent_band",
  "band_low",
];
const OUTCOME_ORDER = [
  "recovered",
  "hard_revoked_mandate",
  "hard_max_attempts",
  "hard_horizon_exhausted",
  "horizon_expired",
  "score_below_band",
];

function toneOf(id) {
  if (id === "recovered") return "brass";
  if (id.startsWith("hard_")) return "rose";
  if (id === "agent_band") return "blue";
  if (id === "band_high") return "blue-soft";
  return "slate";
}

/**
 * Where the 500 debits went. Column one is the input, column two is the first thing that
 * happened to each record (a stopping rule, or the score band it fell into), column three
 * is how it ended. Edge weights are records and sum to the input — `/batch/trace` is
 * tested for exactly that.
 */
export default function Flow({ trace }) {
  const ref = useRef(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  const graph = useMemo(() => {
    if (!trace || width < 320) return null;
    const f = trace.flow;
    const entry = {
      ...f.stopped_at_entry,
      band_high: f.band_high,
      agent_band: f.agent_band,
      band_low: f.band_low,
    };
    const nodes = [{ id: "in", name: `${count(f.input)} failed debits`, col: 0 }];
    for (const id of ENTRY_ORDER) if (entry[id] > 0) nodes.push({ id, name: ENTRY[id], col: 1 });
    const outcomesPresent = new Set(trace.transitions.map((e) => e.target));
    for (const id of OUTCOME_ORDER)
      if (outcomesPresent.has(id)) nodes.push({ id: `out:${id}`, name: OUTCOME[id], col: 2 });

    const index = new Map(nodes.map((n, i) => [n.id, i]));
    const links = [];
    for (const id of ENTRY_ORDER)
      if (entry[id] > 0) links.push({ source: index.get("in"), target: index.get(id), value: entry[id], tone: toneOf(id) });
    for (const e of trace.transitions)
      links.push({
        source: index.get(e.source),
        target: index.get(`out:${e.target}`),
        value: e.n,
        tone: toneOf(e.target),
      });

    const height = Math.max(300, Math.min(420, width * 0.42));
    const layout = sankey()
      .nodeId((d) => d.index)
      .nodeWidth(8)
      .nodePadding(width < 640 ? 8 : 14)
      .nodeAlign(sankeyLeft)
      .nodeSort(null)
      .linkSort(null)
      .extent([
        [0, 8],
        [width, height - 8],
      ]);
    const g = layout({
      nodes: nodes.map((n, index) => ({ ...n, index })),
      links,
    });
    return { ...g, height };
  }, [trace, width]);

  const compact = width < 640;
  const path = sankeyLinkHorizontal();

  return (
    <section className="flow" ref={ref} aria-label="where the debits went">
      <header className="section-head">
        <h2>Where the 500 went</h2>
        <p>
          First touch, then outcome. A rule ends a record before its score is read; the agent
          only ever sees the middle band.
        </p>
      </header>
      {graph && (
        <svg width={width} height={graph.height} role="img" aria-label="flow of records">
          {graph.links.map((l, i) => (
            <path
              key={i}
              d={path(l)}
              className={`link tone-${l.tone}`}
              strokeWidth={Math.max(1, l.width)}
              pathLength="1"
              style={{ "--i": i }}
            >
              <title>
                {l.source.name} → {l.target.name}: {count(l.value)} records
              </title>
            </path>
          ))}
          {graph.nodes.map((n) => {
            const tone = n.col === 0 ? "neutral" : toneOf(n.id.replace("out:", ""));
            const right = n.col === 2;
            const x = right ? n.x0 - 8 : n.x1 + 8;
            const y = (n.y0 + n.y1) / 2;
            const tall = n.y1 - n.y0 >= 9;
            return (
              <g key={n.id} className={`node tone-${tone}`}>
                <rect x={n.x0} y={n.y0} width={n.x1 - n.x0} height={Math.max(1, n.y1 - n.y0)} rx="1.5" />
                {(tall || n.col !== 1) && !(compact && n.col === 1) && (
                  <text x={x} y={y} textAnchor={right ? "end" : "start"} dominantBaseline="middle">
                    <tspan className="node-count">{count(n.value)}</tspan>
                    <tspan dx="6" className="node-name">
                      {n.name}
                    </tspan>
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      )}
      {compact && graph && (
        <ul className="flow-legend">
          {graph.nodes
            .filter((n) => n.col === 1)
            .map((n) => (
              <li key={n.id} className={`tone-${toneOf(n.id)}`}>
                <i aria-hidden="true" /> {count(n.value)} {n.name}
              </li>
            ))}
        </ul>
      )}
    </section>
  );
}
