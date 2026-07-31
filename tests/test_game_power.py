import asyncio
import json
import math
from dataclasses import replace
from pathlib import Path

from steamos_intel_handheld import game_power
from steamos_intel_handheld.game_power import (
    CpuPolicyActuator,
    CpuPolicyClass,
    CpuPolicySnapshot,
    EnergyReading,
    FramePerformanceTelemetry,
    FrameTargetTelemetry,
    GamePowerAction,
    GamePowerActuation,
    GamePowerClassification,
    GamePowerConfig,
    GamePowerController,
    GamePowerGovernor,
    GamePowerMode,
    GamePowerPhase,
    GamePowerSample,
    GameProcess,
    PressureSignal,
    PressureTelemetry,
    RaplObserver,
    RaplPowerWindow,
    classify_game_power_phase,
    classify_game_power_sample,
    compute_fdinfo_busy,
    compute_foreground_runqueue_wait_ms_per_s,
    compute_rapl_power_window,
    discover_cpu_policies,
    find_steam_game_processes,
    parse_fdinfo_engine_times,
    parse_pressure_signal,
    parse_proc_stat_starttime_ticks,
    parse_thread_schedstat,
    read_process_age_s,
    resolve_cgroup_v2_path,
)


def test_compute_rapl_power_window_converts_energy_delta_to_watts():
    start = EnergyReading(
        timestamp_s=10.0,
        energy_uj={
            "package": 100_000_000,
            "core": 40_000_000,
            "uncore": 30_000_000,
            "dram": 2_000_000,
            "psys": 140_000_000,
        },
    )
    end = EnergyReading(
        timestamp_s=20.0,
        energy_uj={
            "package": 319_111_133,
            "core": 125_587_122,
            "uncore": 104_545_525,
            "dram": 6_523_548,
            "psys": 450_515_380,
        },
    )

    window = compute_rapl_power_window(start, end)

    assert isinstance(window, RaplPowerWindow)
    assert window.duration_s == 10.0
    assert round(window.package_w, 2) == 21.91
    assert round(window.core_w, 2) == 8.56
    assert round(window.uncore_w, 2) == 7.45
    assert round(window.dram_w, 2) == 0.45
    assert round(window.psys_w, 2) == 31.05
    assert round(window.core_share, 2) == 0.39
    assert round(window.uncore_share, 2) == 0.34


def test_compute_rapl_power_window_rejects_non_positive_duration():
    start = EnergyReading(timestamp_s=10.0, energy_uj={"package": 1})
    end = EnergyReading(timestamp_s=10.0, energy_uj={"package": 2})

    try:
        compute_rapl_power_window(start, end)
    except ValueError as exc:
        assert "positive duration" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_game_power_mode_values_are_stable_for_cli_and_service_config():
    assert [mode.value for mode in GamePowerMode] == [
        "off",
        "observe",
        "gpu-priority",
        "target-balance",
    ]


def make_cpu_policy(
    sysfs_root: Path,
    index: int,
    *,
    cpu: int,
    capacity: int,
    epp: str = "balance_performance",
    max_freq: int = 4_800_000,
    min_freq: int = 400_000,
):
    policy = sysfs_root / "devices" / "system" / "cpu" / "cpufreq" / f"policy{index}"
    policy.mkdir(parents=True)
    (policy / "affected_cpus").write_text(str(cpu))
    (policy / "related_cpus").write_text(str(cpu))
    (policy / "energy_performance_preference").write_text(epp)
    (policy / "energy_performance_available_preferences").write_text(
        "default performance balance_performance balance_power power"
    )
    (policy / "scaling_max_freq").write_text(str(max_freq))
    (policy / "scaling_min_freq").write_text(str(min_freq))
    cpu_root = sysfs_root / "devices" / "system" / "cpu" / f"cpu{cpu}"
    cpu_root.mkdir(parents=True)
    (cpu_root / "cpu_capacity").write_text(str(capacity))
    return policy


def test_f1_discover_classifies_real_lunar_lake_capacities(tmp_path):
    # MSI Claw 8 AI+ measured capacities: cpu0/1=1005, cpu2/3=1024, cpu4-7=676.
    # 1005/1024 = 0.98 >= PCORE_CAPACITY_RATIO -> PCORE (not ECORE).
    sysfs_root = tmp_path / "sys"
    capacities = [1005, 1005, 1024, 1024, 676, 676, 676, 676]
    for cpu, capacity in enumerate(capacities):
        make_cpu_policy(sysfs_root, cpu, cpu=cpu, capacity=capacity)

    policies = discover_cpu_policies(sysfs_root)

    classes = [policy.policy_class for policy in policies]
    assert classes[:4] == [CpuPolicyClass.PCORE] * 4
    assert classes[4:] == [CpuPolicyClass.ECORE] * 4


def test_f1_discover_homogeneous_capacity_is_all_pcore(tmp_path):
    sysfs_root = tmp_path / "sys"
    for cpu in range(4):
        make_cpu_policy(sysfs_root, cpu, cpu=cpu, capacity=1024)

    policies = discover_cpu_policies(sysfs_root)

    assert [policy.policy_class for policy in policies] == [CpuPolicyClass.PCORE] * 4


