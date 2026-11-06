"""Authored, plain-language wording for the patient trial page (Issue #66).

Every string we write ourselves lives here so a maintainer can review the wording in one
place. Registry text (summaries, intervention descriptions, criteria) is never edited: the
page shows it verbatim and labels it. Aim for about a 9th-grade reading level; no automated
readability check exists yet (known gap).
"""

from __future__ import annotations

REGISTRY_TEXT_LABEL = "Registry text, shown exactly as written"

PAGE_NOTICE = (
    "This page is for information only. It is not medical advice. "
    "Talk with your clinician and the study team before you decide anything."
)
ELIGIBILITY_NOTE = (
    "These are the study's own rules for who can join. "
    "Review them with a clinician and the study team."
)
UNCLASSIFIED_NOTE = (
    "We could not safely sort this text into a join list or a do-not-join list. "
    "Please read it as written."
)
NO_CRITERIA = "The registry has no eligibility text for this study."
NO_INTERVENTIONS = "The registry lists no treatments or other interventions for this study."
CONTACT_TEXT = (
    "We do not keep contact names, phone numbers, or emails. "
    "The official registry record has the current contact details for the study team."
)
DISTANCE_HINT = "Enter your ZIP code to sort sites by distance. Distances are straight-line miles."
DISTANCE_UNKNOWN_ZIP = "We could not find that ZIP code, so sites are not sorted by distance."
NO_SITES = "The registry lists no sites for this study."

STATUS = {
    "RECRUITING": "Recruiting",
    "NOT_YET_RECRUITING": "Not yet recruiting",
    "ENROLLING_BY_INVITATION": "Enrolling by invitation only",
    "ACTIVE_NOT_RECRUITING": "Active, not recruiting",
    "COMPLETED": "Completed",
    "SUSPENDED": "Paused",
    "TERMINATED": "Stopped early",
    "WITHDRAWN": "Withdrawn before enrolling anyone",
    "UNKNOWN": "Status not known",
}

PHASE_WORDS = {
    "EARLY_PHASE1": "First test in people",
    "PHASE1": "Early safety study",
    "PHASE1/PHASE2": "Early safety and effectiveness study",
    "PHASE2": "Study of whether a treatment works",
    "PHASE2/PHASE3": "Study of whether a treatment works, in a larger group",
    "PHASE3": "Large study to confirm a treatment works",
    "PHASE4": "Study of a treatment that is already approved",
    "NOT_APPLICABLE": "This kind of study has no phase",
}
PHASE_REGISTRY = {
    "EARLY_PHASE1": "Early Phase 1",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
}

PURPOSE = {
    "TREATMENT": "Treating a condition",
    "PREVENTION": "Preventing a condition",
    "DIAGNOSTIC": "Finding or diagnosing a condition",
    "SUPPORTIVE_CARE": "Supportive care, such as easing symptoms or side effects",
    "SCREENING": "Screening for a condition",
    "HEALTH_SERVICES_RESEARCH": "Learning how health care is delivered",
    "BASIC_SCIENCE": "Basic science, to learn how the body works",
    "DEVICE_FEASIBILITY": "Testing whether a device can work",
    "OTHER": "Another purpose",
}

ALLOCATION = {
    "RANDOMIZED": "Chance decides which group you are in (randomized).",
    "NON_RANDOMIZED": "People are placed in groups without using chance (not randomized).",
    "NA": "There is only one group, so no one is assigned to a group.",
}

MASKING = {
    "NONE": "Open label: everyone knows which treatment is given.",
    "SINGLE": "Blinded: one group involved does not know which treatment is given.",
    "DOUBLE": "Blinded: two groups involved do not know which treatment is given.",
    "TRIPLE": "Blinded: three groups involved do not know which treatment is given.",
    "QUADRUPLE": "Blinded: four groups involved do not know which treatment is given.",
}

INTERVENTION_TYPE = {
    "DRUG": "Drug",
    "DEVICE": "Device",
    "BIOLOGICAL": "Biological, such as a vaccine or blood product",
    "PROCEDURE": "Procedure or surgery",
    "RADIATION": "Radiation",
    "BEHAVIORAL": "Behavioral, such as counseling or exercise",
    "GENETIC": "Genetic",
    "DIETARY_SUPPLEMENT": "Dietary supplement",
    "COMBINATION_PRODUCT": "Combination product",
    "DIAGNOSTIC_TEST": "Diagnostic test",
    "OTHER": "Other",
}

PLACEBO = {
    True: "This study has a placebo group. A placebo looks like the real treatment but has "
    "no active medicine.",
    False: "The registry does not list a placebo group for this study.",
    None: "The registry record does not say whether there is a placebo group.",
}

SEX = {
    "ALL": "All sexes",
    "FEMALE": "Female only",
    "MALE": "Male only",
    "UNKNOWN": "Not stated",
}

HEALTHY_VOLUNTEERS = {
    True: "Healthy volunteers are accepted",
    False: "Healthy volunteers are not accepted",
    None: "Not stated",
}

AGE_GROUP = {
    "CHILD": "Child (up to 17)",
    "ADULT": "Adult (18 to 64)",
    "OLDER_ADULT": "Older adult (65 and over)",
}

NO_AGE_LIMITS = "No age limits stated"


def lookup(table: dict[str, str], key: str | None) -> str | None:
    """Return the authored wording, else the registry value made readable (never invented)."""
    if not key:
        return None
    return table.get(key) or key.replace("_", " ").capitalize()


def phase_label(normalized: str | None) -> str:
    """Phase in words plus the registry phase, e.g. 'Early safety study (Phase 1)'."""
    value = normalized or ""
    words = PHASE_WORDS.get(value)
    registry = "/".join(PHASE_REGISTRY.get(token, token) for token in value.split("/") if token)
    if value in ("", "UNKNOWN", "NOT_APPLICABLE"):
        registry = ""
    if words and registry:
        return f"{words} ({registry})"
    return words or registry or "Phase not stated"


def age_text(min_raw: str | None, max_raw: str | None) -> str:
    """Age limits using the registry's own wording (for example '18 Years')."""
    if min_raw and max_raw:
        return f"{min_raw} to {max_raw}"
    if min_raw:
        return f"{min_raw} and older"
    if max_raw:
        return f"Up to {max_raw}"
    return NO_AGE_LIMITS


def span_words(start: str | None, end: str | None) -> str | None:
    """Rough length between two registry dates (YYYY-MM or YYYY-MM-DD), or None if unclear."""
    try:
        sy, sm = (int(part) for part in (start or "").split("-")[:2])
        ey, em = (int(part) for part in (end or "").split("-")[:2])
    except ValueError:
        return None
    months = (ey - sy) * 12 + (em - sm)
    if months < 0:
        return None
    if months < 2:
        return "about a month or less"
    if months < 24:
        return f"about {months} months"
    return f"about {round(months / 12)} years"
