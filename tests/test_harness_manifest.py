import hashlib
import json
import subprocess
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def load_manifest() -> dict:
    return tomllib.loads((ROOT / "harness.toml").read_text())


def checks_by_id() -> dict[str, dict]:
    manifest = load_manifest()
    return {check["id"]: check for check in manifest["checks"]}


def write_basic_harness_fixture(
    fixture: Path,
    *,
    local_command: str = "python3 -c 'print(\"ok\")'",
    evidence_artifacts: list[str] | None = None,
    extra_checks: str = "",
) -> None:
    fixture.mkdir()
    artifacts = evidence_artifacts if evidence_artifacts is not None else ["fixture-output"]
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture trusted suite"
command = {json.dumps(local_command)}
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture output"
evidence_artifacts = {json.dumps(artifacts)}
{extra_checks}
""".strip()
        + "\n"
    )


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, text=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True, text=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Harness Test",
            "-c",
            "user.email=harness@example.invalid",
            "commit",
            "-m",
            "initial fixture",
        ],
        cwd=path,
        check=True,
        text=True,
        capture_output=True,
    )


def test_harness_manifest_defines_agent_runnable_layers():
    manifest = load_manifest()
    checks = checks_by_id()

    assert manifest["trusted_suite"] == (
        "scripts/harness.py sweep required --report .cache/harness/required.json"
    )
    assert manifest["iteration_hint"] == ".venv/bin/python -m pytest <path-or-node>"
    assert manifest["report_path"] == ".cache/harness/required.json"
    assert set(checks) >= {
        "local",
        "device-full",
        "release-artifact",
        "qemu-mangoapp-rootfs",
    }
    assert checks["local"]["command"] == "PYTHON=.venv/bin/python scripts/check-local.sh"
    assert checks["local"]["requires"] == []
    assert checks["local"]["safe_for_agents"] is True
    assert checks["local"]["tier"] == "required"
    assert checks["local"]["expectation"] == "pass"
    assert checks["local"]["evidence_artifacts"] == [
        "command-output",
        "ruff-summary",
        "engineering-policy-summary",
        "shell-syntax-summary",
        "pytest-summary",
        "compileall-summary",
    ]
    assert checks["device-full"]["requires"] == ["root-ssh", "handheld"]
    assert checks["device-full"]["safe_for_agents"] is False
    assert checks["device-full"]["tier"] == "guarded"
    assert checks["device-full"]["expectation"] == "blocked"
    assert checks["device-full"]["evidence_artifacts"] == [
        "verify-on-device-output",
        "systemd-state",
        "steamos-manager-state",
        "rapl-pl1-state",
        "failed-units-list",
    ]
    assert checks["release-artifact"]["requires"] == ["artifact-path"]
    assert checks["qemu-mangoapp-rootfs"]["requires"] == [
        "linux-x86_64",
        "network",
        "sudo",
        "20gb-disk",
    ]


def test_agents_md_is_concise_and_points_to_machine_readable_harness():
    agents = (ROOT / "AGENTS.md").read_text()

    assert "harness.toml" in agents
    assert "scripts/harness.py list --json" in agents
    assert "scripts/harness.py sweep required" in agents
    assert "PYTHON=.venv/bin/python scripts/check-local.sh" in agents
    assert "root@10.100.0.19" in agents
    assert "Do not run device, QEMU, release, or network-heavy checks unless" in agents
    assert len(agents.split()) < 350


def test_harness_cli_lists_manifest_as_json():
    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "list", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    local = next(check for check in payload["checks"] if check["id"] == "local")
    device = next(check for check in payload["checks"] if check["id"] == "device-full")

    assert local["safe_for_agents"] is True
    assert local["command"] == "PYTHON=.venv/bin/python scripts/check-local.sh"
    assert device["safe_for_agents"] is False
    assert device["requires"] == ["root-ssh", "handheld"]


def test_harness_cli_status_reports_control_plane_and_last_sweep(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    report = fixture / ".cache/harness/required.json"
    report.parent.mkdir(parents=True)
    (fixture / "harness.toml").write_text(
        """
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture trusted suite"
command = "python3 -c 'print(\\\"ok\\\")'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture output"

