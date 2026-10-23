"""Tests for tests.fixture_warehouse CLI and fixture generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixture_warehouse import (
    AddedStudy,
    base_adrd_run,
    base_nsclc_run,
    build_parser,
    default_runs,
    main,
    prepare_fixture_root,
    variant_page,
)


def test_cli_help(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "--root" in captured.out
    assert "--prepare-only" in captured.out


def test_cli_requires_root() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([])
    assert exc.value.code != 0


def test_cli_refuses_nonempty_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "existing.txt").write_text("already here", encoding="utf-8")
    ret = main(["--root", str(tmp_path), "--prepare-only"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Refusing to overwrite nonempty destination directory" in captured.err


def test_prepare_fixture_root_refuses_nonempty(tmp_path: Path) -> None:
    (tmp_path / "dummy.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError, match="not empty"):
        prepare_fixture_root(tmp_path)


def test_cli_prepare_only(tmp_path: Path) -> None:
    target = tmp_path / "target_fixture"
    ret = main(["--root", str(target), "--prepare-only"])
    assert ret == 0

    assert (target / "config" / "project_config.yml").exists()
    assert (target / "config" / "profiles" / "adrd.yml").exists()
    assert (target / "profiles.yml").exists()
    assert (target / "data" / "warehouse").is_dir()

    # Silver Parquet files should exist
    silver_trials = list((target / "data" / "silver" / "silver_trials").glob("*.parquet"))
    assert len(silver_trials) >= 1

    # Warehouse DuckDB file should NOT exist under prepare-only
    duckdb_file = target / "data" / "warehouse" / "clinical_trials.duckdb"
    assert not duckdb_file.exists()


def test_default_runs_definition() -> None:
    runs = default_runs()
    assert len(runs) == 2
    assert runs[0].profile == "adrd"
    assert runs[1].profile == "oncology_nsclc"
    assert base_adrd_run().profile == "adrd"
    assert base_nsclc_run().profile == "oncology_nsclc"


def test_variant_page_modifications(tmp_path: Path) -> None:
    sample_doc = {
        "studies": [
            {
                "protocolSection": {
                    "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Sample 1"},
                    "statusModule": {
                        "overallStatus": "RECRUITING",
                        "lastKnownStatus": "RECRUITING",
                    },
                }
            }
        ]
    }
    src_file = tmp_path / "page=00001.json"
    src_file.write_text(json.dumps(sample_doc), encoding="utf-8")

    added = (
        AddedStudy(
            nct_id="NCT00000002",
            clone_of="NCT00000001",
            overall_status="COMPLETED",
            condition="Alzheimer Disease",
            phase="PHASE3",
            sponsor="Test Sponsor",
            location=("Test Facility", "Boston", "MA"),
        ),
    )
    result = variant_page(
        src_file,
        status_overrides={"NCT00000001": "TERMINATED"},
        added=added,
    )

    studies = result["studies"]
    assert len(studies) == 2
    assert studies[0]["protocolSection"]["statusModule"]["overallStatus"] == "TERMINATED"
    assert studies[1]["protocolSection"]["identificationModule"]["nctId"] == "NCT00000002"
    assert studies[1]["protocolSection"]["statusModule"]["overallStatus"] == "COMPLETED"
