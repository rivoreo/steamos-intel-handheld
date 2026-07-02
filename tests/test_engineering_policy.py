import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_engineering_policy_gate_passes_current_repo():
    result = subprocess.run(
        [sys.executable, "scripts/check-engineering-policy.py"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "engineering policy passed" in result.stdout


def test_engineering_policy_gate_runs_directly_with_repo_shebang():
    result = subprocess.run(
        ["scripts/check-engineering-policy.py"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "engineering policy passed" in result.stdout


def test_local_harness_runs_engineering_policy_gate():
    script = (ROOT / "scripts/check-local.sh").read_text()

    assert 'run_step "engineering policy"' in script
    assert "scripts/check-engineering-policy.py" in script


def test_engineering_policy_rejects_unsafe_fixture_repo(tmp_path):
    fixture = tmp_path / "repo"
    scripts = fixture / "scripts"
    scripts.mkdir(parents=True)
    (fixture / "harness.toml").write_text(
        """
version = 1

[[checks]]
id = "bad-device"
description = "incorrectly safe device gate"
command = "ssh root@10.100.0.19 true"
requires = []
safe_for_agents = true
""".strip()
        + "\n"
    )
    (scripts / "bad.sh").write_text("#!/usr/bin/env bash\necho unsafe\n")
    (scripts / "check-local.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\npython -m pytest\n"
    )
    (fixture / "AGENTS.md").write_text("harness.toml\n")
    (fixture / "README.md").write_text("root@192.168.128.214\n")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-engineering-policy.py"), "--root", str(fixture)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "bad-device is marked safe_for_agents but declares no requirements" in result.stderr
    assert "bad-device safe_for_agents command contains guarded token: ssh" in result.stderr
    assert "scripts/bad.sh is missing set -euo pipefail" in result.stderr
    assert (
        "scripts/check-local.sh does not run scripts/check-engineering-policy.py"
        in result.stderr
    )
    assert "README.md contains stale device target root@192.168.128.214" in result.stderr


def test_engineering_policy_requires_gate_ledger_fields(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    (fixture / "harness.toml").write_text(
        """
version = 1

[[checks]]
id = "missing-ledger"
description = "missing ledger fields"
command = "python3 -c 'print(1)'"
requires = []
safe_for_agents = true

[[checks]]
id = "bad-known-fail"
description = "missing known failure metadata"
command = "python3 -c 'raise SystemExit(1)'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "known-fail"
""".strip()
        + "\n"
    )
    scripts = fixture / "scripts"
    scripts.mkdir()
    check_local = scripts / "check-local.sh"
    check_local.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'run_step "ruff" true',
                'run_step "engineering policy" scripts/check-engineering-policy.py',
                'run_step "shell syntax" true',
                'run_step "pytest" true',
                'run_step "compileall" true',
                "",
            ]
        )
    )
    check_local.chmod(0o755)
    (fixture / "AGENTS.md").write_text("harness.toml\nroot@10.100.0.19\n")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-engineering-policy.py"), "--root", str(fixture)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "missing-ledger tier must be one of: required, guarded" in result.stderr
    assert "missing-ledger expectation must be one of: pass, blocked, known-fail" in result.stderr
    assert "bad-known-fail known-fail is missing failure_signature" in result.stderr
    assert "bad-known-fail known-fail is missing quarantine_reason" in result.stderr
    assert "bad-known-fail known-fail is missing quarantine_expires" in result.stderr


def test_engineering_policy_rejects_expired_or_invalid_known_fail_quarantine(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    (fixture / "harness.toml").write_text(
        """
version = 1

[[checks]]
id = "expired-known-fail"
description = "expired known failure"
command = "python3 -c 'raise SystemExit(1)'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "known-fail"
failure_signature = "old failure"
quarantine_reason = "fixture proves expiry is enforced"
quarantine_expires = "2000-01-01"

[[checks]]
id = "invalid-known-fail"
description = "invalid known failure"
command = "python3 -c 'raise SystemExit(1)'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "known-fail"
failure_signature = "invalid failure"
quarantine_reason = "fixture proves date format is enforced"
quarantine_expires = "not-a-date"
""".strip()
        + "\n"
    )
    scripts = fixture / "scripts"
    scripts.mkdir()
    check_local = scripts / "check-local.sh"
    check_local.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'run_step "ruff" true',
                'run_step "engineering policy" scripts/check-engineering-policy.py',
                'run_step "shell syntax" true',
                'run_step "pytest" true',
                'run_step "compileall" true',
                "",
            ]
        )
    )
    check_local.chmod(0o755)
    (fixture / "AGENTS.md").write_text("harness.toml\nroot@10.100.0.19\n")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-engineering-policy.py"), "--root", str(fixture)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        "expired-known-fail known-fail quarantine_expires is expired: 2000-01-01"
        in result.stderr
    )
    assert (
        "invalid-known-fail known-fail quarantine_expires must be YYYY-MM-DD"
        in result.stderr
    )
