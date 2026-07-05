import asyncio

from steamos_intel_handheld import game_power_control, power_control
from steamos_intel_handheld.game_power import (
    FrameTargetTelemetry,
    GamePowerSample,
    RaplPowerWindow,
)


def test_wait_and_serve_prepares_mangohud_sensors_before_wait(monkeypatch):
    events = []

    def fake_prepare(args):
        events.append("prepare")

    def fake_wait(user, timeout_s, interval_s):
        events.append("wait")

    async def fake_serve(args):
        events.append("serve")

    monkeypatch.setattr(power_control, "prepare_mangohud_sensors_from_args", fake_prepare)
    monkeypatch.setattr(power_control, "wait_for_user_steamos_manager", fake_wait)
    monkeypatch.setattr(power_control, "serve", fake_serve)

    power_control.main(["wait-and-serve", "--prepare-mangohud-sensors"])

    assert events == ["prepare", "wait", "serve"]


def test_parser_enables_guarded_msi_claw_ec_backend():
    args = power_control.build_parser().parse_args(["serve", "--apply-msi-claw-ec"])
    backend = power_control.build_backend(args)

    assert backend.apply_msi_claw_ec is True


def test_parser_configures_ec_write_debounce_ms():
    args = power_control.build_parser().parse_args(
        ["serve", "--apply-msi-claw-ec", "--ec-write-debounce-ms", "750"]
    )
    backend = power_control.build_backend(args)

    assert backend.ec_write_debounce_ms == 750


def test_parser_configures_tdp_policy_mode_and_power_source_override():
    args = power_control.build_parser().parse_args(
        [
            "serve",
            "--tdp-policy",
            "battery-maxq",
            "--power-source-override",
            "battery",
        ]
    )
    backend = power_control.build_backend(args)

    assert backend.tdp_policy_mode == power_control.TdpPolicyMode.BATTERY_MAXQ
    assert backend.power_source_override == power_control.PowerSource.BATTERY


def test_parser_configures_power_source_poll_interval():
    args = power_control.build_parser().parse_args(["serve", "--power-source-poll-s", "5"])
    backend = power_control.build_backend(args)

    assert backend.power_source_poll_s == 5.0


def test_parser_configures_msi_claw_ec_shift_policy():
    args = power_control.build_parser().parse_args(
        ["serve", "--msi-claw-ec-shift-policy", "profile"]
    )
    backend = power_control.build_backend(args)

    assert backend.msi_claw_ec_shift_policy == power_control.MsiClawEcShiftPolicy.PROFILE


def test_parser_configures_game_power_defaults_gpu_priority_epp_only():
    args = power_control.build_parser().parse_args(["serve"])
    config = power_control.build_game_power_config(args)

    assert config.mode == power_control.GamePowerMode.GPU_PRIORITY
    assert config.poll_s == 2.0
    assert config.epp == "balance_power"
    assert config.pcore_max_khz == 3_000_000
    assert config.ecore_max_khz == 2_400_000
    assert config.cpu_cap_enabled is False
    assert config.cpu_cap_core_share_threshold == 0.30
    assert config.target_appid is None
    assert power_control.build_game_power_governor(args) is not None
    assert args.game_power_hint_cache == "/var/lib/steamos-intel-handheld/game-power-hints.json"
    assert (
        args.game_power_runtime_snapshot_file
        == "/run/steamos-intel-handheld/game-power-runtime.json"
    )


def test_build_game_power_governor_wires_runtime_snapshot_path(tmp_path):
    args = power_control.build_parser().parse_args(
        [
            "serve",
            "--sysfs-root",
            str(tmp_path / "sys"),
            "--game-power-runtime-snapshot-file",
            str(tmp_path / "runtime.json"),
        ]
    )

    governor = power_control.build_game_power_governor(args)

    assert governor is not None
    assert governor.runtime_snapshot_path == tmp_path / "runtime.json"


def test_build_game_power_governor_wires_power_source_context_provider(tmp_path):
    args = power_control.build_parser().parse_args(
        [
            "serve",
            "--sysfs-root",
            str(tmp_path / "sys"),
            "--game-power-hint-cache",
            str(tmp_path / "hints.json"),
            "--power-source-override",
            "battery",
        ]
    )
    backend = power_control.build_backend(args)

    governor = power_control.build_game_power_governor(args, backend=backend)

    assert governor is not None
    assert governor.hint_store is not None
    assert governor.hint_context_provider is not None


