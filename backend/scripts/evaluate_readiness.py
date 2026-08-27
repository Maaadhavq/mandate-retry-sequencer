"""Grade this submission against the Track 03 bar. Run it any time; it re-derives everything.

The bar, verbatim from the brief:

    "Show measured money recovered across a batch, with compliant escalation, stopping rules,
     and an audit trail."

Plus three required deliverables: a public repo, a 5-minute pitch video, architecture docs.

Design rules for this file, because a self-assessment that flatters is worse than none:

- **Every check runs something.** No item passes because a human ticked it. Where a claim
  cannot be checked mechanically (a recorded video), it is reported as UNVERIFIABLE, never
  as a pass.
- **It is allowed to fail.** If the checks cannot fail, they measure nothing.
- **Known-weak areas are graded as weak** even when they are working as designed, because the
  question this answers is "is it good enough to win", not "does it run".

    .venv/Scripts/python -m backend.scripts.evaluate_readiness
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final

REPO = Path(__file__).resolve().parents[2]

PASS, WEAK, FAIL, UNKNOWN = "PASS", "WEAK", "FAIL", "UNVERIFIABLE"
MARK: Final[dict[str, str]] = {PASS: "[ok]  ", WEAK: "[weak]", FAIL: "[FAIL]", UNKNOWN: "[????]"}


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", *args], cwd=REPO, capture_output=True, text=True
    )


# -- the four clauses of the bar ---------------------------------------------------------


def check_measured_money() -> Check:
    from backend.app.runner import run_campaign

    t = run_campaign(seed=42, use_llm=False)["totals"]
    recovered, at_risk = t["recovered_paise"], t["at_risk_paise"]
    if recovered <= 0:
        return Check("Measured money recovered", FAIL, "no rupees recovered")
    return Check(
        "Measured money recovered",
        PASS,
        f"₹{recovered / 100:,.0f} of ₹{at_risk / 100:,.0f} ({recovered / at_risk:.1%}) over 500 records",
    )


def check_compliant_escalation() -> Check:
    """Escalation must be bounded by rules that actually fire, including the NPCI window."""
    import json as _json

    from backend.app.guardrails import in_peak_window
    from datetime import datetime

    ledger = REPO / "data" / "ledger.jsonl"
    if not ledger.exists():
        return Check("Compliant escalation", FAIL, "no ledger — run a batch first")

    rows = [_json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    debits = [r for r in rows if int(r["attempt_cost_paise"]) > 0]
    illegal = [r for r in debits if in_peak_window(datetime.fromisoformat(r["sim_ts"]))]
    deferrals = sum(1 for r in rows if "hard_peak_window" in r["rules_fired"])

    if illegal:
        return Check("Compliant escalation", FAIL, f"{len(illegal)} debits inside an NPCI peak window")
    if deferrals == 0:
        return Check("Compliant escalation", WEAK, "rule 5 never fired — cannot claim it works")
    return Check(
        "Compliant escalation",
        PASS,
        f"{deferrals} peak-window deferrals, 0 of {len(debits)} debits in a restricted window",
    )


def check_stopping_rules() -> Check:
    import json as _json

    ledger = REPO / "data" / "ledger.jsonl"
    if not ledger.exists():
        return Check("Stopping rules", FAIL, "no ledger")

    rows = [_json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    fired = {t for r in rows for t in r["rules_fired"]}
    required = {"hard_revoked_mandate", "hard_max_attempts", "hard_cooling_period", "hard_peak_window"}
    missing = required - fired

    revoked_recovered = sum(
        int(r["recovered_paise"]) for r in rows if r["failure_reason"] == "revoked_mandate"
    )
    if revoked_recovered:
        return Check("Stopping rules", FAIL, f"revoked mandates recovered {revoked_recovered} paise")
    if missing:
        return Check("Stopping rules", WEAK, f"never fired on this batch: {sorted(missing)}")
    return Check("Stopping rules", PASS, f"all 4 stoppable rules fired; revoked recovered ₹0")


def check_audit_trail() -> Check:
    """The ledger must be independently re-derivable, by code that shares nothing with it."""
    from backend.app.runner import run_campaign

    total = run_campaign(seed=42, use_llm=False)["totals"]["recovered_paise"]
    result = _run("backend.scripts.verify_totals", "--expect-recovered", str(total))
    if result.returncode != 0:
        return Check("Audit trail", FAIL, "verify_totals disagrees with the API")

    source = (REPO / "backend" / "scripts" / "verify_totals.py").read_text(encoding="utf-8")
    if "from backend.app.ledger" in source:
        return Check("Audit trail", WEAK, "verify_totals imports ledger.py — agreement is a tautology")
    return Check("Audit trail", PASS, f"₹{total / 100:,.0f} re-derived without importing ledger.py")


# -- deliverables ------------------------------------------------------------------------


def check_public_repo() -> Check:
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    ahead = subprocess.run(
        ["git", "rev-list", "--count", "origin/main..HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()

    if not remote:
        return Check("Public repo", FAIL, "no origin remote")
    if dirty:
        return Check("Public repo", WEAK, f"uncommitted changes ({len(dirty.splitlines())} files)")
    if ahead not in ("", "0"):
        return Check("Public repo", WEAK, f"{ahead} commits not pushed")
    return Check("Public repo", PASS, f"clean and pushed — {remote}")


def check_architecture_docs() -> Check:
    required = {
        "ARCHITECTURE.md": ["mermaid", "limitation"],
        "README.md": ["Run it"],
        "SOURCES.md": ["TIER", "change my mind"],
    }
    missing = []
    for name, needles in required.items():
        path = REPO / name
        if not path.exists():
            missing.append(f"{name} absent")
            continue
        text = path.read_text(encoding="utf-8").lower()
        for needle in needles:
            if needle.lower() not in text:
                missing.append(f"{name} lacks '{needle}'")
    if missing:
        return Check("Architecture docs", WEAK, "; ".join(missing))
    return Check("Architecture docs", PASS, "ARCHITECTURE + README + SOURCES all present and substantive")


def check_video() -> Check:
    """A recorded video cannot be verified from here. Say so; never pass it on a script's say-so."""
    media = [
        p for p in REPO.iterdir()
        if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}
    ]
    script = REPO / "VIDEO.md"
    if media:
        return Check("5-minute video", UNKNOWN, f"found {media[0].name} — length and content unchecked")
    if script.exists():
        return Check("5-minute video", FAIL, "script written, nothing recorded — this blocks submission")
    return Check("5-minute video", FAIL, "no script and no recording")