[[checks]]
id = "device"
description = "fixture guarded device check"
command = "ssh root@10.100.0.19 true"
requires = ["root-ssh", "handheld"]
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "slow"
evidence = "device output"
evidence_artifacts = ["device-output"]
""".strip()
        + "\n"
    )
    report.write_text(
        json.dumps(
            {
                "selector": "required",
                "status": "passed",
                "results": [
                    {
                        "id": "local",
                        "status": "pass",
                        "returncode": 0,
                        "duration_seconds": 1.25,
                    }
                ],
            }
        )
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "status",
            "--json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert payload["trusted_suite"] == (
        "scripts/harness.py sweep required --report .cache/harness/required.json"
    )
    assert payload["iteration_hint"] == ".venv/bin/python -m pytest <path-or-node>"
    assert payload["required_checks"] == ["local"]
    assert payload["guarded_checks"] == ["device"]
    assert payload["last_report"]["status"] == "passed"
    assert payload["last_report"]["results"][0]["id"] == "local"


def test_harness_cli_sweep_report_round_trips_freshness_and_effective_matrix(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(fixture)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            ".cache/harness/required.json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    assert "local: pass" in result.stdout

    report = json.loads((fixture / ".cache/harness/required.json").read_text())
    assert report["context"]["manifest"]["sha256"]
    assert report["context"]["trusted_suite"] == (
        "scripts/harness.py sweep required --report .cache/harness/required.json"
    )

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(status.stdout)
    assert payload["freshness"] == payload["last_report"]["freshness"]
    assert payload["gate_matrix"] == payload["effective_checks"]
    assert payload["last_report"]["freshness"]["status"] == "fresh"
    assert payload["last_report"]["freshness"]["reasons"] == []
    assert payload["pending_verification"] == []

    matrix = {check["id"]: check for check in payload["effective_checks"]}
    assert matrix["local"]["effective_tier"] == "required"
    assert matrix["local"]["runnable_by_default"] is True
    assert matrix["local"]["blocked_reason"] is None
    assert matrix["local"]["required_evidence"] == "fixture output"
    assert matrix["local"]["evidence_artifacts"] == ["fixture-output"]
    assert matrix["local"]["verification_state"] == "verified"
    assert matrix["local"]["last_result"]["status"] == "pass"


def test_harness_cli_status_rejects_single_run_report_at_required_report_path(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(
        fixture,
        extra_checks="""

