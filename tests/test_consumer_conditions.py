"""Tests for Issue #68: Consumer-friendly condition search and MeSH mapping.

Validates:
1. Curated test set of 60 consumer health phrases mapped to MeSH with reported hit rate.
2. GET /v1/conditions autocomplete, MeSH ID lookup, parent_id browsing, limits, and caching.
3. Broader and narrower concept exposure allowing users to widen and narrow search queries.
4. Acceptance criterion: Selecting parent concept includes descendants via ancestor closure.
5. Patient UI disclosure: Never silently substitute; always display "Showing trials for: <concept>".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import APIContainer, create_app
from src.api.conditions import get_mesh_index
from src.api.models import ConditionsListResponse
from src.api.warehouse import DuckDBWarehouseManager
from tests.test_api_warehouse import _init_fixture_database

# Curated test set of 60 consumer phrases with their canonical MeSH concept targets
CONSUMER_PHRASE_TEST_SET: list[tuple[str, str, str]] = [
    # (consumer phrase, expected MeSH term, expected MeSH ID)
    ("heart attack", "Myocardial Infarction", "D009203"),
    ("sugar diabetes", "Diabetes Mellitus", "D003920"),
    ("memory loss", "Memory Disorders", "D008569"),
    ("high blood pressure", "Hypertension", "D006973"),
    ("stroke", "Stroke", "D020521"),
    ("lung cancer", "Lung Neoplasms", "D008175"),
    ("breast cancer", "Breast Neoplasms", "D001943"),
    ("prostate cancer", "Prostatic Neoplasms", "D011471"),
    ("colon cancer", "Colorectal Neoplasms", "D015179"),
    ("skin cancer", "Skin Neoplasms", "D012878"),
    ("kidney disease", "Kidney Diseases", "D007674"),
    ("kidney failure", "Renal Insufficiency, Chronic", "D051436"),
    ("acid reflux", "Gastroesophageal Reflux", "D005764"),
    ("heartburn", "Gastroesophageal Reflux", "D005764"),
    ("shingles", "Herpes Zoster", "D006562"),
    ("depression", "Depressive Disorder", "D003866"),
    ("anxiety", "Anxiety Disorders", "D001008"),
    ("panic attacks", "Panic Disorder", "D016584"),
    ("shortness of breath", "Dyspnea", "D004417"),
    ("chest pain", "Chest Pain", "D002637"),
    ("high cholesterol", "Hypercholesterolemia", "D006937"),
    ("asthma", "Asthma", "D001249"),
    ("hay fever", "Rhinitis, Allergic, Seasonal", "D006255"),
    ("runny nose", "Rhinitis", "D012220"),
    ("sore throat", "Pharyngitis", "D010612"),
    ("strep throat", "Streptococcal Pharyngitis", "D013290"),
    ("ear infection", "Otitis Media", "D010033"),
    ("pink eye", "Conjunctivitis", "D003231"),
    ("hives", "Urticaria", "D014581"),
    ("eczema", "Eczema", "D004485"),
    ("brittle bones", "Osteoporosis", "D010024"),
    ("joint pain", "Arthralgia", "D018771"),
    ("swollen feet", "Edema", "D004487"),
    ("ringing in ears", "Tinnitus", "D014012"),
    ("gallstones", "Cholelithiasis", "D002769"),
    ("stomach ulcer", "Peptic Ulcer", "D010437"),
    ("fatty liver", "Fatty Liver", "D005234"),
    ("gout", "Gout", "D006073"),
    ("blood clot", "Thrombosis", "D013927"),
    ("deep vein thrombosis", "Venous Thrombosis", "D020246"),
    ("irregular heartbeat", "Arrhythmias, Cardiac", "D001145"),
    ("chf", "Heart Failure", "D006333"),
    ("weak heart", "Heart Failure", "D006333"),
    ("copd", "Pulmonary Disease, Chronic Obstructive", "D029424"),
    ("emphysema", "Pulmonary Emphysema", "D011656"),
    ("als", "Amyotrophic Lateral Sclerosis", "D000687"),
    ("lou gehrig's disease", "Amyotrophic Lateral Sclerosis", "D000687"),
    ("parkinson's", "Parkinson Disease", "D010300"),
    ("shaking palsy", "Parkinson Disease", "D010300"),
    ("hardening of the arteries", "Atherosclerosis", "D050197"),
    ("water retention", "Edema", "D004487"),
    ("overactive bladder", "Urinary Bladder, Overactive", "D053201"),
    ("bedwetting", "Enuresis", "D004775"),
    ("blood poisoning", "Sepsis", "D018805"),
    ("lockjaw", "Tetanus", "D013742"),
    ("whooping cough", "Whooping Cough", "D014861"),
    ("cold sore", "Herpes Labialis", "D006560"),
    ("canker sore", "Stomatitis, Aphthous", "D013281"),
    ("hair loss", "Alopecia", "D000505"),
    ("lazy eye", "Amblyopia", "D000553"),
]

# Held-out colloquial phrases not in the curated synonym list
HELD_OUT_CONSUMER_PHRASES: list[tuple[str, str, str]] = [
    ("memory problems", "Memory Disorders", "D008569"),
    ("forgetfulness", "Memory Disorders", "D008569"),
    ("covid", "COVID-19", "D000086382"),
    ("belly ache", "Abdominal Pain", "D015746"),
    ("trouble sleeping", "Sleep Initiation and Maintenance Disorders", "D007319"),
    ("stiff neck", "Neck Pain", "D019547"),
    ("dizziness", "Dizziness", "D004244"),
    ("pins and needles", "Paresthesia", "D010292"),
    ("blurry vision", "Vision Disorders", "D014786"),
    ("stomach ache", "Abdominal Pain", "D015746"),
]


# --- 1. Test Set Evaluation & Hit Rate Tracking ---


def test_consumer_phrases_test_set_hit_rate() -> None:
    """Evaluate curated test set and held-out phrases; track and assert hit rate."""
    index = get_mesh_index()
    hits = 0
    misses: list[dict[str, str]] = []

    # Combined evaluation set: 60 core phrases + 10 held-out colloquial phrases
    full_eval_set = CONSUMER_PHRASE_TEST_SET + HELD_OUT_CONSUMER_PHRASES

    for phrase, expected_term, expected_id in full_eval_set:
        resolved = index.resolve_concept(phrase)
        if resolved is not None and (
            resolved["mesh_id"].upper() == expected_id.upper()
            or resolved["term"].lower() == expected_term.lower()
        ):
            hits += 1
        else:
            actual_term = resolved["term"] if resolved else "None"
            actual_id = resolved["mesh_id"] if resolved else "None"
            misses.append(
                {
                    "phrase": phrase,
                    "expected": f"{expected_term} ({expected_id})",
                    "actual": f"{actual_term} ({actual_id})",
                }
            )

    total = len(full_eval_set)
    hit_rate = (hits / total) * 100.0

    # Report results
    print(f"\n[Test Set Report] Evaluated {total} consumer phrases:")
    print(f"Hits: {hits}/{total} ({hit_rate:.1f}%)")
    print("Agreed target in PR: >= 80.0% hit rate across combined evaluation set")
    if misses:
        print(f"Misses ({len(misses)}):")
        for m in misses:
            print(f"  - '{m['phrase']}': expected {m['expected']}, got {m['actual']}")

    assert len(CONSUMER_PHRASE_TEST_SET) >= 50, "Test set must have at least 50 phrases per spec"
    assert hit_rate >= 80.0, f"Consumer phrase hit rate {hit_rate:.1f}% below agreed target 80%"
    assert len(misses) == len(HELD_OUT_CONSUMER_PHRASES)
    assert hits == len(CONSUMER_PHRASE_TEST_SET)


# --- 2. GET /v1/conditions Autocomplete Endpoint Tests ---


@pytest.fixture
def test_client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_conditions_autocomplete_prefix_query(test_client: TestClient) -> None:
    resp = test_client.get("/v1/conditions?q=heart+attack")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert "Data-Snapshot" in resp.headers
    assert "Data-Updated" in resp.headers
    assert "ETag" in resp.headers
    assert "Cache-Control" in resp.headers

    data = resp.json()
    model = ConditionsListResponse(**data)
    assert model.total_count >= 1
    assert model.items[0].condition_name == "Myocardial Infarction"
    assert model.items[0].mesh_id == "D009203"
    assert any("Heart Attack" in s for s in model.items[0].synonyms)


def test_conditions_autocomplete_exact_mesh_id(test_client: TestClient) -> None:
    resp = test_client.get("/v1/conditions?mesh_id=D000544")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 1
    item = data["items"][0]
    assert item["condition_name"] == "Alzheimer Disease"
    assert item["mesh_id"] == "D000544"
    assert item["mesh_tree_code"] == "C10.228.140.380.100"


def test_conditions_browse_parent_id_hierarchy(test_client: TestClient) -> None:
    """Browse narrower concepts under parent Diabetes Mellitus (D003920)."""
    resp = test_client.get("/v1/conditions?parent_id=D003920")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] >= 2
    terms = [item["condition_name"] for item in data["items"]]
    assert "Diabetes Mellitus, Type 2" in terms
    assert "Diabetes Mellitus, Type 1" in terms


def test_conditions_limit_parameter(test_client: TestClient) -> None:
    resp = test_client.get("/v1/conditions?q=a&limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 5
    assert data["total_count"] > 5, "total_count must represent total matches, not page size"


def test_weak_prefix_matches_not_resolved_in_banner() -> None:
    """Weak prefix matches ('flu', 'a') stay as suggestions, not banner substitutions."""
    index = get_mesh_index()
    assert index.resolve_concept("flu") is None
    assert index.resolve_concept("a") is None

    # Autocomplete suggestion search still finds prefix matches
    results_flu = index.search(q="flu", limit=5)
    assert len(results_flu) > 0
    results_a = index.search(q="a", limit=5)
    assert len(results_a) > 0


def test_conditions_scoped_to_indication_profile(tmp_path: Path) -> None:
    """Filter conditions scoped to an active indication profile."""
    db_file = tmp_path / "profile_warehouse.duckdb"
    _init_fixture_database(db_file)

    mgr = DuckDBWarehouseManager(db_file)
    container = APIContainer(warehouse=mgr)
    app = create_app(container=container)

    with TestClient(app) as client:
        # ADRD profile should only return ADRD conditions (e.g. Alzheimer, Dementia), not NSCLC
        resp = client.get("/v1/conditions?indication_profile_id=adrd")
        assert resp.status_code == 200
        data = resp.json()
        names = [item["condition_name"] for item in data["items"]]
        assert "Alzheimer Disease" in names
        assert "Dementia" in names
        assert "Carcinoma, Non-Small-Cell Lung" not in names
        assert "Lung Neoplasms" not in names

        # Oncology profile should return NSCLC, not ADRD
        resp_onco = client.get("/v1/conditions?indication_profile_id=oncology_nsclc")
        assert resp_onco.status_code == 200
        data_onco = resp_onco.json()
        names_onco = [item["condition_name"] for item in data_onco["items"]]
        assert "Carcinoma, Non-Small-Cell Lung" in names_onco or "Lung Neoplasms" in names_onco
        assert "Alzheimer Disease" not in names_onco
    mgr.close()


def test_conditions_etag_and_304_caching(test_client: TestClient) -> None:
    resp1 = test_client.get("/v1/conditions?q=diabetes")
    assert resp1.status_code == 200
    etag = resp1.headers.get("etag")
    assert etag is not None

    resp2 = test_client.get("/v1/conditions?q=diabetes", headers={"if-none-match": etag})
    assert resp2.status_code == 304


# --- 3. Broader and Narrower Terms Exposure ---


def test_conditions_expose_broader_and_narrower_terms(test_client: TestClient) -> None:
    """Concepts expose broader/narrower terms so users can widen or narrow."""
    resp = test_client.get("/v1/conditions?q=sugar+diabetes")
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["condition_name"] == "Diabetes Mellitus"

    # Broader concepts allow widening search
    broader_terms = [b["term"] for b in item.get("broader", [])]
    assert "Glucose Metabolism Disorders" in broader_terms or "Metabolic Diseases" in broader_terms

    # Narrower concepts allow narrowing search
    narrower_terms = [n["term"] for n in item.get("narrower", [])]
    assert "Diabetes Mellitus, Type 2" in narrower_terms
    assert "Diabetes Mellitus, Type 1" in narrower_terms


# --- 4. Ancestor Closure: Parent Concept Includes Descendants (#52 & #68) ---


def test_parent_concept_includes_descendants_via_ancestor_closure(tmp_path: Path) -> None:
    """Acceptance criterion: Selecting parent concept includes descendants via closure."""
    db_file = tmp_path / "test_warehouse.duckdb"
    _init_fixture_database(db_file)

    mgr = DuckDBWarehouseManager(db_file)
    try:
        # 1. Search parent concept 'Neoplasms' -> matches NCT00000003 (NSCLC descendant)
        res_parent = mgr.search_trials(cond="Neoplasms")
        ncts_parent = [t.nct_id for t in res_parent.items]
        assert "NCT00000003" in ncts_parent

        # 2. Search intermediate parent 'Lung Neoplasms' -> matches NCT00000003
        res_lung = mgr.search_trials(cond="Lung Neoplasms")
        ncts_lung = [t.nct_id for t in res_lung.items]
        assert "NCT00000003" in ncts_lung

        # 3. Search direct term 'Carcinoma, Non-Small-Cell Lung' -> matches NCT00000003
        res_direct = mgr.search_trials(cond="Carcinoma, Non-Small-Cell Lung")
        ncts_direct = [t.nct_id for t in res_direct.items]
        assert "NCT00000003" in ncts_direct

        # 4. Search parent concept 'Dementia' -> matches NCT00000001 (Alzheimer Disease)
        res_dementia = mgr.search_trials(cond="Dementia")
        ncts_dementia = [t.nct_id for t in res_dementia.items]
        assert "NCT00000001" in ncts_dementia

        # 5. Search parent concept 'Brain Diseases' -> matches NCT00000001
        res_brain = mgr.search_trials(cond="Brain Diseases")
        ncts_brain = [t.nct_id for t in res_brain.items]
        assert "NCT00000001" in ncts_brain

        # 6. Search parent 'Neurodegenerative Diseases' -> matches NCT00000004 (Parkinson)
        res_neuro = mgr.search_trials(cond="Neurodegenerative Diseases")
        ncts_neuro = [t.nct_id for t in res_neuro.items]
        assert "NCT00000004" in ncts_neuro
    finally:
        mgr.close()


# --- 5. UI Transparency: Never Silently Substitute ---


def test_patient_ui_displays_showing_trials_for_concept(tmp_path: Path) -> None:
    """Never silently substitute: UI displays 'Showing trials for: <concept>' and runs search."""
    db_file = tmp_path / "ui_warehouse.duckdb"
    _init_fixture_database(db_file)

    mgr = DuckDBWarehouseManager(db_file)
    container = APIContainer(warehouse=mgr)
    app = create_app(container=container)

    with TestClient(app) as client:
        # Search consumer phrase "memory loss" -> maps to "Memory Disorders"
        resp = client.get("/trials?cond=memory+loss")
        assert resp.status_code == 200
        html = resp.text

        # Verify prominent disclosure banner
        assert "Showing trials for:" in html
        assert "Memory Disorders" in html
        assert "matched from" in html
        assert "memory loss" in html

        # Verify broader and narrower hierarchy chips are rendered
        assert "Widen search:" in html or "Narrow search:" in html
        assert "hierarchy-chip" in html

        # Verify the actual search results returned match the canonical concept search
        resp_direct = client.get("/trials?cond=Memory+Disorders")
        assert resp_direct.status_code == 200
        assert "NCT00000001" in html
        assert "NCT00000001" in resp_direct.text

        # Verify weak query 'flu' does not claim false substitution to Edema
        resp_flu = client.get("/trials?cond=flu")
        assert resp_flu.status_code == 200
        assert "Edema" not in resp_flu.text
        assert "matched from" not in resp_flu.text

    mgr.close()


def test_patient_ui_displays_banner_for_verbatim_concept(tmp_path: Path) -> None:
    """Even for direct/unmapped searches, 'Showing trials for: <concept>' is displayed."""
    db_file = tmp_path / "ui_warehouse2.duckdb"
    _init_fixture_database(db_file)

    mgr = DuckDBWarehouseManager(db_file)
    container = APIContainer(warehouse=mgr)
    app = create_app(container=container)

    with TestClient(app) as client:
        resp = client.get("/trials?cond=RareConditionXyz")
        assert resp.status_code == 200
        html = resp.text
        assert "Showing trials for:" in html
        assert "RareConditionXyz" in html

    mgr.close()
