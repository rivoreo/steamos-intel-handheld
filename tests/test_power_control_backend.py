from pathlib import Path
from stat import S_IMODE

import pytest

from steamos_intel_handheld.power_control import (
    TdpBackend,
    TdpRangeError,
    compute_tdp_limits,
)

MSI_CLAW_EC_FIRMWARE = b"1T52EMS1.1091204202509:10:47"


def make_dmi_root(
    tmp_path: Path,
    *,
    sys_vendor: str = "Micro-Star International Co., Ltd.",
    product_name: str = "Claw 8 AI+ A2VM",
    board_name: str = "MS-1T52",
    bios_version: str = "E1T52IMS.112",
) -> Path:
    dmi_root = tmp_path / "sys" / "class" / "dmi" / "id"
    dmi_root.mkdir(parents=True)
    (dmi_root / "sys_vendor").write_text(sys_vendor)
    (dmi_root / "product_name").write_text(product_name)
    (dmi_root / "board_name").write_text(board_name)
    (dmi_root / "bios_version").write_text(bios_version)
    return dmi_root


def make_ec_io(
    tmp_path: Path,
    *,
    firmware: bytes = MSI_CLAW_EC_FIRMWARE,
) -> tuple[Path, Path]:
    debugfs_root = tmp_path / "sys" / "kernel" / "debug"
    io_path = debugfs_root / "ec" / "ec0" / "io"
    io_path.parent.mkdir(parents=True)
    ec = bytearray(256)
    ec[0x50] = 0x11
    ec[0x51] = 0x25
    ec[0xD2] = 0xC1
    ec[0xA0 : 0xA0 + len(firmware)] = firmware
    io_path.write_bytes(bytes(ec))
    return debugfs_root, io_path


def make_rapl_domain(
    sysfs_root: Path,
    name: str = "intel-rapl:0",
    pl1: int = 30,
    pl2: int = 37,
    pl1_max: int = 37,
    pl2_max: int = 37,
    *,
    swap_constraints: bool = False,
):
    domain = sysfs_root / "class" / "powercap" / name
    domain.mkdir(parents=True)
    if swap_constraints:
        constraints = (("short_term", pl2, pl2_max), ("long_term", pl1, pl1_max))
    else:
        constraints = (("long_term", pl1, pl1_max), ("short_term", pl2, pl2_max))
    for index, (constraint_name, watts, max_watts) in enumerate(constraints):
        (domain / f"constraint_{index}_name").write_text(constraint_name)
        (domain / f"constraint_{index}_power_limit_uw").write_text(str(watts * 1_000_000))
        (domain / f"constraint_{index}_max_power_uw").write_text(str(max_watts * 1_000_000))
    return domain


def test_compute_tdp_limits_uses_258v_handheld_curve():
    expected_limits = {
        8: (8_000_000, 10_000_000),
        10: (10_000_000, 12_000_000),
        12: (12_000_000, 14_000_000),
        15: (15_000_000, 17_000_000),
        17: (17_000_000, 19_000_000),
        20: (20_000_000, 22_000_000),
        22: (22_000_000, 24_000_000),
        25: (25_000_000, 27_000_000),
        28: (28_000_000, 30_000_000),
        30: (30_000_000, 32_000_000),
    }

    for requested_watts, (pl1_uw, pl2_uw) in expected_limits.items():
        limits = compute_tdp_limits(requested_watts, 37)
        assert limits.pl1_uw == pl1_uw
        assert limits.pl2_uw == pl2_uw


def test_compute_tdp_limits_clamps_to_258v_handheld_sustained_range():
    assert compute_tdp_limits(5, 37).pl1_uw == 8_000_000
    assert compute_tdp_limits(5, 37).pl2_uw == 10_000_000
    assert compute_tdp_limits(37, 37).pl1_uw == 30_000_000
    assert compute_tdp_limits(37, 37).pl2_uw == 32_000_000


def test_compute_tdp_limits_respects_short_term_limit_max():
    assert compute_tdp_limits(30, 31).pl1_uw == 30_000_000
    assert compute_tdp_limits(30, 31).pl2_uw == 31_000_000


def test_compute_tdp_limits_allows_device_specific_pl2_wattage():
    assert compute_tdp_limits(17, 37, pl2_w=21).pl2_uw == 21_000_000
    assert compute_tdp_limits(28, 37, pl2_w=21).pl2_uw == 28_000_000
    assert compute_tdp_limits(20, 37, pl2_w=45).pl2_uw == 37_000_000


