import { useMemo, useState } from "react";
import { area, line, curveMonotoneX } from "d3-shape";
import { count, rupees } from "../api";

const W = 480;
const H = 200;
const PAD = { top: 12, right: 12, bottom: 34, left: 12 };

/**
 * ₹ recovered, cumulative, across the simulated fortnight. The ticks under the axis are
 * debit attempts per day — the rhythm the retry ladder and the peak windows impose.
 */
export default function Timeline({ trace, totals }) {
  const [hover, setHover] = useState(null);
  const days = trace?.daily ?? [];

  const series = useMemo(() => {
    let acc = 0;
    return days.map((d) => ({ ...d, cumulative: (acc += d.recovered_paise) }));
  }, [days]);

  if (!series.length) return null;

  const total = series[series.length - 1].cumulative;
  const x = (i) => PAD.left + (i / (series.length - 1)) * (W - PAD.left - PAD.right);
  const y = (v) => PAD.top + (1 - v / (total || 1)) * (H - PAD.top - PAD.bottom);
  const maxAttempts = Math.max(...series.map((d) => d.attempts), 1);

  const areaPath = area()
    .x((d, i) => x(i))
    .y0(H - PAD.bottom)
    .y1((d) => y(d.cumulative))
    .curve(curveMonotoneX)(series);
  const linePath = line()
    .x((d, i) => x(i))
    .y((d) => y(d.cumulative))
    .curve(curveMonotoneX)(series);

  function onMove(e) {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((px - PAD.left) / (W - PAD.left - PAD.right)) * (series.length - 1));
    setHover(Math.min(series.length - 1, Math.max(0, i)));
  }

  const h = hover === null ? null : series[hover];

  return (
    <section className="panel timeline" aria-label="recovery over fourteen days">
      <header className="panel-head">
        <h2>Fourteen days</h2>
        <p>{rupees(total)} recovered, cumulative</p>
      </header>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label="cumulative rupees recovered per simulated day"
      >
        <path d={areaPath} className="tl-area" />
        <path d={linePath} className="tl-line" pathLength="1" />
        {series.map((d, i) => (
          <rect
            key={i}
            x={x(i) - 3}
            y={H - PAD.bottom + 8}
            width={6}
            height={Math.max(d.attempts ? 2 : 0, (d.attempts / maxAttempts) * 16)}
            className="tl-attempt"
            rx="1"
          />
        ))}
        {[0, 7, 14].map((d) => (
          <text key={d} x={x(d)} y={H - 2} className="tl-axis" textAnchor={d === 0 ? "start" : d === 14 ? "end" : "middle"}>
            day {d}
          </text>
        ))}
        {h && (
          <g className="tl-hover">
            <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={H - PAD.bottom} />
            <circle cx={x(hover)} cy={y(h.cumulative)} r="4" />
          </g>
        )}
      </svg>
      <p className="tl-readout" aria-live="polite">
        {h ? (
          <>
            <b>day {h.day}</b> · {rupees(h.cumulative)} so far · {count(h.attempts)} debits ·{" "}
            {count(h.recoveries)} recoveries
          </>
        ) : (
          <>ticks beneath the axis are debit attempts per day · hover for the running total</>
        )}
      </p>
    </section>
  );
}
