"""Exercise the real supervisor with local command stubs; never touch /app or Fly."""

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


def executable(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(0o755)


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
        " (root / 'attempt-failed').touch()\n",
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
        "echo 'pipeline stdout sentinel'\n"
        "echo 'pipeline stderr sentinel' >&2\n"
        'touch "$CTI_PROJECT_ROOT/pipeline-ran"\n'
        'exit "${PIPELINE_FAIL:-0}"\n',
    )
    return tmp_path


def run_supervisor(root, env, ready):
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
            deadline = time.monotonic() + 15
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
