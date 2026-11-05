# Usability Study Task Script & Facilitator Protocol

Protocol for moderated sessions evaluating the patient and caregiver clinical-trial finder.

> **Template Notice**: This document is an unapproved draft template provided for research preparation. It does **not** claim or imply legal or ethics-board approval. Institutional review and approval must be completed by the study owner prior to participant recruitment. This document contains no real participant data.

---

## Facilitator Protocol & Observer Rules

1. **Think-Aloud Protocol**: Ask the participant to speak their thoughts aloud as they navigate ("Please tell me what you are looking at, what you expect to happen, and anything that confuses you").
2. **Neutral Observation**:
   - Do **not** guide, lead, or correct the participant.
   - Do **not** explain how features are intended to work or defend design decisions.
3. **Handling Hesitation**:
   - If the participant pauses or appears stuck, avoid pointing to buttons or controls.
   - Instead, ask neutral prompting questions:
     - *"What are you looking for right now?"*
     - *"What did you expect to see on this screen?"*
     - *"What does that label mean to you?"*
4. **Data Capture**:
   - Start the task timer when the prompt is read; stop the timer when the participant indicates completion or abandons the task.
   - Record time-to-task, wrong turns or navigation errors, comprehension accuracy, and verbatim quotes in the [Notes Template](notes-template.md).

## Session Setup

To run the patient finder for usability sessions, start the local application using one of the following verified setup modes:

### Mode 1: Live Registry Search (Default Patient Finder MVP)
This mode connects to the ClinicalTrials.gov API v2 and uses the local Census Gazetteer for offline postal code resolution:

```bash
# Point to Census Gazetteer coordinates for ZIP code lookups:
export CTI_GAZETTEER_DIR="tests/fixtures/gazetteer"

# Start the patient-facing FastAPI server on port 8000:
uv run uvicorn src.api.app:create_app --factory --host 127.0.0.1 --port 8000
```
Open `http://localhost:8000/` in the participant's or facilitator's web browser.

### Mode 2: Local DuckDB Warehouse Backend
If evaluating against a pre-built local DuckDB warehouse snapshot:

```bash
export CTI_GAZETTEER_DIR="tests/fixtures/gazetteer"
export CTI_WAREHOUSE_PATH="data/warehouse/clinical_trials.duckdb"
uv run uvicorn src.api.app:create_app --factory --host 127.0.0.1 --port 8000
```
Open `http://localhost:8000/` in the web browser.

> **Facilitator Note on Live Trials**: Live registry searches return recruiting trials currently listed on ClinicalTrials.gov. When evaluating against live search or a custom build, the facilitator should perform a quick preliminary search for the scenario condition and ZIP code before the session to confirm which studies appear and note their placebo status for the answer key, or guide the participant to the illustrative reference trial.

---

## Test Scenarios (Illustrative & Fixture-Safe Data)

Each session uses one of two standardized fictional scenarios. Participants must **never** use their own personal health data. Scenarios are clearly labeled as illustrative practice scenarios with defined benchmark answers for scoring.

### Scenario A: Early Alzheimer Disease (San Francisco Bay Area)
- **Fictional Persona**: "Pat", 68 years old, recently diagnosed with early-stage Alzheimer disease. Seeking active trial options near San Francisco, California.
- **Starting Location**: ZIP code `94143` (San Francisco, CA).
- **Condition Keywords**: `Alzheimer` or `Early Alzheimer Disease`.
- **Target Reference Trial**: `NCT04245644` (*Phase 3 Evaluation of Novel Tau-Targeted Therapy in Early Alzheimer Disease*, facility located at UCSF Memory and Aging Center, San Francisco, CA 94143; represented in repository fixtures `tests/fixtures/api/live_search.json` and `live_detail.json`).
- **Intervention Arms**: Two parallel arms: Experimental arm receiving BIIB080 (intrathecal antisense oligonucleotide) vs. Placebo comparator arm receiving matching intrathecal placebo.

