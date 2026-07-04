import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write_hook_fixture(
    fixture: Path,
    *,
    command: str = "python3 -c 'print(\"ok\")'",
) -> None:
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
command = {json.dumps(command)}
requires = []
safe_for_agents = true
tier = "required"
expectation = "pass"
expected_duration = "fast"
evidence = "fixture output"
evidence_artifacts = ["fixture-output"]
""".strip()
        + "\n"
    )


def run_hook(
    fixture: Path,
    state_dir: Path,
    payload: dict,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "scripts/harness-hook.py",
            "--root",
            str(fixture),
            "--state-dir",
            str(state_dir),
            "--platform",
            "codex",
        ],
        cwd=ROOT,
        input=json.dumps(payload) + "\n",
        text=True,
        capture_output=True,
    )


def test_harness_hook_stop_blocks_pending_without_running_checks(tmp_path):
    fixture = tmp_path / "repo"
    marker = tmp_path / "hook-ran-check"
    marker_command = (
        f"python3 -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\""
    )
    write_hook_fixture(fixture, command=marker_command)

    result = run_hook(
        fixture,
        tmp_path / "state",
        {"hook_event_name": "Stop", "session_id": "stop-pending"},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "Harness pending verification" in payload["reason"]
    assert "scripts/harness.py sweep required --report .cache/harness/required.json" in (
        payload["reason"]
    )
    assert payload["pending_verification"][0]["reason"] == "last report is missing"
    assert not marker.exists()


def test_harness_hook_stop_blocks_same_pending_state_only_once(tmp_path):
    fixture = tmp_path / "repo"
    write_hook_fixture(fixture)
    state_dir = tmp_path / "state"
    payload = {"hook_event_name": "Stop", "session_id": "same-pending"}

    first = run_hook(fixture, state_dir, payload)
    second = run_hook(fixture, state_dir, payload)

    assert first.returncode == 0
    assert json.loads(first.stdout)["decision"] == "block"
    assert second.returncode == 0
    assert second.stdout == ""


def test_harness_hook_subagent_stop_blocks_same_pending_state_only_once(tmp_path):
    fixture = tmp_path / "repo"
    write_hook_fixture(fixture)
    state_dir = tmp_path / "state"
    payload = {"hook_event_name": "SubagentStop", "session_id": "same-subagent-pending"}

    first = run_hook(fixture, state_dir, payload)
    second = run_hook(fixture, state_dir, payload)

    assert first.returncode == 0
    assert json.loads(first.stdout)["decision"] == "block"
    assert second.returncode == 0
    assert second.stdout == ""


def test_harness_hook_session_start_adds_pending_context_once(tmp_path):
    fixture = tmp_path / "repo"
    write_hook_fixture(fixture)
    state_dir = tmp_path / "state"
    payload = {"hook_event_name": "SessionStart", "session_id": "startup-pending"}

    first = run_hook(fixture, state_dir, payload)
    second = run_hook(fixture, state_dir, payload)

    assert first.returncode == 0
    response = json.loads(first.stdout)
    context = response["hookSpecificOutput"]["additionalContext"]
    assert "Harness pending verification" in context
    assert "last report is missing" in context
    assert second.returncode == 0
    assert second.stdout == ""


def test_harness_hook_stays_silent_when_trusted_report_is_fresh(tmp_path):
    fixture = tmp_path / "repo"
    write_hook_fixture(fixture, command="python3 -c 'print(\"ok\")'")
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

    result = run_hook(
        fixture,
        tmp_path / "state",
        {"hook_event_name": "Stop", "session_id": "fresh"},
    )

    assert result.returncode == 0
    assert result.stdout == ""


def test_harness_hook_pretooluse_denies_git_commit_when_verification_pending(tmp_path):
    fixture = tmp_path / "repo"
    write_hook_fixture(fixture)

    result = run_hook(
        fixture,
        tmp_path / "state",
        {
            "hook_event_name": "PreToolUse",
            "session_id": "pending-commit",
            "tool_name": "functions.exec_command",
            "tool_input": {"cmd": "git commit -m harness"},
        },
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["permissionDecision"] == "deny"
    assert "Harness pending verification" in payload["permissionDecisionReason"]
    assert "git commit is blocked" in payload["permissionDecisionReason"]
    assert "scripts/harness.py sweep required --report .cache/harness/required.json" in (
        payload["permissionDecisionReason"]
    )


def test_harness_hook_pretooluse_allows_git_commit_when_trusted_report_is_fresh(tmp_path):
    fixture = tmp_path / "repo"
    write_hook_fixture(fixture, command="python3 -c 'print(\"ok\")'")
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

    result = run_hook(
        fixture,
        tmp_path / "state",
        {
            "hook_event_name": "PreToolUse",
            "session_id": "fresh-commit",
            "tool_name": "functions.exec_command",
            "tool_input": {"cmd": "git commit -m harness"},
        },
    )

    assert result.returncode == 0
    assert result.stdout == ""


def test_harness_hook_pretooluse_does_not_deny_non_commit_command_mentions(tmp_path):
    fixture = tmp_path / "repo"
    write_hook_fixture(fixture)

    result = run_hook(
        fixture,
        tmp_path / "state",
        {
            "hook_event_name": "PreToolUse",
            "session_id": "pending-echo",
            "tool_name": "functions.exec_command",
            "tool_input": {"cmd": "printf 'run git commit after verification\\n'"},
        },
    )

    assert result.returncode == 0
    assert result.stdout == ""


def test_harness_hook_pretooluse_denies_git_commit_variants(tmp_path):
    fixture = tmp_path / "repo"
    write_hook_fixture(fixture)

    for index, command in enumerate(
        [
            "git -C repo commit -m harness",
            "/usr/bin/git commit -m harness",
            "git status && git commit -m harness",
            "git status; git commit -m harness",
            "git status;git commit -m harness",
            "true&&git commit -m harness",
            "env GIT_AUTHOR_NAME=Harness git commit -m harness",
            "command git commit -m harness",
        ]
    ):
        result = run_hook(
            fixture,
            tmp_path / "state",
            {
                "hook_event_name": "PreToolUse",
                "session_id": f"pending-commit-variant-{index}",
                "tool_name": "functions.exec_command",
                "tool_input": {"cmd": command},
            },
        )

        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["permissionDecision"] == "deny"
        assert "git commit is blocked" in payload["permissionDecisionReason"]


def test_harness_hook_pretooluse_allows_git_non_commit_subcommands(tmp_path):
    fixture = tmp_path / "repo"
    write_hook_fixture(fixture)

    for index, command in enumerate(["git help commit", "git show commit"]):
        result = run_hook(
            fixture,
            tmp_path / "state",
            {
                "hook_event_name": "PreToolUse",
                "session_id": f"pending-git-noncommit-{index}",
                "tool_name": "functions.exec_command",
                "tool_input": {"cmd": command},
            },
        )

        assert result.returncode == 0
        assert result.stdout == ""


def test_codex_hooks_config_wires_safe_harness_events():
    hooks = json.loads((ROOT / ".codex/hooks.json").read_text())

    wired_events = hooks["hooks"]
    assert set(wired_events) == {"SessionStart", "PreToolUse", "Stop", "SubagentStop"}
    for event in ("SessionStart", "PreToolUse", "Stop", "SubagentStop"):
        command = wired_events[event][0]["hooks"][0]["command"]
        assert command == "scripts/harness-hook.py --platform codex"
        assert "sweep" not in command
        assert "run " not in command