[[checks]]
id = "docs"
description = "second required gate"
command = "python3 -c 'print(\\\"docs ok\\\")'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture docs output"
evidence_artifacts = ["fixture-output"]
""",
    )

    subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "run",
            "local",
            "--report",
            ".cache/harness/required.json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["report_type"] == "run"
    assert payload["last_report"]["freshness"]["status"] == "stale"
    assert "required report is not a sweep report" in payload["freshness"]["reasons"]
    assert payload["pending_verification"] == [
        {
            "scope": "required",
            "checks": ["local", "docs"],
            "reason": "last report is stale: required report is not a sweep report",
            "command": "scripts/harness.py sweep required --report .cache/harness/required.json",
        }
    ]


def test_harness_cli_sweep_report_validates_local_evidence_artifacts(tmp_path):
    fixture = tmp_path / "repo"
    local_command = (
        "python3 -c "
        "\"print('==> ruff'); "
        "print('All checks passed!'); "
        "print('==> engineering policy'); "
        "print('engineering policy passed'); "
        "print('==> shell syntax'); "
        "print('==> pytest'); "
        "print('344 passed in 7.74s'); "
        "print('==> compileall')\""
    )
    artifacts = [
        "command-output",
        "ruff-summary",
        "engineering-policy-summary",
        "shell-syntax-summary",
        "pytest-summary",
        "compileall-summary",
    ]
    write_basic_harness_fixture(
        fixture,
        local_command=local_command,
        evidence_artifacts=artifacts,
    )

    subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            ".cache/harness/required.json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    report = json.loads((fixture / ".cache/harness/required.json").read_text())
    result = report["results"][0]
    assert result["stdout"].startswith("==> ruff\n")
    assert "344 passed in 7.74s" in result["stdout"]
    assert result["stderr"] == ""
    artifact_results = {
        item["id"]: item["status"] for item in result["evidence_artifact_results"]
    }
    assert artifact_results == {artifact: "pass" for artifact in artifacts}

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    matrix = {check["id"]: check for check in json.loads(status.stdout)["effective_checks"]}
    assert matrix["local"]["evidence_state"] == "verified"
    assert matrix["local"]["verification_state"] == "verified"


def test_harness_cli_sweep_fails_when_declared_local_artifact_is_missing(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(
        fixture,
        local_command="python3 -c 'print(\"ok\")'",
        evidence_artifacts=["pytest-summary"],
    )

    sweep = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            ".cache/harness/required.json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert sweep.returncode == 1
    report = json.loads((fixture / ".cache/harness/required.json").read_text())
    assert report["status"] == "failed"
    result = report["results"][0]
    assert result["status"] == "evidence-fail"
    assert result["evidence_artifact_results"] == [
        {
            "id": "pytest-summary",
            "status": "missing",
            "detail": "pytest summary marker not found",
        }
    ]

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(status.stdout)
    matrix = {check["id"]: check for check in payload["effective_checks"]}
    assert matrix["local"]["evidence_state"] == "failed"
    assert matrix["local"]["verification_state"] == "failed"
    assert payload["pending_verification"] == [
        {
            "scope": "check",
            "checks": ["local"],
            "reason": "local evidence artifacts are missing: pytest-summary",
            "command": "scripts/harness.py sweep required --report .cache/harness/required.json",
        }
    ]


def test_harness_cli_sweep_validates_guarded_evidence_artifact_markers(tmp_path):
    fixture = tmp_path / "repo"
    artifacts = [
        "verify-on-device-output",
        "systemd-state",
        "steamos-manager-state",
        "rapl-pl1-state",
        "failed-units-list",
        "observe-output",
        "gpu-priority-output",
        "cpu-policy-restore-diff",
        "profile-manifest-json",
        "profile-summary-json",
        "mangohud-csv",
        "game-power-jsonl",
        "restore-snapshot",
        "runtime-telemetry-contract-json",
        "profile-runtime-telemetry-contract-json",
        "action-equivalence-replay-summary",
        "pacman-repository-shape",
        "artifact-root",
        "verification-output",
        "mangoapp-artifact-path",
        "file-output",
        "rootfs-build-log",
    ]
    marker_lines = [
        "OK: SteamOS Manager TDP remote works and restored 30W",
        "OK: systemd failed-unit list is empty",
        "OK: RAPL PL1/PL2 restored",
        "== game-power observe ==",
        "== game-power gpu-priority ==",
        "game-power verifier: CPU policy restored",
        "profile artifact manifest.json: .cache/game-power/profiles/run/manifest.json",
        "profile artifact summary.json: .cache/game-power/profiles/run/summary.json",
        "profile artifact mangohud.csv: .cache/game-power/profiles/run/mangohud.csv",
        "profile artifact game-power.jsonl: .cache/game-power/profiles/run/game-power.jsonl",
        "profile artifact restore snapshot: .cache/game-power/profiles/run/restore-affinity.json",
        (
            "profile artifact runtime telemetry contract: "
            ".cache/game-power/profiles/run/runtime-telemetry-contract.json"
        ),
        (
            "profile artifact runtime telemetry aggregate: "
            ".cache/game-power/profiles/profile-runtime-telemetry-contract.json"
        ),
        (
            "profile artifact action equivalence: "
            ".cache/game-power/profiles/action-equivalence.json"
        ),
        (
            "GitLab pacman artifact dry run passed: "
            "/tmp/artifact/rivoreo-steamos/os/x86_64"
        ),
        "mangoapp artifact path: .cache/steamos-qemu/mangoapp",
        ".cache/steamos-qemu/mangoapp: ELF 64-bit LSB pie executable",
        "rootfs build log: meson compile mangoapp completed",
    ]
    script = "\n".join(f"print({json.dumps(line)})" for line in marker_lines)
    write_basic_harness_fixture(
        fixture,
        local_command="python3 scripts/emit_markers.py",
        evidence_artifacts=artifacts,
    )
    scripts_dir = fixture / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "emit_markers.py").write_text(script + "\n")

    sweep = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            ".cache/harness/required.json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert sweep.returncode == 0
    report = json.loads((fixture / ".cache/harness/required.json").read_text())
    result = report["results"][0]
    assert result["status"] == "pass"
    assert {
        item["id"]: item["status"] for item in result["evidence_artifact_results"]
    } == {artifact: "pass" for artifact in artifacts}


def test_harness_cli_sweep_rejects_missing_guarded_artifact_marker(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(
        fixture,
        local_command="python3 -c 'print(\"== game-power gpu-priority ==\")'",
        evidence_artifacts=["cpu-policy-restore-diff"],
    )

    sweep = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            ".cache/harness/required.json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert sweep.returncode == 1
    report = json.loads((fixture / ".cache/harness/required.json").read_text())
    result = report["results"][0]
    assert result["status"] == "evidence-fail"
    assert result["evidence_artifact_results"] == [
        {
            "id": "cpu-policy-restore-diff",
            "status": "missing",
            "detail": "CPU policy restore marker not found",
        }
    ]


def test_guarded_verifier_scripts_emit_machine_readable_artifact_markers():
    verify_on_device = (ROOT / "scripts/verify-on-device.sh").read_text()
    verify_game_power = (ROOT / "scripts/verify-game-power-on-device.sh").read_text()
    profile_game_power = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()
    qemu_build = (ROOT / "scripts/steamos-qemu-build-env.sh").read_text()

    assert "OK: systemd failed-unit list is empty" in verify_on_device
    assert "OK: RAPL PL1/PL2 restored" in verify_on_device
    assert "game-power verifier: CPU policy restored" in verify_game_power
    assert "profile artifact manifest.json:" in profile_game_power
    assert "profile artifact summary.json:" in profile_game_power
    assert "profile artifact mangohud.csv:" in profile_game_power
    assert "profile artifact game-power.jsonl:" in profile_game_power
    assert "profile artifact restore snapshot:" in profile_game_power
    assert "wait_for_live_mangohud_csv" in profile_game_power
    assert "--frame-performance-csv" in profile_game_power
    assert "--require-frame-performance" in profile_game_power
    assert "--require-fps-target-satisfied" in profile_game_power
    assert "mangoapp artifact path:" in qemu_build
    assert "rootfs build log: meson compile mangoapp completed" in qemu_build


def test_harness_cli_status_marks_unvalidated_evidence_artifacts_pending(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(fixture, evidence_artifacts=["pytest-summary"])
    report = fixture / ".cache/harness/required.json"
    report.parent.mkdir(parents=True)
    manifest_sha256 = hashlib.sha256((fixture / "harness.toml").read_bytes()).hexdigest()
    report.write_text(
        json.dumps(
            {
                "selector": "required",
                "status": "passed",
                "results": [{"id": "local", "status": "pass", "returncode": 0}],
                "context": {
                    "schema_version": 1,
                    "trusted_suite": (
                        "scripts/harness.py sweep required --report .cache/harness/required.json"
                    ),
                    "manifest": {
                        "path": "harness.toml",
                        "sha256": manifest_sha256,
                    },
                    "workspace": {"available": False, "fingerprint": "unavailable"},
                },
            }
        )
        + "\n"
    )

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["freshness"]["status"] == "fresh"
    matrix = {check["id"]: check for check in payload["effective_checks"]}
    assert matrix["local"]["evidence_state"] == "pending"
    assert matrix["local"]["verification_state"] == "pending"
    assert payload["pending_verification"] == [
        {
            "scope": "check",
            "checks": ["local"],
            "reason": "local evidence artifacts are unverified: pytest-summary",
            "command": "scripts/harness.py sweep required --report .cache/harness/required.json",
        }
    ]


def test_harness_cli_status_marks_report_stale_after_manifest_change(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(fixture)
    subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            ".cache/harness/required.json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    manifest_path = fixture / "harness.toml"
    manifest_path.write_text(
        manifest_path.read_text().replace("fixture output", "changed fixture output")
    )

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["freshness"]["status"] == "stale"
    assert "manifest changed since report" in payload["last_report"]["freshness"]["reasons"]
    assert payload["pending_verification"] == [
        {
            "scope": "required",
            "checks": ["local"],
            "reason": "last report is stale: manifest changed since report",
            "command": "scripts/harness.py sweep required --report .cache/harness/required.json",
        }
    ]


def test_harness_cli_status_marks_failed_required_result_pending(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(fixture, local_command="python3 -c 'raise SystemExit(7)'")

    sweep = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            ".cache/harness/required.json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert sweep.returncode == 1

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["freshness"]["status"] == "fresh"
    matrix = {check["id"]: check for check in payload["effective_checks"]}
    assert matrix["local"]["verification_state"] == "failed"
    assert payload["pending_verification"] == [
        {
            "scope": "check",
            "checks": ["local"],
            "reason": "local last result is fail",
            "command": "scripts/harness.py sweep required --report .cache/harness/required.json",
        }
    ]


def test_harness_cli_status_keeps_fresh_git_report_after_report_write(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(fixture)
    init_git_repo(fixture)

    subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            ".cache/harness/required.json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["freshness"]["status"] == "fresh"
    assert payload["pending_verification"] == []


def test_harness_cli_status_ignores_generated_pycache_for_freshness(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(fixture)
    init_git_repo(fixture)
    subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            ".cache/harness/required.json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    pycache = fixture / "scripts/__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "generated.cpython-313.pyc").write_bytes(b"generated")

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["freshness"]["status"] == "fresh"
    assert payload["pending_verification"] == []


def test_harness_cli_status_marks_tracked_git_change_pending(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(fixture)
    scripts_dir = fixture / "scripts"
    scripts_dir.mkdir()
    tracked_file = scripts_dir / "sample.py"
    tracked_file.write_text("VALUE = 1\n")
    init_git_repo(fixture)
    subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            ".cache/harness/required.json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    tracked_file.write_text("VALUE = 2\n")

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["freshness"]["status"] == "stale"
    assert "workspace changed since report" in payload["last_report"]["freshness"]["reasons"]
    assert payload["pending_verification"] == [
        {
            "scope": "required",
            "checks": ["local"],
            "reason": "last report is stale: workspace changed since report",
            "command": "scripts/harness.py sweep required --report .cache/harness/required.json",
        }
    ]


def test_harness_cli_status_marks_invalid_report_schema_pending(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(fixture)
    report = fixture / ".cache/harness/required.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"status": "passed", "results": "not-a-list"}) + "\n")

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["status"] == "invalid"
    assert payload["last_report"]["freshness"]["status"] == "invalid"
    assert payload["pending_verification"][0]["reason"] == "last report is invalid"


def test_harness_cli_status_marks_wrong_selector_report_pending(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(fixture)
    report = fixture / ".cache/harness/required.json"
    report.parent.mkdir(parents=True)
    manifest_sha256 = hashlib.sha256((fixture / "harness.toml").read_bytes()).hexdigest()
    report.write_text(
        json.dumps(
            {
                "selector": "safe",
                "status": "passed",
                "results": [],
                "context": {
                    "schema_version": 1,
                    "trusted_suite": (
                        "scripts/harness.py sweep required --report .cache/harness/required.json"
                    ),
                    "manifest": {
                        "path": "harness.toml",
                        "sha256": manifest_sha256,
                    },
                    "workspace": {"available": False, "fingerprint": "unavailable"},
                },
            }
        )
        + "\n"
    )

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["freshness"]["status"] == "stale"
    assert "report selector is not required" in payload["last_report"]["freshness"]["reasons"]


def test_harness_cli_status_marks_missing_workspace_context_pending(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(fixture)
    init_git_repo(fixture)
    report = fixture / ".cache/harness/required.json"
    report.parent.mkdir(parents=True)
    manifest_sha256 = hashlib.sha256((fixture / "harness.toml").read_bytes()).hexdigest()
    report.write_text(
        json.dumps(
            {
                "selector": "required",
                "status": "passed",
                "results": [{"id": "local", "status": "pass", "returncode": 0}],
                "context": {
                    "schema_version": 1,
                    "trusted_suite": (
                        "scripts/harness.py sweep required --report .cache/harness/required.json"
                    ),
                    "manifest": {"path": "harness.toml", "sha256": manifest_sha256},
                },
            }
        )
        + "\n"
    )

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["freshness"]["status"] == "stale"
    assert "report missing workspace fingerprint" in payload["freshness"]["reasons"]
    assert payload["pending_verification"][0]["reason"] == (
        "last report is stale: report missing workspace fingerprint"
    )


def test_harness_cli_status_hashes_untracked_file_content(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(fixture)
    init_git_repo(fixture)
    untracked = fixture / "new-source.py"
    untracked.write_text("VALUE = 1\n")
    subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            ".cache/harness/required.json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    untracked.write_text("VALUE = 2\n")

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["freshness"]["status"] == "stale"
    assert "workspace changed since report" in payload["freshness"]["reasons"]
    assert payload["pending_verification"][0]["reason"] == (
        "last report is stale: workspace changed since report"
    )


def test_harness_cli_status_marks_failed_aggregate_report_pending(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(fixture)
    report = fixture / ".cache/harness/required.json"
    report.parent.mkdir(parents=True)
    manifest_sha256 = hashlib.sha256((fixture / "harness.toml").read_bytes()).hexdigest()
    report.write_text(
        json.dumps(
            {
                "selector": "required",
                "status": "failed",
                "results": [{"id": "local", "status": "pass", "returncode": 0}],
                "context": {
                    "schema_version": 1,
                    "trusted_suite": (
                        "scripts/harness.py sweep required --report .cache/harness/required.json"
                    ),
                    "manifest": {
                        "path": "harness.toml",
                        "sha256": manifest_sha256,
                    },
                    "workspace": {"available": False, "fingerprint": "unavailable"},
                },
            }
        )
        + "\n"
    )

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["freshness"]["status"] == "fresh"
    assert payload["pending_verification"] == [
        {
            "scope": "required",
            "checks": ["local"],
            "reason": "last report status is failed",
            "command": "scripts/harness.py sweep required --report .cache/harness/required.json",
        }
    ]


def test_harness_cli_status_reports_effective_guarded_matrix_and_missing_report(tmp_path):
    fixture = tmp_path / "repo"
    write_basic_harness_fixture(
        fixture,
        extra_checks="""

