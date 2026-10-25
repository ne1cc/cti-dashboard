# ADR 0082: Repositioning Analyst Persona as Exploratory Hypotheses and Archiving Business Case

- **Status:** Accepted (Approved User Mandate)
- **Deciders:** Product, Architecture, Clinical Operations Review
- **Date:** 2026-10-25
- **Technical Story:** Issue #82 (Part of Epic #46 — Rework the analyst workbench: score, small-n handling, provenance UX)

---

## Context

The Clinical Trial Access & Recruitment Competition Intelligence repository originally emphasized a single primary user persona: a commercial clinical-operations analyst preparing geographic feasibility reviews for pharmaceutical sponsors (`docs/business_case.md`). This document outlined assumed current-state workflows, estimated manual time burdens, and proposed return-on-investment (ROI) calculations.

### Identified Challenges and Misalignment

1. **Unvalidated Assumptions:** The analyst persona, time-and-motion estimates, and commercial workflow assumptions were formulated as speculative hypotheses. They were not validated through structured practitioner interviews, contextual inquiry, or empirical operational baselining.
2. **Platform Strategic Evolution:** With the ratification of Epic #40, ADR 0061 (Dual-Frontend Architecture), and ADR 0065 (Patient & Caregiver Contact Policy), the platform's core identity broadened into an open, multi-stakeholder trial access and intelligence system serving patients, caregivers, academic researchers, and clinical investigational teams alongside industry analysts.
3. **Documentation Misrepresentation:** Presenting unverified commercial ROI narratives in root documentation conflicted with the platform's commitment to rigorous empirical honesty and clinical interpretation guardrails (`docs/clinical_interpretation_guardrails.md`).

---

## Decision

The platform adopts **Option B (User Approved)**:
> **"Reposition the clinical-operations analyst persona and business case as exploratory hypotheses; archive `docs/business_case.md`; headline the open multi-stakeholder access platform."**

Specifically:
1. **Archive Business Case Document:**
   - Move `docs/business_case.md` to `docs/archive/business_case.md`.
   - Add a prominent, standardized disclaimer banner at the head of the archived document:
     ```markdown
     > [!NOTE]
     > **Archived Exploratory Hypothesis (ADR 0082)**
     > This document details an exploratory business case and persona hypothesis for clinical feasibility analysts. It has not been validated through empirical practitioner interviews or operational time-and-motion studies. Current development focuses on the open, multi-stakeholder access platform (ADR 0040).
     ```
2. **Headline Open Multi-Stakeholder Access Platform:**
   - Update `README.md` and `PROJECT_DOCUMENTATION.md` to establish the primary identity of the project: an open access trial discovery and competition intelligence platform that serves patients, caregivers, investigators, and researchers.
   - Describe analyst and feasibility screening features as exploratory capabilities over public registry data rather than validated commercial workflows.
3. **Update Cross-Document References:**
   - Update all references and hyperlinks across repository documentation to reference `docs/archive/business_case.md` with appropriate historical context.
4. **Retain Streamlit Explorer as an Exploratory Tool:**
   - Retain the Streamlit interface as an exploratory analytical workbench for registry data exploration, while clearly labeling all views as descriptive public data exploration rather than prescriptive decision systems.

---

## Options Evaluated

- **Option A (Rejected):** Conduct 3-5 in-depth practitioner interviews to validate the commercial feasibility analyst persona and workflow before continuing feature development.
  - *Pros:* Would gather primary empirical evidence on whether feasibility analysts actually need this specific workflow.
  - *Cons:* Delays platform development; prioritizes an unproven commercial niche over high-impact patient and multi-stakeholder access capabilities (Epic #42).
- **Option B (Adopted — Approved):** Reposition the analyst persona and business case as exploratory hypotheses; archive `docs/business_case.md`; headline the open multi-stakeholder access platform.
  - *Pros:* Immediately aligns documentation with truth; removes unvalidated marketing claims; anchors the project around its open-data core mission (ADR 0040); preserves exploratory code without false claims.
  - *Cons:* Explicitly acknowledges that commercial ROI for analysts is an open, unvalidated question.
- **Option C (Rejected):** Completely deprecate and remove all analyst views, retaining only patient-facing interfaces.
  - *Pros:* Single persona focus.
  - *Cons:* Destroys valuable analytical models (e.g., sponsor concentration HHI, site overlap, geographic density) that researchers and developers actively use.

---

## Consequences

- The platform's public documentation accurately communicates its verified capabilities without speculative claims.
- Development priorities focus on robust public registry data pipelines, public read APIs, and patient/caregiver search experiences.
- Historical thinking regarding feasibility analysts is preserved in `docs/archive/business_case.md` for transparency and potential future discovery work.
