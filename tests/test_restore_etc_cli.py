import json
import os
import subprocess
import sys
from pathlib import Path

import tomllib

from steamos_intel_handheld import restore_etc

ROOT = Path(__file__).resolve().parents[1]


def write_file(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def write_basic_manifest(artifact_root: Path) -> None:
    write_file(artifact_root / "payload/example.conf", "canonical\n")
    write_file(
        artifact_root / "manifest.toml",
        """
[[artifact]]
destination = "/etc/example.conf"
source = "payload/example.conf"
type = "file"
policy = "managed"
mode = "0644"
owner = "root"
group = "root"
actions = ["systemd-system"]
""",
    )


def test_json_check_reports_missing_file_and_exit_one_for_missing_managed(tmp_path, capsys):
    artifact_root = tmp_path / "opt/share/etc-artifacts"
    etc_root = tmp_path / "etc"
    write_basic_manifest(artifact_root)

    code = restore_etc.main(
        [
            "--artifact-root",
            str(artifact_root),
            "--etc-root",
            str(etc_root),
            "--json",
            "--check",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["changed"] is False
    assert payload["failures"] == []
    assert payload["artifacts"][0]["destination"] == "/etc/example.conf"
    assert payload["artifacts"][0]["state"] == "missing"


def test_json_apply_restores_file_and_returns_zero(tmp_path, capsys):
    artifact_root = tmp_path / "opt/share/etc-artifacts"
    etc_root = tmp_path / "etc"
    write_basic_manifest(artifact_root)

    code = restore_etc.main(
        [
            "--artifact-root",
            str(artifact_root),
            "--etc-root",
            str(etc_root),
            "--json",
            "--apply",
            "--skip-actions",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["changed"] is True
    assert payload["failures"] == []
    assert (etc_root / "example.conf").read_text() == "canonical\n"


def test_invalid_manifest_returns_exit_code_two(tmp_path, capsys):
    artifact_root = tmp_path / "opt/share/etc-artifacts"
    write_file(
        artifact_root / "manifest.toml",
        """
[[artifact]]
destination = "/var/lib/not-etc"
type = "file"
policy = "managed"
owner = "root"
group = "root"
actions = []
""",
    )

    code = restore_etc.main(
        [
            "--artifact-root",
            str(artifact_root),
            "--etc-root",
            str(tmp_path / "etc"),
            "--json",
            "--check",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "destination must be under /etc" in payload["failures"][0]


def test_module_entrypoint_accepts_json_check(tmp_path):
    artifact_root = tmp_path / "opt/share/etc-artifacts"
    etc_root = tmp_path / "etc"
    write_basic_manifest(artifact_root)
    write_file(etc_root / "example.conf", "canonical\n")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.restore_etc",
            "--artifact-root",
            str(artifact_root),
            "--etc-root",
            str(etc_root),
            "--json",
            "--check",
        ],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["changed"] is False
    assert payload["artifacts"][0]["state"] == "present"


def test_console_script_is_declared_in_pyproject():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert (
        pyproject["project"]["scripts"]["steamos-intel-handheld-restore-etc"]
        == "steamos_intel_handheld.restore_etc:main"
    )
