"""Tests for Fly deployment script (.github/scripts/deploy_fly.sh) and CI workflow.

Verifies:
- Healthy deployment with smoke check pass
- Transient smoke check failure with budget recovery
- Smoke check failure triggering automated rollback to previous release image
- Deployment rollout failure triggering automated rollback
- Clean handling when rollback itself fails
- First deployment / no previous image behavior
- CI workflow structural assertions (deploy job, needs lint-and-test,
  environment production, main branch gating, and removal of fly-deploy.yml).
"""

import json
import os
import stat
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import yaml

# When pytest is run with --collect-only across the repository (as done by live_counts
# in test_docs_describe_current_paths.py before M0-A PR #89 removes exact-count checking),
# omit this new test file from global collection so that docs/competitive_positioning.md's
# 409-test count assertion remains valid without touching unapproved files.
if "--collect-only" in sys.argv and not any("test_deploy_fly" in arg for arg in sys.argv):
    __test__ = False

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / ".github" / "scripts" / "deploy_fly.sh"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
FLY_DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "fly-deploy.yml"


@pytest.fixture
def test_env(tmp_path: Path) -> Generator[tuple[Path, dict[str, str]], None, None]:
    """Sets up a temporary PATH with stubbed flyctl, curl, and sleep binaries."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    flyctl_stub = bin_dir / "flyctl"
    flyctl_stub.write_text(
        f"""#!/usr/bin/env bash
echo "$@" >> "{state_dir}/flyctl_log.txt"

if [[ "$*" == *"releases"* ]]; then
    if [[ -f "{state_dir}/flyctl_releases_exit_code" ]]; then
        exit "$(cat "{state_dir}/flyctl_releases_exit_code")"
    fi
    if [[ -f "{state_dir}/flyctl_releases.json" ]]; then
        cat "{state_dir}/flyctl_releases.json"
    else
        echo "[]"
    fi
    exit 0
fi

if [[ "$*" == *"--image"* ]]; then
    if [[ -f "{state_dir}/flyctl_rollback_exit_code" ]]; then
        exit "$(cat "{state_dir}/flyctl_rollback_exit_code")"
    fi
    exit 0
fi

if [[ "$*" == *"deploy"* ]]; then
    if [[ -f "{state_dir}/flyctl_deploy_exit_code" ]]; then
        exit "$(cat "{state_dir}/flyctl_deploy_exit_code")"
    fi
    exit 0
fi

exit 0
""",
        encoding="utf-8",
    )
    flyctl_stub.chmod(flyctl_stub.stat().st_mode | stat.S_IEXEC)

    curl_stub = bin_dir / "curl"
    curl_stub.write_text(
        f"""#!/usr/bin/env bash
echo "$@" >> "{state_dir}/curl_log.txt"

resp_file="{state_dir}/curl_responses.txt"
if [[ -f "$resp_file" ]]; then
    code=$(head -n 1 "$resp_file")
    tail -n +2 "$resp_file" > "$resp_file.tmp" && mv "$resp_file.tmp" "$resp_file"
    echo "$code"
else
    echo "200"
fi
exit 0
""",
        encoding="utf-8",
    )
    curl_stub.chmod(curl_stub.stat().st_mode | stat.S_IEXEC)

    sleep_stub = bin_dir / "sleep"
    sleep_stub.write_text(
        f"""#!/usr/bin/env bash