def test_f1_discover_two_tier_classic_split(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_cpu_policy(sysfs_root, 0, cpu=0, capacity=1024)
    make_cpu_policy(sysfs_root, 1, cpu=1, capacity=676, max_freq=3_700_000)

    policies = discover_cpu_policies(sysfs_root)

    assert policies[0].policy_class == CpuPolicyClass.PCORE
    assert policies[1].policy_class == CpuPolicyClass.ECORE


def test_discover_cpu_policies_classifies_highest_capacity_as_pcore(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_cpu_policy(sysfs_root, 0, cpu=0, capacity=1024)
    make_cpu_policy(sysfs_root, 1, cpu=1, capacity=676, max_freq=3_700_000)

    policies = discover_cpu_policies(sysfs_root)

    assert [policy.name for policy in policies] == ["policy0", "policy1"]
    assert policies[0].policy_class == CpuPolicyClass.PCORE
    assert policies[1].policy_class == CpuPolicyClass.ECORE
    assert policies[0].current_epp == "balance_performance"
    assert policies[1].scaling_max_freq == 3_700_000


def test_cpu_policy_actuator_applies_and_restores_epp_and_frequency_caps(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_cpu_policy(sysfs_root, 0, cpu=0, capacity=1024, max_freq=4_800_000)
    make_cpu_policy(sysfs_root, 1, cpu=1, capacity=676, max_freq=3_700_000)
    policies = discover_cpu_policies(sysfs_root)
    actuator = CpuPolicyActuator(policies)

    snapshot = actuator.snapshot()
    actuator.apply(epp="balance_power", pcore_max_khz=3_200_000, ecore_max_khz=2_800_000)

    assert isinstance(snapshot, CpuPolicySnapshot)
    assert (policies[0].path / "energy_performance_preference").read_text() == "balance_power"
    assert (policies[1].path / "energy_performance_preference").read_text() == "balance_power"
    assert (policies[0].path / "scaling_max_freq").read_text() == "3200000"
    assert (policies[1].path / "scaling_max_freq").read_text() == "2800000"

    actuator.restore(snapshot)

    assert (
        policies[0].path / "energy_performance_preference"
    ).read_text() == "balance_performance"
    assert (
        policies[1].path / "energy_performance_preference"
    ).read_text() == "balance_performance"
    assert (policies[0].path / "scaling_max_freq").read_text() == "4800000"
    assert (policies[1].path / "scaling_max_freq").read_text() == "3700000"


class _StickyControlFile:
    """Simulates a sysfs control file whose write silently does not persist."""

    def __init__(self, value, sticky=False):
        self.value = value
        self.sticky = sticky
        self.writes = 0

    def read_text(self):
        return self.value

    def write_text(self, value):
        self.writes += 1
        if not self.sticky:
            self.value = value

    def __str__(self):
        return f"sticky:{id(self)}"


class _FakeControlDir:
    def __init__(self, files):
        self.files = files

    def __truediv__(self, name):
        return self.files[name]


def _fake_policy(name, files, policy_class=CpuPolicyClass.PCORE):
    return game_power.CpuPolicy(
        name=name,
        path=_FakeControlDir(files),
        affected_cpus=(0,),
        capacity=1024,
        policy_class=policy_class,
        available_epp=("performance", "balance_performance", "balance_power"),
        current_epp="balance_performance",
        scaling_min_freq=400_000,
        scaling_max_freq=4_800_000,
    )


def test_f2_actuator_restore_verifies_readback_retries_and_reports(tmp_path):
    sticky_freq = _StickyControlFile("3000000", sticky=True)
    good_epp = _StickyControlFile("balance_power")
    policy = _fake_policy(
        "policy0",
        {
            "scaling_max_freq": sticky_freq,
            "energy_performance_preference": good_epp,
        },
    )
    actuator = CpuPolicyActuator([policy])
    snapshot = CpuPolicySnapshot(
        values={"policy0": ("balance_performance", 4_800_000)}
    )

    failed = actuator.restore(snapshot)

    # The sticky freq write was retried once, then reported as failed.
    assert sticky_freq.writes == 2
    assert failed == [str(sticky_freq)]
    # The EPP write next to it still restored.
    assert good_epp.value == "balance_performance"


def test_f2_actuator_restore_writes_freq_before_epp_and_skips_matching(tmp_path):
    order = []

    class OrderedFile(_StickyControlFile):
        def __init__(self, value, label):
            super().__init__(value)
            self.label = label

        def write_text(self, value):
            order.append(self.label)
            super().write_text(value)

    freq = OrderedFile("3000000", "freq")
    epp = OrderedFile("balance_power", "epp")
    matching = OrderedFile("4800000", "freq-match")
    policy0 = _fake_policy(
        "policy0", {"scaling_max_freq": freq, "energy_performance_preference": epp}
    )
    policy1 = _fake_policy(
        "policy1",
        {
            "scaling_max_freq": matching,
            "energy_performance_preference": OrderedFile("balance_performance", "epp-match"),
        },
    )
    actuator = CpuPolicyActuator([policy0, policy1])
    snapshot = CpuPolicySnapshot(
        values={
            "policy0": ("balance_performance", 4_800_000),
            "policy1": ("balance_performance", 4_800_000),
        }
    )

    failed = actuator.restore(snapshot)

    assert failed == []
    # F2.3: freq restored before EPP within a policy; pre-matching values skipped.
    assert order == ["freq", "epp"]


def test_f2_actuator_restore_clean_returns_no_failures(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_cpu_policy(sysfs_root, 0, cpu=0, capacity=1024, max_freq=4_800_000)
    policies = discover_cpu_policies(sysfs_root)
    actuator = CpuPolicyActuator(policies)
    snapshot = actuator.snapshot()
    actuator.apply(epp="balance_power", pcore_max_khz=3_000_000)

    assert actuator.restore(snapshot) == []
    assert (policies[0].path / "scaling_max_freq").read_text() == "4800000"


def make_rapl_domain(sysfs_root: Path, domain: str, name: str, energy_uj: int):
    path = sysfs_root / "class" / "powercap" / domain
    path.mkdir(parents=True)
    (path / "name").write_text(name)
    (path / "energy_uj").write_text(str(energy_uj))
    return path


def test_rapl_observer_reads_named_domains(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_rapl_domain(sysfs_root, "intel-rapl:0", "package-0", 100)
    make_rapl_domain(sysfs_root, "intel-rapl:0:0", "core", 40)
    make_rapl_domain(sysfs_root, "intel-rapl:0:1", "uncore", 30)
    make_rapl_domain(sysfs_root, "intel-rapl:1", "psys", 140)

    reading = RaplObserver(sysfs_root=sysfs_root, clock=lambda: 5.0).read()

    assert reading.timestamp_s == 5.0
    assert reading.energy_uj == {
        "package": 100,
        "core": 40,
        "uncore": 30,
        "psys": 140,
    }


def test_parse_fdinfo_engine_times_reads_drm_engine_keys():
    fdinfo = """
drm-engine-render: 123456789 ns
drm-engine-copy: 999 ns
drm-engine-compute: 234000000 ns
drm-total-engine-render: 200000000 ns
"""

    parsed = parse_fdinfo_engine_times(fdinfo)

    assert parsed["render"] == 123456789
    assert parsed["compute"] == 234000000
    assert parsed["total-render"] == 200000000


def test_compute_fdinfo_busy_uses_nanosecond_delta_over_window():
    start = {"render": 1_000_000_000, "compute": 500_000_000}
    end = {"render": 2_500_000_000, "compute": 1_000_000_000}

    busy = compute_fdinfo_busy(start, end, duration_s=2.0)

    assert busy["render"] == 0.75
    assert busy["compute"] == 0.25


def make_sample(
    *,
    appid: str | None = "1091500",
    package_w: float = 22.0,
    core_w: float = 8.8,
    uncore_w: float = 7.4,
    pl1_w: int = 22,
    render_busy: float | None = 0.75,
    frame_target: FrameTargetTelemetry | None = None,
    frame_performance: FramePerformanceTelemetry | None = None,
):
    return GamePowerSample(
        appid=appid,
        rapl=RaplPowerWindow(
            duration_s=2.0,
            package_w=package_w,
            core_w=core_w,
            uncore_w=uncore_w,
            dram_w=0.4,
            psys_w=31.0,
        ),
        pl1_w=pl1_w,
        fdinfo_busy={"render": render_busy} if render_busy is not None else {},
        frame_target=frame_target,
        frame_performance=frame_performance,
    )


def frame_target_40():
    return FrameTargetTelemetry(fps_target=40.0, source="manual", confidence="high")


def frame_performance(
    *,
    avg_fps: float,
    p95_frame_ms: float,
    sample_count: int = 20,
):
    return FramePerformanceTelemetry(
        avg_fps=avg_fps,
        p95_frame_ms=p95_frame_ms,
        sample_count=sample_count,
        window_s=2.0,
        source="mangohud-csv",
        confidence="high",
    )


def test_controller_waits_for_hysteresis_before_applying_gpu_priority():
    controller = GamePowerController(GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY))

    first = controller.evaluate(make_sample())
    second = controller.evaluate(make_sample())

    assert first.action == GamePowerAction.OBSERVE_ONLY
    assert second.action == GamePowerAction.GPU_PRIORITY_EPP


def test_controller_suppresses_gpu_priority_when_fps_target_is_satisfied():
    config = GamePowerConfig(
        mode=GamePowerMode.GPU_PRIORITY,
        activate_samples=1,
        rolling_window_samples=1,
    )
    controller = GamePowerController(config)

    decision = controller.evaluate(
        make_sample(
            frame_target=frame_target_40(),
            frame_performance=frame_performance(avg_fps=56.0, p95_frame_ms=22.0),
        )
    )

    assert decision.action == GamePowerAction.OBSERVE_ONLY
    assert decision.reason == "fps target satisfied"
    assert decision.classification is not None
    assert decision.classification.primary == "fps-target-satisfied"
    assert decision.classification.evidence["fps_target_ratio"] == 1.4
    assert controller.last_positive is False


def test_controller_keeps_gpu_priority_when_fps_target_is_not_satisfied():
    config = GamePowerConfig(
        mode=GamePowerMode.GPU_PRIORITY,
        activate_samples=1,
        rolling_window_samples=1,
    )
    controller = GamePowerController(config)

    decision = controller.evaluate(
        make_sample(
            package_w=12.0,
            core_w=4.3,
            uncore_w=3.8,
            pl1_w=12,
            frame_target=frame_target_40(),
            frame_performance=frame_performance(avg_fps=34.0, p95_frame_ms=35.0),
        )
    )

    assert decision.action == GamePowerAction.GPU_PRIORITY_EPP
    assert decision.classification is not None
    assert decision.classification.primary == "gpu-package-bound"


def test_controller_preserves_gpu_priority_without_frame_performance_telemetry():
    config = GamePowerConfig(
        mode=GamePowerMode.GPU_PRIORITY,
        activate_samples=1,
        rolling_window_samples=1,
    )
    controller = GamePowerController(config)

    decision = controller.evaluate(make_sample(frame_target=frame_target_40()))

    assert decision.action == GamePowerAction.GPU_PRIORITY_EPP
    assert decision.reason == "package limited with GPU activity"


def test_active_controller_restores_after_target_satisfied_hysteresis():
    config = GamePowerConfig(
        mode=GamePowerMode.GPU_PRIORITY,
        activate_samples=1,
        restore_samples=2,
        rolling_window_samples=1,
    )
    controller = GamePowerController(config)

    active = controller.evaluate(
        make_sample(
            frame_target=frame_target_40(),
            frame_performance=frame_performance(avg_fps=34.0, p95_frame_ms=35.0),
        )
    )
    first_satisfied = controller.evaluate(
        make_sample(
            frame_target=frame_target_40(),
            frame_performance=frame_performance(avg_fps=56.0, p95_frame_ms=22.0),
        )
    )
    restored = controller.evaluate(
        make_sample(
            frame_target=frame_target_40(),
            frame_performance=frame_performance(avg_fps=57.0, p95_frame_ms=21.0),
        )
    )

    assert active.action == GamePowerAction.GPU_PRIORITY_EPP
    assert first_satisfied.action == GamePowerAction.OBSERVE_ONLY
    assert restored.action == GamePowerAction.RESTORE
    assert restored.reason == "restore hysteresis reached"


def test_controller_rolling_majority_blocks_activation_after_two_recent_positives():
    config = GamePowerConfig(
        mode=GamePowerMode.GPU_PRIORITY,
        activate_samples=2,
        rolling_window_samples=4,
    )
    controller = GamePowerController(config)

    decisions = [
        controller.evaluate(make_sample(appid=None)),
        controller.evaluate(make_sample(appid=None)),
        controller.evaluate(make_sample()),
        controller.evaluate(make_sample()),
    ]

    assert [decision.action for decision in decisions] == [
        GamePowerAction.OBSERVE_ONLY,
        GamePowerAction.OBSERVE_ONLY,
        GamePowerAction.OBSERVE_ONLY,
        GamePowerAction.OBSERVE_ONLY,
    ]
    assert decisions[-1].reason == "waiting for rolling activation evidence"


def test_active_controller_rolling_majority_blocks_restore_after_two_recent_negatives():
    config = GamePowerConfig(
        mode=GamePowerMode.GPU_PRIORITY,
        activate_samples=1,
        restore_samples=2,
        rolling_window_samples=5,
    )
    controller = GamePowerController(config)

    decisions = [
        controller.evaluate(make_sample()),
        controller.evaluate(make_sample()),
        controller.evaluate(make_sample()),
        controller.evaluate(make_sample(appid=None)),
        controller.evaluate(make_sample(appid=None)),
    ]

    assert decisions[0].action == GamePowerAction.GPU_PRIORITY_EPP
    assert decisions[-1].action == GamePowerAction.OBSERVE_ONLY
    assert decisions[-1].reason == "waiting for rolling restore evidence"


def test_rolling_window_zero_and_one_preserve_legacy_hysteresis():
    for rolling_window_samples in (0, 1):
        controller = GamePowerController(
            GamePowerConfig(
                mode=GamePowerMode.GPU_PRIORITY,
                activate_samples=2,
                rolling_window_samples=rolling_window_samples,
            )
        )

        decisions = [
            controller.evaluate(make_sample(appid=None)),
            controller.evaluate(make_sample(appid=None)),
            controller.evaluate(make_sample()),
            controller.evaluate(make_sample()),
        ]

        assert decisions[-1].action == GamePowerAction.GPU_PRIORITY_EPP


def test_controller_classification_evidence_includes_post_update_rolling_state():
    controller = GamePowerController(
        GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY, rolling_window_samples=4)
    )

    first = controller.evaluate(make_sample())
    second = controller.evaluate(make_sample())

    assert first.classification is not None
    assert first.classification.evidence["rolling_window_samples"] == 4
    assert first.classification.evidence["rolling_positive_samples"] == 1
    assert first.classification.evidence["rolling_negative_samples"] == 0
    assert first.classification.evidence["rolling_ready"] is False
    assert second.classification is not None
    assert second.classification.evidence["rolling_positive_samples"] == 2
    assert second.classification.evidence["rolling_ready"] is True


def test_controller_restores_after_consecutive_invalid_samples():
    controller = GamePowerController(GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY))
    controller.evaluate(make_sample())
    controller.evaluate(make_sample())

    assert controller.evaluate(make_sample(appid=None)).action == GamePowerAction.OBSERVE_ONLY
    assert controller.evaluate(make_sample(appid=None)).action == GamePowerAction.OBSERVE_ONLY
    assert controller.evaluate(make_sample(appid=None)).action == GamePowerAction.RESTORE


def test_controller_uses_cpu_cap_when_enabled_and_epp_is_not_enough():
    config = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY, cpu_cap_enabled=True)
    controller = GamePowerController(config)

    controller.evaluate(make_sample(core_w=10.0, render_busy=0.90))
    decision = controller.evaluate(make_sample(core_w=10.0, render_busy=0.90))

    assert decision.action == GamePowerAction.GPU_PRIORITY_CPU_CAP


def test_controller_cpu_cap_threshold_is_configurable_for_profile_sweeps():
    config = GamePowerConfig(
        mode=GamePowerMode.GPU_PRIORITY,
        cpu_cap_enabled=True,
        cpu_cap_core_share_threshold=0.30,
    )
    controller = GamePowerController(config)

    controller.evaluate(make_sample(core_w=6.8, package_w=22.0, render_busy=0.90))
    decision = controller.evaluate(make_sample(core_w=6.8, package_w=22.0, render_busy=0.90))

    assert decision.action == GamePowerAction.GPU_PRIORITY_CPU_CAP


def test_controller_does_not_restore_cpu_cap_only_because_cap_lowered_core_share():
    config = GamePowerConfig(
        mode=GamePowerMode.GPU_PRIORITY,
        cpu_cap_enabled=True,
        cpu_cap_core_share_threshold=0.30,
    )
    controller = GamePowerController(config)

    controller.evaluate(make_sample(core_w=7.2, package_w=22.0, uncore_w=8.6))
    assert (
        controller.evaluate(make_sample(core_w=7.2, package_w=22.0, uncore_w=8.6)).action
        == GamePowerAction.GPU_PRIORITY_CPU_CAP
    )
    low_core_samples = [
        controller.evaluate(make_sample(core_w=6.0, package_w=22.0, uncore_w=9.4))
        for _ in range(config.restore_samples)
    ]

    assert [sample.action for sample in low_core_samples] == [
        GamePowerAction.GPU_PRIORITY_EPP,
        GamePowerAction.GPU_PRIORITY_EPP,
        GamePowerAction.GPU_PRIORITY_EPP,
    ]


def test_controller_allows_initial_epp_activation_without_high_core_share():
    config = GamePowerConfig(
        mode=GamePowerMode.GPU_PRIORITY,
        cpu_cap_enabled=True,
        cpu_cap_core_share_threshold=0.30,
    )
    controller = GamePowerController(config)

    first = controller.evaluate(make_sample(core_w=6.0, package_w=22.0, uncore_w=9.4))
    second = controller.evaluate(make_sample(core_w=6.0, package_w=22.0, uncore_w=9.4))

    assert first.action == GamePowerAction.OBSERVE_ONLY
    assert second.action == GamePowerAction.GPU_PRIORITY_EPP


class FakeObserver:
    def __init__(self, samples):
        self.samples = list(samples)

    async def sample(self):
        return self.samples.pop(0)


class RecordingActuator:
    def __init__(self):
        self.events = []
        self.snapshot_value = object()

    def snapshot(self):
        self.events.append(("snapshot",))
        return self.snapshot_value

    def apply(
        self,
        *,
        epp=None,
        pcore_epp=None,
        ecore_epp=None,
        pcore_max_khz=None,
        ecore_max_khz=None,
    ):
        if pcore_epp is None and ecore_epp is None:
            self.events.append(("apply", epp, pcore_max_khz, ecore_max_khz))
        else:
            self.events.append(
                ("apply-per-class", pcore_epp, ecore_epp, pcore_max_khz, ecore_max_khz)
            )

    def restore(self, snapshot):
        self.events.append(("restore", snapshot))


class FailingActuator(RecordingActuator):
    def apply(
        self,
        *,
        epp=None,
        pcore_epp=None,
        ecore_epp=None,
        pcore_max_khz=None,
        ecore_max_khz=None,
    ):
        if pcore_epp is None and ecore_epp is None:
            self.events.append(("apply-failed", epp, pcore_max_khz, ecore_max_khz))
        else:
            self.events.append(
                ("apply-failed-per-class", pcore_epp, ecore_epp, pcore_max_khz, ecore_max_khz)
            )
        raise OSError("simulated sysfs write failure")


class RestoreFailingActuator(RecordingActuator):
    def restore(self, snapshot):
        self.events.append(("restore-failed", snapshot))
        raise OSError("simulated restore failure")


class PartialRestoreActuator(RecordingActuator):
    """Restore reports one silently-unrestored control file (F2)."""

    def restore(self, snapshot):
        self.events.append(("restore", snapshot))
        return ["/sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq"]


def test_f2_governor_latches_fail_closed_on_partial_restore(capsys):
    # Tick 1 applies the loading boost; tick 2 exhausts the boost budget and
    # emits a None actuation, driving the _apply_cpu_intent restore branch.
    observer = FakeObserver([phase_sample(age=5.0), phase_sample(age=5.0)])
    actuator = PartialRestoreActuator()
    governor = GamePowerGovernor(
        config=tb_config(loading_boost_max_s=3.0, poll_s=2.0),
        observer=observer,
        actuator=actuator,
    )
    asyncio.run(governor.run_iterations(2))

    err = capsys.readouterr().err
    assert "game-power: restore-mismatch" in err
    assert "policy0/scaling_max_freq" in err
    assert governor._write_failed is True


def test_f2_governor_restore_outcome_reports_failure(capsys):
    actuator = PartialRestoreActuator()
    governor = GamePowerGovernor(
        config=tb_config(), observer=FakeObserver([]), actuator=actuator
    )
    governor._snapshot = actuator.snapshot()
    governor._applied_actuation = GamePowerActuation(pcore_epp="performance")

    outcome = governor.restore()

    assert outcome.attempted is True
    assert outcome.succeeded is False
    assert "restore-mismatch" in outcome.reason
    assert governor._write_failed is True
    err = capsys.readouterr().err
    assert "game-power: restore-mismatch" in err


def test_f2_governor_clean_restore_has_no_stderr(capsys):
    actuator = RecordingActuator()
    governor = GamePowerGovernor(
        config=tb_config(), observer=FakeObserver([]), actuator=actuator
    )
    governor._snapshot = actuator.snapshot()

    outcome = governor.restore()

    assert outcome.attempted is True
    assert outcome.succeeded is True
    assert capsys.readouterr().err == ""
    assert governor._write_failed is False



def make_hint_context(**overrides):
    values = {
        "appid": "1091500",
        "pl1_w": 22,
        "power_source": "battery",
        "fps_target": "60",
        "topology_signature": "p|e|with|delimiters",
        "os_signature": "kernel=6.16;driver=xe",
        "runtime_signature": "unavailable",
        "runtime_signature_known": False,
        "policy_version": "game-power-sampling-v1",
        "complete": True,
    }
    values.update(overrides)
    return game_power.GamePowerHintContext(**values)


def make_session_summary(context=None, **overrides):
    values = {
        "context": context or make_hint_context(),
        "started_s": 1.0,
        "samples": 1,
        "positive_samples": 1,
        "negative_samples": 0,
        "applied_samples": 1,
        "restored_samples": 1,
        "cpu_cap_samples": 0,
        "contradiction_samples": 0,
        "hint_was_used": False,
        "hint_disabled": False,
        "hint_disable_reason": None,
        "write_failed": False,
        "restore_attempted": True,
        "restore_succeeded": True,
        "restore_error": None,
    }
    values.update(overrides)
    return game_power.GamePowerSessionSummary(**values)


def test_hint_context_key_uses_canonical_json_hash_and_rejects_mismatch():
    context = make_hint_context()
    same_context = make_hint_context()
    changed_context = make_hint_context(topology_signature="p|different")

    key = game_power.canonical_hint_key(context)

    assert key.startswith("game-power-context-v1:")
    assert len(key.removeprefix("game-power-context-v1:")) == 64
    assert key == game_power.canonical_hint_key(same_context)
    assert key != game_power.canonical_hint_key(changed_context)


def test_pl1_bucket_rounding_is_deterministic():
    assert game_power.pl1_bucket_w(21.49) == 21
    assert game_power.pl1_bucket_w(21.5) == 22
    assert game_power.pl1_bucket_w(22.01) == 22
    assert game_power.pl1_bucket_w(0.49) is None
    assert game_power.pl1_bucket_w(None) is None


def test_hint_store_promotes_after_two_clean_matching_sessions(tmp_path):
    context = make_hint_context()
    policy = game_power.GamePowerHintPolicy(
        min_hint_sessions=2,
        min_hint_samples=2,
        min_hint_positive_ratio=0.50,
    )
    store = game_power.GamePowerHintStore(tmp_path / "hints.json", policy=policy)

    first = store.record_session(make_session_summary(context))
    second = store.record_session(make_session_summary(context))

    assert first.aggregate_updated is True
    assert first.hint_promoted is False
    assert second.aggregate_updated is True
    assert second.hint_promoted is True
    assert store.get_hint(context) is not None


def test_hint_store_ignores_oversized_invalid_and_malformed_cache(tmp_path):
    oversized = tmp_path / "oversized.json"
    oversized.write_text("x" * 64)
    small_policy = game_power.GamePowerHintPolicy(max_hint_cache_bytes=16)

    oversized_store = game_power.GamePowerHintStore(oversized, policy=small_policy)

    assert oversized_store.load_error == "cache_over_budget"
    assert oversized_store.get_hint(make_hint_context()) is None

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not json")
    invalid_store = game_power.GamePowerHintStore(invalid)

    assert invalid_store.load_error == "invalid_json"
    assert invalid.read_text() == "{not json"

    malformed = tmp_path / "malformed.json"
    malformed.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "policy_version": "game-power-sampling-v1",
                "aggregates": {"bad": {"context": {"appid": "1091500"}}},
                "entries": {"bad": {"preferred_mode": "gpu-priority"}},
            }
        )
    )
    malformed_store = game_power.GamePowerHintStore(malformed)

    assert malformed_store.get_hint(make_hint_context()) is None


