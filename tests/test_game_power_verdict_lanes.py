import asyncio
import json
from pathlib import Path

from steamos_intel_handheld.game_power import (
    CpuPolicy,
    CpuPolicyClass,
    FramePerformanceTelemetry,
    FrameTargetTelemetry,
    GamePowerConfig,
    GamePowerController,
    GamePowerGovernor,
    GamePowerMode,
    GamePowerPhase,
    GamePowerSample,
    GamePowerVerdictEnv,
    GamePowerVerdictLedger,
    RaplPowerWindow,
    _DaemonCgroupWriter,
    topology_fingerprint,
    verdict_tdp_bucket,
)

ENV = GamePowerVerdictEnv(
    topology_fingerprint="4p4e-nosmt-deadbeef",
    kernel="6.16.12-valve24.4",
)


def _write_ledger(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps({"entries": entries}) + "\n")


def _better_entry(actuator: str, **over) -> dict:
    base = {
        "appid": "1903340",
        "tdp_w": 17,
        "fps_target": 30,
        "topology_fingerprint": ENV.topology_fingerprint,
        "kernel": ENV.kernel,
        "policy_version": "game-power-target-balance-v9",
        "actuator": actuator,
        "verdict": "BETTER",
    }
    base.update(over)
    return base


def cpu_bound_sample(**over) -> GamePowerSample:
    base = dict(
        appid="1903340",
        rapl=RaplPowerWindow(
            duration_s=2.0, package_w=17.0, core_w=8.5, uncore_w=1.0
        ),
        pl1_w=17,
        fdinfo_busy={"render": 0.3},
        frame_target=FrameTargetTelemetry(fps_target=30.0, source="manual", confidence="high"),
        frame_performance=FramePerformanceTelemetry(
            avg_fps=20.0, p95_frame_ms=60.0, sample_count=20, window_s=2.0,
            source="mangohud-csv", confidence="high",
        ),
        foreground_process_age_s=100.0,
        foreground_runqueue_wait_ms_per_s=60.0,
        frame_feed_stalled=False,
    )
    base.update(over)
    return GamePowerSample(**base)


def tb_controller(ledger=None):
    return GamePowerController(
        GamePowerConfig(mode=GamePowerMode.TARGET_BALANCE, phase_stable_samples=1),
        verdict_ledger=ledger,
        verdict_env=ENV if ledger is not None else None,
    )


# ---------------------------------------------------------------------------
# Topology fingerprint + TDP bucket
# ---------------------------------------------------------------------------
def _policy(name, cpu, capacity, cls):
    return CpuPolicy(
        name=name,
        path=Path("/x") / name,
        affected_cpus=(cpu,),
        capacity=capacity,
        policy_class=cls,
        available_epp=(),
        current_epp=None,
        scaling_min_freq=None,
        scaling_max_freq=4_800_000 if cls == CpuPolicyClass.PCORE else 3_700_000,
    )


def _policy_ci(name, cpu, capacity, cls, *, scaling, cpuinfo):
    return CpuPolicy(
        name=name,
        path=Path("/x") / name,
        affected_cpus=(cpu,),
        capacity=capacity,
        policy_class=cls,
        available_epp=(),
        current_epp=None,
        scaling_min_freq=None,
        scaling_max_freq=scaling,
        cpuinfo_max_freq=cpuinfo,
    )


def test_c15_fingerprint_uses_immutable_cpuinfo_max_freq():
    base = [
        _policy_ci("policy0", 0, 1024, CpuPolicyClass.PCORE, scaling=4_800_000, cpuinfo=4_800_000),
        _policy_ci("policy1", 1, 676, CpuPolicyClass.ECORE, scaling=3_700_000, cpuinfo=3_700_000),
    ]
    # The ladder (or a user limit) has written scaling_max_freq to a cap; the
    # immutable cpuinfo_max_freq is unchanged, so the fingerprint must not move.
    capped = [
        _policy_ci("policy0", 0, 1024, CpuPolicyClass.PCORE, scaling=3_000_000, cpuinfo=4_800_000),
        _policy_ci("policy1", 1, 676, CpuPolicyClass.ECORE, scaling=3_700_000, cpuinfo=3_700_000),
    ]
    assert topology_fingerprint(base) == topology_fingerprint(capped)


def test_topology_fingerprint_is_deterministic_and_layout_labeled():
    policies = [
        _policy("policy0", 0, 1024, CpuPolicyClass.PCORE),
        _policy("policy1", 1, 1024, CpuPolicyClass.PCORE),
        _policy("policy2", 2, 676, CpuPolicyClass.ECORE),
    ]
    fp1 = topology_fingerprint(policies)
    fp2 = topology_fingerprint(list(reversed(policies)))
    assert fp1 == fp2
    assert fp1.startswith("2p1e-nosmt-")


