"""Game Power V10 Slice A tests: frame feed, GPU/soft-PL1 actuation, fast boost
lane, personas, and telemetry v3."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from steamos_intel_handheld import game_power_control
from steamos_intel_handheld.game_power import (
    CpuPolicyActuator,
    FrameFeedReader,
    FramePerformanceTelemetry,
    FrameTargetTelemetry,
    GamePowerAction,
    GamePowerActuation,
    GamePowerConfig,
    GamePowerController,
    GamePowerGovernor,
    GamePowerMode,
    GamePowerPersona,
    GamePowerSample,
    RaplPowerWindow,
    build_parser,
    config_from_args,
    discover_cpu_policies,
    format_decision_jsonl,
    gpu_freq_bounds,
)
from steamos_intel_handheld.game_power_gpu import GpuFreqActuator, discover_gpu_gts

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def make_gt_tree(root, *, rp0=1950, rpe=800, rpn=100, min_freq=100, max_freq=1950):
    for gt in ("gt0", "gt1"):
        d = root / "class" / "drm" / "card0" / "device" / "tile0" / gt / "freq0"
        d.mkdir(parents=True)
        (d / "rp0_freq").write_text(f"{rp0}\n")
        (d / "rpe_freq").write_text(f"{rpe}\n")
        (d / "rpn_freq").write_text(f"{rpn}\n")
        (d / "min_freq").write_text(f"{min_freq}\n")
        (d / "max_freq").write_text(f"{max_freq}\n")
    return root


def make_cpu_tree(root):
    base = root / "devices" / "system" / "cpu"
    for cpu, cap in ((0, 1024), (1, 676)):
        pol = base / "cpufreq" / f"policy{cpu}"
        pol.mkdir(parents=True)
        (pol / "affected_cpus").write_text(str(cpu))
        (pol / "energy_performance_available_preferences").write_text(
            "performance balance_performance balance_power power"
        )
        (pol / "energy_performance_preference").write_text("balance_performance")
        (pol / "scaling_max_freq").write_text("4800000")
        (pol / "cpuinfo_max_freq").write_text("4800000")
        (base / f"cpu{cpu}").mkdir(parents=True, exist_ok=True)
        (base / f"cpu{cpu}" / "cpu_capacity").write_text(str(cap))
    return root


class FakeObserver:
    def __init__(self, samples):
        self.samples = list(samples)

    async def sample(self, *, sleep_between=True):
        return self.samples.pop(0)


class FakeSoftPl1:
    def __init__(self):
        self.calls = []
        self.value = None

    def set_soft_pl1_w(self, value_w):
        self.calls.append(value_w)
        self.value = value_w


def tb_config(**over):
    base = dict(
        mode=GamePowerMode.TARGET_BALANCE,
        poll_s=2.0,
        phase_stable_samples=1,
        ladder_hold_samples=1,
    )
    base.update(over)
    return GamePowerConfig(**base)


def at_target(**over):
    kw = dict(
        appid="1091500",
        package_w=22.0,
        core_w=8.8,
        uncore_w=7.4,
        avg_fps=63.0,
        p95=15.0,
        fps=60.0,
        rp0=1950,
        rpe=800,
        median=22.0,
    )
    kw.update(over)
    return GamePowerSample(
        appid=kw["appid"],
        rapl=RaplPowerWindow(
            duration_s=2.0,
            package_w=kw["package_w"],
            core_w=kw["core_w"],
            uncore_w=kw["uncore_w"],
        ),
        pl1_w=22,
        fdinfo_busy={"render": 0.75},
        frame_target=FrameTargetTelemetry(
            fps_target=kw["fps"], source="manual", confidence="high"
        ),
        frame_performance=FramePerformanceTelemetry(
            avg_fps=kw["avg_fps"],
            p95_frame_ms=kw["p95"],
            sample_count=20,
            window_s=2.0,
            source="mangoapp-feed",
            confidence="high",
        ),
        foreground_process_age_s=100.0,
        gpu_rp0_mhz=kw["rp0"],
        gpu_rpe_mhz=kw["rpe"],
        package_median_w=kw["median"],
        frame_feed_status="live",
    )


# ---------------------------------------------------------------------------
# Contract 1.1: FrameFeedReader
# ---------------------------------------------------------------------------


def _write_feed(path, *, updated, avg_fps=59.6, p95=18.9, last=16.7, worst=27.3):
    path.write_text(
        json.dumps(
            {
                "schema": "steamos-intel-handheld-frame-feed-v1",
                "pid": 123,
                "appid": "3423533071",
                "updated_monotonic_s": updated,
                "window_s": 2.0,
                "frame_count": 119,
                "avg_fps": avg_fps,
                "p95_frame_ms": p95,
                "last_frame_ms": last,
                "spike": {"count": 2, "worst_ms": worst},
            }
        )
    )


def test_frame_feed_live_upgrades_confidence(tmp_path):
    feed = tmp_path / "frame-feed.json"
    clock = [1000.0]
    _write_feed(feed, updated=998.0)
    reader = FrameFeedReader(feed, stale_s=5.0, clock=lambda: clock[0])
    telemetry = reader.read()
    assert telemetry is not None
    assert telemetry.source == "mangoapp-feed"
    assert telemetry.confidence == "high"
    assert telemetry.avg_fps == 59.6
    assert telemetry.p95_frame_ms == 18.9
    assert reader.last_status == "live"


def test_frame_feed_stale_is_absent(tmp_path):
    feed = tmp_path / "frame-feed.json"
    clock = [1000.0]
    _write_feed(feed, updated=990.0)  # 10s old, stale_s=5
    reader = FrameFeedReader(feed, stale_s=5.0, clock=lambda: clock[0])
    assert reader.read() is None
    assert reader.status() == "stale"


def test_frame_feed_missing_and_corrupt_are_absent(tmp_path):
    reader = FrameFeedReader(tmp_path / "missing.json", clock=lambda: 0.0)
    assert reader.read() is None
    assert reader.last_status == "absent"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert FrameFeedReader(bad, clock=lambda: 0.0).read() is None
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"schema": "other", "updated_monotonic_s": 0.0}))
    assert FrameFeedReader(wrong, clock=lambda: 0.0).read() is None


def test_frame_feed_read_fast_returns_spike(tmp_path):
    feed = tmp_path / "frame-feed.json"
    _write_feed(feed, updated=1.0, worst=30.0, last=16.0)
    fast = FrameFeedReader(feed, stale_s=5.0, clock=lambda: 2.0).read_fast()
    assert fast.status == "live"
    assert fast.spike_worst_ms == 30.0
    assert fast.last_frame_ms == 16.0


# ---------------------------------------------------------------------------
# Contract 1.2: GPU actuator reduction-only, wired via the governor
# ---------------------------------------------------------------------------


def _governor_with_gpu(tmp_path, config, samples, soft_pl1=None):
    make_cpu_tree(tmp_path / "sys")
    make_gt_tree(tmp_path / "sys")
    gts = discover_gpu_gts(tmp_path / "sys")
    gpu_actuator = GpuFreqActuator(gts)
    actuator = CpuPolicyActuator(discover_cpu_policies(tmp_path / "sys"))
    governor = GamePowerGovernor(
        config=config,
        observer=FakeObserver(samples),
        actuator=actuator,
        gpu_actuator=gpu_actuator,
        soft_pl1_actuator=soft_pl1,
    )
    return governor, gpu_actuator, tmp_path / "sys"


def _gt_max(sysfs):
    d = sysfs / "class" / "drm" / "card0" / "device" / "tile0" / "gt0" / "freq0"
    return int((d / "max_freq").read_text())


def _gt_min(sysfs):
    d = sysfs / "class" / "drm" / "card0" / "device" / "tile0" / "gt0" / "freq0"
    return int((d / "min_freq").read_text())


def test_gpu_cap_default_ratios_match_probe_plateau():
    # D3: 17W/60fps probe -- pacing plateau holds to rp0*0.69 (-31%), knee
    # below. Battery rungs stop at -30%; the -45% depth is only the
    # verdict-gated G4CAP. D2: P1 anchors at least 1 W below the user slider.
    config = GamePowerConfig()
    assert config.gpu_cap_g1_ratio == 0.12
    assert config.gpu_cap_g2_ratio == 0.22
    assert config.gpu_cap_g3_ratio == 0.30
    assert config.gpu_cap_g4_ratio == 0.45
    assert config.soft_pl1_p1_slider_margin_w == 1.0
    assert config.soft_pl1_p2_step_w == 1.0
    assert config.soft_pl1_p3_step_w == 2.0


def test_limiter_like_target_can_trim_without_uncapped_headroom():
    # A 60fps limiter often reports just below 60 even when pacing is healthy;
    # the ladder should use the p95 guard, not require impossible 63fps headroom.
    controller = GamePowerController(tb_config())

    decision = controller.evaluate(at_target(avg_fps=59.0, p95=18.0, fps=60.0))

    assert controller.ladder_step == 1
    assert decision.action == GamePowerAction.TARGET_BALANCE_TRIM
    # Step 1 is the package budget: a GPU cap alone gets re-spent by the CPU.
    assert decision.trim_rungs_active == ["P1"]


def test_gpu_cap_never_exceeds_rp0_and_matches_ratio(tmp_path):
    # Climb to G1 (step 2; step 1 is the P1 budget) -> max_freq =
    # int(rp0 * (1 - 0.12)) = 1716, <= rp0.
    governor, actuator, sysfs = _governor_with_gpu(
        tmp_path, tb_config(), [at_target() for _ in range(2)]
    )
    asyncio.run(governor.run_iterations(2))
    assert _gt_max(sysfs) == 1716
    assert _gt_max(sysfs) <= 1950  # reduction-only: never above rp0
    governor.close()


def test_gpu_cap_restored_on_close(tmp_path):
    governor, actuator, sysfs = _governor_with_gpu(
        tmp_path, tb_config(), [at_target() for _ in range(2)]
    )
    asyncio.run(governor.run_iterations(2))
    assert _gt_max(sysfs) == 1716
    governor.close()
    assert _gt_max(sysfs) == 1950  # baseline restored


def _governor_with_latched_gpu(tmp_path, config, samples):
    # D1 device reality: gt min_freq latched at rp0 (1950) before we act.
    make_cpu_tree(tmp_path / "sys")
    make_gt_tree(tmp_path / "sys", min_freq=1950)
    gts = discover_gpu_gts(tmp_path / "sys")
    governor = GamePowerGovernor(
        config=config,
        observer=FakeObserver(samples),
        actuator=CpuPolicyActuator(discover_cpu_policies(tmp_path / "sys")),
        gpu_actuator=GpuFreqActuator(gts),
    )
    return governor, tmp_path / "sys"


def test_gpu_cap_with_min_latched_at_rp0_lowers_min_and_is_effective(tmp_path):
    # D1 (BLOCKER): with min latched at rp0 a max-only G1 cap used to leave
    # min(1950) > max(1716) -- a live no-op. The governor's cap must also lower
    # min to min(cap, rpe) = 800 so the cap takes effect.
    governor, sysfs = _governor_with_latched_gpu(
        tmp_path, tb_config(), [at_target() for _ in range(2)]
    )
    asyncio.run(governor.run_iterations(2))
    assert _gt_max(sysfs) == 1716
    assert _gt_min(sysfs) == 800  # lowered from the rp0 latch
    assert _gt_min(sysfs) <= _gt_max(sysfs)
    governor.close()


def test_gpu_cap_with_latched_min_restores_both_min_and_max(tmp_path):
    governor, sysfs = _governor_with_latched_gpu(
        tmp_path, tb_config(), [at_target() for _ in range(2)]
    )
    asyncio.run(governor.run_iterations(2))
    assert _gt_min(sysfs) == 800
    governor.close()
    # Restore returns BOTH knobs to the snapshotted (latched) baseline.
    assert _gt_max(sysfs) == 1950
    assert _gt_min(sysfs) == 1950


def test_gpu_cap_telemetry_reports_min_actually_applied(tmp_path):
    # D1 telemetry: gpu_freq_caps.min_mhz must show the min the actuator
    # actually wrote (800), not null, when the cap forced a min lowering.
    governor, _sysfs = _governor_with_latched_gpu(
        tmp_path, tb_config(), [at_target() for _ in range(2)]
    )
    asyncio.run(governor.run_once())          # step 1: P1 budget
    decision = asyncio.run(governor.run_once())  # step 2: G1 cap
    # D6: flat min_mhz/max_mhz carry the render GT (gt0) values; per_gt breaks
    # them out for every GT (both GTs share rp0 1950 in this fixture).
    assert decision.gpu_freq_caps == {
        "min_mhz": 800,
        "max_mhz": 1716,
        "per_gt": {
            "gt0": {"min_mhz": 800, "max_mhz": 1716},
            "gt1": {"min_mhz": 800, "max_mhz": 1716},
        },
    }
    governor.close()


def _governor_with_asymmetric_gpu(tmp_path, config, samples):
    # D6 device reality: render gt0 rp0 1950, media gt1 rp0 1200; both mins
    # latched at their own rp0 (the D1 latch), so a G-rung cap must lower each.
    make_cpu_tree(tmp_path / "sys")
    for gt, rp0, rpe in (("gt0", 1950, 800), ("gt1", 1200, 700)):
        d = tmp_path / "sys" / "class" / "drm" / "card0" / "device" / "tile0" / gt / "freq0"
        d.mkdir(parents=True)
        (d / "rp0_freq").write_text(f"{rp0}\n")
        (d / "rpe_freq").write_text(f"{rpe}\n")
        (d / "rpn_freq").write_text("100\n")
        (d / "min_freq").write_text(f"{rp0}\n")
        (d / "max_freq").write_text(f"{rp0}\n")
    gts = discover_gpu_gts(tmp_path / "sys")
    governor = GamePowerGovernor(
        config=config,
        observer=FakeObserver(samples),
        actuator=CpuPolicyActuator(discover_cpu_policies(tmp_path / "sys")),
        gpu_actuator=GpuFreqActuator(gts),
    )
    return governor, tmp_path / "sys"


def _gt_freq(sysfs, gt, name):
    d = sysfs / "class" / "drm" / "card0" / "device" / "tile0" / gt / "freq0"
    return int((d / f"{name}_freq").read_text())


def test_gpu_cap_per_gt_uses_each_gts_own_rp0(tmp_path):
    # D6 (BLOCKER): a G1 cap trims the render GT from 1950 (-> 1716) and the
    # media GT from 1200 (-> 1056). The old min-across helper capped BOTH at
    # 1056, a -46% trim on the render GT that dropped fps to 47.7.
    governor, sysfs = _governor_with_asymmetric_gpu(
        tmp_path, tb_config(), [at_target() for _ in range(2)]
    )
    asyncio.run(governor.run_once())          # step 1: P1 budget
    decision = asyncio.run(governor.run_once())  # step 2: G1 cap
    assert _gt_freq(sysfs, "gt0", "max") == 1716
    assert _gt_freq(sysfs, "gt1", "max") == 1056
    # Mins lowered per GT with that GT's own rpe (D1, per-GT).
    assert _gt_freq(sysfs, "gt0", "min") == 800
    assert _gt_freq(sysfs, "gt1", "min") == 700
    # Telemetry: flat keys carry the render GT (gt0); per_gt breaks out both.
    assert decision.gpu_freq_caps == {
        "min_mhz": 800,
        "max_mhz": 1716,
        "per_gt": {
            "gt0": {"min_mhz": 800, "max_mhz": 1716},
            "gt1": {"min_mhz": 700, "max_mhz": 1056},
        },
    }
    governor.close()
    # Restore returns BOTH GTs to their own latched baseline.
    assert _gt_freq(sysfs, "gt0", "max") == 1950
    assert _gt_freq(sysfs, "gt0", "min") == 1950
    assert _gt_freq(sysfs, "gt1", "max") == 1200
    assert _gt_freq(sysfs, "gt1", "min") == 1200


def test_boost_lifts_prior_gpu_cap_and_floors_min(tmp_path):
    # After a G rung caps GPU max, a LOADING boost must lift the cap (max back to
    # rp0 baseline) while flooring min at rpe -- boost releases all rungs.
    samples = [
        at_target(),
        at_target(),
        replace(at_target(), foreground_process_age_s=5.0),
    ]
    governor, _actuator, sysfs = _governor_with_gpu(tmp_path, tb_config(), samples)
    asyncio.run(governor.run_iterations(2))  # P1 then G1: max capped to 1716
    assert _gt_max(sysfs) == 1716
    asyncio.run(governor.run_iterations(1))  # LOADING boost
    assert _gt_max(sysfs) == 1950  # cap lifted
    assert _gt_min(sysfs) == 800  # rpe floor
    governor.close()


def test_gpu_boost_floor_is_rpe_not_rp0(tmp_path):
    # LOADING implies boost posture: GPU min_freq floored at rpe (800), max left
    # at rp0 (never floored to rp0).
    loading = replace(at_target(), foreground_process_age_s=5.0)
    governor, actuator, sysfs = _governor_with_gpu(tmp_path, tb_config(), [loading])
    asyncio.run(governor.run_iterations(1))
    assert _gt_min(sysfs) == 800
    assert _gt_max(sysfs) == 1950
    governor.close()


# ---------------------------------------------------------------------------
# Contract 1.3: soft-PL1 overlay wired via the governor (reduction-only)
# ---------------------------------------------------------------------------


def test_soft_pl1_applied_at_p_rung_and_cleared_on_release(tmp_path):
    soft = FakeSoftPl1()
    # P1 is step 1: the budget is established before the GPU cap, because a cap
    # without it is re-spent by the CPU. D2: P1 = min(slider 22 - 1,
    # ceil(22)+1.5) = min(21, 23.5) = 21 (always below the user slider).
    governor, _actuator, _sysfs = _governor_with_gpu(
        tmp_path, tb_config(), [at_target() for _ in range(4)], soft_pl1=soft
    )
    asyncio.run(governor.run_iterations(1))
    assert soft.value == 21
    # Close restores everything, clearing the soft-PL1 overlay.
    governor.close()
    assert soft.value is None


def test_soft_pl1_reduction_only_never_exceeds_user_via_backend():
    import tempfile

    from steamos_intel_handheld.power_control import TdpBackend

    with tempfile.TemporaryDirectory() as d:
        backend = TdpBackend(state_file=f"{d}/state", apply_rapl=False, soft_pl1_floor_w=8)
        # No RAPL applied (apply_rapl False), but the reduction math is provable.
        assert backend._effective_pl1_w(17) == 17
        backend._soft_pl1_w = 11
        assert backend._effective_pl1_w(17) == 11
        backend._soft_pl1_w = 3
        assert backend._effective_pl1_w(17) == 8  # floored
        backend._soft_pl1_w = 6
        assert backend._effective_pl1_w(5) == 5  # never exceeds a low user slider


def _p_rung_values(sample):
    """Drive a P-rung-only controller through P1/P2/P3 and return soft_pl1_w."""

    controller = GamePowerController(tb_config(trim_rung_filter=("P",)))
    values = {}
    for _ in range(3):
        d = controller.evaluate(sample)
        values[controller.ladder_step] = d.soft_pl1_w
    return values


def test_soft_pl1_p1_starts_below_slider_on_pinned_scene():
    # D2: the shipped P1 = ceil(median + 1.5) computed 19 W on the probed
    # 17 W-pinned scene (median ~16.9) -> above the slider -> min(slider, soft)
    # clamped it to a live no-op. P1 must be min(slider - margin, ceil(median)
    # + headroom) = min(17-1, 17+1.5) = 16, i.e. always <= slider - 1.
    sample = at_target(package_w=16.9, median=16.9)
    sample = replace(sample, pl1_w=17)
    values = _p_rung_values(sample)
    assert values[1] == 16  # P1 below the 17 W slider
    assert values[2] == 15  # P2 = P1 - 1
    assert values[3] == 14  # P3 = P1 - 2


def test_soft_pl1_p1_uses_demand_when_median_is_low():
    # When demand (ceil(median) + headroom) sits well below the slider, P1
    # follows demand, not the slider anchor: min(22-1, ceil(11)+1.5) = 12.5 ->
    # ceil -> 13.
    sample = at_target(package_w=11.0, median=11.0)  # slider stays 22
    values = _p_rung_values(sample)
    assert values[1] == 13
    assert values[2] == 12
    assert values[3] == 11


def test_soft_pl1_p1_slider_margin_is_configurable():
    controller = GamePowerController(
        tb_config(trim_rung_filter=("P",), soft_pl1_p1_slider_margin_w=2.0)
    )
    sample = replace(at_target(package_w=16.9, median=16.9), pl1_w=17)
    d = controller.evaluate(sample)
    assert controller.ladder_step == 1
    assert d.soft_pl1_w == 15  # slider 17 - margin 2


# ---------------------------------------------------------------------------
# Contract 1.5: FastBoostLane + boost posture
# ---------------------------------------------------------------------------


def test_fast_boost_lane_triggers_and_holds():
    from steamos_intel_handheld.game_power import FastBoostLane

    clock = [0.0]
    lane = FastBoostLane(
        3.0, spike_boost_ratio=1.5, psi_boost_delta=15.0, clock=lambda: clock[0]
    )
    # target 60 fps -> 16.667 ms; bar = 1.5 * 16.667 = 25 ms.
    active, reason = lane.evaluate(target_frame_ms=16.667, spike_worst_ms=30.0)
    assert active and reason == "frame-spike"
    # No trigger but within hold window -> still active.
    clock[0] = 2.0
    active, reason = lane.evaluate(target_frame_ms=16.667, spike_worst_ms=10.0)
    assert active and reason == "boost-hold"
    # Past hold window -> inactive.
    clock[0] = 4.0
    active, reason = lane.evaluate(target_frame_ms=16.667, spike_worst_ms=10.0)
    assert not active


def test_fast_boost_lane_psi_jump_and_loading():
    from steamos_intel_handheld.game_power import FastBoostLane

    lane = FastBoostLane(3.0, spike_boost_ratio=1.5, psi_boost_delta=15.0, clock=lambda: 0.0)
    lane.evaluate(psi_avg10=10.0)  # prime
    active, reason = lane.evaluate(psi_avg10=30.0)  # +20 > 15
    assert active and reason == "psi-jump"
    active, reason = lane.evaluate(phase_is_loading=True)
    assert active and reason == "loading"


def test_boost_is_not_verdict_gated(tmp_path):
    # A spike triggers boost even with no verdict ledger; boost posture sets
    # pcore EPP performance + GPU min floor at rpe.
    class FeedStub:
        last_status = "live"

        def read_fast(self):
            from steamos_intel_handheld.game_power import FrameFeedFast

            return FrameFeedFast(status="live", spike_worst_ms=40.0, last_frame_ms=40.0)

        def read(self):
            return None

    make_cpu_tree(tmp_path / "sys")
    make_gt_tree(tmp_path / "sys")
    gts = discover_gpu_gts(tmp_path / "sys")
    governor = GamePowerGovernor(
        config=tb_config(),
        observer=FakeObserver([at_target()]),
        actuator=CpuPolicyActuator(discover_cpu_policies(tmp_path / "sys")),
        gpu_actuator=GpuFreqActuator(gts),
        frame_feed_reader=FeedStub(),
    )
    decision = asyncio.run(governor.run_once())
    assert decision.boost_active is True
    assert decision.boost_reason == "frame-spike"
    assert _gt_min(tmp_path / "sys") == 800  # rpe floor applied
    governor.close()


# ---------------------------------------------------------------------------
# Personas (plan section 0 / contract 1.4)
# ---------------------------------------------------------------------------


def test_ac_performance_persona_is_epp_only():
    controller = GamePowerController(tb_config(persona=GamePowerPersona.AC_PERFORMANCE))
    steps = {}
    for _ in range(4):
        d = controller.evaluate(at_target())
        steps[controller.ladder_step] = d.actuation
    # ac-performance sequence is just C1 (ecore) then C2 (pcore); tops out at 2.
    assert controller.ladder_step == 2
    assert steps[1] == GamePowerActuation(ecore_epp="balance_power")
    assert steps[2] == GamePowerActuation(
        ecore_epp="balance_power", pcore_epp="balance_power"
    )


def test_ac_quiet_uses_wider_p95_guard():
    # p95 18.5 ms sits in the 1.10-1.15 band for a 60 fps target (16.667 ms):
    # still fps-target-satisfied (<= 1.15) but breaches the battery ladder guard
    # (1.10 -> 18.33 ms), while the ac-quiet guard (1.20 -> 20.0 ms) holds.
    battery = GamePowerController(tb_config(persona=GamePowerPersona.BATTERY))
    battery.evaluate(at_target())  # step 1
    # Release takes two consecutive breaching samples (ladder_release_samples).
    battery.evaluate(at_target(p95=18.5))
    d = battery.evaluate(at_target(p95=18.5))
    assert d.action == GamePowerAction.TARGET_BALANCE_RELEASE  # guard 1.10 -> breach

    quiet = GamePowerController(tb_config(persona=GamePowerPersona.AC_QUIET))
    quiet.evaluate(at_target())  # step 1
    d = quiet.evaluate(at_target(p95=18.5))
    assert d.action == GamePowerAction.TARGET_BALANCE_TRIM  # guard 1.20 -> holds


# ---------------------------------------------------------------------------
# Profiler-only rung-subset selection (--trim-rungs, v10-gpu-cap / v10-soft-pl1)
# ---------------------------------------------------------------------------


def test_trim_rungs_g_only_climbs_only_gpu_cap_rungs():
    controller = GamePowerController(tb_config(trim_rung_filter=("G",)))
    seen = []
    for _ in range(6):
        d = controller.evaluate(at_target())
        seen.append(list(d.trim_rungs_active or []))
    # Only G1/G2/G3 are reachable; the ladder tops out at 3 and never engages
    # soft-PL1 or EPP rungs.
    assert controller.ladder_step == 3
    assert seen[-1] == ["G1", "G2", "G3"]
    d = controller.evaluate(at_target())
    assert d.soft_pl1_w is None


def test_trim_rungs_p_only_climbs_only_soft_pl1_rungs():
    controller = GamePowerController(tb_config(trim_rung_filter=("P",)))
    for _ in range(6):
        d = controller.evaluate(at_target())
    assert controller.ladder_step == 3
    assert list(d.trim_rungs_active or []) == ["P1", "P2", "P3"]
    assert d.gpu_freq_caps is None
    assert d.soft_pl1_w is not None


def test_trim_rungs_default_all_is_byte_identical_full_ladder():
    default = GamePowerController(tb_config())
    filtered = GamePowerController(tb_config(trim_rung_filter=None))
    for _ in range(8):
        a = default.evaluate(at_target())
        b = filtered.evaluate(at_target())
        assert list(a.trim_rungs_active or []) == list(b.trim_rungs_active or [])
    assert default.ladder_step == filtered.ladder_step == 8


def test_trim_rungs_cli_flag_maps_to_filter():
    parser = build_parser()
    args = parser.parse_args(["--mode", "target-balance", "--trim-rungs", "G"])
    config = config_from_args(args)
    assert config.trim_rung_filter == ("G",)
    default = config_from_args(parser.parse_args(["--mode", "target-balance"]))
    assert default.trim_rung_filter is None


# ---------------------------------------------------------------------------
# Contract 1.7: telemetry v3 (additive, target-balance only)
# ---------------------------------------------------------------------------


def test_telemetry_v3_fields_present_on_target_balance():
    controller = GamePowerController(tb_config())
    controller.evaluate(at_target())  # step 1 (P1)
    controller.evaluate(at_target())  # step 2 (G1)
    controller.evaluate(at_target())  # step 3 (P2)
    decision = controller.evaluate(at_target())  # step 4 (G2)
    row = json.loads(format_decision_jsonl(at_target(), decision, elapsed_s=1.0))
    assert row["persona"] == "battery"
    # Controller-side telemetry: G2 caps max to int(1950 * 0.78) = 1521; the
    # paired min is data-dependent (applied by the actuator) so it is null here.
    assert row["gpu_freq_caps"] == {"min_mhz": None, "max_mhz": 1521}
    assert row["trim_rungs_active"] == ["P1", "G1", "P2", "G2"]
    assert row["frame_feed_status"] == "live"
    assert row["limiter_state"] == "unknown"
    assert row["boost_active"] is False


def test_gpu_priority_jsonl_stays_byte_identical():
    # gpu-priority decisions never set persona, so none of the v10 fields appear.
    controller = GamePowerController(
        GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY, activate_samples=1)
    )
    sample = at_target(avg_fps=30.0, p95=40.0)  # not fps-target-satisfied
    decision = controller.evaluate(sample)
    row = json.loads(format_decision_jsonl(sample, decision, elapsed_s=1.0))
    for key in (
        "persona",
        "soft_pl1_w",
        "gpu_freq_caps",
        "boost_active",
        "trim_rungs_active",
        "frame_feed_status",
        "limiter_state",
    ):
        assert key not in row


# ---------------------------------------------------------------------------
# Persona runtime control override (game_power_control.py)
# ---------------------------------------------------------------------------


def test_persona_override_valid_applies(tmp_path):
    control = tmp_path / "control.json"
    game_power_control.set_runtime_mode(control, "automatic")
    game_power_control.set_persona(control, "ac-quiet")
    base = GamePowerConfig(persona=GamePowerPersona.BATTERY)
    cfg = game_power_control.effective_config_from_runtime_file(base, control)
    assert cfg.persona == GamePowerPersona.AC_QUIET


def test_persona_override_invalid_fails_closed(tmp_path):
    control = tmp_path / "control.json"
    control.write_text(
        json.dumps({"schema_version": 1, "mode": "automatic", "persona": {"persona": "turbo"}})
    )
    status = game_power_control.read_runtime_status(control)
    assert status.persona_override.status == "invalid"
    base = GamePowerConfig(mode=GamePowerMode.TARGET_BALANCE)
    cfg = game_power_control.effective_config_from_runtime_file(base, control)
    # Fail-closed like the fps override: control invalid -> mode OFF.
    assert cfg.mode == GamePowerMode.OFF


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_config_from_args_parses_persona_and_frame_feed():
    args = build_parser().parse_args(
        [
            "--mode",
            "target-balance",
            "--persona",
            "ac-quiet",
            "--frame-feed-file",
            "/run/x/frame-feed.json",
            "--frame-feed-stale-s",
            "3.0",
        ]
    )
    cfg = config_from_args(args)
    assert cfg.persona == GamePowerPersona.AC_QUIET
    assert cfg.frame_feed_file == "/run/x/frame-feed.json"
    assert cfg.frame_feed_stale_s == 3.0


def test_gpu_freq_bounds_uses_render_rp0_and_conservative_rpe():
    # D6: the GTs do NOT share bounds -- rp0 is the render GT's ceiling (MAX
    # across GTs) while rpe stays the conservative (MIN across) boost floor.
    class G:
        def __init__(self, rp0, rpe):
            self.rp0_mhz = rp0
            self.rpe_mhz = rpe

    assert gpu_freq_bounds([G(1950, 800), G(1950, 800)]) == (1950, 800)
    # Render gt0 rp0 1950, media gt1 rp0 1200 -> rp0 1950; rpe min(800, 700)=700.
    assert gpu_freq_bounds([G(1950, 800), G(1200, 700)]) == (1950, 700)
    assert gpu_freq_bounds([]) == (None, None)
