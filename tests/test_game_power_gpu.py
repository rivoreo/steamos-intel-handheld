import os
from pathlib import Path

import pytest

from steamos_intel_handheld.game_power_gpu import (
    GpuFreqActuator,
    GpuFreqSnapshot,
    GpuGt,
    discover_gpu_gts,
)


def make_gt(
    sysfs_root: Path,
    *,
    card: int = 0,
    tile: int = 0,
    gt: int = 0,
    rp0: int = 1950,
    rpe: int = 800,
    rpn: int = 100,
    min_freq: int = 100,
    max_freq: int = 1950,
    slpc: str | None = None,
    slpc_name: str = "slpc_power_profile",
    make_min: bool = True,
    make_max: bool = True,
) -> Path:
    """Create a fake xe GT freq0 tree; return the freq0 dir path."""

    freq = (
        sysfs_root
        / "class"
        / "drm"
        / f"card{card}"
        / "device"
        / f"tile{tile}"
        / f"gt{gt}"
        / "freq0"
    )
    freq.mkdir(parents=True)
    (freq / "rp0_freq").write_text(str(rp0))
    (freq / "rpe_freq").write_text(str(rpe))
    (freq / "rpn_freq").write_text(str(rpn))
    if make_min:
        (freq / "min_freq").write_text(str(min_freq))
    if make_max:
        (freq / "max_freq").write_text(str(max_freq))
    # Read-only siblings that must be ignored.
    (freq / "act_freq").write_text("500")
    (freq / "cur_freq").write_text("500")
    if slpc is not None:
        (freq.parent / slpc_name).write_text(slpc)
    return freq


def read_int(path: Path) -> int:
    return int(path.read_text().strip())


# --- 1. discovery -----------------------------------------------------------


def test_discover_finds_all_gts_parses_bounds_and_detects_slpc(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, card=0, tile=0, gt=0, rp0=1950, rpe=800, rpn=100, slpc="base")
    make_gt(sysfs_root, card=0, tile=0, gt=1, rp0=1500, rpe=700, rpn=200)
    make_gt(sysfs_root, card=1, tile=0, gt=0, rp0=1200, rpe=600, rpn=300, slpc="base")

    gts = discover_gpu_gts(sysfs_root)

    assert [gt.name for gt in gts] == ["gt0", "gt1", "gt0"]
    # Deterministic sort by path: card0 before card1.
    assert "card0" in gts[0].gt_path.parts
    assert "card1" in gts[2].gt_path.parts
    assert (gts[0].rp0_mhz, gts[0].rpe_mhz, gts[0].rpn_mhz) == (1950, 800, 100)
    assert (gts[1].rp0_mhz, gts[1].rpe_mhz, gts[1].rpn_mhz) == (1500, 700, 200)
    assert all(gt.min_writable and gt.max_writable for gt in gts)
    assert gts[0].freq_path.name == "freq0"
    assert gts[0].gt_path == gts[0].freq_path.parent
    # SLPC detected only where present.
    assert gts[0].slpc_power_profile_path is not None
    assert gts[0].slpc_power_profile_path.name == "slpc_power_profile"
    assert gts[1].slpc_power_profile_path is None
    assert gts[2].slpc_power_profile_path is not None


def test_discover_detects_missing_actuator_file_as_not_writable(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, make_max=False)

    (gt,) = discover_gpu_gts(sysfs_root)

    assert gt.min_writable is True
    assert gt.max_writable is False