def test_verdict_tdp_bucket_snaps_within_tolerance():
    assert verdict_tdp_bucket(17) == 17
    assert verdict_tdp_bucket(16) == 17
    assert verdict_tdp_bucket(11) == 12
    assert verdict_tdp_bucket(20) == 22  # |20-22| == 2, within tolerance
    assert verdict_tdp_bucket(25) is None  # 3W from 22, 5W from 30


# ---------------------------------------------------------------------------
# Verdict ledger fail-closed + exact match
# ---------------------------------------------------------------------------
def test_verdict_ledger_missing_is_fail_closed(tmp_path):
    ledger = GamePowerVerdictLedger(tmp_path / "absent.json")
    assert ledger.health()["status"] == "unavailable"
    assert not ledger.lookup(
        appid="1903340", fps_target=30, pl1_w=17, actuator="uclamp-min", env=ENV
    )


def test_verdict_ledger_corrupt_is_fail_closed(tmp_path):
    path = tmp_path / "verdicts.json"
    path.write_text("{not json")
    ledger = GamePowerVerdictLedger(path)
    assert ledger.health()["status"] == "corrupt"
    assert not ledger.lookup(
        appid="1903340", fps_target=30, pl1_w=17, actuator="uclamp-min", env=ENV
    )


def test_verdict_ledger_requires_exact_context_match(tmp_path):
    path = tmp_path / "verdicts.json"
    _write_ledger(path, [_better_entry("uclamp-min")])
    ledger = GamePowerVerdictLedger(path)

    assert ledger.lookup(
        appid="1903340", fps_target=30, pl1_w=17, actuator="uclamp-min", env=ENV
    )
    # Wrong actuator, fps, appid, or kernel all miss.
    assert not ledger.lookup(
        appid="1903340", fps_target=30, pl1_w=17, actuator="bg-weight", env=ENV
    )
    assert not ledger.lookup(
        appid="1903340", fps_target=60, pl1_w=17, actuator="uclamp-min", env=ENV
    )
    assert not ledger.lookup(
        appid="9999", fps_target=30, pl1_w=17, actuator="uclamp-min", env=ENV
    )
    other_kernel = GamePowerVerdictEnv(
        topology_fingerprint=ENV.topology_fingerprint, kernel="other"
    )
    assert not ledger.lookup(
        appid="1903340", fps_target=30, pl1_w=17, actuator="uclamp-min", env=other_kernel
    )


def test_verdict_ledger_ignores_non_better_entries(tmp_path):
    path = tmp_path / "verdicts.json"
    _write_ledger(path, [_better_entry("uclamp-min", verdict="INCONCLUSIVE")])
    ledger = GamePowerVerdictLedger(path)
    assert not ledger.lookup(
        appid="1903340", fps_target=30, pl1_w=17, actuator="uclamp-min", env=ENV
    )


def test_verdict_ledger_reloads_on_mtime_change(tmp_path):
    path = tmp_path / "verdicts.json"
    _write_ledger(path, [])
    ledger = GamePowerVerdictLedger(path)
    assert not ledger.lookup(
        appid="1903340", fps_target=30, pl1_w=17, actuator="uclamp-min", env=ENV
    )
    import os
    import time

    _write_ledger(path, [_better_entry("uclamp-min")])
    os.utime(path, (time.time() + 10, time.time() + 10))
    assert ledger.lookup(
        appid="1903340", fps_target=30, pl1_w=17, actuator="uclamp-min", env=ENV
    )


# ---------------------------------------------------------------------------
# Gated lanes in the controller
# ---------------------------------------------------------------------------
def test_cpu_bound_lane_blocked_without_verdict():
    controller = tb_controller(ledger=None)
    decision = controller.evaluate(cpu_bound_sample())
    assert decision.phase == GamePowerPhase.BELOW_TARGET_CPU_BOUND
    fg = decision.gated_lanes["foreground_uclamp_min"]
    assert fg["state"] == "blocked"
    assert decision.verdict_ledger_health["status"] == "unavailable"


def test_cpu_bound_uclamp_lane_active_with_matching_verdict(tmp_path):
    path = tmp_path / "verdicts.json"
    _write_ledger(path, [_better_entry("uclamp-min")])
    controller = tb_controller(ledger=GamePowerVerdictLedger(path))

    decision = controller.evaluate(cpu_bound_sample())

    assert decision.gated_lanes["foreground_uclamp_min"]["state"] == "active"
    # The color ledger for a color-A role now shows the actuator unlocked.
    assert decision.verdict_ledger_health["status"] == "ready"