# -- quality signals ---------------------------------------------------------------------


def check_tests() -> Check:
    # `pyproject.toml` already puts `-q` in addopts; passing it again gives `-qq`, which
    # suppresses the "N passed" summary entirely and leaves nothing to count.
    result = _run("pytest")
    if result.returncode != 0:
        return Check("Test suite", FAIL, "pytest is red")

    collected = _run("pytest", "--collect-only").stdout
    match = re.search(r"(\d+) tests? collected", collected)
    n = int(match.group(1)) if match else 0
    if n == 0:
        return Check("Test suite", WEAK, "green, but no tests were collected — that is not a pass")
    return Check("Test suite", PASS, f"{n} passing")


def check_honest_metrics() -> Check:
    metrics = REPO / "models" / "metrics.json"
    if not metrics.exists():
        return Check("Honest metrics", FAIL, "no metrics.json")
    m = json.loads(metrics.read_text(encoding="utf-8"))
    auc, (lo, hi) = m["auc"], m["auc_ci95"]
    ceiling = m["population_oracle_auc"]
    if auc > ceiling + 0.02:
        return Check("Honest metrics", FAIL, f"AUC {auc} exceeds the {ceiling} ceiling — leak")
    return Check(
        "Honest metrics",
        PASS,
        f"AUC {auc} CI [{lo}, {hi}] against a measured {ceiling} ceiling, reported with the CI",
    )