def test_discover_detects_readonly_actuator_as_not_writable(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root bypasses W_OK permission checks")
    sysfs_root = tmp_path / "sys"
    freq = make_gt(sysfs_root)
    (freq / "min_freq").chmod(0o444)

    (gt,) = discover_gpu_gts(sysfs_root)

    assert gt.min_writable is False
    assert gt.max_writable is True


def test_discover_matches_power_profile_named_slpc_knob(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, slpc="base", slpc_name="power_profile")

    (gt,) = discover_gpu_gts(sysfs_root)

    assert gt.slpc_power_profile_path is not None
    assert gt.slpc_power_profile_path.name == "power_profile"


# --- 2. missing tree --------------------------------------------------------


def test_discover_on_missing_tree_returns_empty(tmp_path):
    assert discover_gpu_gts(tmp_path / "nonexistent") == []


# --- 3. apply clamps --------------------------------------------------------


def test_apply_clamps_request_into_rpn_rp0_on_every_gt(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, gt=0, rpn=100, rp0=1950, min_freq=100, max_freq=1950)
    make_gt(sysfs_root, gt=1, rpn=200, rp0=1500, min_freq=200, max_freq=1500)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    actuator.apply(min_mhz=50, max_mhz=3000)

    for gt in gts:
        written_min = read_int(gt.freq_path / "min_freq")
        written_max = read_int(gt.freq_path / "max_freq")
        assert written_max <= gt.rp0_mhz
        assert written_min >= gt.rpn_mhz
        assert written_min <= written_max
    assert read_int(gts[0].freq_path / "max_freq") == 1950
    assert read_int(gts[0].freq_path / "min_freq") == 100
    assert read_int(gts[1].freq_path / "max_freq") == 1500
    assert read_int(gts[1].freq_path / "min_freq") == 200
    assert actuator.failed is False


def test_apply_clamps_min_down_to_max_when_min_exceeds_max(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, rpn=100, rp0=1950, min_freq=100, max_freq=1950)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    # min 1900 (in bounds) but requested max 1200 -> min clamped down to 1200.
    actuator.apply(min_mhz=1900, max_mhz=1200)

    assert read_int(gts[0].freq_path / "max_freq") == 1200
    assert read_int(gts[0].freq_path / "min_freq") == 1200


# --- 3b. D1: a max-only cap must not be defeated by a latched-high min -------


def test_apply_max_only_lowers_latched_min_to_min_of_cap_and_rpe(tmp_path):
    # Real-device defect (D1): gt0 min_freq is latched at rp0 (1950), so a
    # max-only cap leaves min > max and the kernel keeps cur at min -- a live
    # no-op. The actuator must lower min to min(cap, rpe) alongside the cap.
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, rp0=1950, rpe=800, rpn=100, min_freq=1950, max_freq=1950)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    actuator.apply(max_mhz=1716)  # G1 cap; no explicit min

    assert read_int(gts[0].freq_path / "max_freq") == 1716
    # min lowered to min(cap 1716, rpe 800) = 800: cap now effective (min <= max).
    assert read_int(gts[0].freq_path / "min_freq") == 800
    assert actuator.last_applied_min_mhz == 800


def test_apply_max_only_lowers_latched_min_when_cap_below_rpe(tmp_path):
    # Deep cap below rpe: min follows the cap itself, never below rpn.
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, rp0=1950, rpe=800, rpn=600, min_freq=1950, max_freq=1950)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    actuator.apply(max_mhz=700)  # cap below rpe (800), above rpn (600)

    assert read_int(gts[0].freq_path / "max_freq") == 700
    assert read_int(gts[0].freq_path / "min_freq") == 700

    actuator.apply(max_mhz=650)
    # Cap 650 -> min(650, rpe 800) = 650, clamped to >= rpn 600 -> 650.
    assert read_int(gts[0].freq_path / "min_freq") == 650


def test_apply_max_only_never_raises_a_low_min(tmp_path):
    # A GT whose min already sits below the cap (e.g. gt1 drifting 500<->550)
    # must NOT have its min touched: lowering-only, per reduction-only rules.
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, gt=0, rp0=1950, rpe=800, rpn=100, min_freq=1950, max_freq=1950)
    make_gt(sysfs_root, gt=1, rp0=1950, rpe=800, rpn=100, min_freq=500, max_freq=1950)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    actuator.apply(max_mhz=1716)

    latched, drifting = gts
    assert read_int(latched.freq_path / "min_freq") == 800  # lowered (was 1950)
    assert read_int(drifting.freq_path / "min_freq") == 500  # untouched
    assert read_int(latched.freq_path / "max_freq") == 1716
    assert read_int(drifting.freq_path / "max_freq") == 1716