def test_read_limit_prefers_valid_state_file(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_rapl_domain(sysfs_root, pl1=37, pl2=37)
    state_file = tmp_path / "state" / "tdp_w"
    state_file.parent.mkdir()
    state_file.write_text("30")

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root)

    assert backend.read_limit_w() == 30


def test_read_limit_falls_back_to_named_long_term_rapl_when_state_is_missing(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_rapl_domain(sysfs_root, pl1=28, pl2=35, swap_constraints=True)

    backend = TdpBackend(state_file=tmp_path / "missing", sysfs_root=sysfs_root)

    assert backend.read_limit_w() == 28


def test_write_limit_updates_state_and_rapl(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=30, pl2=37)
    state_file = tmp_path / "state" / "tdp_w"

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root)
    backend.write_limit_w(28)

    assert state_file.read_text() == "28"
    assert (domain / "constraint_0_power_limit_uw").read_text() == "28000000"
    assert (domain / "constraint_1_power_limit_uw").read_text() == "30000000"


def test_write_limit_updates_msi_claw_ec_when_guard_matches(tmp_path):
    dmi_root = make_dmi_root(tmp_path)
    debugfs_root, io_path = make_ec_io(tmp_path)
    state_file = tmp_path / "state" / "tdp_w"

    backend = TdpBackend(
        state_file=state_file,
        apply_rapl=False,
        apply_msi_claw_ec=True,
        dmi_root=dmi_root,
        debugfs_root=debugfs_root,
    )

    assert backend.write_limit_w(30) == 30

    ec = io_path.read_bytes()
    assert state_file.read_text() == "30"
    assert ec[0x50] == 30
    assert ec[0x51] == 32
    assert ec[0xD2] == 0xC4


def test_write_limit_sets_msi_claw_ec_comfort_mode_at_17_watts(tmp_path):
    dmi_root = make_dmi_root(tmp_path)
    debugfs_root, io_path = make_ec_io(tmp_path)

    backend = TdpBackend(
        state_file=tmp_path / "state" / "tdp_w",
        apply_rapl=False,
        apply_msi_claw_ec=True,
        dmi_root=dmi_root,
        debugfs_root=debugfs_root,
    )

    assert backend.write_limit_w(17) == 17

    ec = io_path.read_bytes()
    assert ec[0x50] == 17
    assert ec[0x51] == 19
    assert ec[0xD2] == 0xC1


def test_write_limit_sets_msi_claw_ec_turbo_mode_above_17_watts(tmp_path):
    dmi_root = make_dmi_root(tmp_path)
    debugfs_root, io_path = make_ec_io(tmp_path)

    backend = TdpBackend(
        state_file=tmp_path / "state" / "tdp_w",
        apply_rapl=False,
        apply_msi_claw_ec=True,
        dmi_root=dmi_root,
        debugfs_root=debugfs_root,
    )

    assert backend.write_limit_w(18) == 18

    ec = io_path.read_bytes()
    assert ec[0x50] == 18
    assert ec[0x51] == 20
    assert ec[0xD2] == 0xC4


def test_write_limit_skips_msi_claw_ec_write_when_values_are_unchanged(
    tmp_path,
    monkeypatch,
):
    dmi_root = make_dmi_root(tmp_path)
    debugfs_root, io_path = make_ec_io(tmp_path)
    ec = bytearray(io_path.read_bytes())
    ec[0x50] = 30
    ec[0x51] = 32
    ec[0xD2] = 0xC4
    io_path.write_bytes(bytes(ec))

    backend = TdpBackend(
        state_file=tmp_path / "state" / "tdp_w",
        apply_rapl=False,
        apply_msi_claw_ec=True,
        dmi_root=dmi_root,
        debugfs_root=debugfs_root,
    )

    def fail_write(*_args):
        raise AssertionError("unchanged EC values should not be written")

    monkeypatch.setattr(backend.ec_controller, "_write_ec_byte", fail_write)

    assert backend.write_limit_w(30) == 30
    assert io_path.read_bytes()[0x50] == 30
    assert io_path.read_bytes()[0x51] == 32
    assert io_path.read_bytes()[0xD2] == 0xC4