def check_false_positive_cost() -> Check:
    from backend.app.runner import run_campaign

    t = run_campaign(seed=42, use_llm=False)["totals"]
    cost = t["false_positive_cost_paise"]
    if cost <= 0:
        return Check("False-positive cost", WEAK, "reported as ₹0 — nothing was wasted, or it is not measured")
    return Check(
        "False-positive cost",
        WEAK,
        f"₹{cost / 100:,.2f} — processing spend only; customer-goodwill cost is not modelled",
    )


def check_agent_contribution() -> Check:
    """The AI track's weakest point while the cache is empty. Graded as weak, not hidden."""
    from backend.app.llm_cache import DEFAULT_CACHE_DIR

    entries = len(list((REPO / DEFAULT_CACHE_DIR).glob("*.json"))) if (REPO / DEFAULT_CACHE_DIR).exists() else 0
    if entries == 0:
        return Check(
            "Agent contribution measured",
            FAIL,
            "cache/llm/ is empty — the ablation delta is ₹0 by construction, not by measurement. "
            "This is an AI track; run `ablate --populate` with a key.",
        )
    return Check("Agent contribution measured", PASS, f"{entries} cached decisions; ablation is real")


def check_honest_failures() -> Check:
    from backend.app.runner import run_campaign

    r = run_campaign(seed=42, use_llm=False)
    failures = r["failures"]
    left = sum(f["amount_paise"] for f in failures)
    if not failures:
        return Check("Honest failure list", WEAK, "nothing unrecovered — suspiciously clean")
    return Check(
        "Honest failure list",
        PASS,
        f"{len(failures)} records, ₹{left / 100:,.0f} left on the table, listed in full",
    )


CHECKS: Final[list[tuple[str, Callable[[], Check]]]] = [
    ("Track 03 bar", check_measured_money),
    ("Track 03 bar", check_compliant_escalation),
    ("Track 03 bar", check_stopping_rules),
    ("Track 03 bar", check_audit_trail),
    ("Deliverables", check_public_repo),
    ("Deliverables", check_architecture_docs),
    ("Deliverables", check_video),
    ("Quality", check_tests),
    ("Quality", check_honest_metrics),
    ("Quality", check_honest_failures),
    ("Quality", check_false_positive_cost),
    ("Quality", check_agent_contribution),
]


def main() -> None:
    results: list[tuple[str, Check]] = []
    for section, fn in CHECKS:
        try:
            results.append((section, fn()))
        except Exception as exc:  # a check that crashes is a failing check
            results.append((section, Check(fn.__name__, FAIL, f"check crashed: {exc}")))

    current = None
    for section, check in results:
        if section != current:
            print(f"\n{section}")
            print("-" * 78)
            current = section
        print(f"  {MARK[check.status]} {check.name:<32} {check.detail}")

    tally = {s: sum(1 for _, c in results if c.status == s) for s in (PASS, WEAK, FAIL, UNKNOWN)}
    print("\n" + "=" * 78)
    print(
        f"  {tally[PASS]} pass · {tally[WEAK]} weak · {tally[FAIL]} fail · {tally[UNKNOWN]} unverifiable"
    )

    blockers = [c for _, c in results if c.status == FAIL]
    if blockers:
        print("\n  BLOCKING:")
        for c in blockers:
            print(f"    - {c.name}: {c.detail}")
        print("\n  Not ready to submit.")
        raise SystemExit(1)

    weak = [c for _, c in results if c.status == WEAK]
    if weak:
        print("\n  Submittable. Weakest points, in the order a panel will find them:")
        for c in weak:
            print(f"    - {c.name}: {c.detail}")
    else:
        print("\n  Ready.")


if __name__ == "__main__":
    main()
