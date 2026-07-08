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


def test_engineering_policy_rejects_unsafe_codex_hook_command(tmp_path):
    fixture = tmp_path / "repo"
    scripts = fixture / "scripts"
    codex = fixture / ".codex"
    scripts.mkdir(parents=True)
    codex.mkdir()
    (fixture / "harness.toml").write_text(
        """
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture local gate"
command = "python3 -c 'print(1)'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
evidence_artifacts = ["fixture-output"]
""".strip()
        + "\n"
    )
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
    (codex / "hooks.json").write_text(
        """
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "scripts/harness.py sweep required --report .cache/harness/required.json"
          }
        ]
      }
    ]
  }
}
""".strip()
        + "\n"
    )
    (fixture / "AGENTS.md").write_text("harness.toml\nroot@10.100.0.19\n")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-engineering-policy.py"), "--root", str(fixture)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert (
        ".codex/hooks.json Stop command must be scripts/harness-hook.py --platform codex"
        in result.stderr
    )


def test_engineering_policy_requires_codex_harness_hook_events(tmp_path):
    fixture = tmp_path / "repo"
    scripts = fixture / "scripts"
    codex = fixture / ".codex"
    scripts.mkdir(parents=True)
    codex.mkdir()
    (fixture / "harness.toml").write_text(
        """
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture local gate"
command = "python3 -c 'print(1)'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
evidence_artifacts = ["fixture-output"]
""".strip()
        + "\n"
    )
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
    (codex / "hooks.json").write_text('{"hooks": {"Stop": []}}\n')
    (fixture / "AGENTS.md").write_text("harness.toml\nroot@10.100.0.19\n")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-engineering-policy.py"), "--root", str(fixture)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert ".codex/hooks.json is missing SessionStart harness hook" in result.stderr
    assert ".codex/hooks.json is missing UserPromptSubmit harness hook" in result.stderr
    assert ".codex/hooks.json is missing PreToolUse harness hook" in result.stderr
    assert ".codex/hooks.json is missing SubagentStop harness hook" in result.stderr


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


def test_engineering_policy_rejects_shell_whitespace_guarded_token(tmp_path):
    fixture = tmp_path / "repo"
    scripts = fixture / "scripts"
    scripts.mkdir(parents=True)
    (fixture / "harness.toml").write_text(
        """
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "bad-device"
description = "incorrectly safe device gate"
command = "ssh\troot@10.100.0.19 true"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
""".strip()
        + "\n"
    )
    (scripts / "check-local.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nscripts/check-engineering-policy.py\n"
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-engineering-policy.py"), "--root", str(fixture)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "bad-device is marked safe_for_agents but declares no requirements" in result.stderr
    assert "bad-device safe_for_agents command contains guarded token: ssh" in result.stderr


def test_engineering_policy_rejects_path_and_qemu_guarded_tokens(tmp_path):
    fixture = tmp_path / "repo"
    scripts = fixture / "scripts"
    scripts.mkdir(parents=True)
    (fixture / "harness.toml").write_text(
        """
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "bad-path-device"
description = "incorrectly safe absolute-path device gate"
command = "/usr/bin/ssh\troot@10.100.0.19 true"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"

[[checks]]
id = "bad-qemu"
description = "incorrectly safe qemu gate"
command = "qemu-system-x86_64 --version"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
""".strip()
        + "\n"
    )
    (scripts / "check-local.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nscripts/check-engineering-policy.py\n"
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-engineering-policy.py"), "--root", str(fixture)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "bad-path-device safe_for_agents command contains guarded token: ssh" in result.stderr
    assert "bad-qemu safe_for_agents command contains guarded token: qemu-system" in result.stderr


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


def test_engineering_policy_requires_harness_control_plane_fields(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    (fixture / "harness.toml").write_text(
        """
version = 1

[[checks]]
id = "local"
description = "fixture local gate"
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
    assert "harness.toml is missing trusted_suite" in result.stderr
    assert "harness.toml is missing iteration_hint" in result.stderr
    assert "harness.toml is missing report_path" in result.stderr


def test_engineering_policy_requires_evidence_artifacts(tmp_path):
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
description = "fixture local gate"
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
    assert "local evidence_artifacts must be a non-empty list" in result.stderr


def test_engineering_policy_rejects_invalid_evidence_artifact_values(tmp_path):
    cases = {
        "empty-list": (
            "evidence_artifacts = []",
            "local evidence_artifacts must be a non-empty list",
        ),
        "blank-entry": (
            'evidence_artifacts = [""]',
            "local evidence_artifacts entries must be non-empty strings",
        ),
        "non-string-entry": (
            "evidence_artifacts = [1]",
            "local evidence_artifacts entries must be non-empty strings",
        ),
    }

    for name, (artifact_line, expected_error) in cases.items():
        fixture = tmp_path / name
        fixture.mkdir()
        (fixture / "harness.toml").write_text(
            f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report .cache/harness/required.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture local gate"
command = "python3 -c 'print(1)'"
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture"
{artifact_line}
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
            [
                sys.executable,
                str(ROOT / "scripts/check-engineering-policy.py"),
                "--root",
                str(fixture),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

        assert result.returncode == 1, result.stdout + result.stderr
        assert expected_error in result.stderr


def test_engineering_policy_rejects_trusted_suite_that_runs_guarded_sweep(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    bad_trusted_suite = (
        "scripts/harness.py sweep required --report .cache/harness/required.json"
        " && scripts/harness.py sweep all"
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "{bad_trusted_suite}"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = ".cache/harness/required.json"

[[checks]]
id = "local"
description = "fixture local gate"
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
    assert "harness.toml trusted_suite must be the required sweep only" in result.stderr


def test_engineering_policy_rejects_unsafe_report_path_in_trusted_suite(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    bad_report_path = (
        ".cache/harness/required.json && scripts/harness.py sweep all"
        " --report .cache/harness/all.json"
    )
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report {bad_report_path}"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = "{bad_report_path}"

[[checks]]
id = "local"
description = "fixture local gate"
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
    assert "harness.toml report_path contains unsafe shell characters" in result.stderr


def test_engineering_policy_rejects_report_path_shell_redirection(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    bad_report_path = ".cache/harness/required.json>pwn.json"
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report {bad_report_path}"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = "{bad_report_path}"

[[checks]]
id = "local"
description = "fixture local gate"
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
    assert "harness.toml report_path contains unsafe shell characters" in result.stderr


def test_engineering_policy_rejects_report_path_that_escapes_repo_root(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    (fixture / "harness.toml").write_text(
        """
version = 1
trusted_suite = "scripts/harness.py sweep required --report ../outside.json"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = "../outside.json"

[[checks]]
id = "local"
description = "fixture local gate"
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
    assert "harness.toml report_path must stay within the repository root" in result.stderr


def test_engineering_policy_rejects_absolute_report_path(tmp_path):
    fixture = tmp_path / "repo"
    fixture.mkdir()
    outside = tmp_path / "outside.json"
    (fixture / "harness.toml").write_text(
        f"""
version = 1
trusted_suite = "scripts/harness.py sweep required --report {outside}"
iteration_hint = ".venv/bin/python -m pytest <path-or-node>"
report_path = "{outside}"

[[checks]]
id = "local"
description = "fixture local gate"
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
    assert "harness.toml report_path must stay within the repository root" in result.stderr


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
