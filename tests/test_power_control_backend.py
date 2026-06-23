from pathlib import Path

import pytest

from steamos_intel_handheld.power_control import (
    TdpBackend,
    TdpRangeError,
    compute_tdp_limits,
)


def make_rapl_domain(sysfs_root: Path, name: str = "intel-rapl:0", pl1: int = 30, pl2: int = 37):
    domain = sysfs_root / "class" / "powercap" / name
    domain.mkdir(parents=True)
    (domain / "constraint_0_power_limit_uw").write_text(str(pl1 * 1_000_000))
    (domain / "constraint_1_power_limit_uw").write_text(str(pl2 * 1_000_000))
    return domain


def test_compute_tdp_limits_caps_pl2_at_max():
    assert compute_tdp_limits(17, 37).pl1_uw == 17_000_000
    assert compute_tdp_limits(17, 37).pl2_uw == 37_000_000
    assert compute_tdp_limits(28, 37).pl1_uw == 28_000_000
    assert compute_tdp_limits(28, 37).pl2_uw == 37_000_000
    assert compute_tdp_limits(30, 37).pl2_uw == 37_000_000
    assert compute_tdp_limits(37, 37).pl2_uw == 37_000_000


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


def test_read_limit_falls_back_to_rapl_when_state_is_missing(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_rapl_domain(sysfs_root, pl1=28, pl2=35)

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
    assert (domain / "constraint_1_power_limit_uw").read_text() == "37000000"


def test_write_limit_uses_configured_pl2_wattage(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=30, pl2=37)
    state_file = tmp_path / "state" / "tdp_w"

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root, pl2_w=22)
    backend.write_limit_w(20)

    assert (domain / "constraint_0_power_limit_uw").read_text() == "20000000"
    assert (domain / "constraint_1_power_limit_uw").read_text() == "22000000"


def test_write_limit_rejects_out_of_range_values(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=30, pl2=37)
    state_file = tmp_path / "state" / "tdp_w"

    backend = TdpBackend(min_w=5, max_w=37, state_file=state_file, sysfs_root=sysfs_root)

    with pytest.raises(TdpRangeError):
        backend.write_limit_w(4)

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
    assert (domain / "constraint_1_power_limit_uw").read_text() == "37000000"


def test_restore_state_to_rapl_ignores_missing_or_invalid_state(tmp_path):
    sysfs_root = tmp_path / "sys"
    domain = make_rapl_domain(sysfs_root, pl1=37, pl2=37)
    state_file = tmp_path / "state" / "tdp_w"
    state_file.parent.mkdir()
    state_file.write_text("999")

    backend = TdpBackend(state_file=state_file, sysfs_root=sysfs_root)

    assert backend.restore_state_to_rapl() is None
    assert (domain / "constraint_0_power_limit_uw").read_text() == "37000000"
