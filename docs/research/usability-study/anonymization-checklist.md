# Anonymization Checklist for Usability Study Artifacts

Mandatory de-identification protocol to run before committing any session notes, quotes, or findings to git.

> **Template Notice**: This checklist is an unapproved draft template provided for research data protection and operational hygiene. It does **not** claim or imply legal or ethics-board approval. The study owner must ensure all institutional data governance and privacy policies are adhered to.

---

## Zero-PHI and Zero-PII Policy

All artifacts committed to the repository must contain **zero real participant data**, zero Protected Health Information (PHI), and zero Personally Identifiable Information (PII).

Any personal detail mentioned inadvertently during a session must be redacted immediately upon note-taking.

---

## Pre-Commit Verification Checklist

Review every file intended for commit against the following criteria:

- [ ] **No Participant Names or Monikers**:
  - Participant full names, first names, nicknames, and initials have been completely removed.
  - Participants are referenced exclusively by their assigned codes: `P1`, `P2`, `P3`, `P4`, or `P5`.

- [ ] **No Contact Information**:
  - No personal email addresses, phone numbers, postal addresses, or social media handles.
  - No meeting invite URLs, Zoom/Teams links, or meeting room IDs.

- [ ] **No Protected Health Information (PHI)**:
  - No personal medical history, individual diagnoses, medication regimens, surgery history, or private health narratives of participants or their family members.
  - Fictional scenario details (e.g. "Pat", "Morgan") are preserved; any personal disclosures made by participants during think-aloud sessions are completely omitted.

- [ ] **No Identifying Institutional or Clinical Affiliations**:
  - Specific clinic names, hospital department names, employer names, or specialized practice groups that could narrow down the participant's identity are replaced with generic descriptors (e.g., replace *"St. Jude Oncology Clinic 3B"* with *"[Regional academic cancer center]"*).

- [ ] **No Precise Timestamps or Calendar Correlation**:
  - Precise calendar start/stop timestamps that correlate directly with email invites or video meeting logs are omitted. Use relative elapsed times (e.g., `45 seconds`, `2 minutes`) rather than absolute calendar times.

- [ ] **No Audio, Video, or Raw Recording Artifacts**:
  - No video files (`.mp4`, `.mov`), audio recordings (`.mp3`, `.wav`), or raw un-redacted machine transcripts are committed to version control or stored in repository history.
  - Only clean, de-identified markdown notes and synthesized summaries are tracked in git.

- [ ] **Verbatim Quotes Scrubbing**:
  - All quoted remarks are screened to ensure they do not disclose unique biographical details, location identifiers outside the scenario, or idiosyncratic identifiable details.

---

## Automated Verification Audit

Run this automated shell check before adding files to git to catch common patterns of accidental disclosure:

```bash
# 1. Check for email patterns
git diff --cached | grep -E -i "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}" && echo "WARNING: Possible email detected in staged diff"

# 2. Check for phone number patterns
git diff --cached | grep -E "\b[0-9]{3}[-. ][0-9]{3}[-. ][0-9]{4}\b" && echo "WARNING: Possible phone number detected in staged diff"

# 3. Check for unauthorized media files
git status --porcelain | grep -E "\.(mp4|mov|mp3|wav|m4a|webm)$" && echo "ERROR: Media files detected in repository tree"
```

---

## Accidental Disclosure Remediation

If identifiable participant data or PHI is committed:
1. **Immediate Notice**: Inform the study owner and repository maintainer immediately.
2. **Rewrite Git History**: Do not merely revert the commit with a second commit; use `git filter-repo` or repository history rewriting tools to purge the artifact from all commits and branch refs.
3. **Notify Institution**: Follow the sponsoring institution's protocol for reporting and mitigating data incidents.
