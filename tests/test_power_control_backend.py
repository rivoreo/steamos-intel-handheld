from pathlib import Path

import pytest

from steamos_intel_handheld.power_control import (
    TdpBackend,
    TdpRangeError,
    compute_tdp_limits,
)


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
