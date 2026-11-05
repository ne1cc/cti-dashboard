# Usability Study Kit (Issue #70, Epic #42)

Preparation materials and protocol templates for the 5-person usability study of the patient and caregiver trial finder.

> **Template Notice**: This kit contains preparation templates for research facilitators and study owners. It does **not** claim or imply legal, regulatory, or Institutional Review Board (IRB) / research ethics board approval. The study owner and sponsoring organization must review, adapt, and secure all required institutional and ethics approvals before recruiting human participants or administering sessions.

---

## Purpose

Issue #70 specifies a 5-person qualitative usability study evaluating the patient and caregiver clinical-trial finder (part of Epic #42). Recruiting human participants, conducting live sessions, and analyzing qualitative feedback are human activities that cannot be run by an automated agent.

This kit prepares all necessary materials in advance so that a study owner or research facilitator can immediately recruit participants, conduct sessions, capture standardized observations, and synthesize findings into actionable product roadmap improvements.

Findings from this study directly inform issue #40 (*Roadmap: from analyst feasibility tool to an open clinical-trial access platform*), particularly Milestone M1 (Patient finder MVP).

---

## Recruitment Target: Three Roles (5 Participants)

The study recruits 5 participants across three distinct user roles to capture representative perspectives on trial discovery, comprehension, and contact pathways:

| Role | Target Count | Participant Codes | Profile & Experience |
|---|---|---|---|
| **Patient or caregiver** | 2 | `P1`, `P2` | Non-clinicians who have previously searched online for healthcare information, treatment options, or clinical trials for themselves or a family member. |
| **Nurse navigator or clinic coordinator** | 2 | `P3`, `P4` | Clinical support professionals who assist patients with care navigation, trial identification, or referral options in daily practice. |
| **Clinician** | 1 | `P5` | Physician or advanced practice provider familiar with clinical trial eligibility, patient referral, and arm allocation. |

---

## Session Structure & Length

- **Session Duration**: 30 minutes per participant.
- **Session Format**: 1-on-1 moderated session conducted remotely via screen-share or in person.
- **Facilitation Method**: Concurrent Think-Aloud protocol (participants verbalize their thoughts, expectations, and hesitations as they interact with the interface).
- **Test Scenarios**: Standardized, fictional practice scenarios using fixture-safe data.

---

## Data Privacy & Zero-PHI Guarantee

To protect participant privacy and maintain compliance:
- **No Protected Health Information (PHI)**: Participants must **never** share their personal medical history, individual diagnoses, or health records. Standardized fictional personas are used for all tasks.
- **Strict Anonymization**: Real participant names, contact details, and institutional affiliations are never committed to version control. Notes refer solely to anonymous participant codes (`P1` through `P5`).
- **Pre-Commit Verification**: All session notes and findings must be audited against the [Anonymization Checklist](anonymization-checklist.md) prior to being committed to the repository.

---

## How Findings Feed Roadmap #40

The primary objective of this usability evaluation is to validate and refine the transition outlined in #40:
1. **Identify Critical Usability Blockers**: Pinpoint where patients, navigators, and clinicians face friction when finding local trials, interpreting eligibility criteria, and locating contact information.
2. **Top 3 Actionable Problems**: Findings are synthesized in the [Findings Template](findings-template.md) to identify the top 3 usability problems, each linked directly to a newly opened follow-up GitHub issue.
3. **Roadmap Recalibration**: Results provide empirical evidence to confirm or adjust the priorities of Epic #42 and Milestone M1 in #40 before subsequent development phases begin.

---

## Study Kit Documents

This directory contains the complete set of preparation templates:

- **[Task Script](task-script.md)**: Moderated protocol containing the four core tasks from #70, two fixture-safe realistic scenarios, comprehension questions, and observer rules.
- **[Consent Template](consent-template.md)**: Plain-language informed consent form with institutional placeholders, zero-PHI commitments, and withdrawal rights.
- **[Notes Template](notes-template.md)**: Standardized observation template tracking time-to-task, wrong turns, comprehension accuracy, and verbatim confusion points for each participant.
- **[Findings Template](findings-template.md)**: Post-study synthesis template aggregating results across all 5 sessions, cataloging verbatim quotes, defining the top 3 issues, and detailing the impact on #40.
- **[Anonymization Checklist](anonymization-checklist.md)**: Mandatory verification checklist to inspect notes and reports for PHI/PII before committing to git.

---

## Study Execution Lifecycle

```
[1. Adapt Consent] ──> [2. Recruit 3 Roles] ──> [3. Conduct Sessions] ──> [4. Anonymize Notes] ──> [5. Synthesize & Feed #40]
  consent-template        P1-P5 (5 users)         task-script.md            anonymization-           findings-template.md
                                                  notes-template.md         checklist.md             (Top 3 follow-up issues)
```
