import json
import os
import subprocess
import sys
from pathlib import Path

from steamos_intel_handheld import ec_charge_control

MSI_CLAW_EC_FIRMWARE = b"1T52EMS1.1091204202509:10:47"


def make_dmi_root(tmp_path: Path) -> Path:
    dmi_root = tmp_path / "sys" / "class" / "dmi" / "id"
    dmi_root.mkdir(parents=True)
    (dmi_root / "sys_vendor").write_text("Micro-Star International Co., Ltd.")
    (dmi_root / "product_name").write_text("Claw 8 AI+ A2VM")
    (dmi_root / "board_name").write_text("MS-1T52")
    return dmi_root


def make_ec_io(tmp_path: Path, raw_value: int = 0xD0) -> Path:
    io_path = tmp_path / "sys" / "kernel" / "debug" / "ec" / "ec0" / "io"
    io_path.parent.mkdir(parents=True)
    ec = bytearray(256)
    ec[0x50] = 0x0C
    ec[0x51] = 0x19
    ec[0xD2] = 0xC1
    ec[0xA0 : 0xA0 + len(MSI_CLAW_EC_FIRMWARE)] = MSI_CLAW_EC_FIRMWARE
    ec[0xD7] = raw_value
    io_path.write_bytes(bytes(ec))
    return io_path


def test_status_command_prints_charge_limit_json(tmp_path, capsys):
    dmi_root = make_dmi_root(tmp_path)
    make_ec_io(tmp_path, raw_value=0xD0)

    ec_charge_control.main(
        [
            "status",
            "--json",
            "--debugfs-root",
            str(tmp_path / "sys" / "kernel" / "debug"),
            "--dmi-root",
            str(dmi_root),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["raw_value"] == 208
    assert payload["raw_hex"] == "0xd0"
    assert payload["address_hex"] == "0xd7"
    assert payload["start_threshold"] == 70
    assert payload["end_threshold"] == 80
    assert payload["writes_enabled"] is True


def test_preview_command_prints_target_without_writing_ec(tmp_path, capsys):
    dmi_root = make_dmi_root(tmp_path)
    io_path = make_ec_io(tmp_path, raw_value=0xD0)

    ec_charge_control.main(
        [
            "preview",
            "60",
            "--json",
            "--debugfs-root",
            str(tmp_path / "sys" / "kernel" / "debug"),
            "--dmi-root",
            str(dmi_root),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["current"]["end_threshold"] == 80
    assert payload["target"]["raw_hex"] == "0xbc"
    assert payload["target"]["start_threshold"] == 50
    assert payload["target"]["end_threshold"] == 60
    assert payload["would_write"] is True
    assert io_path.read_bytes()[0xD7] == 0xD0


def test_apply_command_writes_target_and_prints_json(tmp_path, capsys):
    dmi_root = make_dmi_root(tmp_path)
    io_path = make_ec_io(tmp_path, raw_value=0xE4)

    ec_charge_control.main(
        [
            "apply",
            "60",
            "--json",
            "--debugfs-root",
            str(tmp_path / "sys" / "kernel" / "debug"),
            "--dmi-root",
            str(dmi_root),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["current"]["raw_hex"] == "0xe4"
    assert payload["target"]["raw_hex"] == "0xbc"
    assert payload["applied"]["raw_hex"] == "0xbc"
    assert payload["wrote"] is True
    assert payload["applied"]["writes_enabled"] is True
    assert io_path.read_bytes()[0xD7] == 0xBC


def test_module_entrypoint_prints_status_json(tmp_path):
    dmi_root = make_dmi_root(tmp_path)
    make_ec_io(tmp_path, raw_value=0xD0)
    debugfs_root = tmp_path / "sys" / "kernel" / "debug"
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "steamos_intel_handheld.ec_charge_control",
            "status",
            "--json",
            "--debugfs-root",
            str(debugfs_root),
            "--dmi-root",
            str(dmi_root),
        ],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["raw_hex"] == "0xd0"
    assert payload["address_hex"] == "0xd7"
    assert payload["writes_enabled"] is True
