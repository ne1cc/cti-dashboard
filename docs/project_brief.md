# Project brief — public-registry landscape screening

## Intended user and decision

The proposed user is a clinical-operations analyst preparing an initial geographic
feasibility review. The project helps describe reported study presence, inspect
contributing records and identify questions for further investigation.

The workflow and requirements are proposed. No stakeholder interviews or measured
business-benefit evaluation have established their operational usefulness.

## Proposed workflow

Agree on screening scope → examine the full geographic landscape → inspect
contributors and data gaps → export evidence → formulate follow-up questions.

The dashboard is an exploration tool. A short memo communicates scoped observations;
the audit export and metric dictionary let a reviewer inspect their calculation.
The [national case study](feasibility_case_study.md) demonstrates this workflow.

## Case-study question and scope

Describe the registry-reported recruiting-study landscape across all 50 U.S. states
for Phase 3 Alzheimer's disease studies. Compare distinct study counts and data
coverage using one consistently defined captured dataset. Identify patterns and
data gaps that warrant further investigation; do not infer recruitment prospects
or recommend states or sites from registry counts alone.

- Profile: existing `adrd` ingestion configuration.
- Condition: existing `alzheimers_disease` taxonomy group, without new matching rules.
- Phase: exactly `PHASE3`; existing normalization means combined phase strings
  such as `PHASE2/PHASE3` do not match this filter.
- Status: exactly overall `RECRUITING`, as reported in the captured public record.
  Location status is inspectable but does not gate the existing state calculation.
  These are studies listing locations, not verified recruiting sites.
- Geography: all 50 states. D.C. and PR, GU, VI, AS and MP are separately labeled
  additional jurisdictions and excluded from the 50-state national total.
- Geography matching: existing reported U.S.-country aliases and supported normalized
  state codes. Territories reported under a different country label may be excluded
  by that existing rule; the supplement does not promise complete territorial coverage.
- Dataset: freeze the existing competition observation selection on one complete
  captured date and one evaluation timestamp for every jurisdiction.

These choices precede inspecting the case-study results. Acquisition retains the
ADRD profile's existing query and status restrictions; this is not a census of all
registered or unregistered studies.

## Metric and reporting requirements

| Requirement | Acceptance criterion |
|---|---|
| Complete landscape | All 50 state rows appear, including observed zeros; other jurisdictions are separate |
| Study grain | One NCT ID counts once per state, even with repeated locations |
| Geographic overlap | National distinct count uses the union across the explicit 50-state scope, not the sum of state counts |
| Defined percentages | Every state share divides its contributors by the same eligible recruiting cohort; national geographic coverage uses the 50-state contributor union over that cohort |
| Coverage interpretation | State shares describe cohort representation under the rules, not capture completeness within a state |
| Unknown geography | Report studies with undetermined state membership; distinguish them from known nonmatching geography |
| Zero/missing | Zero means no qualifying recorded match; unavailable dataset means no published result; zero-denominator percentages are undefined |
| Enrollment | Trial-wide enrollment stays in audited study detail, outside headline state results |
| Provenance | Captured dates/run IDs, public source dates, retrieval times, filters and rule identity are inspectable |
| Validation | Every published figure reconciles to its audit and an independent SQL calculation; raw-record sampling is documented separately |

The existing audit exports include both a broader all-status eligible denominator
and a confirmed-recruiting denominator. The case study labels and uses the latter
for its headline recruiting-cohort representation and national coverage.

## Interpretation and follow-up

Counts establish reported study presence under stated screening rules. They do
not measure patient availability, site capacity, recruitment speed, clinical
validity or protocol equivalence. No state/site recommendation or unsupported
feasibility score belongs in this descriptive case study.

Follow-up requires protocol review, confirmation of current study information,
relevant site capabilities and other evidence appropriate to the decision.
See [business use](business_case.md) and
[interpretation guardrails](clinical_interpretation_guardrails.md).

## Reliability and evidence boundaries

Use complete successful captures and a validated warehouse. The case-study builder
reads the warehouse without modifying the app or deployed data. Preserve each
existing audit structure inside a compressed JSON package.

The package records active rule hashes and the source code commit; reproducing it
from source requires retained raw records and the corresponding rule files.
Raw references may stop resolving after configured pruning. The package preserves
exported audit evidence, not the entire underlying registry source archive.
