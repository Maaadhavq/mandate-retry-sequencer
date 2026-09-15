/**
 * Human names for the ledger's machine labels. The rule copy and provenance tiers are
 * quoted from backend/app/policy.py and SPEC §3 — nothing here makes a regulatory claim
 * the backend does not.
 */

export const RULES = [
  {
    id: "hard_revoked_mandate",
    n: 1,
    name: "Revoked mandate",
    rule: "A revoked mandate is never debited. Recovery is exactly zero, not a probability.",
    tier: "invariant",
    effect: "stops",
  },
  {
    id: "hard_max_attempts",
    n: 2,
    name: "Attempt cap",
    rule: "Four debit attempts per mandate cycle — one original plus three retries — then write off.",
    tier: "regulation",
    source: "NPCI guidelines, notified 21 May 2025, effective 1 Aug 2025",
    effect: "stops",
  },
  {
    id: "hard_cooling_period",
    n: 3,
    name: "Cooling period",
    rule: "At least 24 hours between two attempts on the same mandate. Postpones; never stops.",
    tier: "assumption",
    source: "Unsourced. Treated as a design choice and flagged as such",
    effect: "defers",
  },
  {
    id: "hard_horizon_exhausted",
    n: 4,
    name: "Campaign horizon",
    rule: "Nothing executes past day 14. An unresolved record is written off as expired.",
    tier: "design choice",
    effect: "stops",
  },
  {
    id: "hard_peak_window",
    n: 5,
    name: "NPCI peak window",
    rule: "Autopay may not execute 10:00–13:00 or 17:00–21:30 IST. A due retry is deferred to the window edge.",
    tier: "regulation",
    source: "NPCI, effective 1 Aug 2025 — the one rule here checkable against a dated public source",
    effect: "defers",
  },
];

export const RULE_BY_ID = Object.fromEntries(RULES.map((r) => [r.id, r]));

export const OUTCOME = {
  recovered: "Recovered",
  score_below_band: "Written off by score",
  hard_revoked_mandate: "Rule 1 · revoked",
  hard_max_attempts: "Rule 2 · attempt cap",
  hard_cooling_period: "Rule 3 · cooling",
  hard_horizon_exhausted: "Rule 4 · horizon",
  hard_peak_window: "Rule 5 · peak window",
  horizon_expired: "Expired at day 14",
  vetoed_agent_proposal: "Agent proposal vetoed",
  agent_proposal_accepted: "Agent proposal accepted",
  agent_proposal_rejected: "Agent proposal rejected",
  band_high: "Score ≥ 0.65 · retry now",
  band_low: "Score < 0.15 · stop",
  band_mid_upper: "Ambiguous band",
  band_mid_lower: "Ambiguous band",
  scheduled_retry_fired: "Scheduled retry fired",
  promise_resolved: "Promise resolved",
};

export const ENTRY = {
  hard_revoked_mandate: "Rule 1 · revoked",
  hard_max_attempts: "Rule 2 · at cap",
  hard_horizon_exhausted: "Rule 4 · horizon",
  band_high: "Score ≥ 0.65",
  agent_band: "Agent band 0.15–0.65",
  band_low: "Score < 0.15",
};

export const COHORT = {
  insufficient_balance: "Insufficient balance",
  technical_decline: "Technical decline",
  revoked_mandate: "Revoked mandate",
  saas: "SaaS",
  edtech: "Edtech",
  ott: "OTT",
  fitness: "Fitness",
  utilities: "Utilities",
};

export const SOURCE = {
  live: "live agent",
  cache: "replayed from cache",
  fallback: "deterministic fallback",
  deterministic: "no agent needed",
};

export function label(map, key) {
  return map[key] ?? key.replaceAll("_", " ");
}
