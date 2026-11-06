"""Server-rendered patient finder backed by the existing ASGI read API."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.types import Message, Scope

from src.api import patient_copy as copy
from src.api.conditions import resolve_consumer_condition
from src.api.gazetteer import resolve_postal_code
from src.utils.privacy import patient_request

_ROOT = Path(__file__).resolve().parent
_TEMPLATES = Environment(
    loader=FileSystemLoader(_ROOT / "templates"),
    autoescape=select_autoescape(("html", "xml")),
)
_FIELDS = ("cond", "postal_code", "radius_mi", "age", "sex", "status")


class PathOnlyAccessFilter(logging.Filter):
    """Remove query strings from Uvicorn access records before they are formatted."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, target, version, status = record.args
            if isinstance(target, str):
                record.args = (client, method, urlsplit(target).path, version, status)
        return True


class PatientLogFilter(logging.Filter):
    """Remove exception and request details from logs during patient searches."""

    def filter(self, record: logging.LogRecord) -> bool:
        if patient_request.get():
            record.msg = "Patient request diagnostic (details suppressed)"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def install_access_log_filter() -> None:
    """Install the query-string filter once for each process running this app."""
    access_log = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, PathOnlyAccessFilter) for item in access_log.filters):
        access_log.addFilter(PathOnlyAccessFilter())
    for name in ("src.api.app", "src.api.cache", "urllib3.connectionpool"):
        target = logging.getLogger(name)
        if not any(isinstance(item, PatientLogFilter) for item in target.filters):
            target.addFilter(PatientLogFilter())


async def _read_api(
    app: FastAPI, request: Request, path: str, params: dict[str, str]
) -> tuple[int, dict[str, str], dict[str, Any]]:
    """Call the app's existing JSON route in-process, without making a network request."""
    scope: Scope = dict(request.scope)
    scope.update(
        path=path,
        raw_path=path.encode("ascii"),
        query_string=urlencode(params).encode("ascii"),
        method="GET",
        headers=[],
        state={"patient_request": True},
    )
    started = False
    status = 500
    headers: dict[str, str] = {}
    chunks: list[bytes] = []
    disconnected = asyncio.Event()

    async def receive() -> Message:
        nonlocal started
        if not started:
            started = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        nonlocal status, headers
        if message["type"] == "http.response.start":
            status = message["status"]
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1") for k, v in message["headers"]
            }
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    token = patient_request.set(True)
    try:
        await app(scope, receive, send)
    except Exception:
        # Error bodies from the shared API are handled below; never log query details here.
        status = 500
        chunks = []
    finally:
        patient_request.reset(token)
    try:
        payload = json.loads(b"".join(chunks)) if chunks else {}
    except (ValueError, TypeError):
        payload = {}
    return status, headers, payload if isinstance(payload, dict) else {}


def _freshness(headers: dict[str, str]) -> str:
    """Describe actual data retrieval/snapshot time, never a health-check timestamp."""
    timestamp = headers.get("data-updated")
    if not timestamp:
        return "Data freshness unavailable."
    if headers.get("data-snapshot") == "live-api":
        stale = " (cached data may be stale)" if headers.get("x-cache-status") == "STALE" else ""
        return f"Registry data retrieved: {timestamp}{stale}."
    return f"Warehouse snapshot: {timestamp}."