def test_write_limit_debounces_msi_claw_ec_writes_to_last_value(tmp_path, monkeypatch):
    dmi_root = make_dmi_root(tmp_path)
    debugfs_root, _io_path = make_ec_io(tmp_path)
    applied = []

    backend = TdpBackend(
        state_file=tmp_path / "state" / "tdp_w",
        apply_rapl=False,
        apply_msi_claw_ec=True,
        ec_write_debounce_ms=750,
        dmi_root=dmi_root,
        debugfs_root=debugfs_root,
    )

    def record_apply(watts):
        applied.append(watts)

    monkeypatch.setattr(backend, "apply_limit_to_msi_claw_ec", record_apply)

    backend.write_limit_w(18)
    backend.write_limit_w(24)
    backend.write_limit_w(30)

    assert applied == []
    assert (tmp_path / "state" / "tdp_w").read_text() == "30"

    backend.flush_pending_ec_write()

    assert applied == [30]


def test_write_limit_caps_msi_claw_ec_pl1_to_30_watts_and_allows_pl2_to_37(tmp_path):
    dmi_root = make_dmi_root(tmp_path)
    debugfs_root, io_path = make_ec_io(tmp_path)

    backend = TdpBackend(
        max_w=37,
        short_limit_max_w=37,
        state_file=tmp_path / "state" / "tdp_w",
        apply_rapl=False,
        apply_msi_claw_ec=True,
        dmi_root=dmi_root,
        debugfs_root=debugfs_root,
    )

    assert backend.write_limit_w(37) == 37

    ec = io_path.read_bytes()
    assert ec[0x50] == 30
    assert ec[0x51] == 37


def test_write_limit_refuses_msi_claw_ec_on_unmatched_dmi(tmp_path):
    dmi_root = make_dmi_root(tmp_path, product_name="Claw A1M")
    debugfs_root, io_path = make_ec_io(tmp_path)
    state_file = tmp_path / "state" / "tdp_w"

    backend = TdpBackend(
        state_file=state_file,
        apply_rapl=False,
        apply_msi_claw_ec=True,
        dmi_root=dmi_root,
        debugfs_root=debugfs_root,
    )

    with pytest.raises(RuntimeError, match="unsupported MSI Claw EC target"):
        backend.write_limit_w(30)

    ec = io_path.read_bytes()
    assert not state_file.exists()
    assert ec[0x50] == 0x11
    assert ec[0x51] == 0x25


def test_write_limit_uses_rapl_constraint_names(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=30, pl2=37, swap_constraints=True)
    state_file = tmp_path / "state" / "tdp_w"

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root)
    backend.write_limit_w(17)

    assert (domain / "constraint_0_power_limit_uw").read_text() == "19000000"
    assert (domain / "constraint_1_power_limit_uw").read_text() == "17000000"


def test_write_limit_respects_short_term_constraint_max(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=30, pl2=37, pl2_max=31)
    state_file = tmp_path / "state" / "tdp_w"

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root)
    backend.write_limit_w(30)

    assert (domain / "constraint_0_power_limit_uw").read_text() == "30000000"
    assert (domain / "constraint_1_power_limit_uw").read_text() == "31000000"


def test_write_limit_does_not_cap_long_term_to_reported_max_power(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=17, pl2=37, pl1_max=17)
    state_file = tmp_path / "state" / "tdp_w"

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root)
    backend.write_limit_w(30)

    assert (domain / "constraint_0_power_limit_uw").read_text() == "30000000"
    assert (domain / "constraint_1_power_limit_uw").read_text() == "32000000"


def test_write_limit_does_not_change_tau_windows(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=30, pl2=37)
    (domain / "constraint_0_time_window_us").write_text("1000000")
    (domain / "constraint_1_time_window_us").write_text("28000000")
    state_file = tmp_path / "state" / "tdp_w"

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root)
    backend.write_limit_w(17)

    assert (domain / "constraint_0_time_window_us").read_text() == "1000000"
    assert (domain / "constraint_1_time_window_us").read_text() == "28000000"


def test_write_limit_uses_configured_pl2_wattage(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=30, pl2=37)
    state_file = tmp_path / "state" / "tdp_w"

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root, pl2_w=22)
    backend.write_limit_w(20)

    assert (domain / "constraint_0_power_limit_uw").read_text() == "20000000"
    assert (domain / "constraint_1_power_limit_uw").read_text() == "22000000"