[[checks]]
id = "device"
description = "fixture guarded device check"
command = "ssh root@10.100.0.19 true"
requires = ["root-ssh", "handheld"]
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "slow"
evidence = "device output"
evidence_artifacts = ["device-output"]
""",
    )

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["freshness"]["status"] == "missing"
    assert payload["pending_verification"][0]["reason"] == "last report is missing"

    matrix = {check["id"]: check for check in payload["effective_checks"]}
    assert matrix["device"]["effective_tier"] == "guarded"
    assert matrix["device"]["runnable_by_default"] is False
    assert matrix["device"]["blocked_reason"] == "requires: root-ssh, handheld"
    assert matrix["device"]["required_evidence"] == "device output"
    assert matrix["device"]["evidence_artifacts"] == ["device-output"]
    assert matrix["device"]["verification_state"] == "guarded"


def test_harness_cli_status_does_not_execute_checks(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "status-ran-check"
    marker_command = (
        f"python3 -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\""
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "status must not run this"
command = {json.dumps(marker_command)}
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert payload["required_checks"] == ["local"]
    assert not marker.exists()


def test_harness_cli_explain_describes_guarded_check_without_running_it():
    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "explain", "device-full"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "device-full" in result.stdout
    assert "tier: guarded" in result.stdout
    assert "requires: root-ssh, handheld" in result.stdout
    assert "scripts/verify-on-device.sh root@10.100.0.19" in result.stdout
    assert "evidence:" in result.stdout
    assert "evidence artifacts:" in result.stdout
    assert "verify-on-device-output" in result.stdout
    assert "run command: scripts/harness.py run device-full\n" in result.stdout
    assert "run command: scripts/harness.py run device-full --allow" not in result.stdout
    assert "guarded acknowledgement required: --allow-guarded" in result.stdout
    assert "--allow-requirement root-ssh" in result.stdout


def test_harness_cli_explain_does_not_execute_guarded_check(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "explain-ran-guarded"
    marker_command = (
        f"python3 -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\""
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "guarded-device"
description = "explain must not run this"
command = {json.dumps(marker_command)}
requires = ["root-ssh", "handheld"]
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "explain",
            "guarded-device",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "guarded-device" in result.stdout
    assert not marker.exists()


def test_harness_cli_status_rejects_noncanonical_trusted_suite(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    bad_trusted_suite = (
        "scripts/harness.py sweep required --report .cache/harness/required.json "
        "&& scripts/harness.py sweep all"
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "{bad_trusted_suite}"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture"
command = "python3 -c 'print(1)'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "trusted_suite must be the required sweep only" in result.stderr


def test_harness_cli_refuses_requirement_gated_checks_by_default():
    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "run", "device-full"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "Refusing to run device-full" in result.stderr
    assert "--allow-requirement root-ssh" in result.stderr
    assert "--allow-requirement handheld" in result.stderr


def test_harness_cli_run_captures_allowed_guarded_artifact_evidence(tmp_path):
    fixture = tmp_path / "repo"
    marker = "game-power verifier: CPU policy restored"
    command = f'python3 -c "print({marker!r})"'
    write_basic_harness_fixture(
        fixture,
        extra_checks=f"""