def _trial_view(trial: dict[str, Any], nct_id: str, *, has_distance: bool) -> dict[str, Any]:
    """Derive the display facts for one trial; registry text is passed through untouched."""
    design = trial.get("study_design") or {}
    dates = trial.get("dates") or {}
    elig = trial.get("eligibility") or {}
    sections = elig.get("eligibility_sections") or {}

    by_type: dict[str, list[dict[str, Any]]] = {}
    for item in trial.get("interventions") or []:
        label = copy.lookup(copy.INTERVENTION_TYPE, item.get("intervention_type")) or "Other"
        by_type.setdefault(label, []).append(item)

    def site_order(site: dict[str, Any]) -> tuple[Any, ...]:
        distance = site.get("distance_miles")
        return (
            distance is None,
            distance or 0,
            site.get("country") or "",
            site.get("state") or "",
            site.get("city") or "",
            site.get("facility_name") or "",
        )

    sites = []
    for site in sorted(trial.get("sites") or [], key=site_order):
        place = [site.get("city"), site.get("state"), site.get("country")]
        query = ", ".join(
            part
            for part in [site.get("facility_name"), *place[:2], site.get("zip_code"), place[2]]
            if part
        )
        distance = site.get("distance_miles")
        sites.append(
            {
                **site,
                "place": ", ".join(part for part in place if part),
                "status_text": copy.lookup(copy.STATUS, site.get("location_status")),
                "distance_text": None
                if distance is None
                else f"{distance:g} miles away"
                + (" (approximate)" if site.get("coordinate_approximate") else ""),
                "maps_url": "https://www.openstreetmap.org/search?" + urlencode({"query": query}),
            }
        )

    contact_url = trial.get("registry_contact_url") or ""
    if not contact_url.startswith(trial["registry_url"]):
        contact_url = trial["registry_url"]  # never follow an unexpected upstream destination

    return {
        "status": copy.lookup(copy.STATUS, trial.get("overall_status")),
        "phase": copy.phase_label(design.get("phase_normalized")),
        "purpose": copy.lookup(copy.PURPOSE, design.get("primary_purpose")),
        "allocation": copy.lookup(copy.ALLOCATION, design.get("allocation")),
        "masking": copy.lookup(copy.MASKING, design.get("masking")),
        "span": copy.span_words(dates.get("start_date"), dates.get("primary_completion_date")),
        "interventions": by_type,
        "placebo": copy.PLACEBO[trial.get("has_placebo_arm")],
        "age": copy.age_text(elig.get("minimum_age_raw"), elig.get("maximum_age_raw")),
        "sex": copy.lookup(copy.SEX, elig.get("sex")),
        "healthy": copy.HEALTHY_VOLUNTEERS[elig.get("accepts_healthy_volunteers")],
        "age_groups": [
            copy.lookup(copy.AGE_GROUP, group) for group in elig.get("std_age_groups") or []
        ],
        "inclusion": sections.get("inclusion") or [],
        "exclusion": sections.get("exclusion") or [],
        "unclassified": sections.get("unclassified") or [],
        "criteria_text": elig.get("criteria_text"),
        "sites": sites,
        "has_distance": has_distance,
        "contact_url": contact_url,
        "nearest_site": ", ".join(
            part for part in (sites[0]["facility_name"], sites[0]["place"]) if part
        )
        if sites
        else "",
    }


