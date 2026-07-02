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


def test_harness_manifest_defines_agent_runnable_layers():
    checks = checks_by_id()

    assert set(checks) >= {
        "local",
        "device-full",
        "release-artifact",
        "qemu-mangoapp-rootfs",
    }
    assert checks["local"]["command"] == "PYTHON=.venv/bin/python scripts/check-local.sh"
    assert checks["local"]["requires"] == []
    assert checks["local"]["safe_for_agents"] is True
    assert checks["device-full"]["requires"] == ["root-ssh", "handheld"]
    assert checks["device-full"]["safe_for_agents"] is False
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
