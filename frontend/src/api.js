const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

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

export function percent(fraction) {
  return `${(fraction * 100).toFixed(1)}%`;
}