def test_apply_max_only_min_result_is_within_min_le_max_on_every_gt(tmp_path):
    # Combined write must yield min <= max on every GT regardless of prior
    # latched state (mixed latches across GTs).
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, gt=0, rp0=1950, rpe=800, rpn=100, min_freq=1950, max_freq=1950)
    make_gt(sysfs_root, gt=1, rp0=1950, rpe=800, rpn=100, min_freq=1200, max_freq=1950)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    actuator.apply(max_mhz=1365)

    for gt in gts:
        min_v = read_int(gt.freq_path / "min_freq")
        max_v = read_int(gt.freq_path / "max_freq")
        assert min_v <= max_v
        assert max_v == 1365


# --- 3c. D6: a ratio cap is derived per GT from EACH GT's own rp0 -----------


def test_apply_max_ratio_caps_each_gt_from_its_own_rp0(tmp_path):
    # D6 (BLOCKER): the render GT (gt0 rp0 1950) and the media GT (gt1 rp0 1200)
    # must each be trimmed from their OWN rp0. A G1 ratio (0.12) yields gt0 max
    # 1716 and gt1 max 1056 -- NOT a shared min-across cap of 1056 on both, which
    # was a -46% trim on the render GT (the D6 fps regression).
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, gt=0, rp0=1950, rpe=800, rpn=100, min_freq=1950, max_freq=1950)
    make_gt(sysfs_root, gt=1, rp0=1200, rpe=700, rpn=100, min_freq=1200, max_freq=1200)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    actuator.apply(max_ratio=0.12)

    render, media = gts
    assert read_int(render.freq_path / "max_freq") == 1716  # int(1950 * 0.88)
    assert read_int(media.freq_path / "max_freq") == 1056  # int(1200 * 0.88)
    # D1 min-lowering is per-GT with that GT's own rpe: gt0 -> 800, gt1 -> 700.
    assert read_int(render.freq_path / "min_freq") == 800
    assert read_int(media.freq_path / "min_freq") == 700
    assert actuator.last_applied == {"gt0": (800, 1716), "gt1": (700, 1056)}
    assert actuator.failed is False


def test_apply_max_ratio_never_exceeds_any_gt_rp0(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, gt=0, rp0=1950, rpe=800, rpn=100, min_freq=100, max_freq=1950)
    make_gt(sysfs_root, gt=1, rp0=1200, rpe=700, rpn=100, min_freq=100, max_freq=1200)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    actuator.apply(max_ratio=0.0)  # ratio 0 -> cap at rp0, never above

    for gt in gts:
        assert read_int(gt.freq_path / "max_freq") == gt.rp0_mhz


def test_restore_returns_both_gts_after_per_gt_ratio_cap(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, gt=0, rp0=1950, rpe=800, rpn=100, min_freq=1950, max_freq=1950)
    make_gt(sysfs_root, gt=1, rp0=1200, rpe=700, rpn=100, min_freq=1200, max_freq=1200)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    snapshot = actuator.snapshot()
    actuator.apply(max_ratio=0.12)
    assert actuator.restore(snapshot) == []

    assert read_int(gts[0].freq_path / "max_freq") == 1950
    assert read_int(gts[0].freq_path / "min_freq") == 1950
    assert read_int(gts[1].freq_path / "max_freq") == 1200
    assert read_int(gts[1].freq_path / "min_freq") == 1200


