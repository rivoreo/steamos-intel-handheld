from pathlib import Path

import pytest

from steamos_intel_handheld.ec_charge_control import (
    CHARGE_LIMIT_PRESETS,
    CHARGE_LIMIT_WRITE_ENABLED,
    ChargeLimitStatus,
    EcChargeSafetyError,
    MsiEcChargeController,
    decode_charge_limit_raw,
    encode_charge_limit_end_threshold,
)


def make_ec_io(tmp_path: Path, raw_value: int = 0xD0) -> Path:
    io_path = tmp_path / "sys" / "kernel" / "debug" / "ec" / "ec0" / "io"
    io_path.parent.mkdir(parents=True)
    ec = bytearray(256)
    ec[0x50] = 0x0C
    ec[0x51] = 0x19
    ec[0xD2] = 0xC1
    ec[0xD7] = raw_value
    io_path.write_bytes(bytes(ec))
    return io_path


def test_decodes_msi_ec_80_percent_mode_from_raw_d0():
    status = decode_charge_limit_raw(0xD0)

    assert status == ChargeLimitStatus(
        raw_value=0xD0,
        address=0xD7,
        start_threshold=70,
        end_threshold=80,
        writes_enabled=True,
        source="msi-claw-8-ai-plus-validated",
    )


@pytest.mark.parametrize(
    ("end_threshold", "expected_raw", "expected_start"),
    [
        (60, 0xBC, 50),
        (80, 0xD0, 70),
        (100, 0xE4, 90),
    ],
)
def test_encodes_supported_msi_battery_presets(end_threshold, expected_raw, expected_start):
    status = encode_charge_limit_end_threshold(end_threshold)

    assert status.raw_value == expected_raw
    assert status.start_threshold == expected_start
    assert status.end_threshold == end_threshold
    assert status.writes_enabled is True


def test_rejects_non_preset_thresholds_until_claw_mapping_is_verified():
    with pytest.raises(EcChargeSafetyError, match="unsupported charge limit preset"):
        encode_charge_limit_end_threshold(75)


def test_controller_reads_charge_limit_status_from_ec_debugfs(tmp_path):
    make_ec_io(tmp_path, raw_value=0xD0)
    controller = MsiEcChargeController(debugfs_root=tmp_path / "sys" / "kernel" / "debug")

    status = controller.read_status()

    assert status.raw_value == 0xD0
    assert status.start_threshold == 70
    assert status.end_threshold == 80


def test_controller_writes_validated_charge_limit_to_ec_debugfs(tmp_path):
    io_path = make_ec_io(tmp_path, raw_value=0xD0)
    controller = MsiEcChargeController(debugfs_root=tmp_path / "sys" / "kernel" / "debug")
    before = io_path.read_bytes()

    status = controller.apply_preset(60)

    after = io_path.read_bytes()
    assert status.raw_value == 0xBC
    assert status.end_threshold == 60
    assert after[0xD7] == 0xBC
    assert after[:0xD7] == before[:0xD7]
    assert after[0xD8:] == before[0xD8:]
    assert CHARGE_LIMIT_WRITE_ENABLED is True
    assert CHARGE_LIMIT_PRESETS == (60, 80, 100)


def test_controller_rejects_unsupported_write_without_changing_ec(tmp_path):
    io_path = make_ec_io(tmp_path, raw_value=0xE4)
    controller = MsiEcChargeController(debugfs_root=tmp_path / "sys" / "kernel" / "debug")
    before = io_path.read_bytes()

    with pytest.raises(EcChargeSafetyError, match="unsupported charge limit preset"):
        controller.apply_preset(75)

    assert io_path.read_bytes() == before