def test_hint_store_prunes_oldest_records_and_respects_entry_limit(tmp_path):
    policy = game_power.GamePowerHintPolicy(
        min_hint_sessions=1,
        min_hint_samples=1,
        min_hint_positive_ratio=0.50,
        max_hint_entries=1,
        max_aggregate_records=1,
    )
    store = game_power.GamePowerHintStore(tmp_path / "hints.json", policy=policy)
    old_context = make_hint_context(appid="111")
    new_context = make_hint_context(appid="222")

    store.record_session(make_session_summary(old_context, started_s=1.0))
    store.record_session(make_session_summary(new_context, started_s=2.0))

    assert store.get_hint(old_context) is None
    assert store.get_hint(new_context) is not None


def test_matching_hint_reduces_warmup_but_requires_current_positive_sample(tmp_path):
    context = make_hint_context()
    store = game_power.GamePowerHintStore(
        tmp_path / "hints.json",
        policy=game_power.GamePowerHintPolicy(
            min_hint_sessions=1,
            min_hint_samples=1,
            min_hint_positive_ratio=0.50,
        ),
    )
    store.record_session(make_session_summary(context))
    hint = store.get_hint(context)
    assert hint is not None
    controller = GamePowerController(
        GamePowerConfig(
            mode=GamePowerMode.GPU_PRIORITY,
            activate_samples=2,
            hinted_activate_samples=0,
            rolling_window_samples=1,
        ),
        hint=hint,
    )

    negative = controller.evaluate(make_sample(appid=None))
    positive = controller.evaluate(make_sample())

    assert negative.action == GamePowerAction.OBSERVE_ONLY
    assert positive.action == GamePowerAction.GPU_PRIORITY_EPP
    assert positive.reason == "validated hint reduced activation warmup"
    assert positive.classification is not None
    assert positive.classification.evidence["hint_used"] is True
    assert positive.classification.evidence["activation_required_samples"] == 1


def test_controller_disables_hint_after_same_game_contradiction_samples(tmp_path):
    context = make_hint_context()
    store = game_power.GamePowerHintStore(
        tmp_path / "hints.json",
        policy=game_power.GamePowerHintPolicy(
            min_hint_sessions=1,
            min_hint_samples=1,
            min_hint_positive_ratio=0.50,
        ),
    )
    store.record_session(make_session_summary(context))
    hint = store.get_hint(context)
    assert hint is not None
    controller = GamePowerController(
        GamePowerConfig(
            mode=GamePowerMode.GPU_PRIORITY,
            activate_samples=2,
            hinted_activate_samples=1,
            rolling_window_samples=1,
            session_hint_contradiction_samples=2,
        ),
        hint=hint,
    )

    controller.evaluate(make_sample(package_w=3.0))
    second = controller.evaluate(make_sample(package_w=3.0))
    positive_after_disable = controller.evaluate(make_sample())

    assert controller.hint_disabled is True
    assert controller.hint_contradiction_samples == 2
    assert second.classification is not None
    assert second.classification.evidence["hint_disabled"] is True
    assert second.classification.evidence["hint_contradiction_samples"] == 2
    assert positive_after_disable.action == GamePowerAction.OBSERVE_ONLY
    assert positive_after_disable.reason == "waiting for activation hysteresis"
    assert positive_after_disable.classification is not None
    assert positive_after_disable.classification.evidence["activation_required_samples"] == 2


def test_contradicted_hinted_session_cannot_learn_or_repair(tmp_path):
    context = make_hint_context()
    store = game_power.GamePowerHintStore(
        tmp_path / "hints.json",
        policy=game_power.GamePowerHintPolicy(
            min_hint_sessions=1,
            min_hint_samples=1,
            min_hint_positive_ratio=0.50,
        ),
    )

    result = store.record_session(
        make_session_summary(
            context,
            contradiction_samples=1,
            hint_was_used=True,
            hint_disabled=True,
            hint_disable_reason="current-session-contradiction",
        )
    )

    assert result.aggregate_updated is False
    assert result.cache_write_result == "not_eligible"
    assert result.promotion_skip_reason == "hint_contradicted"
    assert store.get_hint(context) is None


def test_hint_store_records_contradiction_count_against_existing_hint(tmp_path):
    context = make_hint_context()
    store = game_power.GamePowerHintStore(
        tmp_path / "hints.json",
        policy=game_power.GamePowerHintPolicy(
            min_hint_sessions=1,
            min_hint_samples=1,
            min_hint_positive_ratio=0.50,
            hint_contradiction_limit=1,
        ),
    )
    store.record_session(make_session_summary(context))
    assert store.get_hint(context) is not None

    result = store.record_session(
        make_session_summary(
            context,
            positive_samples=0,
            negative_samples=2,
            applied_samples=0,
            restored_samples=0,
            contradiction_samples=2,
            hint_was_used=True,
            hint_disabled=True,
            hint_disable_reason="current-session-contradiction",
            restore_attempted=False,
            restore_succeeded=None,
        )
    )

    assert result.aggregate_updated is False
    assert result.cache_write_result == "written"
    assert result.promotion_skip_reason == "hint_contradicted"
    assert result.hint_contradiction_count_before == 0
    assert result.hint_contradiction_count_after == 1
    assert store.get_hint(context) is None


def test_hint_store_does_not_reuse_legacy_targetless_hint_entries(tmp_path):
    legacy_context = make_hint_context(fps_target="none-configured", complete=True)
    legacy_key = game_power.canonical_hint_key(legacy_context)
    path = tmp_path / "hints.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": game_power.GAME_POWER_HINT_SCHEMA_VERSION,
                "policy_version": game_power.DEFAULT_GAME_POWER_POLICY_VERSION,
                "aggregates": {},
                "entries": {
                    legacy_key: {
                        "context": game_power._context_json(legacy_context),
                        "preferred_mode": "gpu-priority",
                        "confidence": "medium",
                        "observed_sessions": 2,
                        "total_samples": 40,
                        "positive_ratio": 0.8,
                        "cpu_cap_ratio": 0.0,
                        "last_validated_at": 100.0,
                        "runtime_unaware": True,
                    },
                },
            }
        )
    )

    store = game_power.GamePowerHintStore(path, now=lambda: 200.0)

    assert store.get_hint(legacy_context) is None


def test_context_change_restores_before_aggregate_update(tmp_path):
    context_a = make_hint_context(appid="1091500")
    context_b = make_hint_context(appid="222")
    contexts = [context_a, context_b]

    def context_provider(_sample):
        return contexts.pop(0)

    store = game_power.GamePowerHintStore(
        tmp_path / "hints.json",
        policy=game_power.GamePowerHintPolicy(
            min_hint_sessions=1,
            min_hint_samples=1,
            min_hint_positive_ratio=0.50,
        ),
    )
    observer = FakeObserver([make_sample(appid="1091500"), make_sample(appid="222")])
    actuator = RestoreFailingActuator()
    governor = GamePowerGovernor(
        config=GamePowerConfig(
            mode=GamePowerMode.GPU_PRIORITY,
            activate_samples=1,
            rolling_window_samples=1,
        ),
        observer=observer,
        actuator=actuator,
        hint_store=store,
        hint_context_provider=context_provider,
    )

    import asyncio

    asyncio.run(governor.run_iterations(2))

    assert ("restore-failed", actuator.snapshot_value) in actuator.events
    assert store.get_hint(context_a) is None


def test_incomplete_context_session_close_is_visible_but_not_learned(tmp_path, capsys):
    context = make_hint_context(fps_target="unknown", complete=False)
    store = game_power.GamePowerHintStore(
        tmp_path / "hints.json",
        policy=game_power.GamePowerHintPolicy(
            min_hint_sessions=1,
            min_hint_samples=1,
            min_hint_positive_ratio=0.50,
        ),
    )
    observer = FakeObserver([make_sample(), make_sample(appid=None)])
    governor = GamePowerGovernor(
        config=GamePowerConfig(
            mode=GamePowerMode.GPU_PRIORITY,
            activate_samples=1,
            rolling_window_samples=1,
        ),
        observer=observer,
        actuator=RecordingActuator(),
        output_format="jsonl",
        hint_store=store,
        hint_context_provider=lambda sample: context if sample.appid else None,
    )

    import asyncio

    asyncio.run(governor.run_iterations(2))

    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    close_rows = [row for row in rows if row.get("event") == "game-power-session-close"]

    assert len(close_rows) == 1
    assert close_rows[0]["appid"] == "1091500"
    assert close_rows[0]["hint_key"] is None
    assert close_rows[0]["aggregate_updated"] is False
    assert close_rows[0]["promotion_skip_reason"] == "context_incomplete"
    assert close_rows[0]["cache_write_result"] == "not_eligible"
    assert store.get_hint(context) is None