def at_target_sample() -> GamePowerSample:
    return GamePowerSample(
        appid="1903340",
        rapl=RaplPowerWindow(duration_s=2.0, package_w=17.0, core_w=6.0, uncore_w=3.0),
        pl1_w=17,
        fdinfo_busy={"render": 0.5},
        frame_target=FrameTargetTelemetry(fps_target=30.0, source="manual", confidence="high"),
        frame_performance=FramePerformanceTelemetry(
            avg_fps=33.0, p95_frame_ms=35.0, sample_count=20, window_s=2.0,
            source="mangohud-csv", confidence="high",
        ),
        foreground_process_age_s=200.0,
        foreground_runqueue_wait_ms_per_s=5.0,
        frame_feed_stalled=False,
    )


def test_ladder_s5_unlocks_only_with_ladder_step_5_verdict(tmp_path):
    path = tmp_path / "verdicts.json"
    _write_ledger(path, [_better_entry("ladder-step-5")])
    config = GamePowerConfig(
        mode=GamePowerMode.TARGET_BALANCE, phase_stable_samples=1, ladder_hold_samples=1
    )
    controller = GamePowerController(
        config, verdict_ledger=GamePowerVerdictLedger(path), verdict_env=ENV
    )

    last = None
    for _ in range(10):
        last = controller.evaluate(at_target_sample())

    # V10: battery base sequence is 8 rungs; the deep verdict unlocks the two
    # gated CPU-cap rungs (S3CAP/S4CAP), so the ladder reaches step 10.
    assert controller.ladder_step == 10
    assert last.gated_lanes["ladder_deep_step"]["state"] == "active"


def test_ladder_stops_at_base_top_without_deep_verdict():
    config = GamePowerConfig(
        mode=GamePowerMode.TARGET_BALANCE, phase_stable_samples=1, ladder_hold_samples=1
    )
    controller = GamePowerController(config)

    last = None
    for _ in range(9):
        last = controller.evaluate(at_target_sample())

    # Without a deep verdict the ladder tops out at the base sequence (step 8).
    assert controller.ladder_step == 8
    assert "no-verdict-for-context" in last.phase_reason_codes


def test_gpu_cap_verdict_unlocks_deep_g4cap_rung(tmp_path):
    # D3: the -45% GPU cap depth (below the measured pacing plateau) is only
    # reachable through the G4CAP rung, unlocked by a ``gpu-cap`` BETTER verdict
    # -- the same mechanism as ladder-step-5 for S3CAP/S4CAP. The daemon now
    # consumes gpu-cap verdicts.
    from dataclasses import replace

    path = tmp_path / "verdicts.json"
    _write_ledger(path, [_better_entry("gpu-cap")])
    config = GamePowerConfig(
        mode=GamePowerMode.TARGET_BALANCE, phase_stable_samples=1, ladder_hold_samples=1
    )
    controller = GamePowerController(
        config, verdict_ledger=GamePowerVerdictLedger(path), verdict_env=ENV
    )

    last = None
    sample = replace(at_target_sample(), gpu_rp0_mhz=1950)
    for _ in range(9):
        last = controller.evaluate(sample)

    # 8 base rungs + G4CAP (no CPU-cap verdict -> S3CAP/S4CAP stay locked).
    assert controller.ladder_step == 9
    assert last.trim_rungs_active[-1] == "G4CAP"
    # G4CAP deepens the cap to rp0 * (1 - 0.45); D6: the fold carries the ratio,
    # the actuator derives the per-GT absolute cap (render gt0 -> int(1950*0.55)).
    assert last.actuation.gpu_max_ratio == 0.45
    assert last.gated_lanes["ladder_deep_step"]["state"] == "active"


def test_gpu_cap_and_ladder5_verdicts_unlock_g4cap_before_cpu_caps(tmp_path):
    path = tmp_path / "verdicts.json"
    _write_ledger(path, [_better_entry("gpu-cap"), _better_entry("ladder-step-5")])
    config = GamePowerConfig(
        mode=GamePowerMode.TARGET_BALANCE, phase_stable_samples=1, ladder_hold_samples=1
    )
    controller = GamePowerController(
        config, verdict_ledger=GamePowerVerdictLedger(path), verdict_env=ENV
    )

    last = None
    for _ in range(11):
        last = controller.evaluate(at_target_sample())

    # 8 base + G4CAP + S3CAP + S4CAP; the GPU deep rung precedes the CPU caps.
    assert controller.ladder_step == 11
    assert list(last.trim_rungs_active[-3:]) == ["G4CAP", "S3CAP", "S4CAP"]