[[checks]]
id = "guarded-game-power"
description = "fixture guarded run"
command = {json.dumps(command)}
requires = ["root-ssh", "handheld"]
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "fast"
evidence = "fixture guarded output"
evidence_artifacts = ["cpu-policy-restore-diff"]
""",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "run",
            "guarded-game-power",
            "--allow-guarded",
            "--allow-requirement",
            "root-ssh",
            "--allow-requirement",
            "handheld",
            "--report",
            ".cache/harness/guarded.json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    report = json.loads((fixture / ".cache/harness/guarded.json").read_text())
    assert report["status"] == "pass"
    assert report["report_type"] == "run"
    assert report["stdout"] == marker + "\n"
    assert report["stderr"] == ""
    assert report["evidence_artifact_results"] == [
        {
            "id": "cpu-policy-restore-diff",
            "status": "pass",
            "detail": "CPU policy restore marker found",
        }
    ]


def test_harness_cli_run_fails_allowed_guarded_check_with_missing_artifact(tmp_path):
    fixture = tmp_path / "repo"
    command = "python3 -c \"print('missing restore marker')\""
    write_basic_harness_fixture(
        fixture,
        extra_checks=f"""

[[checks]]
id = "guarded-game-power"
description = "fixture guarded run"
command = {json.dumps(command)}
requires = ["root-ssh", "handheld"]
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "fast"
evidence = "fixture guarded output"
evidence_artifacts = ["cpu-policy-restore-diff"]
""",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "run",
            "guarded-game-power",
            "--allow-guarded",
            "--allow-requirement",
            "root-ssh",
            "--allow-requirement",
            "handheld",
            "--report",
            ".cache/harness/guarded.json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    report = json.loads((fixture / ".cache/harness/guarded.json").read_text())
    assert report["status"] == "evidence-fail"
    assert report["report_type"] == "run"
    assert report["stdout"] == "missing restore marker\n"
    assert report["evidence_artifact_results"] == [
        {
            "id": "cpu-policy-restore-diff",
            "status": "missing",
            "detail": "CPU policy restore marker not found",
        }
    ]


def test_harness_cli_status_reads_single_run_report(tmp_path):
    fixture = tmp_path / "repo"
    marker = "game-power verifier: CPU policy restored"
    command = f'python3 -c "print({marker!r})"'
    write_basic_harness_fixture(
        fixture,
        extra_checks=f"""

