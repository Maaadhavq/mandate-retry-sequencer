import { count } from "../api";

// NPCI peak windows, IST, mirrored from backend/app/policy.py PEAK_WINDOWS_IST.
const PEAK = [
  [10, 13],
  [17, 21.5],
];
const R = 74;
const CX = 100;
const CY = 100;

function polar(hour, r) {
  const a = (hour / 24) * Math.PI * 2 - Math.PI / 2;
  return [CX + r * Math.cos(a), CY + r * Math.sin(a)];
}

function arc(from, to, r) {
  const [x0, y0] = polar(from, r);
  const [x1, y1] = polar(to, r);
  const large = to - from > 12 ? 1 : 0;
  return `M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1}`;
}

/**
 * A 24-hour ring. The two blocked windows are drawn on it; every executed debit is a tick
 * at its hour of day. The claim the ring makes is visual and checkable: no tick sits inside
 * a blocked arc. `/batch/trace` asserts the same thing as `peak_violations == 0`.
 */
export default function NpciClock({ hours, violations, deferrals }) {
  const total = hours.reduce((a, b) => a + b, 0);
  const max = Math.max(...hours, 1);

  return (
    <figure className="clock">
      <svg viewBox="0 0 200 200" role="img" aria-label="debits by hour of day against NPCI peak windows">
        <circle cx={CX} cy={CY} r={R} className="clock-ring" />
        {PEAK.map(([a, b]) => (
          <path key={a} d={arc(a, b, R)} className="clock-blocked">
            <title>
              blocked {a}:00–{Math.floor(b)}:{b % 1 ? "30" : "00"} IST
            </title>
          </path>
        ))}
        {hours.map((n, h) => {
          if (!n) return null;
          const len = 10 + 28 * Math.sqrt(n / max);
          const [x0, y0] = polar(h + 0.5, R - 6);
          const [x1, y1] = polar(h + 0.5, R - 6 - len);
          return (
            <line key={h} x1={x0} y1={y0} x2={x1} y2={y1} className="clock-tick">
              <title>
                {count(n)} debits at {String(h).padStart(2, "0")}:00–{String(h + 1).padStart(2, "0")}:00
              </title>
            </line>
          );
        })}
        {[0, 6, 12, 18].map((h) => {
          const [x, y] = polar(h, R + 14);
          return (
            <text key={h} x={x} y={y} className="clock-label" textAnchor="middle" dominantBaseline="middle">
              {String(h).padStart(2, "0")}
            </text>
          );
        })}
        <text x={CX} y={CY - 6} className="clock-center" textAnchor="middle">
          {violations} of {count(total)}
        </text>
        <text x={CX} y={CY + 10} className="clock-center-sub" textAnchor="middle">
          in a peak window
        </text>
      </svg>
      <figcaption>
        {count(deferrals)} retries fell due inside a window and were held to its edge — deferred,
        never dropped.
      </figcaption>
    </figure>
  );
}