def test_session_close_jsonl_emits_bounded_persistence_outcome(tmp_path, capsys):
    context = make_hint_context()
    store = game_power.GamePowerHintStore(
        tmp_path / "hints.json",
        policy=game_power.GamePowerHintPolicy(
            min_hint_sessions=1,
            min_hint_samples=1,
            min_hint_positive_ratio=0.50,
        ),
    )
    observer = FakeObserver([make_sample(), make_sample(appid=None)])
    governor = GamePowerGovernor(
        config=GamePowerConfig(
            mode=GamePowerMode.GPU_PRIORITY,
            activate_samples=1,
            rolling_window_samples=1,
        ),
        observer=observer,
        actuator=RecordingActuator(),
        output_format="jsonl",
        hint_store=store,
        hint_context_provider=lambda sample: context if sample.appid else None,
    )

    import asyncio

    asyncio.run(governor.run_iterations(2))

    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    close_rows = [row for row in rows if row.get("event") == "game-power-session-close"]

    assert len(close_rows) == 1
    assert close_rows[0]["appid"] == "1091500"
    assert close_rows[0]["aggregate_updated"] is True
    assert close_rows[0]["hint_promoted"] is True
    assert close_rows[0]["cache_write_result"] == "written"
    assert "contradiction_samples" in close_rows[0]
    assert "restore_succeeded" in close_rows[0]
    assert "pressure" not in close_rows[0]


def test_build_parser_defaults_game_power_cli_to_observe_for_standalone_probe():
    args = game_power.build_parser().parse_args([])
    config = game_power.config_from_args(args)

    assert config.mode == GamePowerMode.OBSERVE
    assert config.cpu_cap_enabled is False


def test_build_parser_accepts_cpu_cap_core_share_threshold():
    args = game_power.build_parser().parse_args(
        ["--cpu-cap", "--cpu-cap-core-share-threshold", "0.31"]
    )
    config = game_power.config_from_args(args)

    assert config.cpu_cap_enabled is True
    assert config.cpu_cap_core_share_threshold == 0.31


def test_format_decision_jsonl_contains_policy_sample_fields():
    decision = GamePowerAction.GPU_PRIORITY_EPP
    sample = make_sample(render_busy=0.75)

    payload = game_power.format_decision_jsonl(
        sample,
        game_power.GamePowerDecision(decision, "package limited with GPU activity"),
        elapsed_s=2.0,
    )

    row = json.loads(payload)
    assert row["elapsed_s"] == 2.0
    assert row["appid"] == "1091500"
    assert row["action"] == "gpu-priority-epp"
    assert row["reason"] == "package limited with GPU activity"
    assert row["package_w"] == 22.0
    assert row["core_w"] == 8.8
    assert row["uncore_w"] == 7.4
    assert row["pl1_w"] == 22
    assert row["render_busy"] == 0.75


def test_format_decision_jsonl_emits_classification_pressure_and_target_schema():
    sample = GamePowerSample(
        appid="1091500",
        rapl=RaplPowerWindow(
            duration_s=2.0,
            package_w=22.0,
            core_w=8.8,
            uncore_w=7.4,
        ),
        pl1_w=22,
        fdinfo_busy={"render": 0.75},
        frame_target=FrameTargetTelemetry(
            fps_target=40.0,
            source="manual",
            confidence="high",
        ),
        pressure=PressureTelemetry(
            cpu=(
                PressureSignal(
                    scope="foreground_cgroup",
                    source_path="/sys/fs/cgroup/app/cpu.pressure",
                    supported=True,
                    some_avg10=2.4,
                    full_avg10=0.1,
                ),
            ),
            memory=(),
            io=(),
        ),
    )
    decision = game_power.GamePowerDecision(
        GamePowerAction.GPU_PRIORITY_CPU_CAP,
        "package limited with high core pressure",
        classification=GamePowerClassification(
            primary="gpu-package-bound-cpu-contention",
            advisories=("foreground-cpu-pressure",),
            confidence="high",
            evidence={
                "package_pressure_ratio": 1.0,
                "target_frame_ms": 25.0,
                "controller_active": False,
            },
        ),
    )

    row = json.loads(game_power.format_decision_jsonl(sample, decision, elapsed_s=2.0))

    assert row["fps_target"] == 40.0
    assert row["fps_target_source"] == "manual"
    assert row["fps_target_confidence"] == "high"
    assert row["target_frame_ms"] == 25.0
    assert row["classification"] == {
        "primary": "gpu-package-bound-cpu-contention",
        "advisories": ["foreground-cpu-pressure"],
        "confidence": "high",
        "evidence": {
            "controller_active": False,
            "package_pressure_ratio": 1.0,
            "target_frame_ms": 25.0,
        },
    }
    assert row["pressure"]["cpu"] == [
        {
            "scope": "foreground_cgroup",
            "source_path": "/sys/fs/cgroup/app/cpu.pressure",
            "supported": True,
            "some_avg10": 2.4,
            "full_avg10": 0.1,
            "error": None,
        }
    ]
    assert row["pressure"]["memory"] == []
    assert row["pressure"]["io"] == []


def test_format_decision_jsonl_emits_frame_performance_schema():
    sample = make_sample(
        frame_target=frame_target_40(),
        frame_performance=frame_performance(avg_fps=56.0, p95_frame_ms=22.0),
    )

    row = json.loads(
        game_power.format_decision_jsonl(
            sample,
            game_power.GamePowerDecision(
                GamePowerAction.OBSERVE_ONLY,
                "fps target satisfied",
                classification=GamePowerClassification(
                    primary="fps-target-satisfied",
                    confidence="high",
                    evidence={
                        "frame_avg_fps": 56.0,
                        "frame_p95_ms": 22.0,
                    },
                ),
            ),
            elapsed_s=2.0,
        )
    )

    assert row["frame_avg_fps"] == 56.0
    assert row["frame_p95_ms"] == 22.0
    assert row["frame_performance_sample_count"] == 20
    assert row["frame_performance_window_s"] == 2.0
    assert row["frame_performance_source"] == "mangohud-csv"
    assert row["frame_performance_confidence"] == "high"


def test_runtime_snapshot_schema_includes_learning_state_for_incomplete_target():
    sample = make_sample(frame_target=None, frame_performance=None)
    decision = game_power.GamePowerDecision(GamePowerAction.OBSERVE_ONLY, "sample")

    row = json.loads(
        game_power.format_runtime_snapshot_json(
            GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY),
            sample,
            decision,
            elapsed_s=2.0,
            sample_source="governor",
            learning={
                "status": "waiting-for-fps-target",
                "session_samples": 3,
                "required_samples": 20,
                "reusable_next_launch": False,
                "skip_reason": "fps_target_unknown",
            },
        )
    )

    assert row["learning"] == {
        "status": "waiting-for-fps-target",
        "session_samples": 3,
        "required_samples": 20,
        "reusable_next_launch": False,
        "skip_reason": "fps_target_unknown",
    }


def test_runtime_snapshot_schema_reports_unknown_target_and_missing_frame_source():
    sample = make_sample(frame_target=None, frame_performance=None)
    decision = game_power.GamePowerDecision(
        GamePowerAction.OBSERVE_ONLY,
        "package limited with GPU activity",
        classification=GamePowerClassification(
            primary="gpu-package-bound",
            confidence="high",
        ),
    )

    row = json.loads(
        game_power.format_runtime_snapshot_json(
            GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY),
            sample,
            decision,
            elapsed_s=2.0,
            sample_source="governor",
        )
    )

    assert row["schema_version"] == "game-power-runtime-snapshot-v1"
    assert row["source"] == "daemon"
    assert row["mode"] == "automatic"
    assert row["control_active"] is True
    assert row["sample_source"] == "governor"
    assert row["appid"] == "1091500"
    assert row["last_action"] == "observe-only"
    assert row["classification_primary"] == "gpu-package-bound"
    assert row["classification_confidence"] == "high"
    assert row["fps_target"] == {
        "status": "unknown",
        "source": "none",
        "confidence": "low",
        "fps": None,
        "target_frame_ms": None,
        "raw": None,
    }
    assert row["frame_source"] == {
        "status": "missing",
        "source": "none",
        "confidence": "low",
        "avg_fps": None,
        "p95_ms": None,
        "p99_ms": None,
        "sample_count": None,
        "window_s": None,
    }
    assert row["package_w"] == 22.0
    assert row["core_w"] == 8.8
    assert row["uncore_w"] == 7.4
    assert row["pl1_w"] == 22
    assert row["render_busy"] == 0.75


def test_system_observer_reads_frame_target_provider_each_sample(tmp_path):
    class FakeRapl:
        def __init__(self):
            self.sysfs_root = tmp_path / "sys"
            self.samples = [
                EnergyReading(timestamp_s=1.0, energy_uj={"package": 100}),
                EnergyReading(timestamp_s=2.0, energy_uj={"package": 200}),
            ]

        def read(self):
            return self.samples.pop(0)

    targets = [
        FrameTargetTelemetry(fps_target=40.0, source="manual", confidence="high")
    ]
    observer = game_power.SystemGamePowerObserver(
        sysfs_root=tmp_path / "sys",
        proc_root=tmp_path / "proc",
        poll_s=0,
        frame_target_provider=lambda: targets.pop(0),
    )
    observer.rapl = FakeRapl()

    import asyncio

    sample = asyncio.run(observer.sample())

    assert sample.frame_target == FrameTargetTelemetry(
        fps_target=40.0,
        source="manual",
        confidence="high",
    )


def test_runtime_snapshot_schema_reports_known_target_and_live_frame_source():
    sample = make_sample(
        frame_target=FrameTargetTelemetry(
            fps_target=40.0,
            source="manual",
            confidence="high",
        ),
        frame_performance=frame_performance(avg_fps=56.0, p95_frame_ms=22.0),
    )

    row = json.loads(
        game_power.format_runtime_snapshot_json(
            GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY),
            sample,
            game_power.GamePowerDecision(GamePowerAction.OBSERVE_ONLY, "sample"),
            elapsed_s=4.0,
        )
    )

    assert row["fps_target"] == {
        "status": "known",
        "source": "manual",
        "confidence": "high",
        "fps": 40.0,
        "target_frame_ms": 25.0,
        "raw": None,
    }
    assert row["frame_source"] == {
        "status": "live",
        "source": "mangohud-csv",
        "confidence": "high",
        "avg_fps": 56.0,
        "p95_ms": 22.0,
        "p99_ms": None,
        "sample_count": 20,
        "window_s": 2.0,
    }


def _runtime_readiness_payload(
    *,
    config: GamePowerConfig | None = None,
    sample: GamePowerSample | None = None,
    learning: dict[str, object] | None = None,
    stale: bool = False,
    error: str | None = None,
) -> dict[str, object]:
    return game_power.runtime_snapshot_payload(
        config
        or GamePowerConfig(
            mode=GamePowerMode.GPU_PRIORITY,
            runtime_control_health={"status": "ready", "reason": "control-ready"},
        ),
        sample
        or make_sample(
            frame_target=FrameTargetTelemetry(
                fps_target=40.0,
                source="manual",
                confidence="high",
            ),
            frame_performance=frame_performance(avg_fps=44.0, p95_frame_ms=24.0),
        ),
        game_power.GamePowerDecision(GamePowerAction.GPU_PRIORITY_EPP, "package limited"),
        elapsed_s=1.0,
        learning=learning,
        stale=stale,
        error=error,
    )


def test_runtime_snapshot_schema_reports_local_target_frame_evidence_ready():
    row = _runtime_readiness_payload()

    readiness = row["evidence_readiness"]
    assert readiness["status"] == "target-aware-live"
    assert readiness["target_ready"] is True
    assert readiness["frame_ready"] is True
    assert readiness["learning_ready"] is False
    assert readiness["claim_ready"] is True
    assert readiness["control_ready"] is True
    assert readiness["write_policy"] == "epp-only"
    assert readiness["reasons"] == [
        "control ready",
        "fps target known",
        "frame data ready",
    ]


def test_runtime_snapshot_schema_reports_power_signals_only_for_missing_frame_data():
    row = _runtime_readiness_payload(
        sample=make_sample(
            frame_target=None,
            frame_performance=None,
        )
    )

    readiness = row["evidence_readiness"]
    assert readiness["status"] == "power-signals-only"
    assert readiness["target_ready"] is False
    assert readiness["frame_ready"] is False
    assert readiness["claim_ready"] is False
    assert readiness["control_ready"] is True
    assert readiness["write_policy"] == "epp-only"
    assert readiness["reasons"] == [
        "control ready",
        "fps target unknown",
        "frame data missing",
    ]


def test_runtime_snapshot_schema_rejects_non_finite_or_non_positive_targets():
    for fps_target in (math.nan, math.inf, 0.0, -40.0):
        row = _runtime_readiness_payload(
            sample=make_sample(
                frame_target=FrameTargetTelemetry(
                    fps_target=fps_target,
                    source="manual",
                    confidence="high",
                ),
                frame_performance=frame_performance(avg_fps=44.0, p95_frame_ms=24.0),
            )
        )

        readiness = row["evidence_readiness"]
        assert readiness["status"] == "power-signals-only"
        assert readiness["target_ready"] is False
        assert readiness["claim_ready"] is False
        json.dumps(row, allow_nan=False)