[[checks]]
id = "guarded-game-power"
description = "fixture guarded run"
command = {json.dumps(command)}
requires = ["root-ssh", "handheld"]
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "fast"
evidence = "fixture guarded output"
evidence_artifacts = ["cpu-policy-restore-diff"]
""",
    )

    subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "run",
            "guarded-game-power",
            "--allow-guarded",
            "--allow-requirement",
            "root-ssh",
            "--allow-requirement",
            "handheld",
            "--report",
            ".cache/harness/guarded.json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    status = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "status",
            "--json",
            "--report",
            ".cache/harness/guarded.json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(status.stdout)
    assert payload["last_report"]["freshness"]["status"] == "fresh"
    matrix = {check["id"]: check for check in payload["effective_checks"]}
    assert matrix["guarded-game-power"]["evidence_state"] == "verified"
    assert matrix["guarded-game-power"]["verification_state"] == "verified"


def test_harness_cli_sweeps_required_fixture_gates_and_records_known_failures(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    report = fixture / "required.json"
    (fixture / "harness.toml").write_text(
        """
version = 1

[[checks]]
id = "quick-pass"
description = "fixture pass"
command = "python3 -c 'print(\\\"pass gate\\\")'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"

[[checks]]
id = "known-stale"
description = "fixture known failure"
command = "python3 -c 'import sys; print(\\\"STALE-GATE\\\", file=sys.stderr); sys.exit(7)'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "known-fail"
failure_signature = "STALE-GATE"
quarantine_reason = "fixture documents known stale gate"
quarantine_expires = "2099-01-01"
expected_duration = "fast"
evidence = "fixture"

[[checks]]
id = "guarded-device"
description = "fixture guarded gate"
command = "ssh root@10.100.0.19 true"
requires = ["root-ssh", "handheld"]
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            str(report),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    payload = json.loads(report.read_text())
    statuses = {item["id"]: item["status"] for item in payload["results"]}
    assert statuses == {
        "quick-pass": "pass",
        "known-stale": "known-fail",
    }
    assert "guarded-device" not in statuses


