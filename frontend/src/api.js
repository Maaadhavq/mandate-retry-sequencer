// Set VITE_API_URL at build time to point the dashboard at a deployed backend.
// Render's `fromService` gives a bare host, so a missing scheme is filled in here.
const RAW = import.meta.env.VITE_API_URL || "http://localhost:8000";
const BASE = (/^https?:\/\//.test(RAW) ? RAW : `https://${RAW}`).replace(/\/+$/, "");

export async function runBatch({ seed = 42, n = 500, useLlm = true } = {}) {
  const res = await fetch(`${BASE}/batch/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ seed, n, use_llm: useLlm }),
  });
  if (!res.ok) {
    throw new Error(`Batch run failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

/** Where the debits went — a second, read-only cut of the ledger the run just wrote. */
export async function fetchTrace() {
  const res = await fetch(`${BASE}/batch/trace`);
  if (!res.ok) throw new Error(`Trace failed: ${res.status} ${res.statusText}`);
  return res.json();
}

/** Why the scorer gave this record its score. SHAP, no API key needed (SPEC §7, F9). */
export async function explain(rowId) {
  const res = await fetch(`${BASE}/explain/${encodeURIComponent(rowId)}`);
  if (!res.ok) throw new Error(`Explain failed: ${res.status} ${res.statusText}`);
  return res.json();
}

/** Paise are integers everywhere (SPEC §11). Format only at the edge, never compute here. */
export function rupees(paise, { compact = false } = {}) {
  const value = paise / 100;
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
    notation: compact ? "compact" : "standard",
  }).format(value);
}

export function percent(fraction, digits = 1) {
  return `${(fraction * 100).toFixed(digits)}%`;
}

/** Indian grouping without the currency sign, for counts and compact figures. */
export function count(n) {
  return new Intl.NumberFormat("en-IN").format(n);
}