def test_runtime_snapshot_schema_rejects_low_confidence_or_undersampled_frame_data():
    cases = (
        (
            FrameTargetTelemetry(fps_target=40.0, source="manual", confidence="low"),
            frame_performance(avg_fps=44.0, p95_frame_ms=24.0),
        ),
        (
            FrameTargetTelemetry(fps_target=40.0, source="manual", confidence="high"),
            frame_performance(avg_fps=44.0, p95_frame_ms=24.0, sample_count=11),
        ),
        (
            FrameTargetTelemetry(fps_target=40.0, source="manual", confidence="high"),
            FramePerformanceTelemetry(
                avg_fps=44.0,
                p95_frame_ms=24.0,
                sample_count=12,
                window_s=2.0,
                source="mangohud-csv",
                confidence="low",
            ),
        ),
    )
    for target, performance in cases:
        row = _runtime_readiness_payload(
            sample=make_sample(frame_target=target, frame_performance=performance)
        )

        readiness = row["evidence_readiness"]
        assert readiness["status"] == "power-signals-only"
        assert readiness["claim_ready"] is False


def test_runtime_snapshot_schema_rejects_non_finite_frame_data():
    cases = (
        {"avg_fps": math.nan, "p95_frame_ms": 24.0},
        {"avg_fps": math.inf, "p95_frame_ms": 24.0},
        {"avg_fps": 44.0, "p95_frame_ms": math.nan},
        {"avg_fps": 44.0, "p95_frame_ms": math.inf},
    )
    for kwargs in cases:
        row = _runtime_readiness_payload(
            sample=make_sample(
                frame_target=FrameTargetTelemetry(
                    fps_target=40.0,
                    source="manual",
                    confidence="high",
                ),
                frame_performance=frame_performance(**kwargs),
            )
        )

        readiness = row["evidence_readiness"]
        assert readiness["status"] == "power-signals-only"
        assert readiness["frame_ready"] is False
        assert readiness["claim_ready"] is False
        json.dumps(row, allow_nan=False)


def test_runtime_snapshot_schema_reports_control_invalid_readiness():
    row = _runtime_readiness_payload(
        config=GamePowerConfig(
            mode=GamePowerMode.OFF,
            runtime_control_health={
                "status": "invalid",
                "reason": "invalid-fps-target-override",
            },
        )
    )

    readiness = row["evidence_readiness"]
    assert readiness["status"] == "control-invalid"
    assert readiness["control_ready"] is False
    assert readiness["claim_ready"] is False
    assert readiness["write_policy"] == "disabled"


def test_runtime_snapshot_schema_reports_cpu_cap_explicit_write_policy():
    row = _runtime_readiness_payload(
        config=GamePowerConfig(
            mode=GamePowerMode.GPU_PRIORITY,
            cpu_cap_enabled=True,
            runtime_control_health={"status": "ready", "reason": "control-ready"},
        )
    )

    readiness = row["evidence_readiness"]
    assert readiness["status"] == "target-aware-live"
    assert readiness["claim_ready"] is True
    assert readiness["write_policy"] == "epp-plus-cpu-cap-explicit"


def test_runtime_snapshot_schema_reports_learning_ready_only_when_reusable():
    ready = _runtime_readiness_payload(
        learning={
            "status": "ready",
            "session_samples": 20,
            "required_samples": 20,
            "reusable_next_launch": True,
            "skip_reason": None,
        }
    )
    waiting = _runtime_readiness_payload(
        learning={
            "status": "waiting-for-fps-target",
            "session_samples": 2,
            "required_samples": 20,
            "reusable_next_launch": False,
            "skip_reason": "fps_target_unknown",
        }
    )

    assert ready["evidence_readiness"]["learning_ready"] is True
    assert waiting["evidence_readiness"]["learning_ready"] is False


def test_runtime_snapshot_schema_reports_unavailable_readiness_for_stale_or_error():
    stale = _runtime_readiness_payload(stale=True)
    errored = _runtime_readiness_payload(error="invalid-runtime-snapshot")

    for row in (stale, errored):
        readiness = row["evidence_readiness"]
        assert readiness["status"] == "unavailable"
        assert readiness["target_ready"] is False
        assert readiness["frame_ready"] is False
        assert readiness["learning_ready"] is False
        assert readiness["claim_ready"] is False
        assert readiness["control_ready"] is False
        assert readiness["write_policy"] == "disabled"


def test_runtime_snapshot_schema_reports_stopped_and_view_data_only_readiness():
    stopped = _runtime_readiness_payload(
        config=GamePowerConfig(
            mode=GamePowerMode.OFF,
            runtime_control_health={"status": "ready", "reason": "control-ready"},
        )
    )
    view_data = _runtime_readiness_payload(
        config=GamePowerConfig(
            mode=GamePowerMode.OBSERVE,
            runtime_control_health={"status": "ready", "reason": "control-ready"},
        )
    )

    assert stopped["evidence_readiness"]["status"] == "stopped"
    assert stopped["evidence_readiness"]["claim_ready"] is False
    assert stopped["evidence_readiness"]["write_policy"] == "disabled"
    assert view_data["evidence_readiness"]["status"] == "view-data-only"
    assert view_data["evidence_readiness"]["claim_ready"] is False
    assert view_data["evidence_readiness"]["write_policy"] == "disabled"


def test_runtime_snapshot_schema_can_represent_unlimited_fps_target():
    sample = make_sample(
        frame_target=FrameTargetTelemetry(
            fps_target=None,
            source="manual-unlimited",
            confidence="high",
        )
    )

    row = json.loads(
        game_power.format_runtime_snapshot_json(
            GamePowerConfig(mode=GamePowerMode.OBSERVE),
            sample,
            game_power.GamePowerDecision(GamePowerAction.OBSERVE_ONLY, "mode is observe"),
            elapsed_s=1.0,
        )
    )

    assert row["control_active"] is False
    assert row["fps_target"] == {
        "status": "unlimited",
        "source": "manual-unlimited",
        "confidence": "high",
        "fps": None,
        "target_frame_ms": None,
        "raw": None,
    }


def test_low_core_share_still_allows_low_risk_epp_gpu_priority():
    config = GamePowerConfig(
        mode=GamePowerMode.GPU_PRIORITY,
        cpu_cap_enabled=True,
        cpu_cap_core_share_threshold=0.30,
    )
    sample = make_sample(core_w=6.0, package_w=22.0, uncore_w=9.4, render_busy=0.9)

    inactive = classify_game_power_sample(config, sample, controller_active=False)
    active = classify_game_power_sample(config, sample, controller_active=True)
    controller = GamePowerController(config)
    decision = controller.evaluate(sample)
    second = controller.evaluate(sample)

    assert inactive.primary == "gpu-package-bound"
    assert active.primary == "gpu-package-bound"
    assert decision.action == GamePowerAction.OBSERVE_ONLY
    assert second.action == GamePowerAction.GPU_PRIORITY_EPP
    assert decision.classification is not None
    assert decision.classification.primary == inactive.primary
    assert decision.classification.evidence["controller_active"] is False


def test_pressure_parser_preserves_missing_full_as_none():
    signal = parse_pressure_signal(
        "cpu",
        "foreground_cgroup",
        "/sys/fs/cgroup/app/cpu.pressure",
        "some avg10=2.50 avg60=1.00 total=123\n",
    )

    assert signal == PressureSignal(
        scope="foreground_cgroup",
        source_path="/sys/fs/cgroup/app/cpu.pressure",
        supported=True,
        some_avg10=2.5,
        full_avg10=None,
        error=None,
    )


def test_resolve_cgroup_v2_path_strips_leading_slash_under_root(tmp_path):
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()

    resolved = resolve_cgroup_v2_path(
        cgroup_root,
        "0::/user.slice/user-1000.slice/app.slice/app-steam-app1091500.scope\n",
    )

    assert resolved == (
        cgroup_root
        / "user.slice"
        / "user-1000.slice"
        / "app.slice"
        / "app-steam-app1091500.scope"
    ).resolve()


def test_fps_target_rejects_non_positive_nan_and_infinite_values():
    parser = game_power.build_parser()

    for value in ("0", "-1", "nan", "inf", "-inf"):
        try:
            parser.parse_args(["--fps-target", value])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"expected parse failure for {value}")

    try:
        game_power.config_from_args(parser.parse_args(["--fps-target-source", "manual"]))
    except ValueError as exc:
        assert "--fps-target-source requires --fps-target" in str(exc)
    else:
        raise AssertionError("expected source without target to fail")

    try:
        game_power.config_from_args(
            parser.parse_args(
                [
                    "--frame-performance-window-samples",
                    "2",
                    "--frame-performance-min-samples",
                    "3",
                ]
            )
        )
    except ValueError as exc:
        assert "--frame-performance-min-samples cannot exceed" in str(exc)
    else:
        raise AssertionError("expected min samples greater than window to fail")


def test_build_parser_accepts_jsonl_output_format():
    args = game_power.build_parser().parse_args(["--output-format", "jsonl"])

    assert args.output_format == "jsonl"


def test_mangohud_csv_frame_performance_reader_uses_last_valid_window(tmp_path):
    csv_path = tmp_path / "mangohud.csv"
    csv_path.write_text(
        "\n".join(
            [
                "os,cpu,gpu",
                "SteamOS,Intel,",
                "fps,frametime,elapsed",
                "10.0,100.0,1",
                "bad,row,ignored",
                "40.0,25.0,2",
                "50.0,20.0,3",
                "60.0,16.0,4",
            ]
        )
        + "\n"
    )
    reader = game_power.MangoHudCsvFramePerformanceReader(
        csv_path,
        window_samples=3,
        min_samples=3,
    )

    telemetry = reader.read()

    assert telemetry is not None
    assert telemetry.avg_fps == 50.0
    assert telemetry.p95_frame_ms == 25.0
    assert telemetry.sample_count == 3
    assert telemetry.source == "mangohud-csv"
    assert telemetry.confidence == "high"


def test_mangohud_csv_frame_performance_reader_reports_low_confidence_until_ready(tmp_path):
    csv_path = tmp_path / "mangohud.csv"
    csv_path.write_text(
        "\n".join(
            [
                "os,cpu,gpu",
                "SteamOS,Intel,",
                "fps,frametime,elapsed",
                "40.0,25.0,2",
            ]
        )
        + "\n"
    )
    reader = game_power.MangoHudCsvFramePerformanceReader(
        csv_path,
        window_samples=3,
        min_samples=3,
    )

    telemetry = reader.read()

    assert telemetry is not None
    assert telemetry.sample_count == 1
    assert telemetry.confidence == "low"


def test_governor_applies_epp_and_restores_when_controller_requests_restore():
    config = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY)
    observer = FakeObserver(
        [
            make_sample(),
            make_sample(),
            make_sample(appid=None),
            make_sample(appid=None),
            make_sample(appid=None),
        ]
    )
    actuator = RecordingActuator()
    governor = GamePowerGovernor(config=config, observer=observer, actuator=actuator)

    import asyncio

    asyncio.run(governor.run_iterations(5))

    assert ("snapshot",) in actuator.events
    assert ("apply", "balance_power", None, None) in actuator.events
    assert ("restore", actuator.snapshot_value) in actuator.events


def test_governor_restores_snapshot_when_active_write_fails():
    config = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY)
    observer = FakeObserver([make_sample(), make_sample()])
    actuator = FailingActuator()
    governor = GamePowerGovernor(config=config, observer=observer, actuator=actuator)

    import asyncio

    asyncio.run(governor.run_iterations(2))

    assert ("snapshot",) in actuator.events
    assert ("apply-failed", "balance_power", None, None) in actuator.events
    assert ("restore", actuator.snapshot_value) in actuator.events


def test_governor_reloads_runtime_config_and_restores_when_mode_changes():
    configs = [
        GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY, activate_samples=1),
        GamePowerConfig(mode=GamePowerMode.OFF, activate_samples=1),
    ]

    def config_provider(_base):
        return configs.pop(0)

    observer = FakeObserver([make_sample()])
    actuator = RecordingActuator()
    governor = GamePowerGovernor(
        config=GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY, activate_samples=1),
        observer=observer,
        actuator=actuator,
        config_provider=config_provider,
    )

    import asyncio

    asyncio.run(governor.run_iterations(2))

    assert ("apply", "balance_power", None, None) in actuator.events
    assert ("restore", actuator.snapshot_value) in actuator.events
    assert observer.samples == []


def test_governor_off_mode_sleeps_without_sampling():
    class ExplodingObserver:
        async def sample(self):
            raise AssertionError("off mode should not sample")

    async def no_sleep(_seconds):
        return None

    governor = GamePowerGovernor(
        config=GamePowerConfig(mode=GamePowerMode.OFF, poll_s=0.01),
        observer=ExplodingObserver(),
        actuator=RecordingActuator(),
        sleep=no_sleep,
    )

    import asyncio

    decision = asyncio.run(governor.run_once())

    assert decision.action == GamePowerAction.IDLE


