# Sources

Every external claim this repository makes, where it came from, and how far I would defend it.

This file exists because the system's central promise is that it refuses to do things it is not
permitted to do. That promise is worthless if the permissions themselves are guesses presented as
facts. So each claim is graded, and the grading is deliberately conservative: **nothing here was
read out of a primary regulatory PDF.** `npci.org.in` and `rbi.org.in` block automated fetches, so
everything below is second-hand from reporting on those documents, however consistent that
reporting is.

**Tiers**

| Tier | Meaning |
|---|---|
| **1 — Regulation** | Attributed to a named, dated regulatory document by multiple independent reports that agree on specifics. Still second-hand. |
| **2 — Convention** | Widely used and widely described as good practice. Not mandated by anyone. |
| **3 — Assumption** | Plausible, internally consistent, unverified. A design choice wearing a regulatory-sounding name. |

---

## 1. NPCI operating rules

The primary document is **"Guidelines on usage of Unified Payments Interface (UPI) and Application
Programming Interface (API)"**, reported as notified **21 May 2025**, effective **1 August 2025**.
I did not read it. Everything below is reporting *about* it.

### 1.1 Attempt cap — TIER 1

> "each mandate now allowed a maximum of four attempts: one original execution plus three retries"

Two sources verified by direct fetch, quoting the split independently:

> "A maximum of four attempts will be allowed for any failed autopay mandate (1 original attempt
> and 3 retries)." — Kiwi

> "Each mandate will be limited to a maximum of four total attempts (1 original + 3 retries)."
> — Ujjivan SFB

Encoded as `MAX_ATTEMPTS = 4`.