def test_g4cap_locked_without_gpu_cap_verdict_even_with_profiler_flag(tmp_path):
    # --allow-ladder-step-5 unlocks only the CPU deep caps; G4CAP needs its own
    # gpu-cap verdict (no profiler shortcut shares the flag).
    config = GamePowerConfig(
        mode=GamePowerMode.TARGET_BALANCE,
        phase_stable_samples=1,
        ladder_hold_samples=1,
        allow_ladder_step_5=True,
    )
    controller = GamePowerController(config)

    last = None
    for _ in range(11):
        last = controller.evaluate(at_target_sample())

    assert controller.ladder_step == 10  # 8 base + S3CAP + S4CAP only
    assert "G4CAP" not in (last.trim_rungs_active or [])


def test_loading_releases_all_gated_lanes(tmp_path):
    path = tmp_path / "verdicts.json"
    _write_ledger(path, [_better_entry("uclamp-min"), _better_entry("bg-weight")])
    controller = tb_controller(ledger=GamePowerVerdictLedger(path))

    loading = cpu_bound_sample(foreground_process_age_s=5.0)  # launch grace
    decision = controller.evaluate(loading)

    assert decision.phase == GamePowerPhase.LOADING
    assert decision.gated_lanes["foreground_uclamp_min"]["state"] == "released"
    assert decision.gated_lanes["background_shaping"]["state"] == "released"


# ---------------------------------------------------------------------------
# Governor gated cgroup writes + restore parity
# ---------------------------------------------------------------------------
class FakeCgroupWriter:
    def __init__(self):
        self.calls = []
        self.failed = False
        self._fg = False
        self._bg = False

    def apply_foreground_uclamp(self, path):
        self.calls.append(("fg-apply", path))
        self._fg = True
        return True

    def restore_foreground_uclamp(self):
        if self._fg:
            self.calls.append(("fg-restore",))
            self._fg = False

    def apply_background(self, cgroups, *, appid, variants):
        self.calls.append(("bg-apply", appid, tuple(variants)))
        self._bg = True
        return True

    def restore_background(self):
        if self._bg:
            self.calls.append(("bg-restore",))
            self._bg = False

    def restore_all(self):
        self.restore_foreground_uclamp()
        self.restore_background()

    def reset(self):
        self.restore_all()


class ListObserver:
    def __init__(self, samples):
        self.samples = list(samples)

    async def sample(self):
        return self.samples.pop(0)


class NoopActuator:
    def __init__(self):
        self.value = object()

    def snapshot(self):
        return self.value

    def apply(self, **kwargs):
        pass

    def restore(self, snapshot):
        pass


def test_governor_applies_and_restores_gated_uclamp_lane(tmp_path):
    path = tmp_path / "verdicts.json"
    _write_ledger(path, [_better_entry("uclamp-min")])
    ledger = GamePowerVerdictLedger(path)

    active = cpu_bound_sample(foreground_cgroup_path="/sys/fs/cgroup/fg")
    observer = ListObserver(
        [active, active, GamePowerSample(appid=None, rapl=None, pl1_w=None)]
    )
    writer = FakeCgroupWriter()
    governor = GamePowerGovernor(
        config=GamePowerConfig(mode=GamePowerMode.TARGET_BALANCE, phase_stable_samples=1),
        observer=observer,
        actuator=NoopActuator(),
        verdict_ledger=ledger,
        verdict_env=ENV,
        cgroup_writer=writer,
    )

    asyncio.run(governor.run_iterations(3))
    governor.close()

    assert ("fg-apply", "/sys/fs/cgroup/fg") in writer.calls
    assert ("fg-restore",) in writer.calls
    # apply precedes restore.
    assert writer.calls.index(("fg-apply", "/sys/fs/cgroup/fg")) < writer.calls.index(
        ("fg-restore",)
    )


def test_governor_never_writes_gated_lane_without_verdict(tmp_path):
    path = tmp_path / "absent.json"  # missing -> fail-closed
    ledger = GamePowerVerdictLedger(path)
    active = cpu_bound_sample(foreground_cgroup_path="/sys/fs/cgroup/fg")
    observer = ListObserver([active, active])
    writer = FakeCgroupWriter()
    governor = GamePowerGovernor(
        config=GamePowerConfig(mode=GamePowerMode.TARGET_BALANCE, phase_stable_samples=1),
        observer=observer,
        actuator=NoopActuator(),
        verdict_ledger=ledger,
        verdict_env=ENV,
        cgroup_writer=writer,
    )

    asyncio.run(governor.run_iterations(2))

    assert not any(call[0] == "fg-apply" for call in writer.calls)


