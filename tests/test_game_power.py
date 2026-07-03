from pathlib import Path

from steamos_intel_handheld import game_power
from steamos_intel_handheld.game_power import (
    CpuPolicyActuator,
    CpuPolicyClass,
    CpuPolicySnapshot,
    EnergyReading,
    GamePowerAction,
    GamePowerConfig,
    GamePowerController,
    GamePowerGovernor,
    GamePowerMode,
    GamePowerSample,
    RaplObserver,
    RaplPowerWindow,
    compute_fdinfo_busy,
    compute_rapl_power_window,
    discover_cpu_policies,
    parse_fdinfo_engine_times,
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
    assert [mode.value for mode in GamePowerMode] == ["off", "observe", "gpu-priority"]


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
    )


def test_controller_waits_for_hysteresis_before_applying_gpu_priority():
    controller = GamePowerController(GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY))

    first = controller.evaluate(make_sample())
    second = controller.evaluate(make_sample())

    assert first.action == GamePowerAction.OBSERVE_ONLY
    assert second.action == GamePowerAction.GPU_PRIORITY_EPP


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

    def apply(self, *, epp, pcore_max_khz=None, ecore_max_khz=None):
        self.events.append(("apply", epp, pcore_max_khz, ecore_max_khz))

    def restore(self, snapshot):
        self.events.append(("restore", snapshot))


class FailingActuator(RecordingActuator):
    def apply(self, *, epp, pcore_max_khz=None, ecore_max_khz=None):
        self.events.append(("apply-failed", epp, pcore_max_khz, ecore_max_khz))
        raise OSError("simulated sysfs write failure")


def test_build_parser_defaults_game_power_cli_to_observe_for_standalone_probe():
    args = game_power.build_parser().parse_args([])
    config = game_power.config_from_args(args)

    assert config.mode == GamePowerMode.OBSERVE
    assert config.cpu_cap_enabled is False


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
