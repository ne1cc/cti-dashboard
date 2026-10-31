"""Exercise the real supervisor with local command stubs; never touch /app or the network."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_freshness import marker_payload

ROOT = Path(__file__).resolve().parents[1]

# Scoped schema upgrades must never advance the refresh marker or trigger a full
# pipeline, so rebuild tests use an interval no elapsed time can reach.
NO_REFRESH_INTERVAL = "100000000000"


def executable(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(0o755)


def make_mart_stale(root, mart):
    stale = root / "stale-marts"
    marts = set(stale.read_text().split()) if stale.exists() else set()
    marts.add(mart)
    stale.write_text(" ".join(sorted(marts)) + "\n")


@pytest.fixture
def supervisor(tmp_path):
    data = tmp_path / "data"
    warehouse = data / "warehouse/clinical_trials.duckdb"
    warehouse.parent.mkdir(parents=True)
    warehouse.write_bytes(b"previous serving warehouse")
    (data / ".last_pipeline_run").write_text("1\n")
    executable(
        tmp_path / ".venv/bin/python",
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "root = pathlib.Path(os.environ['CTI_PROJECT_ROOT'])\n"
        "args = sys.argv[1:]\n"
        "if args[0] == '-c':\n"
        " print(root / 'data/warehouse/clinical_trials.duckdb')\n"
        "elif 'init-data-dirs' in args:\n"
        " (root / 'data/logs').mkdir(exist_ok=True)\n"
        "elif '--preflight' in args:\n"
        " print('disk preflight: free_bytes=1 min_free_bytes=500000000')\n"
        " sys.exit(int(os.environ.get('PREFLIGHT_FAIL', '0')))\n"
        "elif '--record-success' in args:\n"
        " sys.exit(int(os.environ.get('MARKER_FAIL', '0')))\n"
        "elif '--record-failure' in args:\n"
        " (root / 'attempt-failed').touch()\n"
        "elif args[0] == '-':\n"
        " script = sys.stdin.read()\n"
        " stale_file = root / 'stale-marts'\n"
        " stale = set(stale_file.read_text().split()) if stale_file.exists() else set()\n"
        " marts = ('mart_feasibility_priority_queue', 'mart_trial_similarity')\n"
        " checked = next((m for m in marts if m in script), '')\n"
        " sys.exit(1 if checked in stale else 0)\n",
    )
    executable(
        tmp_path / ".venv/bin/streamlit",
        f"#!{sys.executable}\n"
        "import os, pathlib, signal, time\n"
        "root = pathlib.Path(os.environ['CTI_PROJECT_ROOT'])\n"
        "(root / 'serving').write_text(str(os.getpid()))\n"
        "def stop(*args):\n"
        " (root / 'dashboard-stopped').touch()\n"
        " raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "while True: time.sleep(.01)\n",
    )
    executable(
        tmp_path / "bin/make",
        "#!/bin/bash\n"
        'case "$1" in\n'
        "  pipeline)\n"
        "    echo 'pipeline stdout sentinel'\n"
        "    echo 'pipeline stderr sentinel' >&2\n"
        '    touch "$CTI_PROJECT_ROOT/pipeline-ran"\n'
        '    exit "${PIPELINE_FAIL:-0}" ;;\n'
        "  dbt-seed)\n"
        '    touch "$CTI_PROJECT_ROOT/dbt-seed-ran" ;;\n'
        "esac\n",
    )
    executable(
        tmp_path / "bin/uv",
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "root = pathlib.Path(os.environ['CTI_PROJECT_ROOT'])\n"
        "with (root / 'uv-invocations.log').open('a') as f:\n"
        " f.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "mart = next((a[1:] for a in sys.argv[1:] if a.startswith('+')), '')\n"
        "if os.environ.get('REBUILD_FAIL_' + mart) == '1':\n"
        " sys.exit(1)\n"
        "stale = root / 'stale-marts'\n"
        "if stale.exists() and mart:\n"
        " marts = [m for m in stale.read_text().split() if m != mart]\n"
        " stale.write_text(' '.join(marts) + ('\\n' if marts else ''))\n",
    )
    return tmp_path


def run_supervisor(root, env, ready, expect_exit=False):
    output = root / "stdout.log"
    with output.open("w") as sink:
        process = subprocess.Popen(
            ["bash", str(ROOT / "entrypoint.sh")],
            cwd=ROOT,
            env={
                **os.environ,
                "CTI_PROJECT_ROOT": str(root),
                "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
                "CTI_REFRESH_CHECK_SECONDS": "1",
                **env,
            },
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            # The real-python variants import duckdb several times per boot;
            # 30s keeps them stable on loaded machines.
            deadline = time.monotonic() + 30
            if expect_exit:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        assert process.returncode != 0, (
                            "Supervisor exited 0 but was expected to refuse "
                            f"startup: {output.read_text()}"
                        )
                        return output.read_text()
                    time.sleep(0.02)
                pytest.fail(f"Supervisor did not exit: {output.read_text()}")
            while time.monotonic() < deadline:
                text = output.read_text()
                if ready(text):
                    return text
                if process.poll() is not None:
                    pytest.fail(f"Supervisor exited early ({process.returncode}): {text}")
                time.sleep(0.02)
            pytest.fail(f"Supervisor timed out: {output.read_text()}")
        finally:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=7)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)


def test_low_disk_keeps_existing_dashboard_and_warehouse(supervisor):
    warehouse = supervisor / "data/warehouse/clinical_trials.duckdb"
    before = warehouse.read_bytes()

    def ready(text):
        if "refresh skipped" not in text:
            return False
        assert (supervisor / "serving").exists()
        os.kill(int((supervisor / "serving").read_text()), 0)
        assert not (supervisor / "dashboard-stopped").exists()
        return True

    text = run_supervisor(supervisor, {"PREFLIGHT_FAIL": "1"}, ready)
    assert "free_bytes=1" in text and "min_free_bytes=500000000" in text
    assert warehouse.read_bytes() == before
    assert not (supervisor / "pipeline-ran").exists()
    assert (supervisor / "data/.last_pipeline_run").read_text() == "1\n"


def test_first_boot_low_disk_does_not_attempt_bootstrap(supervisor):
    (supervisor / "data/warehouse/clinical_trials.duckdb").unlink()
    text = run_supervisor(
        supervisor, {"PREFLIGHT_FAIL": "1"}, lambda text: "cannot bootstrap" in text
    )
    assert not (supervisor / "pipeline-ran").exists()
    assert "no warehouse" in text


@pytest.mark.parametrize("pipeline_fail,marker_fail", [("1", "0"), ("0", "1"), ("0", "0")])
def test_broken_file_log_cannot_hide_pipeline_or_advance_failed_success(
    supervisor, pipeline_fail, marker_fail
):
    log = supervisor / "data/logs/pipeline.log"
    log.mkdir(parents=True)
    text = run_supervisor(
        supervisor,
        {"PIPELINE_FAIL": pipeline_fail, "MARKER_FAIL": marker_fail},
        lambda text: "dashboard serving again" in text,
    )
    assert "pipeline stdout sentinel" in text
    assert "pipeline stderr sentinel" in text
    epoch = (supervisor / "data/.last_pipeline_run").read_text()
    if pipeline_fail == "1" or marker_fail == "1":
        assert epoch == "1\n"
        assert "FAILED" in text
        assert (supervisor / "attempt-failed").exists()
    else:
        assert int(epoch) > 1
        assert "pipeline succeeded" in text


def test_legacy_epoch_keeps_refresh_schedule(supervisor):
    (supervisor / "data/.last_pipeline_run").write_text(str(int(time.time())))
    run_supervisor(supervisor, {}, lambda text: "refresh check:" in text)
    assert not (supervisor / "pipeline-ran").exists()


def test_real_freshness_verification_error_survives_supervisor_failure(
    supervisor, project_root_tmp
):
    executable(
        supervisor / ".venv/bin/python",
        f'#!/bin/bash\nexec "{sys.executable}" "$@"\n',
    )
    marker = supervisor / "data/.freshness.json"
    previous = marker_payload()
    marker.write_text(json.dumps(previous))
    env = {
        "CTI_PROJECT_ROOT": str(project_root_tmp),
        "CTI_REFRESHABLE_PROFILES": "adrd,oncology_nsclc",
        "CTI_REFRESH_MIN_FREE_BYTES": "1",
        "CTI_FRESHNESS_MAX_AGE_DAYS": "10",
    }
    text = run_supervisor(supervisor, env, lambda text: "dashboard serving again" in text)
    reason = "Missing successful capture for adrd"
    assert reason in text
    assert "pipeline stdout sentinel" in text
    after = json.loads(marker.read_text())
    assert after["attempt_status"] == "failed"
    assert after["last_success"] == previous["last_success"]
    assert after["error"] == reason
    assert (supervisor / "data/.last_pipeline_run").read_text() == "1\n"
    result = subprocess.run(
        [sys.executable, "-m", "src.quality.freshness", "--check"],
        cwd=ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    checked = json.loads(result.stdout)
    assert not checked["ok"]
    assert checked["metadata"]["error"] == reason
    assert reason in checked["errors"]


def test_real_low_disk_replaces_previous_reconciliation_error(supervisor, project_root_tmp):
    executable(
        supervisor / ".venv/bin/python",
        f"#!{sys.executable}\n"
        "import os, runpy, sys\n"
        "if '--preflight' in sys.argv:\n"
        " import shutil\n"
        " from types import SimpleNamespace\n"
        " shutil.disk_usage = lambda path: SimpleNamespace(free=1)\n"
        " sys.argv = ['src.quality.freshness', '--preflight']\n"
        " runpy.run_module('src.quality.freshness', run_name='__main__')\n"
        "else:\n"
        " os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n",
    )
    marker = supervisor / "data/.freshness.json"
    previous = marker_payload()
    previous["attempt_status"] = "failed"
    previous["error"] = "Reconciliation failed for adrd"
    marker.write_text(json.dumps(previous))
    warehouse = supervisor / "data/warehouse/clinical_trials.duckdb"
    before = warehouse.read_bytes()
    env = {
        "CTI_PROJECT_ROOT": str(project_root_tmp),
        "CTI_REFRESHABLE_PROFILES": "adrd,oncology_nsclc",
        "CTI_REFRESH_MIN_FREE_BYTES": "500000000",
    }
    text = run_supervisor(
        supervisor,
        env,
        lambda text: (
            "refresh skipped" in text
            and json.loads(marker.read_text())["attempt_at_utc"] != previous["attempt_at_utc"]
        ),
    )
    assert '"free_bytes": 1' in text and '"min_free_bytes": 500000000' in text
    after = json.loads(marker.read_text())
    assert after["attempt_status"] == "failed"
    assert after["error"] != previous["error"]
    assert "disk preflight" in after["error"].lower()
    assert after["last_success"] == previous["last_success"]
    assert warehouse.read_bytes() == before
    assert not (supervisor / "pipeline-ran").exists()
    assert (supervisor / "data/.last_pipeline_run").read_text() == "1\n"
    result = subprocess.run(
        [sys.executable, "-m", "src.quality.freshness", "--check"],
        cwd=ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    checked = json.loads(result.stdout)
    assert not checked["ok"]
    assert checked["metadata"]["error"] == after["error"]
    assert after["error"] in checked["errors"]
    assert previous["error"] not in checked["errors"]


def test_stale_priority_queue_schema_rebuilds_before_serving(supervisor):
    make_mart_stale(supervisor, "mart_feasibility_priority_queue")
    text = run_supervisor(
        supervisor,
        {"CTI_REFRESH_INTERVAL_SECONDS": NO_REFRESH_INTERVAL},
        lambda text: "dashboard serving (pid=" in text and (supervisor / "serving").exists(),
    )
    assert "priority queue mart schema is outdated" in text
    assert "Priority Queue schema rebuild succeeded" in text
    assert (
        "--select +mart_feasibility_priority_queue"
        in (supervisor / "uv-invocations.log").read_text()
    )
    assert "mart_trial_similarity" not in (supervisor / "uv-invocations.log").read_text()
    assert (supervisor / "dbt-seed-ran").exists()
    assert (supervisor / "serving").exists()
    # Scoped upgrade only: full refresh state and warehouse data untouched.
    assert not (supervisor / "pipeline-ran").exists()
    assert (supervisor / "data/.last_pipeline_run").read_text() == "1\n"
    assert not (supervisor / "attempt-failed").exists()
    assert (supervisor / "data/warehouse/clinical_trials.duckdb").read_bytes() == (
        b"previous serving warehouse"
    )


def test_current_schemas_skip_all_rebuilds(supervisor):
    text = run_supervisor(
        supervisor,
        {"CTI_REFRESH_INTERVAL_SECONDS": NO_REFRESH_INTERVAL},
        lambda text: "dashboard serving (pid=" in text and (supervisor / "serving").exists(),
    )
    assert "schema is outdated" not in text
    assert not (supervisor / "uv-invocations.log").exists()
    assert not (supervisor / "dbt-seed-ran").exists()
    assert (supervisor / "serving").exists()


def test_both_marts_stale_rebuilds_each_before_serving(supervisor):
    make_mart_stale(supervisor, "mart_feasibility_priority_queue")
    make_mart_stale(supervisor, "mart_trial_similarity")
    text = run_supervisor(
        supervisor,
        {"CTI_REFRESH_INTERVAL_SECONDS": NO_REFRESH_INTERVAL},
        lambda text: "dashboard serving (pid=" in text and (supervisor / "serving").exists(),
    )
    uv_log = (supervisor / "uv-invocations.log").read_text()
    assert "--select +mart_feasibility_priority_queue" in uv_log
    assert "--select +mart_trial_similarity" in uv_log
    assert "Priority Queue schema rebuild succeeded" in text
    assert "Trial Similarity schema rebuild succeeded" in text
    assert (supervisor / "serving").exists()
    assert not (supervisor / "pipeline-ran").exists()


def test_failed_required_rebuild_refuses_to_serve(supervisor):
    make_mart_stale(supervisor, "mart_feasibility_priority_queue")
    text = run_supervisor(
        supervisor,
        {
            "REBUILD_FAIL_mart_feasibility_priority_queue": "1",
            "CTI_REFRESH_INTERVAL_SECONDS": NO_REFRESH_INTERVAL,
        },
        lambda text: False,
        expect_exit=True,
    )
    assert "Priority Queue schema rebuild FAILED" in text
    assert "refusing to serve an incompatible dashboard" in text
    assert not (supervisor / "serving").exists()
    assert not (supervisor / "pipeline-ran").exists()
    assert (supervisor / "data/.last_pipeline_run").read_text() == "1\n"


def test_denied_preflight_with_stale_schema_refuses_to_serve(supervisor):
    make_mart_stale(supervisor, "mart_feasibility_priority_queue")
    text = run_supervisor(
        supervisor,
        {"PREFLIGHT_FAIL": "1"},
        lambda text: False,
        expect_exit=True,
    )
    assert "disk preflight denied writes" in text
    assert "refusing to serve an incompatible dashboard" in text
    assert not (supervisor / "uv-invocations.log").exists()
    assert not (supervisor / "dbt-seed-ran").exists()
    assert not (supervisor / "serving").exists()


def test_similarity_rebuild_failure_still_serves_independently(supervisor):
    make_mart_stale(supervisor, "mart_trial_similarity")
    text = run_supervisor(
        supervisor,
        {
            "REBUILD_FAIL_mart_trial_similarity": "1",
            "CTI_REFRESH_INTERVAL_SECONDS": NO_REFRESH_INTERVAL,
        },
        lambda text: "dashboard serving (pid=" in text and (supervisor / "serving").exists(),
    )
    assert "Trial Similarity schema rebuild FAILED" in text
    assert "rebuild message for Trial Similarity" in text
    assert (supervisor / "serving").exists()
    assert (
        "--select +mart_feasibility_priority_queue"
        not in (supervisor / "uv-invocations.log").read_text()
    )