def test_build_game_power_governor_wires_manual_fps_target_provider(tmp_path):
    control_file = tmp_path / "game-power-control.json"
    game_power_control.set_fps_target(control_file, 45, source="decky")
    args = power_control.build_parser().parse_args(
        [
            "serve",
            "--sysfs-root",
            str(tmp_path / "sys"),
            "--game-power-control-file",
            str(control_file),
        ]
    )

    governor = power_control.build_game_power_governor(args)

    assert governor is not None
    assert governor.observer.frame_target_provider() == FrameTargetTelemetry(
        fps_target=45.0,
        source="manual",
        confidence="high",
    )


def test_game_power_hint_context_requires_known_fps_target(monkeypatch, tmp_path):
    monkeypatch.setattr(
        power_control,
        "_game_power_topology_signature",
        lambda _root: "cpu=p+e",
    )
    monkeypatch.setattr(
        power_control,
        "_game_power_os_signature",
        lambda _root: "kernel=6.16;driver=xe",
    )
    args = power_control.build_parser().parse_args(
        [
            "serve",
            "--sysfs-root",
            str(tmp_path / "sys"),
            "--power-source-override",
            "battery",
        ]
    )
    backend = power_control.build_backend(args)
    provider = power_control._build_game_power_hint_context_provider(args, backend)
    base_sample = GamePowerSample(
        appid="1091500",
        rapl=RaplPowerWindow(duration_s=2.0, package_w=12.0),
        pl1_w=12,
        fdinfo_busy={"render": 0.9},
    )

    targetless = provider(base_sample)
    known_target = provider(
        GamePowerSample(
            appid="1091500",
            rapl=base_sample.rapl,
            pl1_w=12,
            fdinfo_busy=base_sample.fdinfo_busy,
            frame_target=FrameTargetTelemetry(
                fps_target=45.0,
                source="manual",
                confidence="high",
            ),
        )
    )

    assert targetless is not None
    assert targetless.fps_target == "unknown"
    assert targetless.complete is False
    assert known_target is not None
    assert known_target.fps_target == "45"
    assert known_target.complete is True


def test_gamescope_fps_target_parser_accepts_refresh_limit_flags():
    assert power_control.frame_target_from_gamescope_args(["gamescope", "-r", "45"]) == (
        FrameTargetTelemetry(fps_target=45.0, source="gamescope", confidence="medium")
    )
    assert power_control.frame_target_from_gamescope_args(
        ["gamescope", "--framerate-limit", "40"]
    ) == FrameTargetTelemetry(
        fps_target=40.0,
        source="gamescope",
        confidence="medium",
    )


def test_parser_configures_game_power_gpu_priority_options():
    args = power_control.build_parser().parse_args(
        [
            "serve",
            "--game-power-mode",
            "gpu-priority",
            "--game-power-poll-s",
            "1.5",
            "--game-power-epp",
            "balance_power",
            "--game-power-pcore-max-mhz",
            "3000",
            "--game-power-ecore-max-mhz",
            "2400",
            "--game-power-cpu-cap",
            "on",
            "--game-power-cpu-cap-core-share-threshold",
            "0.31",
            "--game-power-target-appid",
            "1091500",
        ]
    )
    config = power_control.build_game_power_config(args)

    assert config.mode == power_control.GamePowerMode.GPU_PRIORITY
    assert config.poll_s == 1.5
    assert config.epp == "balance_power"
    assert config.pcore_max_khz == 3_000_000
    assert config.ecore_max_khz == 2_400_000
    assert config.cpu_cap_enabled is True
    assert config.cpu_cap_core_share_threshold == 0.31
    assert config.target_appid == "1091500"


def test_service_task_lifecycle_restores_game_power_governor_on_stop():
    events = []

    class FakeGovernor:
        def restore(self):
            events.append("restore")

    async def background_task():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            events.append("cancelled")
            raise

    async def scenario():
        stop_future = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(background_task())
        stop_future.set_result(None)

        await power_control.run_service_tasks_until_stopped(
            stop_future=stop_future,
            tasks=[task],
            game_power_governor=FakeGovernor(),
        )

    asyncio.run(scenario())

    assert "cancelled" in events
    assert "restore" in events