def make_proc_game(proc_root: Path, pid: int, appid: str, command: str = "Cyberpunk2077.exe"):
    root = proc_root / str(pid)
    root.mkdir(parents=True)
    (root / "cmdline").write_bytes(command.encode() + b"\0")
    (root / "cgroup").write_text(
        "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
        f"app-steam-app{appid}-{pid}.scope\n"
    )
    return root


def test_find_steam_game_processes_reads_appid_from_cgroup(tmp_path):
    proc_root = tmp_path / "proc"
    make_proc_game(proc_root, 1234, "1091500")

    processes = find_steam_game_processes(proc_root)

    assert processes == [
        GameProcess(
            pid=1234,
            appid="1091500",
            command="Cyberpunk2077.exe",
            cgroup_text=(
                "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
                "app-steam-app1091500-1234.scope"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# V9 target-balance: helpers
# ---------------------------------------------------------------------------
def tb_config(**over):
    base = dict(mode=GamePowerMode.TARGET_BALANCE, poll_s=2.0)
    base.update(over)
    return GamePowerConfig(**base)


def tb_target(fps=60.0):
    return FrameTargetTelemetry(fps_target=fps, source="manual", confidence="high")


def tb_perf(avg, p95, n=20):
    return FramePerformanceTelemetry(
        avg_fps=avg,
        p95_frame_ms=p95,
        sample_count=n,
        window_s=2.0,
        source="mangohud-csv",
        confidence="high",
    )


def fg_cpu_psi(avg10):
    return PressureTelemetry(
        cpu=(
            PressureSignal(
                scope="foreground_cgroup",
                source_path="x",
                supported=True,
                some_avg10=avg10,
            ),
        )
    )


def phase_sample(
    *,
    appid="1091500",
    package_w=22.0,
    core_w=8.8,
    uncore_w=7.4,
    pl1_w=22,
    render_busy=0.75,
    fps=60.0,
    avg_fps=None,
    p95=None,
    n=20,
    age=None,
    wait=None,
    stalled=None,
    psi=None,
    gpu_rp0_mhz=1950,
    gpu_rpe_mhz=800,
    package_median_w=None,
):
    frame_target = tb_target(fps) if fps is not None else None
    frame_performance = tb_perf(avg_fps, p95, n) if avg_fps is not None else None
    sample = make_sample(
        appid=appid,
        package_w=package_w,
        core_w=core_w,
        uncore_w=uncore_w,
        pl1_w=pl1_w,
        render_busy=render_busy,
        frame_target=frame_target,
        frame_performance=frame_performance,
    )
    return replace(
        sample,
        foreground_process_age_s=age,
        foreground_runqueue_wait_ms_per_s=wait,
        frame_feed_stalled=stalled,
        pressure=fg_cpu_psi(psi) if psi is not None else None,
        gpu_rp0_mhz=gpu_rp0_mhz,
        gpu_rpe_mhz=gpu_rpe_mhz,
        package_median_w=package_median_w,
    )


def at_target_sample(**over):
    kwargs = dict(avg_fps=63.0, p95=15.0, fps=60.0, age=100.0)
    kwargs.update(over)
    return phase_sample(**kwargs)


# ---------------------------------------------------------------------------
# S1: per-class EPP actuator
# ---------------------------------------------------------------------------
def test_cpu_policy_actuator_applies_per_class_epp_and_shares_one_snapshot(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_cpu_policy(sysfs_root, 0, cpu=0, capacity=1024, max_freq=4_800_000)
    make_cpu_policy(sysfs_root, 1, cpu=1, capacity=676, max_freq=3_700_000)
    policies = discover_cpu_policies(sysfs_root)
    actuator = CpuPolicyActuator(policies)

    snapshot = actuator.snapshot()
    actuator.apply(pcore_epp="performance", ecore_epp="balance_power", pcore_max_khz=4_000_000)

    assert (policies[0].path / "energy_performance_preference").read_text() == "performance"
    assert (policies[1].path / "energy_performance_preference").read_text() == "balance_power"
    assert (policies[0].path / "scaling_max_freq").read_text() == "4000000"
    # E-core cap untouched by S3.
    assert (policies[1].path / "scaling_max_freq").read_text() == "3700000"

    actuator.restore(snapshot)
    assert (
        policies[0].path / "energy_performance_preference"
    ).read_text() == "balance_performance"
    assert (policies[0].path / "scaling_max_freq").read_text() == "4800000"


def test_cpu_policy_actuator_uniform_epp_path_unchanged(tmp_path):
    sysfs_root = tmp_path / "sys"
    make_cpu_policy(sysfs_root, 0, cpu=0, capacity=1024)
    make_cpu_policy(sysfs_root, 1, cpu=1, capacity=676, max_freq=3_700_000)
    policies = discover_cpu_policies(sysfs_root)
    actuator = CpuPolicyActuator(policies)

    actuator.apply(epp="balance_power")

    assert (policies[0].path / "energy_performance_preference").read_text() == "balance_power"
    assert (policies[1].path / "energy_performance_preference").read_text() == "balance_power"


# ---------------------------------------------------------------------------
# S1: phase classification (design section 4)
# ---------------------------------------------------------------------------
def test_classify_phase_no_game_when_no_appid():
    phase, _ = classify_game_power_phase(tb_config(), phase_sample(appid=None))
    assert phase == GamePowerPhase.NO_GAME


def test_classify_phase_no_target_when_target_unknown():
    phase, codes = classify_game_power_phase(tb_config(), phase_sample(fps=None))
    assert phase == GamePowerPhase.NO_TARGET
    assert codes == ("target-unknown-or-unlimited",)


def test_classify_phase_loading_on_launch_grace():
    phase, codes = classify_game_power_phase(tb_config(), phase_sample(age=5.0))
    assert phase == GamePowerPhase.LOADING
    assert "launch-grace" in codes


def test_classify_phase_loading_on_asset_shader_burst():
    sample = phase_sample(avg_fps=20.0, p95=40.0, render_busy=0.2, core_w=13.0, age=100.0)
    phase, codes = classify_game_power_phase(tb_config(), sample)
    assert phase == GamePowerPhase.LOADING
    assert "asset-shader-burst" in codes


def test_classify_phase_loading_on_frame_stall_with_psi():
    sample = phase_sample(avg_fps=55.0, p95=20.0, stalled=True, psi=50.0, age=100.0)
    phase, codes = classify_game_power_phase(tb_config(), sample)
    assert phase == GamePowerPhase.LOADING
    assert "frame-feed-stalled" in codes


def test_classify_phase_at_target():
    phase, _ = classify_game_power_phase(tb_config(), at_target_sample())
    assert phase == GamePowerPhase.AT_TARGET


def test_classify_phase_above_target():
    phase, _ = classify_game_power_phase(tb_config(), at_target_sample(avg_fps=80.0))
    assert phase == GamePowerPhase.ABOVE_TARGET


def test_classify_phase_below_target_gpu_bound():
    sample = phase_sample(avg_fps=50.0, p95=25.0, render_busy=0.75, age=100.0)
    phase, _ = classify_game_power_phase(tb_config(), sample)
    assert phase == GamePowerPhase.BELOW_TARGET_GPU_BOUND


def test_classify_phase_below_target_cpu_bound_via_runqueue():
    sample = phase_sample(
        avg_fps=50.0,
        p95=25.0,
        render_busy=0.5,
        uncore_w=2.0,
        core_w=9.0,
        wait=60.0,
        age=100.0,
    )
    phase, codes = classify_game_power_phase(tb_config(), sample)
    assert phase == GamePowerPhase.BELOW_TARGET_CPU_BOUND
    assert codes == ("cpu-bound",)


def test_classify_phase_unknown_when_no_bound_signal():
    sample = phase_sample(
        avg_fps=50.0, p95=25.0, render_busy=0.5, uncore_w=2.0, core_w=4.0, age=100.0
    )
    phase, _ = classify_game_power_phase(tb_config(), sample)
    assert phase == GamePowerPhase.UNKNOWN


# ---------------------------------------------------------------------------
# S1: phase hysteresis (asymmetric)
# ---------------------------------------------------------------------------
def test_phase_commit_requires_stable_samples():
    controller = GamePowerController(tb_config(phase_stable_samples=3))
    for _ in range(2):
        controller.evaluate(at_target_sample())
        assert controller.committed_phase == GamePowerPhase.NO_GAME
    controller.evaluate(at_target_sample())
    assert controller.committed_phase == GamePowerPhase.AT_TARGET


def test_loading_entry_commits_after_one_tick():
    controller = GamePowerController(tb_config(phase_stable_samples=3))
    decision = controller.evaluate(phase_sample(age=5.0))
    assert controller.committed_phase == GamePowerPhase.LOADING
    assert decision.action == GamePowerAction.LOADING_BOOST


def test_at_target_exit_on_miss_commits_after_one_tick():
    controller = GamePowerController(tb_config(phase_stable_samples=3))
    for _ in range(3):
        controller.evaluate(at_target_sample())
    assert controller.committed_phase == GamePowerPhase.AT_TARGET
    controller.evaluate(phase_sample(avg_fps=50.0, p95=25.0, render_busy=0.75, age=100.0))
    assert controller.committed_phase == GamePowerPhase.BELOW_TARGET_GPU_BOUND


def test_loading_exit_requires_five_samples():
    controller = GamePowerController(tb_config(loading_exit_samples=5))
    controller.evaluate(phase_sample(age=5.0))
    assert controller.committed_phase == GamePowerPhase.LOADING
    for _ in range(4):
        controller.evaluate(at_target_sample())
        assert controller.committed_phase == GamePowerPhase.LOADING
    controller.evaluate(at_target_sample())
    assert controller.committed_phase == GamePowerPhase.AT_TARGET


# ---------------------------------------------------------------------------
# S1: loading budget + per-phase actuation
# ---------------------------------------------------------------------------
def test_loading_boost_actuation_is_per_class_performance():
    controller = GamePowerController(tb_config())
    decision = controller.evaluate(phase_sample(age=5.0))
    assert decision.action == GamePowerAction.LOADING_BOOST
    assert decision.actuation == GamePowerActuation(
        pcore_epp="performance", ecore_epp="balance_performance"
    )
    assert decision.phase == GamePowerPhase.LOADING


def test_loading_budget_exhausted_returns_neutral():
    controller = GamePowerController(tb_config(loading_boost_max_s=3.0, poll_s=2.0))
    first = controller.evaluate(phase_sample(age=5.0))
    assert first.action == GamePowerAction.LOADING_BOOST
    second = controller.evaluate(phase_sample(age=5.0))
    assert second.action == GamePowerAction.OBSERVE_ONLY
    assert second.actuation is None
    assert "loading-budget-exhausted" in second.phase_reason_codes


def test_below_target_cpu_bound_emits_target_balance_trim_per_class():
    controller = GamePowerController(tb_config(phase_stable_samples=1))
    sample = phase_sample(
        avg_fps=50.0,
        p95=25.0,
        render_busy=0.5,
        uncore_w=2.0,
        core_w=9.0,
        wait=60.0,
        age=100.0,
    )
    decision = controller.evaluate(sample)
    assert decision.phase == GamePowerPhase.BELOW_TARGET_CPU_BOUND
    assert decision.action == GamePowerAction.TARGET_BALANCE_TRIM
    assert decision.actuation == GamePowerActuation(
        pcore_epp="performance", ecore_epp="balance_power"
    )


def test_below_target_gpu_bound_uses_uniform_gpu_priority_epp():
    controller = GamePowerController(tb_config(phase_stable_samples=1, epp="balance_power"))
    sample = phase_sample(avg_fps=50.0, p95=25.0, render_busy=0.75, age=100.0)
    decision = controller.evaluate(sample)
    assert decision.phase == GamePowerPhase.BELOW_TARGET_GPU_BOUND
    assert decision.action == GamePowerAction.GPU_PRIORITY_EPP
    assert decision.actuation == GamePowerActuation(
        pcore_epp="balance_power", ecore_epp="balance_power"
    )


def test_no_target_falls_back_to_v7_gpu_priority_predicate():
    controller = GamePowerController(
        tb_config(
            phase_stable_samples=1,
            activate_samples=1,
            rolling_window_samples=1,
            epp="balance_power",
        )
    )
    decision = controller.evaluate(phase_sample(fps=None))
    assert decision.phase == GamePowerPhase.NO_TARGET
    assert decision.action == GamePowerAction.GPU_PRIORITY_EPP
    assert decision.actuation == GamePowerActuation(
        pcore_epp="balance_power", ecore_epp="balance_power"
    )


def test_gpu_priority_mode_decision_carries_no_phase():
    controller = GamePowerController(
        GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY, activate_samples=1)
    )
    decision = controller.evaluate(make_sample())
    assert decision.phase is None


# ---------------------------------------------------------------------------
# S1: governor per-class apply + one snapshot/restore path
# ---------------------------------------------------------------------------
def test_governor_target_balance_applies_per_class_epp_and_restores_on_close():
    observer = FakeObserver([phase_sample(age=5.0)])
    actuator = RecordingActuator()
    governor = GamePowerGovernor(
        config=tb_config(), observer=observer, actuator=actuator
    )
    asyncio.run(governor.run_iterations(1))
    assert ("snapshot",) in actuator.events
    assert (
        "apply-per-class",
        "performance",
        "balance_performance",
        None,
        None,
    ) in actuator.events
    governor.close()
    assert ("restore", actuator.snapshot_value) in actuator.events


def test_governor_target_balance_write_failure_latches_fail_closed():
    observer = FakeObserver([phase_sample(age=5.0), phase_sample(age=5.0)])
    actuator = FailingActuator()
    governor = GamePowerGovernor(
        config=tb_config(), observer=observer, actuator=actuator
    )
    asyncio.run(governor.run_iterations(2))
    applies = [event for event in actuator.events if event[0] == "apply-failed-per-class"]
    assert len(applies) == 1  # second tick is fail-closed (writes disabled)
    assert ("restore", actuator.snapshot_value) in actuator.events


# ---------------------------------------------------------------------------
# S1: telemetry additivity
# ---------------------------------------------------------------------------
def test_gpu_priority_jsonl_has_no_phase_fields():
    sample = make_sample()
    decision = game_power.GamePowerDecision(
        GamePowerAction.GPU_PRIORITY_EPP, "package limited with GPU activity"
    )
    row = json.loads(game_power.format_decision_jsonl(sample, decision, elapsed_s=2.0))
    assert "phase" not in row
    assert "ladder_step" not in row


def test_target_balance_jsonl_includes_phase_and_ladder_step():
    sample = make_sample()
    decision = game_power.GamePowerDecision(
        GamePowerAction.TARGET_BALANCE_TRIM,
        "ladder step up to step 2",
        phase=GamePowerPhase.AT_TARGET,
        phase_reason_codes=("fps-target-satisfied",),
        ladder_step=2,
    )
    row = json.loads(game_power.format_decision_jsonl(sample, decision, elapsed_s=2.0))
    assert row["phase"] == "at-target"
    assert row["phase_reason_codes"] == ["fps-target-satisfied"]
    assert row["ladder_step"] == 2


def test_runtime_snapshot_includes_phase_for_target_balance():
    sample = make_sample()
    decision = game_power.GamePowerDecision(
        GamePowerAction.LOADING_BOOST,
        "loading boost",
        phase=GamePowerPhase.LOADING,
        phase_reason_codes=("launch-grace",),
        ladder_step=0,
    )
    payload = game_power.runtime_snapshot_payload(
        tb_config(), sample, decision, elapsed_s=1.0
    )
    assert payload["phase"] == "loading"
    assert payload["control_active"] is True


# ---------------------------------------------------------------------------
# S1: schedstat + process age observers
# ---------------------------------------------------------------------------
def test_parse_thread_schedstat_reads_cpu_and_wait():
    assert parse_thread_schedstat("123456 7890 42") == (123456, 7890)


def test_compute_runqueue_wait_sums_top_threads_by_cpu_delta():
    prev = {1: (0, 0), 2: (0, 0), 3: (0, 0)}
    curr = {1: (1000, 500), 2: (2000, 700), 3: (10, 100)}
    # top-2 by cpu delta => tids 2 and 1 => wait = 700 + 500 = 1200 ns
    value = compute_foreground_runqueue_wait_ms_per_s(prev, curr, elapsed_s=1.0, top_n=2)
    assert value == round(1200 / 1_000_000, 3)


def test_parse_proc_stat_starttime_handles_comm_with_spaces():
    fields = ["7"] + [str(i) for i in range(3, 22)]  # state..starttime(=21)
    text = "1234 (game with )spaces) " + " ".join(fields)
    assert parse_proc_stat_starttime_ticks(text) == 21


def test_read_process_age_s_from_uptime_and_stat(tmp_path):
    proc = tmp_path / "proc"
    (proc / "1234").mkdir(parents=True)
    (proc / "uptime").write_text("1000.0 900.0\n")
    fields = ["7"] + [str(i) for i in range(3, 21)] + ["50000"]  # starttime ticks
    (proc / "1234" / "stat").write_text("1234 (game) " + " ".join(fields))
    age = read_process_age_s(proc, 1234, clock_ticks_per_s=100)
    assert age == 500.0  # 1000 - 50000/100


def test_observer_carries_runqueue_wait_between_colorize_ticks(tmp_path):
    proc = tmp_path / "proc"
    make_proc_game(proc, 4242, "1091500")
    task = proc / "4242" / "task"

    def write_schedstat(cpu, wait):
        for tid, values in ((1, (cpu, wait)),):
            tdir = task / str(tid)
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "schedstat").write_text(f"{values[0]} {values[1]} 1")

    clock = [0.0]
    observer = game_power.SystemGamePowerObserver(
        proc_root=proc, poll_s=2.0, colorize_interval_s=2.0, clock=lambda: clock[0]
    )
    process = find_steam_game_processes(proc)[0]

    write_schedstat(0, 0)
    clock[0] = 0.0
    assert observer._read_colorize_signals(process) is None  # baseline sample

    write_schedstat(1000, 1_000_000)
    clock[0] = 1.0
    first = observer._read_colorize_signals(process)
    assert first == round(1_000_000 / 1_000_000 / 1.0, 3)

    # Non-colorize tick carries the last value forward unchanged.
    carried = observer._read_colorize_signals(process)
    assert carried == first


def test_q2_single_proc_pass_supplies_wait_and_color_ledger(tmp_path):
    proc = tmp_path / "proc"
    make_proc_game(proc, 4242, "1091500")
    task = proc / "4242" / "task"

    def write_schedstat(cpu, wait):
        tdir = task / "1"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "schedstat").write_text(f"{cpu} {wait} 1")
        (tdir / "comm").write_text("RenderThread\n")

    # Q2: the duplicate per-tick schedstat reader is gone; one colorize pass
    # feeds both the color ledger and the runqueue-wait aggregate.
    assert not hasattr(game_power, "read_foreground_thread_schedstat")

    clock = [0.0]
    observer = game_power.SystemGamePowerObserver(
        proc_root=proc, poll_s=2.0, colorize_interval_s=2.0, clock=lambda: clock[0]
    )
    process = find_steam_game_processes(proc)[0]

    write_schedstat(0, 0)
    clock[0] = 0.0
    observer._read_colorize_signals(process)  # baseline

    write_schedstat(200_000_000, 30_000_000)
    clock[0] = 2.0
    wait = observer._read_colorize_signals(process)

    # Both signals come from one pass over the same rows.
    assert wait == round(30_000_000 / 1_000_000 / 2.0, 3)
    assert observer._last_color_entries is not None
    entries = {e.role_key: e for e in observer._last_color_entries}
    assert "foreground-game:renderthread" in entries


# ---------------------------------------------------------------------------
# S3: runtime thread color ledger
# ---------------------------------------------------------------------------
def _write_task(task_root, tid, comm, cpu_ns, wait_ns, slices, cpu):
    tdir = task_root / str(tid)
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "schedstat").write_text(f"{cpu_ns} {wait_ns} {slices}\n")
    (tdir / "comm").write_text(f"{comm}\n")
    fields = ["0"] * 40
    fields[36] = str(cpu)  # processor is field 39 => index 36 in post-comm tail
    (tdir / "stat").write_text(f"{tid} ({comm}) " + " ".join(fields) + "\n")


