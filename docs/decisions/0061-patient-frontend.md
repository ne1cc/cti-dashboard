# ADR 0061: Dual-Frontend Architecture — Server-Rendered Patient Finder and Streamlit Analyst Workbench

- **Status:** Accepted (Approved User Mandate)
- **Deciders:** Product, Architecture, Engineering
- **Date:** 2026-10-24
- **Technical Story:** Issue #61 (Part of Epic #42 — Patient & Caregiver Trial Finder, and Epic #44 — Public Read API & Front-End Split)

---

## Context

The Clinical Trial Access & Recruitment Competition Intelligence platform currently delivers its analytical outputs through a single user interface: an 8-page Streamlit dashboard (`dashboard/app.py`). This dashboard serves clinical-operations analysts conducting landscape screening, evaluating priority queue segments, analyzing sponsor concentration, and inspecting feasibility scenarios.

Epic #42 expands the platform's mission to prospective trial participants and their caregivers, enabling them to search for active, recruiting clinical trials near their location and understand participation requirements.

However, Streamlit possesses architectural characteristics that make it fundamentally unsuitable for a public-facing, consumer-grade patient application:
1. **Full-Script Execution Model:** Streamlit reruns the Python script on every user interaction or state change, generating latency and server compute overhead for high-concurrency public traffic.
2. **Limited and Fragile Deep-Linking:** State management in Streamlit centers on runtime session state. While `st.query_params` exists, URL synchronization across back/forward browser navigation and external sharing is fragile.
3. **High Client-Side Overhead & Slow First Contentful Paint (FCP):** Streamlit requires an initial bundle download (React runtime, websocket client, layout components) and establishes a stateful WebSocket connection before rendering any content. On mobile devices with spotty cellular networks (3G/4G), this produces sluggish initial load times.
4. **Existing Streamlit Workarounds:** To maintain stability in the analyst dashboard, the repository already carries several defensive workarounds:
   - `_materialize()` in `dashboard/components/data.py` (explicitly converting DuckDB queries to pandas objects to avoid an Arrow renderer SIGSEGV crash);
   - `pyarrow<22` version pinning in `pyproject.toml`;
   - A 132-line DOM patch script in `dashboard/components/scroll_fix.py` to overcome iframe scrolling and container height defects.
5. **SEO & Crawling Incompatibility:** Streamlit applications render within an opaque client-side DOM shell that is hostile to search engine crawlers, preventing public condition and trial discovery pages from being indexed.
6. **Accessibility Challenges:** Meeting WCAG 2.1 AA requirements (semantic HTML landmarks, native focus indicators, predictable tab ordering, and screen-reader accessibility for form fields) is difficult within Streamlit's generated widget tree.

A public patient and caregiver trial finder requires stable URLs, instant mobile rendering, accessible markup, and minimal operational complexity.

---

## Decision

The platform adopts the user-approved architecture:
> **"Server-rendered patient pages with progressive enhancement; retain Streamlit for analysts."**

Specifically:
1. **Dual-Frontend Separation:**
   - **Public Patient Finder:** Implemented as lightweight, server-rendered HTML pages powered by FastAPI and Jinja2 templates, enhanced with unobtrusive vanilla JavaScript (and CSS) for client-side interactions.
   - **Internal Analyst Workbench:** Retained as the existing Streamlit dashboard (`dashboard/app.py`) for internal operations, exploratory data analysis, and feasibility priority scoring.