def _page(name: str, *, status: int = 200, **context: Any) -> HTMLResponse:
    """Render escaped patient-facing HTML and keep sensitive URLs out of referrers."""
    html = _TEMPLATES.get_template(name).render(**context)
    return HTMLResponse(
        html,
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


def register_patient_routes(app: FastAPI) -> None:
    """Install public HTML routes; the existing JSON service remains the data source."""
    install_access_log_filter()

    @app.get("/patient.css", include_in_schema=False)
    async def patient_css() -> FileResponse:
        return FileResponse(_ROOT / "static" / "patient.css", media_type="text/css")

    @app.get("/patient.js", include_in_schema=False)
    async def patient_js() -> FileResponse:
        return FileResponse(_ROOT / "static" / "patient.js", media_type="application/javascript")

    @app.get("/shortlist", include_in_schema=False)
    async def patient_shortlist() -> HTMLResponse:
        freshness = "Data freshness unavailable."
        warehouse = app.state.container.warehouse
        if warehouse is not None:
            try:
                _, timestamp = warehouse.get_snapshot_metadata()
                freshness = _freshness({"data-updated": timestamp})
            except Exception:
                pass
        return _page("shortlist.html", freshness=freshness)

    @app.get("/", include_in_schema=False)
    async def patient_home() -> HTMLResponse:
        freshness = "Data freshness unavailable."
        warehouse = app.state.container.warehouse
        if warehouse is not None:
            try:
                _, timestamp = warehouse.get_snapshot_metadata()
                freshness = _freshness({"data-updated": timestamp})
            except Exception:
                # The search form remains available even when warehouse metadata is unavailable.
                pass
        return _page("search.html", freshness=freshness, fields={})

    @app.get("/trials", include_in_schema=False)
    async def patient_search(request: Request) -> HTMLResponse:
        fields = {name: request.query_params.get(name, "").strip() for name in _FIELDS}
        condition = request.query_params.get("condition", "").strip()
        if condition and fields["cond"] and condition != fields["cond"]:
            return _page(
                "results.html",
                status=400,
                fields=fields,
                freshness="Data freshness unavailable.",
                error="Please enter one condition and try again.",
                items=[],
                next_url=None,
            )
        fields["cond"] = fields["cond"] or condition
        params = {key: value for key, value in fields.items() if value and key != "age"}

        matched_concept: str | None = None
        matched_from: str | None = None
        broader_terms: list[dict[str, Any]] = []
        narrower_terms: list[dict[str, Any]] = []

        if fields["cond"]:
            resolved = resolve_consumer_condition(fields["cond"])
            if resolved:
                matched_concept = resolved.condition_name
                if fields["cond"].strip().lower() != resolved.condition_name.lower():
                    matched_from = fields["cond"].strip()
                broader_terms = [b.model_dump() for b in resolved.broader]
                narrower_terms = [n.model_dump() for n in resolved.narrower]
                params["cond"] = resolved.condition_name
            else:
                matched_concept = fields["cond"]

        if fields["age"]:
            params["min_age"] = fields["age"]
            params["max_age"] = fields["age"]
        cursor = request.query_params.get("cursor", "").strip()
        if cursor:
            params["cursor"] = cursor
        status, headers, data = await _read_api(app, request, "/v1/trials", params)
        if status != 200:
            message = (
                "Please check your search details and try again."
                if status < 500
                else "Trial search is temporarily unavailable. Please try again later."
            )
            return _page(
                "results.html",
                status=status,
                fields=fields,
                freshness="Data freshness unavailable.",
                error=message,
                items=[],
                next_url=None,
                matched_concept=matched_concept,
                matched_from=matched_from,
                broader_terms=broader_terms,
                narrower_terms=narrower_terms,
            )
        next_cursor = data.get("pagination", {}).get("next_cursor")
        next_url = None
        if next_cursor:
            next_fields = {key: value for key, value in fields.items() if value}
            next_fields["cursor"] = next_cursor
            next_url = "/trials?" + urlencode(next_fields)
        return _page(
            "results.html",
            fields=fields,
            freshness=_freshness(headers),
            error=None,
            items=data.get("items", []),
            next_url=next_url,
            matched_concept=matched_concept,
            matched_from=matched_from,
            broader_terms=broader_terms,
            narrower_terms=narrower_terms,
        )

    @app.get("/trials/{nct_id}", include_in_schema=False)
    async def patient_detail(request: Request, nct_id: str) -> HTMLResponse:
        # Invalid IDs must not be interpolated into a new route or registry URL.
        import re

        canonical = nct_id.strip().upper()
        if not re.fullmatch(r"NCT\d{8}", canonical):
            return _page(
                "trial.html",
                status=400,
                freshness="Data freshness unavailable.",
                error="That trial ID is not valid.",
                trial=None,
            )
        # An optional ZIP code only sorts the site list; it is resolved offline, never stored.
        postal_code = request.query_params.get("postal_code", "").strip()
        point = resolve_postal_code(postal_code) if postal_code else None
        geo = {"lat": str(point.lat), "lon": str(point.lon)} if point else {}
        status, headers, trial = await _read_api(app, request, f"/v1/trials/{canonical}", geo)
        if status != 200:
            message = (
                "This trial could not be found."
                if status == 404
                else "Trial details are temporarily unavailable. Please try again later."
            )
            return _page(
                "trial.html",
                status=status,
                freshness="Data freshness unavailable.",
                error=message,
                trial=None,
            )
        # Construct the destination from the validated identifier, never untrusted upstream markup.
        trial["registry_url"] = f"https://clinicaltrials.gov/study/{canonical}"
        related_trials: list[dict[str, Any]] = []
        container = getattr(getattr(app, "state", None), "container", None)
        if container is not None and getattr(container, "warehouse", None) is not None:
            try:
                rel_status, _, rel_data = await _read_api(
                    app, request, f"/v1/trials/{canonical}/related", {"limit": "5"}
                )
                if rel_status == 200 and isinstance(rel_data, dict):
                    related_trials = rel_data.get("related_trials", [])
            except Exception:
                related_trials = []

        return _page(
            "trial.html",
            freshness=_freshness(headers),
            error=None,
            trial=trial,
            view=_trial_view(trial, canonical, has_distance=point is not None),
            postal_code=postal_code,
            postal_unknown=bool(postal_code) and point is None,
            copy=copy,
            related_trials=related_trials,
        )