def test_write_limit_clamps_out_of_range_values_to_handheld_range(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=30, pl2=37)
    state_file = tmp_path / "state" / "tdp_w"

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root)

    assert backend.write_limit_w(4) == 8
    assert state_file.read_text() == "8"
    assert (domain / "constraint_0_power_limit_uw").read_text() == "8000000"
    assert (domain / "constraint_1_power_limit_uw").read_text() == "10000000"

    assert backend.write_limit_w(37) == 30
    assert state_file.read_text() == "30"
    assert (domain / "constraint_0_power_limit_uw").read_text() == "30000000"
    assert (domain / "constraint_1_power_limit_uw").read_text() == "32000000"


def test_write_limit_rejects_values_outside_hardware_sanity_range(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=30, pl2=37)
    state_file = tmp_path / "state" / "tdp_w"

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root)

    with pytest.raises(TdpRangeError):
        backend.write_limit_w(999)

    assert not state_file.exists()
    assert (domain / "constraint_0_power_limit_uw").read_text() == "30000000"


def test_restore_state_to_rapl_applies_persisted_limit(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=37, pl2=37)
    state_file = tmp_path / "state" / "tdp_w"
    state_file.parent.mkdir()
    state_file.write_text("30")

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root)

    assert backend.restore_state_to_rapl() == 30
    assert (domain / "constraint_0_power_limit_uw").read_text() == "30000000"
    assert (domain / "constraint_1_power_limit_uw").read_text() == "32000000"


def test_restore_state_to_rapl_clamps_legacy_persisted_limit(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=37, pl2=37)
    state_file = tmp_path / "state" / "tdp_w"
    state_file.parent.mkdir()
    state_file.write_text("37")

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root)

    assert backend.restore_state_to_rapl() == 30
    assert state_file.read_text() == "30"
    assert (domain / "constraint_0_power_limit_uw").read_text() == "30000000"
    assert (domain / "constraint_1_power_limit_uw").read_text() == "32000000"


def test_restore_state_to_rapl_ignores_missing_or_invalid_state(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=37, pl2=37)
    state_file = tmp_path / "state" / "tdp_w"
    state_file.parent.mkdir()
    state_file.write_text("999")

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root)

    assert backend.restore_state_to_rapl() is None
    assert (domain / "constraint_0_power_limit_uw").read_text() == "37000000"


def test_prepare_mangohud_sensors_makes_package_and_uncore_rapl_energy_readable(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root)
    (domain / "name").write_text("package-0")
    energy_file = domain / "energy_uj"
    energy_file.write_text("123456")
    energy_file.chmod(0o400)
    enabled_file = domain / "enabled"
    enabled_file.write_text("0")
    gpu_domain = sysfs_root / "class" / "powercap" / "intel-rapl:0:1"
    gpu_domain.mkdir()
    (gpu_domain / "name").write_text("uncore")
    gpu_energy_file = gpu_domain / "energy_uj"
    gpu_energy_file.write_text("456789")
    gpu_energy_file.chmod(0o400)
    gpu_enabled_file = gpu_domain / "enabled"
    gpu_enabled_file.write_text("0")

    backend = TdpBackend(state_file=tmp_path / "state", sysfs_root=sysfs_root)

    assert backend.prepare_mangohud_sensors() == [energy_file, gpu_energy_file]
    assert S_IMODE(energy_file.stat().st_mode) == 0o444
    assert S_IMODE(gpu_energy_file.stat().st_mode) == 0o444
    assert enabled_file.read_text() == "1"
    assert gpu_enabled_file.read_text() == "1"
    assert energy_file.read_text() == "123456"
    assert gpu_energy_file.read_text() == "456789"


def test_prepare_mangohud_sensors_keeps_unrelated_rapl_domains_private(tmp_path):
    sysfs_root = tmp_path / "sys"
    core_domain = sysfs_root / "class" / "powercap" / "intel-rapl:0:0"
    core_domain.mkdir(parents=True)
    (core_domain / "name").write_text("core")
    core_energy_file = core_domain / "energy_uj"
    core_energy_file.write_text("123")
    core_energy_file.chmod(0o400)
    core_enabled_file = core_domain / "enabled"
    core_enabled_file.write_text("0")

    backend = TdpBackend(state_file=tmp_path / "state", sysfs_root=sysfs_root)

    assert backend.prepare_mangohud_sensors() == []
    assert S_IMODE(core_energy_file.stat().st_mode) == 0o400
    assert core_enabled_file.read_text() == "0"