- [Major Changes by NPCI on UPI in 2025 — Kiwi](https://gokiwi.in/blog/major-changes-by-npci-on-upi-in-2025/) *(fetched and verified)*
- [UPI New Rules 2025 — Ujjivan SFB](https://www.ujjivansfb.bank.in/banking-blogs/banking-services/upi-rule-updates-npci-august) *(fetched and verified; note this source does **not** define the peak hours — it supports only the attempt cap)*

### 1.2 Peak-hour execution restriction — TIER 1

The definition, quoted:

> "Peak hours are defined as the period during the day when UPI financial transactions reach the
> highest transactions per second, observed from 10:00 hrs to 13:00 hrs and from 17:00 hrs to 21:30
> hrs."

And the resulting permitted windows:

> Allowed: "Before 10:00 AM", "Between 1:00 PM and 5:00 PM", "After 9:30 PM"
> Restricted: "10:00 AM to 1:00 PM", "5:00 PM to 9:30 PM"

Kiwi independently gives the same three windows: "before 10 AM, between 1 PM and 5 PM, and after
9:30 PM."

Encoded as `PEAK_WINDOWS_IST` and enforced as guardrail rule 5. This is the strongest-sourced
constraint in the system and the only one where sources give verbatim time ranges that agree.

- [Your guide to UPI changes starting August 1, 2025 — SCC Online](https://www.scconline.com/blog/post/2025/07/30/upi-changes-starting-august-1-ncpi-guidelines-upi-api-usage-2025/) *(fetched and verified — source of the peak-hours definition quoted above)*
- [Major Changes by NPCI on UPI in 2025 — Kiwi](https://gokiwi.in/blog/major-changes-by-npci-on-upi-in-2025/) *(fetched and verified — same three windows, independently)*
- [NPCI caps UPI balance checks, restricts autopay to non-peak hours — NewsBytes](https://www.newsbytesapp.com/news/business/npci-caps-upi-balance-checks-restricts-autopay-to-non-peak-hours/story) *(search summary only, not fetched)*

### 1.3 Other API caps in the same circular — TIER 1, not implemented

Recorded because they bound a production version of this system, and their absence is a stated
limitation rather than an oversight:

- Balance checks: 50 per day per app
- Linked-account views: 25 per day per app
- Transaction status checks: 3 times, each at a 90-second gap

The last one matters most here: this system assumes it learns each attempt's outcome immediately.
A real integration would poll under that limit, and some outcomes would be unknown at decision time.

### 1.4 Retry ladder — TIER 2, **not** regulation

`RETRY_WINDOWS_HOURS = (24, 72, 168)`.

Described as good practice — space retries so the customer has time to top up — rather than as an
NPCI mandate. One source is explicit that these intervals "appear to be recommended best practices
rather than strictly mandated by NPCI."

An earlier revision of `policy.py` listed this as corroborated regulation. That was an over-claim
and is corrected; the correction is in the git history deliberately.

- [UPI AutoPay: Design Guide for Recurring Payments — productgrowth.in](https://productgrowth.in/insights/fintech/upi-autopay-guide/)

### 1.5 Cooling period — TIER 3, assumption

`COOLING_PERIOD_HOURS = 24`. No source located. Plausible, and consistent with a 24h first retry
window, but it is a design choice and is labelled as one in `policy.py`.

### 1.6 Campaign horizon — TIER 3, not a rule at all

`HORIZON_DAYS = 14`. Entirely a design choice for this simulation.

---

## 2. The problem being solved

### 2.1 Scale of mandate failure — TIER 1 (reported figure, not a regulation)

> "More than 20 million AutoPay mandates on the Unified Payments Interface (UPI) are revoked each
> month as users' accounts fall short of the required balances."

Reported September 2025, covering entertainment/OTT subscriptions, loan repayments, investments and
utilities. This is the single figure that justifies the project existing, and it independently
supports two design decisions: the `revoked_mandate` cohort, and making `days_to_payday` the
dominant feature for `insufficient_balance` failures.

- [UPI autopay revocations hit 20 mn per month on low customer balance — Business Standard](https://www.business-standard.com/finance/news/upi-autopay-revocations-hit-20-mn-monthly-over-low-customer-balances-125090700500_1.html)
- [Secondary coverage — hitch blog](https://blog.hitch.zone/20-million-upi-autopay-mandates-cancelled-every-month-due-to-low-balance/)

### 2.2 Involuntary churn — TIER 2, directional only

Figures in this area vary widely by source and none are India-payments-specific enough to build on.
Recorded for context, **not** used to justify any number in the system:

- 20–40% of subscription churn is involuntary (payment failure, not cancellation)
- Indian SaaS involuntary churn reported at 30–40% of total churn
- UPI Autopay debit success reported at ~85% in month 1, decaying toward ~70% by month 6

I would not quote these in a pitch without a better source. They are consistent with the 20M
revocation figure, which is the one I would actually defend.

- [State of Retention 2025 — Churnkey](https://churnkey.co/reports/state-of-retention-2025)
- [How to Actually Reduce Churn in Recurring Payments — Razorpay](https://razorpay.com/blog/reduce-churn-recurring-payments-guide)

---

## 3. What Razorpay already ships

Recorded honestly, because pretending otherwise would be the fastest way to lose credibility with a
panel who built these.

### 3.1 Intelligent Revenue-Protect / Intelligent Retry Engine

Razorpay ships retry-based recovery for UPI Autopay today. Their published description emphasises
**configurability** — merchants "define retry cadence, choose predefined templates, or create custom
templates" — plus a WhatsApp-led retention loop for abandoned registrations, cancelled mandates and
failed debits.

What the published material does *not* describe: a per-decision audit trail, false-positive cost
accounting, stopping-rule guarantees, or a compliance layer that can veto a proposed retry. That gap
is where this project positions itself. It is a positioning claim about their *public materials*,
not a claim about their internal systems, which I cannot see.

- [UPI Autopay with Intelligent Revenue-Protect — Razorpay](https://razorpay.com/blog/upi-autopay-with-intelligent-revenue-protect/)
- [Payment Retries — Razorpay Docs](https://razorpay.com/docs/payments/subscriptions/payment-retries/)

### 3.2 Razorpay's ML scale — context for why this project does not compete on modelling

- **Vulcan**, an AI payments foundation model built with AWS: ~3,000 signals per transaction, trained
  on ~3 trillion data points from ~4 billion payments; reported 8–10% lift in success rates across
  1.5M transactions and 50,000+ merchants.
- **Optimizer**: ML routing over 150+ parameters and 600M+ payment data points.

A LightGBM trained on 8,000 synthetic rows is not competitive with that and does not try to be. The
scorer here exists to *create a decision boundary worth governing*, not to beat their model. That is
stated in `ARCHITECTURE.md` §8 as well.

- [Razorpay Introduces AI Payments Foundation Model With AWS — Crowdfund Insider](https://www.crowdfundinsider.com/2026/08/298808-razorpay-introduces-ai-payments-foundation-model-with-amazon-web-services-aws/)
- [Optimizer Pro — Razorpay](https://razorpay.com/blog/new-optimizer-pro-indias-first-ai-powered-payments-router-engineered-for-scale/)

---

## 4. The brief

Track 03, AI Revenue Recovery — *"Find revenue that's slipping away and win it back."*

The stated bar, verbatim:

> "Show measured money recovered across a batch, with compliant escalation, stopping rules, and an
> audit trail."

Three of those four clauses are governance rather than prediction, which is what shaped this
project's priorities.

- [Razorpay AI Buildathon](https://razorpay.com/buildathon/)

---

## 5. What would change my mind

Honest list of where this could be wrong:

1. **The peak-window rule may not apply to mandate *execution* the way I have assumed.** Reporting
   describes the restriction as covering "non-customer-initiated APIs," and I have taken a mandate
   debit to be one. If NPCI carves out autopay execution specifically, rule 5 is over-strict —
   though over-strict is the safe direction for a compliance rule.
2. **The 24h cooling period is unsourced** and could be materially wrong in either direction.
3. **Razorpay may already enforce all of this internally.** My positioning rests on what their
   public materials describe, which is not the same as what their systems do.
4. **The primary circular was never read.** Every TIER 1 claim would drop a tier if the reporting
   turns out to have simplified something.
