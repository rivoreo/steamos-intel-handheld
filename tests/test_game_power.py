import json
from pathlib import Path

from steamos_intel_handheld import game_power
from steamos_intel_handheld.game_power import (
    CpuPolicyActuator,
    CpuPolicyClass,
    CpuPolicySnapshot,
    EnergyReading,
    FrameTargetTelemetry,
    GamePowerAction,
    GamePowerClassification,
    GamePowerConfig,
    GamePowerController,
    GamePowerGovernor,
    GamePowerMode,
    GamePowerSample,
    GameProcess,
    PressureSignal,
    PressureTelemetry,
    RaplObserver,
    RaplPowerWindow,
    classify_game_power_sample,
    compute_fdinfo_busy,
    compute_rapl_power_window,
    discover_cpu_policies,
    find_steam_game_processes,
    parse_fdinfo_engine_times,
    parse_pressure_signal,
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

    def apply(self, *, epp, pcore_max_khz=None, ecore_max_khz=None):
        self.events.append(("apply", epp, pcore_max_khz, ecore_max_khz))

    def restore(self, snapshot):
        self.events.append(("restore", snapshot))


class FailingActuator(RecordingActuator):
    def apply(self, *, epp, pcore_max_khz=None, ecore_max_khz=None):
        self.events.append(("apply-failed", epp, pcore_max_khz, ecore_max_khz))
        raise OSError("simulated sysfs write failure")


class RestoreFailingActuator(RecordingActuator):
    def restore(self, snapshot):
        self.events.append(("restore-failed", snapshot))
        raise OSError("simulated restore failure")


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


def test_build_parser_accepts_jsonl_output_format():
    args = game_power.build_parser().parse_args(["--output-format", "jsonl"])

    assert args.output_format == "jsonl"


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