def test_apply_max_ratio_single_gt_unchanged(tmp_path):
    # Single-GT device: the render rp0 is the only rp0, so the ratio behaves
    # exactly like the pre-D6 absolute cap.
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, rp0=1950, rpe=800, rpn=100, min_freq=1950, max_freq=1950)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    actuator.apply(max_ratio=0.12)

    assert read_int(gts[0].freq_path / "max_freq") == 1716
    assert read_int(gts[0].freq_path / "min_freq") == 800
    assert actuator.last_applied == {"gt0": (800, 1716)}


def test_restore_returns_latched_min_and_max_after_max_only_cap(tmp_path):
    # Snapshot captures the latched min (1950); apply lowers it for the cap;
    # restore must return BOTH min and max to the latched pre-cap state.
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, rp0=1950, rpe=800, rpn=100, min_freq=1950, max_freq=1950)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    snapshot = actuator.snapshot()
    actuator.apply(max_mhz=1716)
    assert read_int(gts[0].freq_path / "min_freq") == 800

    failed = actuator.restore(snapshot)

    assert failed == []
    assert read_int(gts[0].freq_path / "min_freq") == 1950
    assert read_int(gts[0].freq_path / "max_freq") == 1950


# --- 4. write order: max before min ----------------------------------------


def test_apply_writes_max_before_min(tmp_path, monkeypatch):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, rpn=100, rp0=1950, min_freq=100, max_freq=1950)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    order: list[str] = []
    original = Path.write_text

    def recording_write(self, data, *args, **kwargs):
        order.append(self.name)
        return original(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", recording_write)
    actuator.apply(min_mhz=800, max_mhz=1200)

    assert order == ["max_freq", "min_freq"]


# --- 5. no-op writes are skipped -------------------------------------------


def test_apply_is_noop_when_values_already_match(tmp_path, monkeypatch):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, rpn=100, rp0=1950, min_freq=100, max_freq=1950)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    writes: list[str] = []
    original = Path.write_text

    def counting_write(self, data, *args, **kwargs):
        writes.append(self.name)
        return original(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", counting_write)
    actuator.apply(min_mhz=100, max_mhz=1950)

    assert writes == []


# --- 6. fail-closed ---------------------------------------------------------


def test_apply_fails_closed_and_ignores_subsequent_calls(tmp_path, monkeypatch):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, card=0, gt=0, rpn=100, rp0=1950, min_freq=100, max_freq=1950)
    make_gt(sysfs_root, card=1, gt=0, rpn=100, rp0=1950, min_freq=100, max_freq=1950)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    bad_min = gts[0].freq_path / "min_freq"
    original = Path.write_text
    writes: list[str] = []

    def failing_write(self, data, *args, **kwargs):
        writes.append(str(self))
        if self == bad_min:
            raise OSError("read-only control file")
        return original(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write)
    actuator.apply(min_mhz=800, max_mhz=1200)

    assert actuator.failed is True
    # gt1 (second GT) never got touched because we stopped at gt0's min write.
    assert read_int(gts[1].freq_path / "max_freq") == 1950
    assert read_int(gts[1].freq_path / "min_freq") == 100

    writes_before = list(writes)
    actuator.apply(min_mhz=700, max_mhz=1100)
    assert writes == writes_before  # latched: no further writes attempted


# --- 7. snapshot / restore round-trip --------------------------------------


def test_snapshot_apply_restore_round_trips_all_gts(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, gt=0, rpn=100, rp0=1950, min_freq=100, max_freq=1950)
    make_gt(sysfs_root, gt=1, rpn=200, rp0=1500, min_freq=300, max_freq=1400)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    snapshot = actuator.snapshot()
    assert isinstance(snapshot, GpuFreqSnapshot)

    actuator.apply(min_mhz=100, max_mhz=800)
    assert read_int(gts[0].freq_path / "max_freq") == 800

    failed = actuator.restore(snapshot)

    assert failed == []
    assert read_int(gts[0].freq_path / "min_freq") == 100
    assert read_int(gts[0].freq_path / "max_freq") == 1950
    assert read_int(gts[1].freq_path / "min_freq") == 300
    assert read_int(gts[1].freq_path / "max_freq") == 1400