2. **Distinct Routing and Deployment Namespaces:**
   - Public patient-facing routes will reside on standard HTTP endpoints: `/` (search interface), `/trials` (search results), and `/trials/{nct_id}` (study detail).
   - Public read API endpoints (Issues #58 / #59 / #60) will serve structured JSON under `/api/v1/`.
   - The analyst workbench operates either on an isolated internal port (8501) or a protected subpath, reading gold DuckDB marts directly.
3. **Preservation of Existing Analyst Dashboard Workarounds:**
   - The existing workarounds (`_materialize()`, `pyarrow<22`, and `dashboard/components/scroll_fix.py`) will **not** be refactored or removed as part of this decision. They remain in place to protect the stability of the analyst workbench.
   - The patient-facing frontend will interact with the data layer via the read API and DuckDB queries using typed Pydantic models, completely bypassing the Streamlit runtime and its workarounds.
4. **Technology Stack for Patient Frontend:**
   - **Backend:** FastAPI service (unified with the public read API from Epic #44).
   - **Templating:** Jinja2 server-rendered templates.
   - **Styling:** Semantic, utility-informed modern CSS (responsive down to 360px viewport width).
   - **Interactivity:** Vanilla JavaScript (<15KB budget, zero npm build toolchain) for progressive enhancement (e.g., accordion toggles, local storage shortlist, asynchronous result updates).

---

## Alternatives Evaluated

| Option | Architecture | Mobile & FCP | SEO & Deep Linking | Solo-Maintainer Ops Burden | Decision |
|---|---|---|---|---|---|
| **Option 1: Streamlit for Everything** | Single Streamlit app with `st.query_params` | Poor FCP; heavy websocket connection | Poor SEO; back-button and URL state quirks | Low (single codebase), but high defect workaround toil | **Rejected:** Unacceptable user experience for mobile patients; fragile deep-linking. |
| **Option 2: Lightweight SPA / SSR** | React / SvelteKit / Next.js front end with MapLibre | Fast FCP (with SSR); rich client state | Good (SSR), but requires Node.js hosting runtime | High: adds Node/npm build chain, bundle management, hydration bugs | **Rejected:** Disproportionate build and operational complexity for a solo maintainer. |
| **Option 3: Server-Rendered + Progressive Enhancement** | FastAPI + Jinja2 templates + vanilla JS/CSS | Instant FCP; zero client bundle overhead | Native HTML indexability; clean canonical URLs | Low: 100% Python stack; zero Node dependencies; shared with Read API | **Accepted (Approved Mandate):** Optimal balance of performance, accessibility, and simplicity. |
| **Option 4: Static Site Generation (SSG)** | Pre-rendered static pages exported to CDN | Instant static delivery | Excellent SEO | High: cannot handle dynamic "condition + ZIP + radius" queries without API runtime | **Rejected:** Dynamic search parameters require live query execution. |

---

## Rationale & Architectural Drivers

### 1. Mobile Responsiveness at 360px Viewport
Prospective patients and caregivers frequently seek clinical trial information on mobile phones while navigating stressful health decisions. The server-rendered layout enforces a fluid, responsive CSS layout tested down to 360px screen width. Forms, trial summary cards, and typography adapt naturally without horizontal clipping, modal lockups, or desktop-centric iframe behaviors.

### 2. Instant First Contentful Paint (FCP) & Zero-Bundle Overhead
By delivering plain HTML and minimal CSS directly from the server, the patient app eliminates client-side JavaScript download and parsing delays. Content displays immediately even over high-latency cellular connections. Progressive enhancement ensures that core search and reading capabilities function fully even if JavaScript fails to execute or is disabled.

### 3. Deep-Linkable, Shareable, and SEO-Friendly URLs
Every search query and trial record has a deterministic, canonical URL:
- `GET /trials?condition=Alzheimer+Disease&zip=02138&radius=50`
- `GET /trials/NCT07721467`

Patients can bookmark searches, text links to family members or caregivers, and open records in new browser tabs without losing search context or resetting session state. Search engines can crawl public trial and condition landing pages without requiring JavaScript pre-rendering services.

### 4. Accessibility Target: WCAG 2.1 AA
Server-rendered semantic HTML enables straightforward compliance with WCAG 2.1 AA accessibility guidelines:
- True HTML5 landmarks (`<header>`, `<nav>`, `<main>`, `<search>`, `<footer>`);
- Explicit `<label for="...">` associations for form controls (condition selector, ZIP code input, radius slider);
- Native keyboard focus management and high-contrast color ratios;
- ARIA live regions for announcing search count updates without disruptive full-page flushes.

### 5. Solo-Maintainer Operational Simplicity
Introducing a modern JavaScript framework (React, Vue, or SvelteKit) introduces a separate Node.js package ecosystem, npm lockfiles, vulnerability scanning, transpilation steps, and dual-container deployment pipelines. Option 3 maintains a 100% Python runtime: FastAPI serves both the JSON API and the Jinja2 HTML templates within the existing deployment container (Fly.io).

### 6. Reuse of the Read API Contract (#58)
The server-rendered frontend directly consumes the same domain entities, Pydantic schemas, and DuckDB query engines established in Issue #58. Data transformations and filtering logic remain centralized in Python without duplication between client and server.

---

## Dual-Frontend Separation Boundaries

| Dimension | Public Patient Finder (`/`, `/trials`) | Analyst Workbench (`dashboard/`, Port 8501) |
|---|---|---|
| **Audience** | Patients, caregivers, primary healthcare advocates | Clinical-operations analysts, feasibility leads |
| **Primary Goal** | Find nearby active trials and understand participation | Screen competitive landscapes and evaluate feasibility |
| **Technology** | FastAPI + Jinja2 + Vanilla JS/CSS | Streamlit + Plotly |
| **Client Stack** | Zero-bundle HTML5 (<15KB vanilla JS enhancement) | React + WebSocket Streamlit frontend |
| **State Model** | URL search parameters (`?condition=...&zip=...`) | Streamlit runtime session state |
| **Data Source** | Read API domain models / DuckDB Gold marts | DuckDB Gold marts (direct read-only connection) |
| **Accessibility** | Strict WCAG 2.1 AA target (semantic HTML, keyboard) | Analyst-facing exploratory dashboard defaults |
| **Workarounds** | None (pure HTTP request/response) | Retains `_materialize()`, `pyarrow<22`, `scroll_fix.py` |

---

## Downstream Implementation Roadmap

1. **Stage A (Issue #61 — Current Stage):**
   - Formalize Option 3 in this ADR (`docs/decisions/0061-patient-frontend.md`).
   - Update `docs/architecture.md` and `PROJECT_DOCUMENTATION.md` to reflect the dual-frontend delivery model.
   - Verify documentation paths and linting.
2. **Package 10 — Read API Foundation (Issues #58, #59, #60):**
   - Establish OpenAPI specification and endpoint contracts for trial search, facility locations, and condition taxonomies.
   - Implement read service querying DuckDB gold marts.
3. **Stage B (Issue #61 — Implementation Spike):**
   - Wire Jinja2 templating into the FastAPI application.
   - Build responsive HTML/CSS layouts with mobile 360px viewport validation.
   - Add progressive enhancement for the "condition + ZIP + radius" search flow.
   - Execute an end-to-end spike validating the search and study detail user journey.
4. **Patient Trial Detail & Site List (Issue #66):**
   - Implement study detail template rendering canonical ClinicalTrials.gov links and site lists per ADR 0065.