def test_harness_cli_sweep_all_blocks_guarded_checks_without_running(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "guarded-ran"
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture pass"
command = "python3 -c 'print(\\\"pass gate\\\")'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"

[[checks]]
id = "guarded-device"
description = "must not run during sweep all without acknowledgement"
command = "python3 -c 'from pathlib import Path; Path({str(marker)!r}).write_text(\\\"ran\\\")'"
requires = ["root-ssh", "handheld"]
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "sweep", "all"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "guarded-device: blocked" in result.stderr
    assert not marker.exists()


def test_harness_cli_sweep_all_blocks_guarded_check_even_without_requirements(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "guarded-no-requires-ran"
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "guarded-no-requires"
description = "invalid manifest fixture, but runtime must still fail closed"
command = "python3 -c 'from pathlib import Path; Path({str(marker)!r}).write_text(\\\"ran\\\")'"
requires = []
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "sweep", "all"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "guarded-no-requires: blocked" in result.stderr
    assert not marker.exists()


def test_harness_cli_run_blocks_guarded_check_even_without_requirements(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "guarded-run-no-requires-ran"
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "guarded-no-requires"
description = "invalid manifest fixture, but runtime must still fail closed"
command = "python3 -c 'from pathlib import Path; Path({str(marker)!r}).write_text(\\\"ran\\\")'"
requires = []
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "run",
            "guarded-no-requires",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "guarded-no-requires is guarded but declares no requirements" in result.stderr
    assert not marker.exists()


def test_harness_cli_run_blocks_guarded_tier_even_if_marked_safe(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "guarded-tier-run-ran"
    marker_command = (
        f"python3 -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\""
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "guarded-tier"
description = "tier guard must win"
command = {json.dumps(marker_command)}
requires = []
safe_for_agents = true
tier = "guarded"
expectation = "blocked"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "run", "guarded-tier"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "guarded-tier is guarded but declares no requirements" in result.stderr
    assert not marker.exists()


def test_harness_cli_run_requires_guarded_acknowledgement_with_requirements(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "guarded-run-ran"
    marker_command = (
        f"python3 -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\""
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "guarded-device"
description = "explicit guarded ack must be separate from requirements"
command = {json.dumps(marker_command)}
requires = ["root-ssh"]
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    blocked = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "run",
            "guarded-device",
            "--allow-requirement",
            "root-ssh",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert blocked.returncode == 2
    assert "Refusing to run guarded-device because it is guarded." in blocked.stderr
    assert "--allow-guarded" in blocked.stderr
    assert not marker.exists()

    allowed = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "run",
            "guarded-device",
            "--allow-guarded",
            "--allow-requirement",
            "root-ssh",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert allowed.returncode == 0
    assert marker.read_text() == "ran"


def test_harness_cli_run_blocks_guarded_command_token_even_if_marked_safe(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "guarded-token-run-ran"
    marker_command = (
        f"python3 -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\" "
        "# ssh root@10.100.0.19"
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "guarded-token"
description = "command token guard must win"
command = {json.dumps(marker_command)}
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "run", "guarded-token"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "guarded-token is guarded but declares no requirements" in result.stderr
    assert not marker.exists()


def test_harness_cli_run_requires_guarded_acknowledgement_for_token_with_requirements(
    tmp_path,
):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "guarded-token-run-with-requires-ran"
    marker_command = (
        f"ssh() {{ python3 -c \"from pathlib import Path; "
        f"Path({str(marker)!r}).write_text('ran')\"; }}; ssh\troot@10.100.0.19"
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "guarded-token"
description = "command token guard must need explicit guarded ack"
command = {json.dumps(marker_command)}
requires = ["root-ssh"]
safe_for_agents = false
tier = "guarded"
expectation = "blocked"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "run",
            "guarded-token",
            "--allow-requirement",
            "root-ssh",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "Refusing to run guarded-token because it is guarded." in result.stderr
    assert not marker.exists()

    allowed = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "run",
            "guarded-token",
            "--allow-guarded",
            "--allow-requirement",
            "root-ssh",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert allowed.returncode == 0
    assert marker.read_text() == "ran"


def test_harness_cli_sweeps_block_guarded_tier_even_if_marked_safe(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "guarded-tier-sweep-ran"
    marker_command = (
        f"python3 -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\""
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "guarded-tier"
description = "tier guard must win"
command = {json.dumps(marker_command)}
requires = []
safe_for_agents = true
tier = "guarded"
expectation = "blocked"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    for selector in ("safe", "all"):
        result = subprocess.run(
            [sys.executable, "scripts/harness.py", "--root", str(fixture), "sweep", selector],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 1
        assert "guarded-tier: blocked" in result.stderr
    assert not marker.exists()


def test_harness_cli_sweep_required_blocks_guarded_command_token(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "guarded-token-sweep-ran"
    marker_command = (
        f"python3 -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\" "
        "# ssh root@10.100.0.19"
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "guarded-token"
description = "command token guard must win"
command = {json.dumps(marker_command)}
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "sweep", "required"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "guarded-token: blocked" in result.stderr
    assert not marker.exists()


def test_harness_cli_sweep_required_blocks_guarded_shell_whitespace_token(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "guarded-tab-token-sweep-ran"
    marker_command = (
        f"ssh() {{ python3 -c \"from pathlib import Path; "
        f"Path({str(marker)!r}).write_text('ran')\"; }}; ssh\troot@10.100.0.19"
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "guarded-tab-token"
description = "shell whitespace command token guard must win"
command = {json.dumps(marker_command)}
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "sweep", "required"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "guarded-tab-token: blocked" in result.stderr
    assert not marker.exists()


def test_harness_cli_sweep_required_blocks_guarded_absolute_path_token(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    marker = tmp_path / "guarded-path-token-sweep-ran"
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text(f"#!/bin/sh\nprintf ran > {marker}\n")
    fake_ssh.chmod(0o755)
    command = f"{fake_ssh}\troot@10.100.0.19"
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "guarded-path-token"
description = "absolute path command token guard must win"
command = {json.dumps(command)}
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "sweep", "required"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "guarded-path-token: blocked" in result.stderr
    assert not marker.exists()


def test_harness_cli_sweep_required_blocks_guarded_qemu_system_binary(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    marker = tmp_path / "guarded-qemu-token-sweep-ran"
    fake_qemu = fake_bin / "qemu-system-x86_64"
    fake_qemu.write_text(f"#!/bin/sh\nprintf ran > {marker}\n")
    fake_qemu.chmod(0o755)
    command = f"PATH={fake_bin}:$PATH qemu-system-x86_64 --version"
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "guarded-qemu-token"
description = "qemu-system family command token guard must win"
command = {json.dumps(command)}
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "slow"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "sweep", "required"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "guarded-qemu-token: blocked" in result.stderr
    assert not marker.exists()


def test_harness_cli_status_rejects_unsafe_manifest_report_path(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    bad_report_path = ".cache/harness/required.json; scripts/harness.py sweep all"
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report {bad_report_path}"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = "{bad_report_path}"

[[checks]]
id = "local"
description = "fixture pass"
command = "python3 -c 'print(\\\"pass gate\\\")'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "report path contains unsafe shell characters" in result.stderr


def test_harness_cli_sweep_report_path_is_relative_to_root(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    (fixture / "harness.toml").write_text(
        """
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture pass"
command = "python3 -c 'print(\\\"pass gate\\\")'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            ".cache/harness/required.json",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    assert "local: pass" in result.stdout

    status = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "status", "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(status.stdout)
    assert payload["last_report"]["exists"] is True
    assert payload["last_report"]["status"] == "passed"
    assert (fixture / ".cache/harness/required.json").exists()


def test_harness_cli_run_report_path_is_relative_to_root(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    caller = tmp_path / "caller"
    caller.mkdir()
    (fixture / "harness.toml").write_text(
        """
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture pass"
command = "python3 -c 'print(\\\"pass gate\\\")'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/harness.py"),
            "--root",
            str(fixture),
            "run",
            "local",
            "--report",
            "reports/run.json",
        ],
        cwd=caller,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "Running local" in result.stdout
    assert (fixture / "reports/run.json").exists()
    assert not (caller / "reports/run.json").exists()


def test_harness_cli_rejects_report_path_escape(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "escaped-report-command-ran"
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture pass"
command = "python3 -c 'from pathlib import Path; Path({str(marker)!r}).write_text(\\\"ran\\\")'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            "../outside.json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "report path escapes repository root" in result.stderr
    assert not marker.exists()
    assert not (tmp_path / "outside.json").exists()


def test_harness_cli_run_rejects_report_path_escape_before_running(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "run-escaped-report-command-ran"
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture pass"
command = "python3 -c 'from pathlib import Path; Path({str(marker)!r}).write_text(\\\"ran\\\")'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "run",
            "local",
            "--report",
            "../outside.json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "report path escapes repository root" in result.stderr
    assert not marker.exists()
    assert not (tmp_path / "outside.json").exists()


def test_harness_cli_run_rejects_absolute_report_path_before_running(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "run-absolute-report-command-ran"
    outside = tmp_path / "outside.json"
    marker_command = (
        f"python3 -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\""
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture pass"
command = {json.dumps(marker_command)}
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "run",
            "local",
            "--report",
            str(outside),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "report path escapes repository root" in result.stderr
    assert not marker.exists()
    assert not outside.exists()


def test_harness_cli_sweep_rejects_absolute_report_path_before_running(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    marker = tmp_path / "sweep-absolute-report-command-ran"
    outside = tmp_path / "outside.json"
    marker_command = (
        f"python3 -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\""
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture pass"
command = {json.dumps(marker_command)}
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "sweep",
            "required",
            "--report",
            str(outside),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "report path escapes repository root" in result.stderr
    assert not marker.exists()
    assert not outside.exists()


def test_harness_cli_status_rejects_report_override_that_escapes_repo_root(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    outside = tmp_path / "outside.json"
    (fixture / "harness.toml").write_text(
        """
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture pass"
command = "python3 -c 'print(1)'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/harness.py",
            "--root",
            str(fixture),
            "status",
            "--json",
            "--report",
            str(outside),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "report path escapes repository root" in result.stderr


def test_harness_cli_fails_when_known_failure_signature_changes(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    (fixture / "harness.toml").write_text(
        """
version = 1

[[checks]]
id = "known-stale"
description = "fixture known failure"
command = "python3 -c 'import sys; print(\\\"DIFFERENT\\\", file=sys.stderr); sys.exit(7)'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "known-fail"
failure_signature = "STALE-GATE"
quarantine_reason = "fixture documents known stale gate"
quarantine_expires = "2099-01-01"
expected_duration = "fast"
evidence = "fixture"
""".strip()
        + "\n"
    )

    result = subprocess.run(
        [sys.executable, "scripts/harness.py", "--root", str(fixture), "sweep", "required"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "known-stale: new-failure" in result.stderr
