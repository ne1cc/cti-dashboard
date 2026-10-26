# Clinical Interpretation Guardrails

These rules bind every deliverable in this project: dashboard pages,
marts, docs, memos, and any language generated from them.

## What the data IS
Public registry **listings**: what sponsors chose to register, worded and
updated on their schedule. Signals derived from listings can inform where
a *human feasibility review* is worth doing first.

## What the data IS NOT
- Not patient-level or participant data (none exists in this project).
- Not verified recruitment activity (statuses can lag operations).
- Not site capacity, workload, quality, or performance.
- Not evidence of clinical effectiveness or safety.
- Not population eligibility or patient availability.

## Required language
| Instead of… | Say… |
|---|---|
| "high competition" | "potential competition signal" |
| "saturated market" | "elevated recruiting-listing density (relative percentile)" |
| "site is overloaded" | "facility listed by multiple recruiting trials" |
| "recruitment will be difficult" | "segment prioritized for feasibility review" |
| "sponsor dominates" | "concentrated lead-sponsor listings (HHI)" |
| "trials are growing fast" | "newly posted/recruiting listings increased in the window" |

## Prohibited claims (never, in any output)
1. Predictions of enrollment success, failure, or timelines.
2. Judgments of any named site, sponsor, investigator, or population.
3. Claims about healthcare quality or access for patients.
4. Speculative ROI, cost savings, or outcome claims presented as observed results or operational models.
5. Clinical interpretations of outcome measures text.
6. Any collection, storage, or display of personal contact or investigator
   details (names, emails, phone numbers). Per ADR 0065 (Contact Policy),
   the platform enforces an official registry link plus site list policy;
   zero personal contact details are stored or exposed, and `stg_trial_contacts`
   is intentionally empty (`where 1 = 0`).
7. Action-prescribing operational directives, playbooks, or speculative financial recommendations derived from registry counts.

## Mechanical enforcement
- `mart_feasibility_priority_queue.interpretation_note` travels with every
  segment row: *"Potential competition signal from public registry
  listings. Not a recruitment forecast; requires human feasibility
  review."*
- Every dashboard page renders the disclaimer banner via one shared
  `page_setup()`; a test asserts each page uses it.
- Competition bands (`low/moderate/elevated`) are relative percentile cuts
  and must always be described as such. The former composite priority score and
  priority bands were removed under ADR 0077 in favor of transparent raw metrics.
- Contact privacy enforcement: `stg_trial_contacts` is verified empty
  (`where 1 = 0`), `flatten_studies.py` drops all contact modules, and
  regression tests in `tests/test_contact_policy.py` mechanically assert zero
  contact leakage into Parquet exports or warehouse models per ADR 0065.

## Escalation rule
If an analysis appears to support a stronger claim than these guardrails
allow, the claim is out of scope for this project — route it to qualified
clinical-operations review with the underlying rows attached.