# --- 8. restore readback failure -------------------------------------------


def test_restore_reports_unverifiable_path_and_restores_the_rest(
    tmp_path, monkeypatch
):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, card=0, gt=0, rpn=100, rp0=1950, min_freq=100, max_freq=1950)
    make_gt(sysfs_root, card=1, gt=0, rpn=100, rp0=1950, min_freq=100, max_freq=1950)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    snapshot = actuator.snapshot()  # both GTs at 100 / 1950
    # Simulate a boost already applied by writing changed values directly.
    for gt in gts:
        (gt.freq_path / "max_freq").write_text("800")
        (gt.freq_path / "min_freq").write_text("800")

    sticky_max = gts[0].freq_path / "max_freq"
    original = Path.write_text

    def sticky_write(self, data, *args, **kwargs):
        if self == sticky_max:
            return None  # swallow the write; readback never matches
        return original(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", sticky_write)
    failed = actuator.restore(snapshot)

    assert failed == [str(sticky_max)]
    # gt0 min still restored; gt1 fully restored.
    assert read_int(gts[0].freq_path / "min_freq") == 100
    assert read_int(gts[1].freq_path / "min_freq") == 100
    assert read_int(gts[1].freq_path / "max_freq") == 1950


# --- 9. SLPC power profile --------------------------------------------------


def test_set_slpc_power_profile_writes_present_skips_absent(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, card=0, gt=0, slpc="base")
    make_gt(sysfs_root, card=0, gt=1)  # no knob
    make_gt(sysfs_root, card=1, gt=0, slpc="base")
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    assert [gt.name for gt in actuator.slpc_power_profile_targets()] == ["gt0", "gt0"]

    report = actuator.set_slpc_power_profile("power_saving")

    assert report["applied"] == ["gt0", "gt0"]
    assert report["skipped"] == [{"gt": "gt1", "reason": "absent"}]
    assert report["failed"] == []
    assert actuator.failed is False
    assert gts[0].slpc_power_profile_path.read_text() == "power_saving"
    assert gts[2].slpc_power_profile_path.read_text() == "power_saving"


def test_set_slpc_power_profile_all_absent_skips_all(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, card=0, gt=0)
    make_gt(sysfs_root, card=0, gt=1)
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    report = actuator.set_slpc_power_profile("base")

    assert report["applied"] == []
    assert report["failed"] == []
    assert report["skipped"] == [
        {"gt": "gt0", "reason": "absent"},
        {"gt": "gt1", "reason": "absent"},
    ]
    assert actuator.failed is False


def test_set_slpc_power_profile_failure_latches_failed(tmp_path, monkeypatch):
    sysfs_root = tmp_path / "sys"
    make_gt(sysfs_root, slpc="base")
    gts = discover_gpu_gts(sysfs_root)
    actuator = GpuFreqActuator(gts)

    knob = gts[0].slpc_power_profile_path
    original = Path.write_text

    def failing_write(self, data, *args, **kwargs):
        if self == knob:
            raise OSError("write rejected")
        return original(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write)
    report = actuator.set_slpc_power_profile("power_saving")

    assert report["applied"] == []
    assert report["failed"] == ["gt0"]
    assert actuator.failed is True


def test_gpu_gt_and_snapshot_are_frozen_dataclasses():
    gt = GpuGt(
        name="gt0",
        freq_path=Path("/sys/x/gt0/freq0"),
        gt_path=Path("/sys/x/gt0"),
        rp0_mhz=1950,
        rpe_mhz=800,
        rpn_mhz=100,
        min_writable=True,
        max_writable=True,
        slpc_power_profile_path=None,
    )
    with pytest.raises(AttributeError):
        gt.name = "gt1"  # type: ignore[misc]
    snap = GpuFreqSnapshot(values={"gt0": (100, 1950)})
    with pytest.raises(AttributeError):
        snap.values = {}  # type: ignore[misc]
