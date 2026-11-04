# Usability study kit (issue #70)

Materials for running the 5-person study of the patient and caregiver trial finder.
The sessions themselves need real participants and cannot be run by an agent. Nothing here
records results; findings go in `docs/research/usability-findings.md` after the sessions.

## Participants (5)

| Role | Count | Notes |
|---|---|---|
| Patient or caregiver | 2 | Not a clinician; has searched for care information online |
| Nurse navigator or clinic coordinator | 2 | Helps patients find trials in their work |
| Clinician | 1 | Familiar with trial referral |

## Consent (read aloud, record verbal or written agreement)

> **Draft wording, have it reviewed before use.** This is a 30-minute session to test a website that
> helps people find clinical trials. We are testing the website, not you. We will ask you to
> complete a few tasks and talk through what you are thinking. Please do not tell us any personal
> health information; use the practice scenarios we give you. We will write notes and may time the
> tasks. Notes are anonymised (P1 to P5) and no names are stored with them. You can stop at any
> time. Do you agree to take part?

Record only: participant code, role, date, and that consent was given. No names, contact details or PHI.

## Task script (per session)

Scenario cards use an invented person; the participant never uses their own details.

1. **Find:** "Someone in ZIP `<ZIP>` has `<condition>`. Find a trial they could look at that is near them."
2. **Explain participation:** "Tell me in your own words what taking part in this trial would involve."
3. **Placebo check:** "Does this trial include a placebo?" (answer is on the page; note where they looked)
4. **Who can join:** "What would stop this person from joining?" (age, sex or exclusion criteria)
5. **Contact:** "How would you get in touch with the study team?"
6. **Clarity:** "What on this page is unclear or surprising?"

Observer rules: do not help or explain; note where the participant hesitates; ask "what are you looking for?"
rather than pointing.

## Measures

For each task record time to complete, errors or wrong turns, whether the participant answered the
comprehension question correctly, and verbatim confusion points.

## Findings template (copy to `docs/research/usability-findings.md`)

```markdown
# Usability findings: <date range>

## Setup
App build / commit: <sha>. Participants: 5 (roles, codes only). Scenarios used: <list>.

## Results by task
| Task | P1 | P2 | P3 | P4 | P5 | Median time | Errors | Comprehension correct |
|---|---|---|---|---|---|---|---|---|

## Verbatim confusion points
- P<n>, task <k>: "<quote>"

## Top 3 problems
1. <problem, evidence, proposed fix> (follow-up issue #<n>)

## Effect on priorities (#40)
<unchanged, or what the findings contradict>
```

## After the sessions
Commit the findings, open follow-up issues for the top 3 problems, and update #40 if the findings
contradict its priorities.