def test_observer_colorizes_foreground_and_compositor_roles(tmp_path):
    from steamos_intel_handheld.game_power_coloring import Color

    proc = tmp_path / "proc"
    make_proc_game(proc, 4242, "1091500")
    game_task = proc / "4242" / "task"

    # A compositor helper process (color C, never shaped).
    comp = proc / "5000"
    comp.mkdir(parents=True)
    (comp / "cmdline").write_bytes(b"gamescope\0")
    (comp / "cgroup").write_text(
        "0::/user.slice/user@1000.service/gamescope-session.service\n"
    )

    clock = [0.0]
    observer = game_power.SystemGamePowerObserver(
        proc_root=proc, poll_s=2.0, colorize_interval_s=2.0, clock=lambda: clock[0]
    )
    process = find_steam_game_processes(proc)[0]

    _write_task(game_task, 101, "RenderThread", 0, 0, 0, 0)
    _write_task(comp / "task", 201, "gamescope", 0, 0, 0, 4)
    clock[0] = 0.0
    observer._read_colorize_signals(process)  # baseline
    assert observer._last_color_entries is None

    _write_task(game_task, 101, "RenderThread", 200_000_000, 30_000_000, 100, 1)
    _write_task(comp / "task", 201, "gamescope", 50_000_000, 0, 10, 4)
    clock[0] = 2.0
    observer._read_colorize_signals(process)

    entries = {e.role_key: e for e in observer._last_color_entries}
    assert entries["foreground-game:renderthread"].color == Color.A
    assert entries["foreground-game:renderthread"].actuator_state == "blocked"
    compositor = entries["gamescope-helper:gamescope"]
    assert compositor.color == Color.C
    assert compositor.actuator == "observe-only"
    assert compositor.actuator_state == "active"


def test_observer_marks_truncated_when_over_tid_budget(tmp_path):
    from steamos_intel_handheld.game_power_coloring import COLOR_LEDGER_TID_BUDGET

    proc = tmp_path / "proc"
    make_proc_game(proc, 4242, "1091500")
    game_task = proc / "4242" / "task"

    clock = [0.0]
    observer = game_power.SystemGamePowerObserver(
        proc_root=proc, poll_s=2.0, colorize_interval_s=2.0, clock=lambda: clock[0]
    )
    process = find_steam_game_processes(proc)[0]

    count = COLOR_LEDGER_TID_BUDGET + 10
    for tid in range(count):
        _write_task(game_task, tid, "worker", 0, 0, 0, 0)
    clock[0] = 0.0
    observer._read_colorize_signals(process)
    for tid in range(count):
        _write_task(game_task, tid, "worker", (tid + 1) * 1_000_000, 0, 1, 0)
    clock[0] = 2.0
    observer._read_colorize_signals(process)

    assert observer._last_color_truncated is True


def test_target_balance_jsonl_includes_color_ledger():
    from steamos_intel_handheld.game_power_coloring import Color, ColorLedgerEntry

    entry = ColorLedgerEntry(
        role_key="foreground-game:renderthread",
        color=Color.A,
        tid_count=1,
        cpu_time_ms_per_s=64.0,
        runqueue_wait_ms_per_s=3.0,
        cpus_seen=(0, 1),
        actuator="uclamp-min",
        actuator_state="blocked",
        blocking_reason_codes=("no-verdict-for-context",),
    )
    controller = GamePowerController(tb_config(activate_samples=1, rolling_window_samples=1))
    sample = replace(
        at_target_sample(),
        color_ledger_entries=(entry,),
        color_ledger_truncated=False,
    )
    decision = controller.evaluate(sample)

    assert decision.color_ledger is not None
    row = json.loads(game_power.format_decision_jsonl(sample, decision, elapsed_s=2.0))
    assert row["color_ledger"]["entries"][0]["color"] == "A"
    assert row["color_ledger"]["entries"][0]["actuator_state"] == "blocked"
    assert row["verdict_ledger_health"]["status"] == "unavailable"


def test_gpu_priority_jsonl_has_no_v9_color_fields():
    controller = GamePowerController(
        GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY, activate_samples=1)
    )
    sample = make_sample()
    decision = controller.evaluate(sample)
    row = json.loads(game_power.format_decision_jsonl(sample, decision, elapsed_s=2.0))
    assert "color_ledger" not in row
    assert "verdict_ledger_health" not in row
    assert "gated_lanes" not in row
    assert "phase" not in row


# ---------------------------------------------------------------------------
# S2: convergence ladder
# ---------------------------------------------------------------------------
def _drive_to_step(controller, steps, sample_factory=at_target_sample):
    decisions = []
    for _ in range(steps):
        decisions.append(controller.evaluate(sample_factory()))
    return decisions


def test_ladder_steps_up_after_hold_samples():
    controller = GamePowerController(
        tb_config(phase_stable_samples=1, ladder_hold_samples=2)
    )
    # tick1 commit AT + hold(1); tick2 hold(2) -> step up to 1
    controller.evaluate(at_target_sample())
    assert controller.ladder_step == 0
    decision = controller.evaluate(at_target_sample())
    assert controller.ladder_step == 1
    assert decision.action == GamePowerAction.TARGET_BALANCE_TRIM
    # V10 battery rung 1 is P1, the package budget. A GPU cap on its own is
    # re-spent by the CPU (measured: graphics-vs-package correlation -0.138), so
    # the budget is established first and the cap follows at step 2.
    assert decision.actuation == GamePowerActuation(soft_pl1_w=21)


