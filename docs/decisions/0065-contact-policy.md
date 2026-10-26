# ADR 0065: Patient & Caregiver Contact Policy — Official Registry Link plus Site List

- **Status:** Accepted (Approved User Mandate)
- **Deciders:** Product, Architecture, Clinical Operations Review
- **Date:** 2026-10-24
- **Technical Story:** Issue #65 (Part of Epic #42 — Patient & Caregiver Trial Finder)

---

## Context

Clinical trial finders designed for patients and caregivers aim to help individuals discover relevant active trials and take the next step toward participation. Connecting prospective participants with the appropriate clinical trial contacts (e.g. study coordinators, central screening teams) is a pivotal moment in this user journey.

The ClinicalTrials.gov API v2 payload exposes several tiers of contact and investigator metadata:
1. `protocolSection.contactsLocationsModule.centralContacts`: Study-wide contacts designated for prospective participants, including individual contact names, roles, telephone numbers, extensions, and email addresses.
2. `protocolSection.contactsLocationsModule.overallOfficials`: Study directors, principal investigators, and medical monitors, including individual names, affiliations, and clinical/scientific roles.
3. `protocolSection.contactsLocationsModule.locations[].contacts`: Facility-level study coordinators and sub-investigators, including individual names, roles, direct facility phone numbers, and email addresses.

Historically, this platform maintained a strict blanket privacy guardrail:
- `src/transform/flatten_studies.py` deliberately bypassed contact and investigator fields during the bronze-to-silver transformation.
- `dbt_clinical_trials/models/staging/stg_trial_contacts.sql` was intentionally deployed as an empty model (`where 1 = 0`).
- Parquet exports and gold warehouse marts retained zero contact records.

In evaluating Epic #42, the core question arose whether to reverse this blanket exclusion for a patient-facing product:
- Should central contact phone numbers and emails be extracted, ingested, and presented directly on the trial detail page?
- Should investigator names or overall officials be surfaced?
- What are the privacy, data freshness, scraping, and regulatory takedown implications of retaining personal contact details in analytical datastores and API endpoints?

---

## Decision

The platform adopts the user-approved policy:
> **"Official registry contact link plus site list; store or expose no contact details."**

Specifically:
1. **Zero Contact Extraction and Storage:**
   The data pipeline will **never** extract, transform, store, or warehouse personal contact or investigator records (`centralContacts`, `overallOfficials`, or location-level `contacts`). Stored entities in silver Parquet files and gold warehouse models remain entirely free of personal contact names, telephone numbers, and email addresses. `stg_trial_contacts` remains an explicitly empty model (`where 1 = 0`).
2. **Authoritative Official Registry Link:**
   Prospective participants and caregivers will be directed to the authoritative, live ClinicalTrials.gov study record (e.g., `https://clinicaltrials.gov/study/{nct_id}`) for study team contact instructions. Trial detail views and read API endpoints will generate and provide canonical registry deep links.
3. **Geographic Facility & Site List:**
   The platform will display facility names and normalized location attributes (facility name, city, state, zip code, country, and location recruitment status) from `silver_trial_locations` and `fct_trial_site`. This empowers patients and caregivers to identify geographically proximate participating centers while leaving actual contact outreach to the official registry record.
4. **API and Bulk Export Boundaries:**
   Read API endpoints (Issue #58 / #59 / #60) and any data export facilities will never expose personal contact fields. Response models will include canonical registry URLs and facility listings only.

---

## Rationale & Tradeoffs

### 1. Data Freshness and Operational Staleness
Contact information for clinical trials changes frequently: site coordinators turn over, institutional phone extensions change, and central recruitment vendors transition. When third-party platforms ingest and cache contact details, the data inevitably grows stale. Stale contact information frustrates patients, increases emotional burden, and leads to misdirected inquiries. Linking directly to the live ClinicalTrials.gov study record guarantees that patients and caregivers always access the latest, sponsor-maintained contact protocols without lag.

### 2. Privacy, Anti-Scraping, and Security
Publishing personal phone numbers, coordinator email addresses, and investigator names in public APIs or downloadable data artifacts creates an attractive target for automated scraping, commercial spam, and unsolicited communications. Protecting the privacy of clinical research coordinators and investigators is paramount to maintaining institutional trust.

### 3. Takedown and Removal Expectations (Zero-Lag / Zero-Toil)
Under modern data privacy frameworks (e.g. GDPR, CCPA/CPRA) and clinical research norms, individuals have the right to request prompt deletion or correction of their contact information. When clinical personnel depart an institution or request removal, local databases with historical snapshots face significant operational toil and synchronization lag to scrub records across bronze, silver, and warehouse layers. Under the zero-contact-storage policy, removal compliance is instantaneous and zero-toil: because no personal contact details are ever ingested or stored, no takedown pipelines or remediation procedures are required.

### 4. Preservation of Platform Guardrails
This decision directly aligns with `docs/clinical_interpretation_guardrails.md`: the platform provides analytical intelligence and registry navigation over public listings without serving as a medical referral agency, clinical recruiter, or intermediary between participants and investigators.

---

## Scope Boundaries

| Category | In Scope | Prohibited / Out of Scope |
|---|---|---|
| **Identifiers & Links** | Canonical study deep links (`https://clinicaltrials.gov/study/{nct_id}`) | Custom contact forms, third-party relay mailers |
| **Site Metadata** | `facility_name`, `city`, `state`, `zip_code`, `country`, `location_status` | Facility coordinator names, phone numbers, email addresses |
| **Central Leadership** | Lead sponsor name and sponsor class | `centralContacts` names, phones, extensions, emails |
| **Investigators & Staff**| None | `overallOfficials` names, affiliations, roles |
| **Data Pipelines** | `silver_trials`, `silver_trial_locations` without contact attributes | Ingestion into `stg_trial_contacts` (`where 1 = 0` preserved) |
| **API Endpoints** | Canonical registry URLs, facility arrays | Contact fields in response schemas or OpenAPI contracts |

---

## Downstream Implementation Roadmap

1. **Stage A (#65 — Current Stage):**
   - Formalize policy in this ADR.
   - Update `docs/clinical_interpretation_guardrails.md` and `docs/data_dictionary.md`.
   - Implement regression test suite in `tests/test_contact_policy.py` verifying contact exclusion across transform, Parquet schemas, and dbt staging.
2. **Read API Contract (Issue #58 / Package 10):**
   - Define OpenAPI specifications incorporating canonical study URLs and facility site lists, omitting contact fields.
3. **Read API Implementation (Issues #59 & #60):**
   - Expose endpoints adhering to the link-plus-site-list contract.
4. **Patient & Caregiver Detail UI (Issue #66):**
   - Implement trial detail view rendering the authoritative ClinicalTrials.gov contact link and participating site locations.
