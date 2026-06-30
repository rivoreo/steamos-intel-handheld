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

MSI_CLAW_EC_FIRMWARE = b"1T52EMS1.1091204202509:10:47"


def make_dmi_root(
    tmp_path: Path,
    *,
    sys_vendor: str = "Micro-Star International Co., Ltd.",
    product_name: str = "Claw 8 AI+ A2VM",
    board_name: str = "MS-1T52",
) -> Path:
    dmi_root = tmp_path / "sys" / "class" / "dmi" / "id"
    dmi_root.mkdir(parents=True)
    (dmi_root / "sys_vendor").write_text(sys_vendor)
    (dmi_root / "product_name").write_text(product_name)
    (dmi_root / "board_name").write_text(board_name)
    return dmi_root


def make_ec_io(
    tmp_path: Path,
    raw_value: int = 0xD0,
    *,
    firmware: bytes = MSI_CLAW_EC_FIRMWARE,
) -> Path:
    io_path = tmp_path / "sys" / "kernel" / "debug" / "ec" / "ec0" / "io"
    io_path.parent.mkdir(parents=True)
    ec = bytearray(256)
    ec[0x50] = 0x0C
    ec[0x51] = 0x19
    ec[0xD2] = 0xC1
    ec[0xA0 : 0xA0 + len(firmware)] = firmware
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
    dmi_root = make_dmi_root(tmp_path)
    make_ec_io(tmp_path, raw_value=0xD0)
    controller = MsiEcChargeController(
        debugfs_root=tmp_path / "sys" / "kernel" / "debug",
        dmi_root=dmi_root,
    )

    status = controller.read_status()

    assert status.raw_value == 0xD0
    assert status.start_threshold == 70
    assert status.end_threshold == 80


def test_controller_writes_validated_charge_limit_to_ec_debugfs(tmp_path):
    dmi_root = make_dmi_root(tmp_path)
    io_path = make_ec_io(tmp_path, raw_value=0xD0)
    controller = MsiEcChargeController(
        debugfs_root=tmp_path / "sys" / "kernel" / "debug",
        dmi_root=dmi_root,
    )
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
    dmi_root = make_dmi_root(tmp_path)
    io_path = make_ec_io(tmp_path, raw_value=0xE4)
    controller = MsiEcChargeController(
        debugfs_root=tmp_path / "sys" / "kernel" / "debug",
        dmi_root=dmi_root,
    )
    before = io_path.read_bytes()

    with pytest.raises(EcChargeSafetyError, match="unsupported charge limit preset"):
        controller.apply_preset(75)

    assert io_path.read_bytes() == before


def test_controller_refuses_charge_limit_write_on_unmatched_dmi(tmp_path):
    dmi_root = make_dmi_root(tmp_path, product_name="Claw A1M")
    io_path = make_ec_io(tmp_path, raw_value=0xE4)
    controller = MsiEcChargeController(
        debugfs_root=tmp_path / "sys" / "kernel" / "debug",
        dmi_root=dmi_root,
    )
    before = io_path.read_bytes()

    with pytest.raises(EcChargeSafetyError, match="unsupported MSI Claw charge-limit target"):
        controller.apply_preset(60)

    assert io_path.read_bytes() == before


def test_controller_allows_charge_limit_write_after_ec_firmware_update(tmp_path):
    dmi_root = make_dmi_root(tmp_path)
    io_path = make_ec_io(tmp_path, raw_value=0xE4, firmware=b"FUTURE-EC-FIRMWARE")
    controller = MsiEcChargeController(
        debugfs_root=tmp_path / "sys" / "kernel" / "debug",
        dmi_root=dmi_root,
    )

    status = controller.apply_preset(60)

    assert status.raw_value == 0xBC
    assert io_path.read_bytes()[0xD7] == 0xBC