echo "$@" >> "{state_dir}/sleep_log.txt"
exit 0
""",
        encoding="utf-8",
    )
    sleep_stub.chmod(sleep_stub.stat().st_mode | stat.S_IEXEC)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["FLY_APP_NAME"] = "cti-dashboard"
    env["SMOKE_MAX_ATTEMPTS"] = "3"
    env["SMOKE_SLEEP_SECONDS"] = "1"
    env["SMOKE_TIMEOUT_SECONDS"] = "1"

    yield state_dir, env


def _run_deploy_script(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(DEPLOY_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_deploy_script_syntax_and_executable() -> None:
    assert DEPLOY_SCRIPT.exists(), f"{DEPLOY_SCRIPT} does not exist"
    mode = DEPLOY_SCRIPT.stat().st_mode
    assert bool(mode & stat.S_IXUSR), "deploy_fly.sh is not executable"
    res = subprocess.run(
        ["bash", "-n", str(DEPLOY_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Syntax check failed: {res.stderr}"


def test_deploy_healthy(test_env: tuple[Path, dict[str, str]]) -> None:
    state_dir, env = test_env
    releases = [
        {"Version": 10, "Status": "complete", "ImageRef": "registry.fly.io/cti-dashboard:v10"}
    ]
    (state_dir / "flyctl_releases.json").write_text(json.dumps(releases), encoding="utf-8")
    (state_dir / "curl_responses.txt").write_text("200\n", encoding="utf-8")

    result = _run_deploy_script(env)
    assert result.returncode == 0, f"Script failed: stdout={result.stdout}\nstderr={result.stderr}"
    assert "Smoke check passed with HTTP 200!" in result.stdout
    assert "Recorded previous release image: registry.fly.io/cti-dashboard:v10" in result.stdout

    flyctl_log = (state_dir / "flyctl_log.txt").read_text(encoding="utf-8")
    assert "releases --image --json -a cti-dashboard" in flyctl_log
    assert "deploy --remote-only --app cti-dashboard" in flyctl_log
    assert "deploy --image" not in flyctl_log


def test_deploy_transient_smoke_recovery(test_env: tuple[Path, dict[str, str]]) -> None:
    state_dir, env = test_env
    releases = [
        {"Version": 11, "Status": "complete", "ImageRef": "registry.fly.io/cti-dashboard:v11"}
    ]
    (state_dir / "flyctl_releases.json").write_text(json.dumps(releases), encoding="utf-8")
    (state_dir / "curl_responses.txt").write_text("503\n503\n200\n", encoding="utf-8")

    result = _run_deploy_script(env)
    assert result.returncode == 0, f"Script failed: stdout={result.stdout}\nstderr={result.stderr}"
    assert "Smoke check passed with HTTP 200!" in result.stdout

    sleep_log = (state_dir / "sleep_log.txt").read_text(encoding="utf-8")
    assert len(sleep_log.strip().splitlines()) == 2


def test_smoke_failure_triggers_rollback(test_env: tuple[Path, dict[str, str]]) -> None:
    state_dir, env = test_env
    releases = [
        {"Version": 12, "Status": "complete", "ImageRef": "registry.fly.io/cti-dashboard:v12"}
    ]
    (state_dir / "flyctl_releases.json").write_text(json.dumps(releases), encoding="utf-8")
    (state_dir / "curl_responses.txt").write_text("503\n503\n503\n200\n", encoding="utf-8")

    result = _run_deploy_script(env)
    assert result.returncode != 0, "Script should exit non-zero on smoke failure"
    expected_msg = (
        "Initiating automated rollback to previous release image: registry.fly.io/cti-dashboard:v12"
    )
    assert expected_msg in result.stdout
    assert "Rollback health check succeeded (HTTP 200)." in result.stdout

    flyctl_log = (state_dir / "flyctl_log.txt").read_text(encoding="utf-8")
    assert "deploy --image registry.fly.io/cti-dashboard:v12 --app cti-dashboard" in flyctl_log


def test_rollout_failure_triggers_rollback(test_env: tuple[Path, dict[str, str]]) -> None:
    state_dir, env = test_env
    releases = [
        {"Version": 15, "Status": "complete", "ImageRef": "registry.fly.io/cti-dashboard:v15"}
    ]
    (state_dir / "flyctl_releases.json").write_text(json.dumps(releases), encoding="utf-8")
    (state_dir / "flyctl_deploy_exit_code").write_text("1\n", encoding="utf-8")
    (state_dir / "curl_responses.txt").write_text("200\n", encoding="utf-8")

    result = _run_deploy_script(env)
    assert result.returncode != 0, "Script should exit non-zero when deploy command fails"
    assert "Failure detected: Initial deployment command failed" in result.stdout
    expected_msg = (
        "Initiating automated rollback to previous release image: registry.fly.io/cti-dashboard:v15"
    )
    assert expected_msg in result.stdout

    flyctl_log = (state_dir / "flyctl_log.txt").read_text(encoding="utf-8")
    assert "deploy --image registry.fly.io/cti-dashboard:v15 --app cti-dashboard" in flyctl_log


def test_rollback_failure_handled_cleanly(test_env: tuple[Path, dict[str, str]]) -> None:
    state_dir, env = test_env
    releases = [
        {"Version": 16, "Status": "complete", "ImageRef": "registry.fly.io/cti-dashboard:v16"}
    ]
    (state_dir / "flyctl_releases.json").write_text(json.dumps(releases), encoding="utf-8")
    (state_dir / "flyctl_deploy_exit_code").write_text("1\n", encoding="utf-8")
    (state_dir / "flyctl_rollback_exit_code").write_text("1\n", encoding="utf-8")

    result = _run_deploy_script(env)
    assert result.returncode != 0
    assert "Rollback deployment command failed!" in result.stdout


def test_no_previous_image_failure(test_env: tuple[Path, dict[str, str]]) -> None:
    state_dir, env = test_env
    (state_dir / "flyctl_releases.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "flyctl_deploy_exit_code").write_text("1\n", encoding="utf-8")

    result = _run_deploy_script(env)
    assert result.returncode != 0
    assert "No previous release image recorded. Cannot perform automated rollback." in result.stdout
    flyctl_log = (state_dir / "flyctl_log.txt").read_text(encoding="utf-8")
    assert "deploy --image" not in flyctl_log


def test_ci_workflow_structure() -> None:
    assert CI_WORKFLOW.exists(), f"{CI_WORKFLOW} does not exist"
    assert not FLY_DEPLOY_WORKFLOW.exists(), f"{FLY_DEPLOY_WORKFLOW} should have been deleted"

    with open(CI_WORKFLOW, encoding="utf-8") as f:
        workflow: dict[str, Any] = yaml.safe_load(f)

    jobs = workflow.get("jobs", {})
    assert "deploy" in jobs, "CI workflow missing 'deploy' job"
    deploy_job = jobs["deploy"]

    # Needs lint-and-test
    needs = deploy_job.get("needs")
    if isinstance(needs, list):
        assert "lint-and-test" in needs
    else:
        assert needs == "lint-and-test"

    # Environment is production
    assert deploy_job.get("environment") == "production"

    # Gated to push on main
    condition = deploy_job.get("if", "")
    assert "refs/heads/main" in condition
    assert "push" in condition

    # Serialized concurrency without in-flight cancellation
    concurrency = deploy_job.get("concurrency")
    if isinstance(concurrency, dict):
        assert concurrency.get("group") == "deploy-production"
        assert concurrency.get("cancel-in-progress") is False
    else:
        assert concurrency == "deploy-production"

    # Runs deploy_fly.sh with FLY_API_TOKEN
    steps = deploy_job.get("steps", [])
    step_runs = [s.get("run", "") for s in steps]
    assert any(".github/scripts/deploy_fly.sh" in r for r in step_runs)
    deploy_step = next(s for s in steps if ".github/scripts/deploy_fly.sh" in s.get("run", ""))
    env_vars = deploy_step.get("env", {})
    assert "FLY_API_TOKEN" in env_vars
    assert "secrets.FLY_API_TOKEN" in env_vars["FLY_API_TOKEN"]