### Scenario B: Non-Small Cell Lung Cancer (Durham / Research Triangle)
- **Fictional Persona**: "Morgan", 62 years old, diagnosed with non-small cell lung cancer. Seeking clinical trial options near Durham, North Carolina.
- **Starting Location**: ZIP code `27710` (Durham, NC).
- **Condition Keywords**: `Non-Small Cell Lung Cancer` or `Lung Cancer`.
- **Target Reference Trial (Illustrative)**: `NCT04859881` (*Phase 3 Study of Pembrolizumab with Chemotherapy versus Chemotherapy Alone in Advanced Non-Small Cell Lung Cancer*, facility located at Duke Cancer Institute / Thoracic Oncology, Durham, NC 27710).
- **Intervention Arms**:
  - Arm A (Experimental): Pembrolizumab + standard chemotherapy (Carboplatin + Pemetrexed).
  - Arm B (Placebo Comparator): Matching Placebo + standard chemotherapy (Carboplatin + Pemetrexed).

---

## Core Tasks & Prompts

### Task 1: Find a Trial Near a Given ZIP Code
- **Scenario A Prompt**:
  > \"Imagine you are helping Pat, who lives near San Francisco ZIP `94143` and has been diagnosed with early Alzheimer disease. Using this website, find a trial near them that Pat could consider.\"
- **Scenario B Prompt**:
  > \"Imagine you are helping Morgan, who lives near Durham, NC ZIP `27710` and has non-small cell lung cancer. Using this website, find a trial near them that Morgan could consider.\"
- **Observer Notes**:
  - Does the participant locate and use the condition and ZIP code input fields?
  - Does the search return relevant nearby results within acceptable distance?
  - Note any formatting issues with ZIP codes, typos, or confusion around distance radius filters.

---

### Task 2: Explain What Participation Involves
- **Prompt**:
  > \"Select one of the trials you found and review the details. In your own words, please explain what taking part in this study would involve for the participant.\"
- **Observer Notes**:
  - Can the participant explain the treatment type, study procedures, visit requirements, and overall study duration?
  - Do they locate the study description, arm details, and schedule?
  - Note any medical jargon or clinical trial terminology that impedes comprehension.

---

### Task 3: Comprehension Questions
After the participant reviews the trial details, ask the following specific verification questions:

- **Question 3a (Placebo Check)**:
  > \"Does this trial include a placebo?\"
  - *Scenario A Reference (NCT04245644)*: **Yes**. The trial includes two parallel arms: active BIIB080 and a matching placebo comparator.
  - *Scenario B Reference (NCT04859881)*: **Yes**. Arm B uses a matching placebo comparator alongside chemotherapy. *(If evaluating an alternative trial from the build under test, verify whether that specific study uses a placebo or active control and record the answer prior to the session).*
  - *Observer Notes*: Note where on the screen the participant looks to verify this (study design section, arm intervention table, or overview). Did they understand the distinction between active drug and placebo?

- **Question 3b (Eligibility & Exclusions)**:
  > "Who can join this trial, and what criteria would prevent someone from participating?"
  - *Observer Notes*: Does the participant find the eligibility criteria? Do they understand age constraints, sex requirements, and medical exclusion clauses? Is the eligibility text readable or overwhelming?

---

### Task 4: Say How to Contact the Study Team
- **Prompt**:
  > "Suppose the participant wants to get in touch with the study team to ask questions or express interest in joining. How would they contact someone about this trial?"
- **Observer Notes**:
  - Does the participant find a phone number, contact email, or study coordinator details?
  - Can they distinguish between central trial contacts and local facility/site contacts?
  - Note if contact details are missing, unclear, or hidden behind extra clicks.

---

### Task 5: Identify What Is Unclear, Confusing, or Surprising
- **Prompt**:
  > "Looking over the search results and the trial details page, what information on this site felt unclear, confusing, or surprising? Was there anything you expected to see that was missing?"
- **Observer Notes**:
  - Record open-ended feedback and exact participant quotes.
  - Note reactions to visual design, scientific terminology, page layout, and usability barriers.

---

## Post-Task Wrap-Up

1. Thank the participant for their time and valuable feedback.
2. Reiterate that their responses are anonymous and that no identifying data is saved.
3. Facilitator immediately completes the [Notes Template](notes-template.md) while observations are fresh.