def _unknown_below_target_sample() -> GamePowerSample:
    return GamePowerSample(
        appid="1903340",
        rapl=RaplPowerWindow(duration_s=2.0, package_w=17.0, core_w=4.0, uncore_w=2.0),
        pl1_w=17,
        fdinfo_busy={"render": 0.5},
        frame_target=FrameTargetTelemetry(fps_target=30.0, source="manual", confidence="high"),
        frame_performance=FramePerformanceTelemetry(
            avg_fps=25.0, p95_frame_ms=45.0, sample_count=20, window_s=2.0,
            source="mangohud-csv", confidence="high",
        ),
        foreground_process_age_s=200.0,
        foreground_runqueue_wait_ms_per_s=5.0,
        frame_feed_stalled=False,
    )


def test_c4b_unknown_holds_gated_lanes(tmp_path):
    path = tmp_path / "verdicts.json"
    _write_ledger(path, [_better_entry("bg-weight")])
    controller = tb_controller(ledger=GamePowerVerdictLedger(path))

    d0 = controller.evaluate(at_target_sample())
    assert d0.gated_lanes["background_shaping"]["state"] == "active"

    d1 = controller.evaluate(_unknown_below_target_sample())
    assert d1.phase == GamePowerPhase.UNKNOWN
    assert d1.gated_lanes["background_shaping"]["state"] == "active"


def _bg_helper_cgroups(tmp_path):
    helper = tmp_path / "helper"
    helper.mkdir()
    (helper / "cpu.uclamp.max").write_text("max\n")
    (helper / "cpu.weight").write_text("100\n")
    # Non-user.slice path so cpu.weight writes go through the direct cgroup file
    # (not the systemd-user runuser path, which is unavailable off-device).
    return helper, [
        {"cgroup": "/app.slice/gamescope-session.service", "path": str(helper)}
    ]


def test_c10_restore_background_failure_latches_fail_closed(tmp_path):
    helper, cgroups = _bg_helper_cgroups(tmp_path)
    writer = _DaemonCgroupWriter()
    assert writer.apply_background(cgroups, appid="1903340", variants=["uclamp-max-85"])
    assert (helper / "cpu.uclamp.max").read_text().strip() == "85.00"

    # Break the control file so restore cannot succeed.
    ctrl = helper / "cpu.uclamp.max"
    ctrl.unlink()
    ctrl.mkdir()

    writer.restore_background()
    assert writer.failed is True
    # The unrestored report is kept for telemetry, not silently dropped.
    assert writer.background_reports


def test_c12_apply_background_reapplies_on_variant_change(tmp_path):
    helper, cgroups = _bg_helper_cgroups(tmp_path)
    writer = _DaemonCgroupWriter()
    assert writer.apply_background(cgroups, appid="1903340", variants=["uclamp-max-85"])
    assert (helper / "cpu.uclamp.max").read_text().strip() == "85.00"

    # A changed variant set must be applied (delta), not held/ignored.
    assert writer.apply_background(cgroups, appid="1903340", variants=["cpu-weight-80"])
    assert (helper / "cpu.weight").read_text().strip() == "80"
    # The old uclamp.max lane was restored as part of the switch.
    assert (helper / "cpu.uclamp.max").read_text().strip() == "max"


def test_c11_active_actuators_reflect_only_unlocked_variant(tmp_path):
    path = tmp_path / "verdicts.json"
    _write_ledger(path, [_better_entry("bg-uclamp")])  # only bg-uclamp unlocked
    controller = tb_controller(ledger=GamePowerVerdictLedger(path))
    controller.evaluate(at_target_sample())
    assert "bg-uclamp" in controller._active_actuators
    assert "bg-weight" not in controller._active_actuators


def test_c5_ladder_clamps_when_deep_verdict_lock_lost(tmp_path):
    from dataclasses import replace

    path = tmp_path / "verdicts.json"
    _write_ledger(path, [_better_entry("ladder-step-5")])
    config = GamePowerConfig(
        mode=GamePowerMode.TARGET_BALANCE, phase_stable_samples=1, ladder_hold_samples=1
    )
    controller = GamePowerController(
        config, verdict_ledger=GamePowerVerdictLedger(path), verdict_env=ENV
    )
    for _ in range(10):
        controller.evaluate(at_target_sample())
    assert controller.ladder_step == 10
    off = replace(at_target_sample(), pl1_w=40)
    decision = controller.evaluate(off)
    # Deep verdict no longer matches -> clamp down to the base-sequence top (8).
    assert controller.ladder_step == 8
    assert "ladder-verdict-lock-lost" in decision.phase_reason_codes