def test_above_target_halves_ladder_hold_requirement():
    controller = GamePowerController(
        tb_config(phase_stable_samples=1, ladder_hold_samples=4)
    )
    controller.evaluate(at_target_sample(avg_fps=80.0))  # commit ABOVE + hold(1)
    decision = controller.evaluate(
        at_target_sample(avg_fps=80.0)
    )  # hold(2) == 4//2 -> step up
    assert controller.ladder_step == 1
    assert decision.action == GamePowerAction.TARGET_BALANCE_TRIM


def test_ladder_actuation_folds_battery_rung_sequence():
    # V10: battery sequence P1 G1 P2 G2 P3 G3 C1 C2. Lanes interleaved so a GPU
    # rung the scene cannot sustain does not strand the soft-PL1 lane behind it,
    # and the budget leads because a cap without it does not reduce package power
    # (the V9 S3/S4 CPU caps are NOT in this sequence; verdict-gated only).
    controller = GamePowerController(
        tb_config(phase_stable_samples=1, ladder_hold_samples=1)
    )
    steps = {}
    for _ in range(8):
        decision = controller.evaluate(at_target_sample())
        steps[controller.ladder_step] = decision.actuation
    # P1 = min(slider 22 - 1, ceil(median 22) + 1.5) = min(21, 23.5) = 21
    # (D2: always below the slider).
    assert steps[1] == GamePowerActuation(soft_pl1_w=21)  # P1
    # Step 2 is the pair, which is also the best point the controlled A/B found.
    # G rungs cap GPU max_freq at rp0 * (1 - ratio); deepest G wins cumulatively.
    # D6: the fold carries the ratio; the actuator derives per-GT.
    assert steps[2] == GamePowerActuation(gpu_max_ratio=0.12, soft_pl1_w=21)
    assert steps[3] == GamePowerActuation(gpu_max_ratio=0.12, soft_pl1_w=20)  # P2
    assert steps[5] == GamePowerActuation(gpu_max_ratio=0.22, soft_pl1_w=19)  # P3
    assert steps[6] == GamePowerActuation(gpu_max_ratio=0.30, soft_pl1_w=19)  # P3
    # C1 then C2 fold ecore then pcore EPP on top.
    assert steps[7] == GamePowerActuation(
        gpu_max_ratio=0.30, soft_pl1_w=19, ecore_epp="balance_power"
    )
    assert steps[8] == GamePowerActuation(
        gpu_max_ratio=0.30,
        soft_pl1_w=19,
        ecore_epp="balance_power",
        pcore_epp="balance_power",
    )


def test_ladder_verdict_gated_deep_cpu_caps():
    # The V9 S3/S4 CPU-frequency caps stay available only as verdict-gated deep
    # rungs (S3CAP/S4CAP), appended after C2 when unlocked via the profiler flag.
    controller = GamePowerController(
        tb_config(phase_stable_samples=1, ladder_hold_samples=1, allow_ladder_step_5=True)
    )
    steps = {}
    for _ in range(10):
        decision = controller.evaluate(at_target_sample())
        steps[controller.ladder_step] = decision.actuation
    assert steps[9].pcore_max_khz == 4_000_000  # S3CAP
    assert steps[10].pcore_max_khz == 3_000_000  # S4CAP
    assert steps[10].ecore_max_khz == 2_400_000


def test_ladder_fast_release_drops_all_rungs_on_p95_breach():
    # V10 contract 1.4: fast release drops ALL rungs at once (to step 0).
    controller = GamePowerController(
        tb_config(phase_stable_samples=1, ladder_hold_samples=1)
    )
    _drive_to_step(controller, 3)  # step up to 3
    assert controller.ladder_step == 3
    # p95 breach but still fps-target-satisfied (guard 1.10 < ratio <= 1.15)
    breach = at_target_sample(avg_fps=63.0, p95=19.0)
    # First breach is unconfirmed: hold the rungs rather than resetting on a
    # single noisy 2 s window (ladder_release_samples).
    first = controller.evaluate(breach)
    assert controller.ladder_step == 3
    assert "ladder-breach-unconfirmed" in first.phase_reason_codes
    decision = controller.evaluate(breach)
    assert controller.ladder_step == 0
    assert decision.action == GamePowerAction.TARGET_BALANCE_RELEASE
    assert "ladder-p95-breach" in decision.phase_reason_codes


def test_ladder_backoff_blocks_reentry_of_failed_step():
    controller = GamePowerController(
        tb_config(phase_stable_samples=1, ladder_hold_samples=1, ladder_backoff_s=1000.0)
    )
    _drive_to_step(controller, 3)
    # Two consecutive breaches are required before the fast release fires.
    controller.evaluate(at_target_sample(avg_fps=63.0, p95=19.0))
    controller.evaluate(at_target_sample(avg_fps=63.0, p95=19.0))  # fast release 3 -> 0
    assert controller.ladder_step == 0
    # Climb: 0 -> 1 -> 2, then blocked at 2 because step 3 is in backoff.
    controller.evaluate(at_target_sample())
    controller.evaluate(at_target_sample())
    assert controller.ladder_step == 2
    decision = controller.evaluate(at_target_sample())
    assert controller.ladder_step == 2
    assert "ladder-backoff-active" in decision.phase_reason_codes


def test_ladder_top_of_sequence_is_locked_with_no_verdict_reason():
    # Battery base sequence is 8 rungs; beyond C2 the deep CPU-cap rungs stay
    # locked until a verdict entry exists.
    controller = GamePowerController(
        tb_config(phase_stable_samples=1, ladder_hold_samples=1)
    )
    _drive_to_step(controller, 8)
    assert controller.ladder_step == 8
    decision = controller.evaluate(at_target_sample())
    assert controller.ladder_step == 8
    assert "no-verdict-for-context" in decision.phase_reason_codes


def test_governor_ladder_transition_restores_then_applies_absolute():
    samples = [at_target_sample() for _ in range(3)]
    observer = FakeObserver(samples)
    actuator = RecordingActuator()
    governor = GamePowerGovernor(
        config=tb_config(phase_stable_samples=1, ladder_hold_samples=1),
        observer=observer,
        actuator=actuator,
    )
    asyncio.run(governor.run_iterations(3))
    kinds = [event[0] for event in actuator.events]
    # First trim: snapshot then apply. Second trim: restore-to-baseline then apply.
    # V10 battery rungs 1-3 (G1/G2/G3) carry only GPU intent, so the CPU actuator
    # is invoked via the uniform "apply" path; the restore-to-baseline discipline
    # between differing absolute states is what this proves.
    assert "snapshot" in kinds
    assert "restore" in kinds
    applies = [event for event in actuator.events if event[0] in {"apply", "apply-per-class"}]
    assert len(applies) >= 2


# ---------------------------------------------------------------------------
# V9 defect fixes: target-balance control loop (C1..C7)
# ---------------------------------------------------------------------------
def test_c1_non_target_appid_restores_and_releases_lanes():
    controller = GamePowerController(
        tb_config(
            phase_stable_samples=1, ladder_hold_samples=1, target_appid="1091500"
        )
    )
    _drive_to_step(controller, 3)
    assert controller.ladder_step == 3
    decision = controller.evaluate(at_target_sample(appid="999999"))
    assert decision.phase == GamePowerPhase.NO_GAME
    assert "non-target-game" in decision.phase_reason_codes
    assert decision.action == GamePowerAction.RESTORE
    assert decision.actuation is None
    assert controller.ladder_step == 0
    assert decision.gated_lanes["background_shaping"]["state"] == "released"


def test_c2_refresh_config_restores_target_balance_snapshot_on_mode_change():
    tb = tb_config(phase_stable_samples=1, ladder_hold_samples=1)
    observe = GamePowerConfig(mode=GamePowerMode.OBSERVE)
    seq = [tb, tb, tb, observe]
    calls = {"i": 0}

    def provider(_base):
        cfg = seq[min(calls["i"], len(seq) - 1)]
        calls["i"] += 1
        return cfg

    observer = FakeObserver([at_target_sample() for _ in range(4)])
    actuator = RecordingActuator()
    governor = GamePowerGovernor(
        config=tb, observer=observer, actuator=actuator, config_provider=provider
    )
    asyncio.run(governor.run_iterations(3))
    assert governor._applied_actuation is not None
    restores_before = sum(1 for e in actuator.events if e[0] == "restore")
    asyncio.run(governor.run_iterations(1))
    assert governor._applied_actuation is None
    assert governor._snapshot is None
    restores_after = sum(1 for e in actuator.events if e[0] == "restore")
    assert restores_after > restores_before


def test_c3_ladder_records_backoff_on_real_target_miss():
    controller = GamePowerController(
        tb_config(
            phase_stable_samples=1, ladder_hold_samples=1, ladder_backoff_s=1000.0
        )
    )
    _drive_to_step(controller, 3)
    assert controller.ladder_step == 3
    miss = phase_sample(avg_fps=50.0, p95=25.0, render_busy=0.75, age=100.0)
    # Leaving the band on the first miss hands power back but keeps the ladder
    # position (unconfirmed); the backoff is only earned once the miss repeats.
    first = controller.evaluate(miss)
    assert first.phase == GamePowerPhase.BELOW_TARGET_GPU_BOUND
    assert "ladder-target-miss" not in first.phase_reason_codes
    assert controller.ladder_step == 3
    decision = controller.evaluate(miss)
    assert decision.phase == GamePowerPhase.BELOW_TARGET_GPU_BOUND
    assert "ladder-target-miss" in decision.phase_reason_codes
    assert controller.ladder_step == 0
    controller.evaluate(at_target_sample())  # 0 -> 1
    controller.evaluate(at_target_sample())  # 1 -> 2
    blocked = controller.evaluate(at_target_sample())  # step 3 in backoff
    assert controller.ladder_step == 2
    assert "ladder-backoff-active" in blocked.phase_reason_codes


def test_c4a_unknown_below_target_releases_ladder_and_records_backoff():
    controller = GamePowerController(
        tb_config(
            phase_stable_samples=1, ladder_hold_samples=1, ladder_backoff_s=1000.0
        )
    )
    _drive_to_step(controller, 4)
    assert controller.ladder_step == 4
    unknown = phase_sample(
        avg_fps=50.0, p95=25.0, render_busy=0.5, uncore_w=2.0, core_w=4.0, age=100.0
    )
    # Same confirmation rule in the UNKNOWN branch (C4a).
    first = controller.evaluate(unknown)
    assert first.phase == GamePowerPhase.UNKNOWN
    assert controller.ladder_step == 4
    decision = controller.evaluate(unknown)
    assert decision.phase == GamePowerPhase.UNKNOWN
    assert decision.action == GamePowerAction.TARGET_BALANCE_RELEASE
    assert decision.actuation is None
    assert "unknown-below-target-release" in decision.phase_reason_codes
    assert controller.ladder_step == 0
    last = None
    for _ in range(5):
        last = controller.evaluate(at_target_sample())
    assert controller.ladder_step == 3
    assert "ladder-backoff-active" in last.phase_reason_codes


def test_c16_allow_ladder_step_5_reaches_s5_without_verdict():
    controller = GamePowerController(
        tb_config(
            phase_stable_samples=1, ladder_hold_samples=1, allow_ladder_step_5=True
        )
    )
    last = None
    for _ in range(10):
        last = controller.evaluate(at_target_sample())
    assert controller.ladder_step == 10
    assert last.gated_lanes["ladder_deep_step"]["state"] == "active"


def test_c16_cli_flag_allow_ladder_step_5_default_off_and_parsed():
    parser = game_power.build_parser()
    off = game_power.config_from_args(parser.parse_args(["--mode", "target-balance"]))
    assert off.allow_ladder_step_5 is False
    on = game_power.config_from_args(
        parser.parse_args(["--mode", "target-balance", "--allow-ladder-step-5"])
    )
    assert on.allow_ladder_step_5 is True


def test_c6_frame_stall_does_not_load_when_target_satisfied():
    sample = at_target_sample(stalled=True, core_w=13.0)
    phase, codes = classify_game_power_phase(tb_config(), sample)
    assert phase == GamePowerPhase.AT_TARGET
    assert "frame-feed-stalled" not in codes


def test_c6_frame_stall_still_loads_when_target_not_satisfied():
    sample = phase_sample(
        avg_fps=40.0, p95=30.0, fps=60.0, stalled=True, core_w=13.0, age=100.0
    )
    phase, codes = classify_game_power_phase(tb_config(), sample)
    assert phase == GamePowerPhase.LOADING
    assert "frame-feed-stalled" in codes


def test_c7_loading_exit_requires_cadence_at_ratio():
    controller = GamePowerController(
        tb_config(loading_exit_samples=3, loading_exit_fps_ratio=0.7)
    )
    controller.evaluate(phase_sample(age=5.0))
    assert controller.committed_phase == GamePowerPhase.LOADING
    low = phase_sample(avg_fps=30.0, p95=40.0, fps=60.0, age=100.0)
    for _ in range(5):
        controller.evaluate(low)
        assert controller.committed_phase == GamePowerPhase.LOADING
    good = at_target_sample()
    for _ in range(2):
        controller.evaluate(good)
        assert controller.committed_phase == GamePowerPhase.LOADING
    controller.evaluate(good)
    assert controller.committed_phase == GamePowerPhase.AT_TARGET
