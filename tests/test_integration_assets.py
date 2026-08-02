import subprocess
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_high_risk_scripts_require_explicit_authority_flags():
    cases = [
        ("verify-on-device.sh", ["root@example.invalid"], "--allow-device"),
        ("verify-game-power-on-device.sh", ["root@example.invalid"], "--allow-device"),
        ("profile-game-power-on-device.sh", ["root@example.invalid"], "--allow-device"),
        ("steamos-qemu-build-env.sh", ["fetch-raw"], "--allow-qemu"),
    ]

    for script_name, args, expected_flag in cases:
        result = subprocess.run(
            [str(ROOT / "scripts" / script_name), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert expected_flag in result.stderr


def test_steamos_manager_remote_config_uses_rivoreo_bus_name():
    config = (ROOT / "data/steamos-manager/remotes.d/99-rivoreo-power-control.toml").read_text()

    assert "[TdpLimit1]" in config
    assert 'bus_name = "org.rivoreo.SteamOSManager.PowerControl"' in config
    assert 'object_path = "/org/rivoreo/SteamOSManager/PowerControl"' in config
    assert "/com/steampowered/SteamOSManager1" not in config


def test_power_control_exports_steamos_manager_canonical_object_path():
    source = (ROOT / "src/steamos_intel_handheld/power_control.py").read_text()

    assert 'STEAMOS_MANAGER_OBJ_PATH = "/com/steampowered/SteamOSManager1"' in source
    assert "for object_path in (OBJ_PATH, STEAMOS_MANAGER_OBJ_PATH):" in source


def test_systemd_unit_waits_for_user_steamos_manager_before_serving_remote():
    unit = (ROOT / "data/systemd/steamos-intel-handheld-power-control.service").read_text()

    assert "Wants=steamos-intel-handheld-restore.service" in unit
    assert "After=steamos-intel-handheld-restore.service" in unit
    assert " wait-and-serve " in unit
    assert " serve " not in unit
    assert "ExecStart=/opt/steamos-intel-handheld/bin/steamos-intel-handheld-power-control" in unit
    assert "PATH=/etc/rivoreo/bin" not in unit
    assert "--user deck" in unit
    assert "--apply-rapl" in unit
    assert "--apply-msi-claw-ec" in unit
    assert "--ec-write-debounce-ms 750" in unit
    assert "--tdp-policy auto" in unit
    assert "--msi-claw-ec-shift-policy tdp-threshold" in unit
    assert "--prepare-mangohud-sensors" in unit
    assert "StateDirectory=steamos-intel-handheld" in unit


def test_power_control_service_enables_game_power_governor_by_default():
    unit = (ROOT / "data/systemd/steamos-intel-handheld-power-control.service").read_text()

    assert "--game-power-mode target-balance" in unit
    assert "--game-power-target-appid" not in unit
    assert "--game-power-cpu-cap off" in unit
    assert "--game-power-cpu-cap on" not in unit
    assert "--game-power-pcore-max-mhz 3000" in unit
    assert "--game-power-ecore-max-mhz 2400" in unit
    assert "--game-power-cpu-cap-core-share-threshold 0.30" in unit
    assert (
        "--game-power-control-file "
        "/var/lib/steamos-intel-handheld/game-power-control.json" in unit
    )
    assert (
        "--game-power-frame-feed-file "
        "/run/user/1000/steamos-intel-handheld/frame-feed.json"
        in unit
    )


def test_restore_service_unit_runs_restore_cli_before_power_control():
    unit = (ROOT / "data/systemd/steamos-intel-handheld-restore.service").read_text()

    assert "Description=Restore steamos-intel-handheld /etc integration files" in unit
    assert "DefaultDependencies=no" in unit
    assert "Before=multi-user.target" in unit
    assert (
        "ExecStart=/opt/steamos-intel-handheld/bin/steamos-intel-handheld-restore-etc --apply"
        in unit
    )
    assert "WantedBy=multi-user.target" in unit


def test_restore_manifest_lists_main_package_artifacts_without_mangoapp_dropin():
    manifest = (ROOT / "data/restore/manifest.toml").read_text()

    assert (
        'destination = "/etc/dbus-1/system.d/'
        'org.rivoreo.SteamOSManager.PowerControl.conf"'
    ) in manifest
    assert "/etc/steamos-manager/remotes.d/99-rivoreo-power-control.toml" in manifest
    assert (
        'destination = "/etc/systemd/system/'
        'steamos-intel-handheld-power-control.service"'
    ) in manifest
    assert "steamos-intel-handheld-steamos-manager-remote.service" not in manifest
    assert (
        'destination = "/etc/systemd/user/gamescope-session.service.d/'
        '20-native-panel-resolution.conf"'
    ) in manifest
    assert (
        'destination = "/etc/systemd/user/gamescope-session.service.wants/'
        'steamos-intel-handheld-gamescope-display.service"'
    ) in manifest
    assert (
        'target = "../steamos-intel-handheld-gamescope-display.service"'
        in manifest
    )
    assert (
        'destination = "/etc/gamescope/scripts/00-steamos-intel-handheld/displays/'
        'msi.claw-8-ai-plus.lcd.lua"'
    ) in manifest
    assert (
        'destination = "/etc/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg"'
        in manifest
    )
    assert 'destination = "/etc/wireguard/rncn-steamdeck.conf"' in manifest
    assert 'policy = "health-check"' in manifest
    assert "/etc/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf" not in manifest


def test_restore_manifest_manages_steamos_manager_remote_without_bridge():
    payload = tomllib.loads((ROOT / "data/restore/manifest.toml").read_text())
    artifacts = {item["destination"]: item for item in payload["artifact"]}

    remote = artifacts["/etc/steamos-manager/remotes.d/99-rivoreo-power-control.toml"]
    assert remote["source"] == "steamos-manager/remotes.d/99-rivoreo-power-control.toml"
    assert remote["actions"] == []
    assert "service_restarts" not in remote
    assert (
        "/etc/systemd/system/steamos-intel-handheld-steamos-manager-remote.service"
        not in artifacts
    )


def test_mangoapp_dropin_enables_game_power_frame_feed():
    dropin = (
        ROOT
        / "data/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf"
    ).read_text()

    assert "Environment=MANGOAPP_FRAME_FEED=1" in dropin
    # The custom mangoapp launch must survive so the frame feed exporter runs.
    assert "ExecStart=/opt/steamos-intel-handheld/bin/mangoapp" in dropin
    # The restore fragment (not the main manifest) still owns this drop-in, so the
    # frame-feed env is covered by restore-etc without leaking into the main list.
    assert (
        "/etc/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf"
        not in (ROOT / "data/restore/manifest.toml").read_text()
    )
    fragment = (ROOT / "data/restore/manifest.d/10-mangoapp.toml").read_text()
    assert (
        "/etc/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf"
        in fragment
    )


def test_mangoapp_restore_fragment_owns_only_mangoapp_dropin():
    fragment = (ROOT / "data/restore/manifest.d/10-mangoapp.toml").read_text()

    assert (
        'destination = "/etc/systemd/user/gamescope-mangoapp.service.d/'
        '10-rivoreo-mangoapp.conf"'
    ) in fragment
    assert (
        'source = "systemd/user/gamescope-mangoapp.service.d/'
        '10-rivoreo-mangoapp.conf"'
    ) in fragment
    assert 'service_restarts = ["gamescope-mangoapp.service"]' in fragment
    assert "rncn-steamdeck" not in fragment


def test_networkmanager_dispatcher_is_packaged_as_executable_source():
    dispatcher = ROOT / "data/NetworkManager/dispatcher.d/90-rncn-steamdeck-wg"
    script = dispatcher.read_text()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert dispatcher.stat().st_mode & 0o111
    assert "rncn-steamdeck" in script
    assert 'systemctl restart "$service"' not in script
    assert 'systemctl is-active --quiet "$service"' in script
    assert 'systemctl reset-failed "$service"' in script
    assert 'systemctl start "$service"' in script


WRAPPERS = {
    "steamos-intel-handheld-power-control": "power_control",
    "steamos-intel-handheld-ec-control": "ec_charge_control",
    "steamos-intel-handheld-restore-etc": "restore_etc",
    "steamos-intel-handheld-game-power": "game_power",
    "steamos-intel-handheld-game-power-profile": "game_power_profile",
    "steamos-intel-handheld-game-power-control": "game_power_control",
}


def _install_payload() -> str:
    """Both install paths run this, so asserting here covers the one-line
    installer and the developer installer at once."""
    return (ROOT / "scripts/install-payload.sh").read_text()


def test_manual_installer_installs_every_cli_wrapper():
    payload = _install_payload()

    assert '/opt/steamos-intel-handheld/bin/$name' in payload
    assert 'exec /usr/bin/python3 -m steamos_intel_handheld.$module "\\$@"' in payload
    for wrapper, module in WRAPPERS.items():
        assert f"write_wrapper {wrapper} {module}" in payload


def test_manual_installer_installs_restore_service_and_canonical_artifacts():
    payload = _install_payload()

    assert "write_wrapper steamos-intel-handheld-restore-etc restore_etc" in payload
    assert "steamos-intel-handheld-steamos-manager-remote" in payload
    assert "systemctl disable steamos-intel-handheld-steamos-manager-remote.service" in payload
    assert "systemctl enable --now steamos-intel-handheld-restore.service" in payload
    assert "/opt/steamos-intel-handheld/bin/steamos-intel-handheld-restore-etc --apply" in payload
    # Every managed file has to reach the artifact tree as well as its live
    # location, or the restore service has nothing to replay after an update.
    assert 'install -m "$mode" "$src/data/$relative" "$artifact_root/$relative"' in payload


def test_manual_installer_installs_both_decky_plugins():
    payload = _install_payload()

    assert "install_decky_plugin steamos-intel-handheld-ec" in payload
    assert "install_decky_plugin steamos-intel-handheld-game-power" in payload
    assert '/home/deck/homebrew/plugins/$plugin' in payload
    assert '"$plugin_src/dist/index.js" "$plugin_dst/dist/index.js"' in payload
    assert '"$plugin_src/plugin.json" "$plugin_dst/plugin.json"' in payload
    assert '"$plugin_src/package.json" "$plugin_dst/package.json"' in payload
    assert "report_decky_loader_status" in payload
    assert "/home/deck/homebrew/services/PluginLoader" in payload
    assert "Decky Loader not detected" in payload


def test_developer_installer_ships_the_plugin_sources_it_installs():
    """The payload installs from an unpacked tree, so the SSH path has to put
    those files on the far end first."""
    script = (ROOT / "scripts/install-on-device.sh").read_text()

    for plugin in ("steamos-intel-handheld-ec", "steamos-intel-handheld-game-power"):
        assert f"decky/{plugin}/plugin.json" in script
        assert f"decky/{plugin}/package.json" in script
        assert f"decky/{plugin}/dist/index.js" in script
        assert f"decky/{plugin}/main.py" in script
    assert "scripts/install-payload.sh" in script


def test_arch_package_installs_decky_game_power_plugin():
    pkgbuild = (ROOT / "packaging/arch/PKGBUILD").read_text()

    assert 'game_power_decky_src="decky/steamos-intel-handheld-game-power"' in pkgbuild
    assert (
        'game_power_decky_dst="$pkgdir/home/deck/homebrew/plugins/'
        'steamos-intel-handheld-game-power"'
    ) in pkgbuild
    assert '"$game_power_decky_src/plugin.json"' in pkgbuild
    assert '"$game_power_decky_src/package.json"' in pkgbuild
    assert '"$game_power_decky_src/main.py"' in pkgbuild
    assert '"$game_power_decky_src/dist/index.js"' in pkgbuild


def test_arch_package_declares_game_power_control_console_script():
    pyproject = (ROOT / "pyproject.toml").read_text()

    assert (
        'steamos-intel-handheld-game-power-control = '
        '"steamos_intel_handheld.game_power_control:main"'
    ) in pyproject


def test_gamescope_display_helper_sets_runtime_composite_force():
    helper = (ROOT / "data/bin/steamos-intel-handheld-gamescope-display").read_text()

    assert "GAMESCOPECTL" in helper
    assert "GAMESCOPE_DISPLAY_APPLY_ATTEMPTS" in helper
    assert "GAMESCOPE_DISPLAY_APPLY_INTERVAL_SEC" in helper
    assert '"$gamescopectl" composite_force 1' in helper
    assert '"$gamescopectl" composite_force 0' in helper
    assert 'for attempt in $(seq 1 "$apply_attempts")' in helper
    assert "gamescope-environment" in helper
    assert "drm_single_plane_optimizations" not in helper


def test_gamescope_display_user_service_runs_after_gamescope_session():
    service = (
        ROOT / "data/systemd/user/steamos-intel-handheld-gamescope-display.service"
    ).read_text()

    assert "After=gamescope-session.service" in service
    assert "BindsTo=gamescope-session.service" in service
    assert "PartOf=gamescope-session.service" in service
    assert "PartOf=gamescope-session.target" not in service
    assert (
        "ExecStart=/opt/steamos-intel-handheld/bin/steamos-intel-handheld-gamescope-display apply"
        in service
    )
    assert "Type=simple" in service
    assert "Type=oneshot" not in service
    assert "RemainAfterExit=" not in service
    assert "TimeoutStartSec=" not in service
    assert "WantedBy=gamescope-session.service" in service
    assert "WantedBy=gamescope-session.target" not in service


def test_gamescope_session_prefers_native_panel_resolution_wrapper():
    dropin = (
        ROOT
        / "data/systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf"
    ).read_text()
    script = (ROOT / "scripts/configure-gamescope-display-workaround.sh").read_text()

    assert "Environment=PATH=/opt/steamos-intel-handheld/bin:" in dropin
    assert "/opt/steamos-intel-handheld/bin/gamescope" in script
    assert "20-native-panel-resolution.conf" in script


def test_msi_claw_gamescope_known_display_profile_matches_internal_panel():
    profile = (
        ROOT
        / "data/gamescope/scripts/00-steamos-intel-handheld/displays/msi.claw-8-ai-plus.lcd.lua"
    ).read_text()

    assert "gamescope.config.known_displays.msi_claw_8_ai_plus_lcd" in profile
    assert 'pretty_name = "MSI Claw 8 AI+ LCD"' in profile
    assert "supported = false" in profile
    assert "48, 49, 50" in profile
    assert "118, 119, 120" in profile
    assert "gamescope.modegen.set_resolution(mode, 1920, 1200)" in profile
    assert "gamescope.modegen.set_h_timings(mode, 48, 32, 80)" in profile
    assert "gamescope.modegen.set_v_timings(mode, 54, 6, 4)" in profile
    assert '{ vendor = "CSW", model = "PN8007QB1-2", product = 0x0801 }' in profile


def test_gamescope_workaround_installs_msi_claw_known_display_profile():
    script = (ROOT / "scripts/configure-gamescope-display-workaround.sh").read_text()

    assert (
        "data/gamescope/scripts/00-steamos-intel-handheld/displays/"
        "msi.claw-8-ai-plus.lcd.lua"
    ) in script
    assert (
        "remote_gamescope_profile="
        '"/etc/gamescope/scripts/00-steamos-intel-handheld/displays/'
        'msi.claw-8-ai-plus.lcd.lua"'
    ) in script
    assert (
        "install -d -m 0755 /opt/steamos-intel-handheld/bin /etc/systemd/user "
        "/etc/systemd/user/gamescope-session.service.d "
        "/etc/systemd/user/gamescope-session.service.wants "
        "/etc/gamescope/scripts/00-steamos-intel-handheld/displays"
    ) in script
    assert "'$remote_gamescope_profile'" in script
    assert "rm -f '$remote_gamescope_profile'" in script
    assert "/opt/steamos-intel-handheld/share/etc-artifacts" in script
    assert "systemd/user/gamescope-session.service.d/20-native-panel-resolution.conf" in script


def test_device_verifier_checks_mangohud_cpu_power_sensor_access():
    script = (ROOT / "scripts/verify-on-device.sh").read_text()

    assert "verify_mangohud_cpu_power_sensor" in script
    assert "MangoHud CPU power sensor" in script
    assert "energy_uj" in script


def test_device_verifier_checks_mangohud_gpu_power_sensor_access():
    script = (ROOT / "scripts/verify-on-device.sh").read_text()

    assert "verify_mangohud_gpu_power_sensor" in script
    assert "MangoHud GPU power sensor" in script
    assert "uncore" in script
    assert "energy_uj" in script


def test_device_verifier_reports_gpu_temperature_sensor_availability():
    script = (ROOT / "scripts/verify-on-device.sh").read_text()

    assert "report_mangohud_gpu_temperature_sensor" in script
    assert "MangoHud GPU temperature sensor" in script
    assert "/sys/class/drm/renderD*" in script
    assert "temp*_input" in script
    assert "no DRM hwmon temp input is exposed" in script


def test_device_verifier_reports_mangohud_gpu_memory_fdinfo():
    script = (ROOT / "scripts/verify-on-device.sh").read_text()

    assert "report_mangohud_gpu_memory_fdinfo" in script
    assert "MangoHud GPU memory fdinfo" in script
    assert "drm-resident-gtt" in script
    assert "drm-resident-system0" in script


def test_game_power_device_verifier_restores_cpu_policy_snapshot():
    script = (ROOT / "scripts/verify-game-power-on-device.sh").read_text()

    assert "snapshot_cpu_policy()" in script
    assert "restore_cpu_policy()" in script
    assert "assert_cpu_policy_restored()" in script
    assert 'trap restore_cpu_policy EXIT' in script
    assert 'diff -u "$snapshot" "$after"' in script
    assert "steamos-intel-handheld-game-power --mode observe" in script
    assert "steamos-intel-handheld-game-power --mode gpu-priority" in script
    assert "VERIFY_GAME_POWER_CPU_CAP_CORE_SHARE_THRESHOLD" in script
    assert '--cpu-cap-core-share-threshold "$cpu_cap_core_share_threshold"' in script
    assert "drm-resident-vram0" in script


def test_game_power_profile_wrapper_restores_tdp_cpu_policy_and_service_mode():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    assert "snapshot_cpu_policy()" in script
    assert "restore_cpu_policy()" in script
    assert "set_service_game_power_mode()" in script
    assert "restore_service_game_power_mode()" in script
    assert (
        "PROFILE_CONTROL_FILE=/run/steamos-intel-handheld/game-power-profile-control.json"
        in script
    )
    assert "--game-power-control-file $PROFILE_CONTROL_FILE" in script
    assert 'rm -f "$PROFILE_CONTROL_FILE"' in script
    assert "setup_mangohud_controlled_capture()" in script
    assert "restore_mangohud_controlled_capture()" in script
    assert "provider_tdp()" in script
    assert "set_provider_tdp()" in script
    assert "wait_for_power_provider()" in script
    assert 'set_provider_tdp "$current"' in script
    assert 'trap restore_state EXIT' in script
    assert "--game-power-mode $mode" in script
    assert "--game-power-frame-feed-file $FRAME_FEED_FILE" in script
    assert "set_service_game_power_mode off" in script
    assert (
        "--game-power-hint-cache /var/lib/steamos-intel-handheld/game-power-hints.json"
        in script
    )
    assert "sample_cgroup_pressure()" in script
    assert "sample_thread_affinity()" in script
    assert "sample_thread_schedstat()" in script
    assert "sample_process_cgroups()" in script
    assert "collect_cpu_topology()" in script
    assert "discover_fps_target()" in script
    assert "fps-target.discovery.json" in script
    assert "cpu-topology.json" in script
    assert "affinity-advice.json" in script
    assert "thread-schedstat.jsonl" in script
    assert "process-cgroups.jsonl" in script
    assert "background-shaping.json" in script
    assert "--output-format jsonl" in script
    assert "--pressure-jsonl" in script
    assert "--thread-affinity-jsonl" in script
    assert "--thread-schedstat-jsonl" in script
    assert "--cpu-topology-json" in script
    assert "--process-cgroups-jsonl" in script
    assert "PROFILE_GAME_POWER_FPS_TARGET" in script
    assert "--fps-target" in script
    assert "--fps-target-confidence" in script
    assert "replay-action-equivalence" in script
    assert "validate-runtime-telemetry" in script
    assert "runtime-telemetry-contract.json" in script
    assert "profile-runtime-telemetry-contract.json" in script
    assert "steamos-intel-handheld-game-power-profile summarize" in script
    assert "capture_mode" in script
    assert ".cache/game-power/profiles" in script
    assert '"$target:$remote_root/."' in script


def test_game_power_profile_wrapper_wires_frame_feed_path():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    assert (
        'frame_feed_file="${PROFILE_GAME_POWER_FRAME_FEED_FILE:-'
        '/run/user/1000/steamos-intel-handheld/frame-feed.json}"'
        in script
    )
    assert "FRAME_FEED_FILE='$frame_feed_file'" in script
    assert "Environment=MANGOAPP_FRAME_FEED=1" in script
    assert "Environment=MANGOAPP_FRAME_FEED_FILE=$FRAME_FEED_FILE" in script
    assert '--frame-feed-file "$FRAME_FEED_FILE"' in script


def test_game_power_profile_wrapper_supports_controlled_mangohud_capture():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    assert 'if [ "$CAPTURE_MODE" = "controlled" ]; then' in script
    assert "MANGOHUD_CONFIG=" in script
    assert "output_folder=$MANGOHUD_OUTPUT_DIR" in script
    assert 'chmod 0755 "$REMOTE_ROOT"' in script
    assert "gamescope-mangoapp.service.d" in script
    assert "mangohudctl set log_session true" in script
    assert "mangohudctl set log_session false" in script
    assert "collect_controlled_mangohud_csv()" in script
    assert "mangohud-summary.csv" in script
    assert "controlled capture mode is not enabled" not in script


def test_game_power_profile_wrapper_supports_cpu_cap_policy_variants():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    assert "PROFILE_GAME_POWER_REPEATS" in script
    assert "PROFILE_GAME_POWER_CPU_CAP_VARIANTS" in script
    assert "parse_cpu_cap_variant()" in script
    assert "CPU_CAP_VARIANTS_EFFECTIVE" in script
    assert "variant_label:pcore_mhz:ecore_mhz:threshold" in script
    assert "PROFILE_GAME_POWER_EPP" in script
    assert "PROFILE_GAME_POWER_PCORE_MAX_MHZ" in script
    assert "PROFILE_GAME_POWER_ECORE_MAX_MHZ" in script
    assert "PROFILE_GAME_POWER_CPU_CAP_CORE_SHARE_THRESHOLD" in script
    assert 'pcore_max_mhz="${PROFILE_GAME_POWER_PCORE_MAX_MHZ:-3000}"' in script
    assert 'ecore_max_mhz="${PROFILE_GAME_POWER_ECORE_MAX_MHZ:-2400}"' in script
    assert (
        'cpu_cap_core_share_threshold="${PROFILE_GAME_POWER_CPU_CAP_CORE_SHARE_THRESHOLD:-0.30}"'
        in script
    )
    assert "gpu-priority-cpu-cap" in script
    assert "--cpu-cap" in script
    assert '--pcore-max-mhz "$variant_pcore_max_mhz"' in script
    assert '--ecore-max-mhz "$variant_ecore_max_mhz"' in script
    assert '--cpu-cap-core-share-threshold "$variant_core_share_threshold"' in script
    assert '--cpu-cap-enabled "$cpu_cap_enabled"' in script
    assert '--duration-s "$DURATION_S"' in script
    assert '--warmup-s "$WARMUP_S"' in script
    assert '--poll-s "$POLL_S"' in script


def test_game_power_profile_wrapper_supports_background_shaping_policy_variants():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    assert "apply_background_shaping_variant()" in script
    assert "restore_background_shaping_variant()" in script
    assert "gpu-priority-bg-weight" in script
    assert "gpu-priority-bg-uclamp" in script
    assert "apply-background-shaping" in script
    assert "restore-background-shaping" in script
    assert '--variant "$variant"' in script
    assert 'background_shaping_variant="cpu-weight-80"' in script
    assert 'background_shaping_variant="uclamp-max-85"' in script
    assert "background-shaping-writes.json" in script
    assert "background-shaping-restore.json" in script
    assert 'restore_background_shaping_variant "$run_dir" || restored=false' in script


def test_game_power_profile_wrapper_supports_foreground_affinity_policy_variant():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    assert 'affinity_plan_json="${PROFILE_GAME_POWER_AFFINITY_PLAN_JSON:-}"' in script
    assert "remote_affinity_plan_json=" in script
    assert 'scp "$affinity_plan_json" "$target:$remote_affinity_plan_json"' in script
    assert "AFFINITY_PLAN_JSON='$remote_affinity_plan_json'" in script
    assert "resolve_foreground_affinity_candidate()" in script
    assert "foreground_affinity_candidate_field()" in script
    assert "apply_foreground_affinity_variant()" in script
    assert "restore_foreground_affinity_variant()" in script
    assert "gpu-priority-affinity)" in script
    assert 'foreground_affinity_variant="foreground-role-compact"' in script
    assert "resolve-foreground-affinity" in script
    assert "apply-foreground-affinity" in script
    assert "restore-foreground-affinity" in script
    assert "foreground-affinity-candidate.json" in script
    assert "foreground-affinity-writes.json" in script
    assert "foreground-affinity-restore.json" in script
    apply_line = (
        'if ! apply_foreground_affinity_variant "$run_dir" '
        '"$foreground_affinity_variant"; then'
    )
    assert apply_line in script
    assert 'restore_foreground_affinity_variant "$run_dir" || restored=false' in script
    assert "PROFILE_GAME_POWER_AFFINITY_ROLE_KEY" not in script
    assert "PROFILE_GAME_POWER_AFFINITY_CPUS" not in script


def test_game_power_profile_wrapper_aborts_after_unrestored_policy_run():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    assert "failure_marker=\"${remote_root##*/}.failed\"" in script
    assert "FAILURE_MARKER='$failure_marker'" in script
    assert "profile_failed=false" in script
    assert 'if [ "$restored" != true ]; then' in script
    assert 'echo "run did not restore cleanly: $run_dir" >&2' in script
    assert '>"$REMOTE_ROOT/$FAILURE_MARKER"' in script
    assert "break 4" in script
    assert 'if [ -f "$local_root/$failure_marker" ]; then' in script


def test_game_power_profile_wrapper_records_affinity_restore_snapshot():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    assert "snapshot_affinity_restore_state()" in script
    assert "restore_snapshot_relevant_cgroup" in script
    assert "foreground_app_cgroup" in script
    assert 'snapshot_affinity_restore_state "$run_dir/restore-affinity.json"' in script
    assert "--restore-affinity-json" in script
    assert '"$run_dir/restore-affinity.json"' in script


def test_c16_profile_wrapper_supports_uclampmin_and_ladder5_policies():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    # target-balance-uclampmin: run-scoped foreground cpu.uclamp.min floor via
    # the shared guarded writer CLI, with apply/restore evidence artifacts.
    assert "target-balance-uclampmin)" in script
    assert "apply_foreground_uclamp_variant()" in script
    assert "restore_foreground_uclamp_variant()" in script
    assert "apply-foreground-uclamp" in script
    assert "restore-foreground-uclamp" in script
    assert "foreground-uclamp-writes.json" in script
    assert "foreground-uclamp-restore.json" in script
    assert 'foreground_uclamp_variant="foreground-uclamp-min-25"' in script
    assert 'restore_foreground_uclamp_variant "$run_dir" || restored=false' in script

    # target-balance-ladder5: run-scoped CLI flag, never set by the service.
    assert "target-balance-ladder5)" in script
    assert "--allow-ladder-step-5" in script


def test_c18_exit_trap_restores_gpu_floor_and_scx_lavd():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    # The EXIT trap must idempotently restore the GPU floor from the active
    # run's state file and stop scx_lavd if it was started.
    assert "GPU_FLOOR_ACTIVE_RUN_DIR" in script
    assert "SCX_LAVD_ACTIVE_RUN_DIR" in script
    restore_body = script.split("restore_state() {", 1)[1].split("\n}", 1)[0]
    assert "restore_gpu_floor_variant" in restore_body
    assert "stop_scx_lavd_variant" in restore_body


def test_c19_scx_stop_has_bounded_wait_and_sigkill_escalation():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    stop_body = script.split("stop_scx_lavd_variant() {", 1)[1].split("\n}", 1)[0]
    # No unguarded blocking wait on the scx_lavd pid.
    assert 'wait "$SCX_LAVD_PID" 2>/dev/null || true\n' not in stop_body + "\n"
    assert "kill -KILL" in stop_body
    assert "stop_escalated" in stop_body
    # Escalation invalidates the run's sched-ext evidence.
    assert 'payload.get("stop_escalated") is not True' in stop_body


def test_c20_gpu_floor_missing_gt_records_skip_not_abort():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    apply_body = script.split("apply_gpu_floor_variant() {", 1)[1].split(
        "\nrestore_gpu_floor_variant", 1
    )[0]
    assert "missing-gt-freq-dirs" in apply_body
    assert '"skipped": True' in apply_body or '"skipped": true' in apply_body
    restore_body = script.split("restore_gpu_floor_variant() {", 1)[1].split(
        "\n# --- V9 guarded sched_ext lane", 1
    )[0]
    assert 'payload.get("skipped")' in restore_body


def _extract_compare_gpu_freq_snapshots():
    """Extract the D4 comparator function + its tolerance default from the
    profiler so the tests exercise the real shell/python behavior."""

    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()
    tolerance_line = next(
        line
        for line in script.splitlines()
        if line.startswith("GPU_MIN_FREQ_DRIFT_TOLERANCE_MHZ=")
    )
    body = "compare_gpu_freq_snapshots() {" + script.split(
        "compare_gpu_freq_snapshots() {", 1
    )[1].split("\n}", 1)[0] + "\n}"
    return tolerance_line + "\n" + body


def _run_gpu_freq_compare(tmp_path, before, after):
    import subprocess

    (tmp_path / "before").write_text(before)
    (tmp_path / "after").write_text(after)
    snippet = _extract_compare_gpu_freq_snapshots()
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'{snippet}\ncompare_gpu_freq_snapshots "$1" "$2" "$3"',
            "compare",
            str(tmp_path / "before"),
            str(tmp_path / "after"),
            str(tmp_path / "report"),
        ],
        capture_output=True,
        text=True,
    )
    return proc.returncode, (tmp_path / "report").read_text()


def test_d4_gpu_freq_diff_tolerates_slpc_min_drift_on_untouched_gt(tmp_path):
    # Autonomous SLPC drift: gt1 min_freq oscillates 500<->550 without any
    # write from us. That must NOT invalidate the run (it aborted two device
    # sessions as a false positive).
    code, report = _run_gpu_freq_compare(
        tmp_path,
        "card0/gt0\t1950\t1950\ncard0/gt1\t500\t1950\n",
        "card0/gt0\t1950\t1950\ncard0/gt1\t550\t1950\n",
    )
    assert code == 0
    assert "TOLERATED card0/gt1" in report
    assert "autonomous SLPC drift" in report


def test_d4_gpu_freq_diff_hard_fails_on_max_freq_mismatch(tmp_path):
    # max_freq is never SLPC-driven: any delta is our residue -> hard fail.
    code, report = _run_gpu_freq_compare(
        tmp_path,
        "card0/gt0\t1950\t1950\ncard0/gt1\t500\t1950\n",
        "card0/gt0\t1950\t1716\ncard0/gt1\t500\t1950\n",
    )
    assert code != 0
    assert "HARD-FAIL card0/gt0" in report
    assert "max_freq" in report


def test_d4_gpu_freq_diff_hard_fails_on_min_drift_above_tolerance(tmp_path):
    # A min_freq delta beyond the 100 MHz SLPC band (e.g. our unrestored 800
    # floor vs the 1950 latch) is residue, not drift.
    code, report = _run_gpu_freq_compare(
        tmp_path,
        "card0/gt0\t1950\t1950\n",
        "card0/gt0\t800\t1950\n",
    )
    assert code != 0
    assert "HARD-FAIL card0/gt0" in report
    assert "min_freq" in report


def test_d4_probe_and_ab_gates_use_tolerant_gpu_freq_compare():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    # Both the A/B restore gate and the probe restore path go through the
    # tolerant comparator instead of a strict byte diff.
    assert script.count("compare_gpu_freq_snapshots \\") >= 2
    assert 'diff -u "$run_dir/gpu-freq.before"' not in script
    assert 'diff -u "$probe_dir/gpu-freq.before"' not in script
    # RAPL PL1 keeps the strict diff (no autonomous drift there).
    assert 'diff -u "$run_dir/rapl-pl1.before"' in script


def test_d5_gpu_cap_sweep_probe_lowers_min_alongside_max():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    sweep_body = script.split("run_probe_gpu_cap_sweep() {", 1)[1].split("\n}", 1)[0]
    # D5: the sweep must not be confounded by the gt0 min latch -- when the
    # swept cap sits below a GT's current min, min is lowered to min(cap, rpe)
    # clamped to >= rpn (same rule as the daemon actuator, D1).
    assert "min_freq" in sweep_body
    assert "rpe_freq" in sweep_body
    assert "rpn_freq" in sweep_body
    assert "cur_min > step" in sweep_body
    assert "min(step, rpe)" in sweep_body
    assert "max(new_min, rpn)" in sweep_body


def test_profile_wrapper_uses_paired_baseline_order_for_controlled_ab():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    assert 'ab_order_strategy="${PROFILE_GAME_POWER_AB_ORDER_STRATEGY:-paired-baseline}"' in script
    assert 'scene_evidence="${PROFILE_GAME_POWER_SCENE_EVIDENCE:-}"' in script
    assert 'cooldown_rule="${PROFILE_GAME_POWER_COOLDOWN_RULE:-fixed-60s}"' in script
    assert "validate_ab_profile_shape()" in script
    assert 'POLICY_SEQUENCE="off $AB_CANDIDATE_POLICY off"' in script
    assert 'ab_run_order="off,$AB_CANDIDATE_POLICY,off"' in script
    assert 'ab_pair_position="baseline-before"' in script
    assert 'ab_pair_position="candidate"' in script
    assert 'ab_pair_position="baseline-after"' in script


def test_profile_wrapper_passes_full_ab_identity_tuple_to_summarize():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    assert 'AB_INVOCATION_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"' in script
    assert (
        'ab_pair_id="${AB_INVOCATION_ID}-r${repeat}-tdp${tdp}-candidate-'
        '${AB_CANDIDATE_POLICY}${ab_pair_variant_suffix}"'
    ) in script
    assert '--ab-order-strategy "$AB_ORDER_STRATEGY"' in script
    assert '--ab-run-order "$ab_run_order"' in script
    assert '--ab-order-valid "$ab_order_valid"' in script
    assert '--ab-candidate-policy "$AB_CANDIDATE_POLICY"' in script
    assert '--ab-invocation-id "$AB_INVOCATION_ID"' in script
    assert '--ab-pair-id "$ab_pair_id"' in script
    assert '--ab-pair-position "$ab_pair_position"' in script
    assert '--scene-evidence "$SCENE_EVIDENCE"' in script


def test_profile_wrapper_records_cooldown_power_run_and_thermal_evidence():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    assert "monotonic_now()" in script
    assert 'sleep "$cooldown_sleep_s"' in script
    assert (
        'cooldown_elapsed_s="$(monotonic_delta "$cooldown_started_at_s" '
        '"$cooldown_ended_at_s")"'
    ) in script
    assert "read_power_source_state()" in script
    assert (
        'power_source_samples="${power_source_start_state},'
        '${power_source_pre_run_state},${power_source_end_state}"'
    ) in script
    assert "select_thermal_source()" in script
    assert "read_thermal_c()" in script
    assert '--power-source-start-state "$power_source_start_state"' in script
    assert '--power-source-pre-run-state "$power_source_pre_run_state"' in script
    assert '--power-source-end-state "$power_source_end_state"' in script
    assert '--power-source-samples "$power_source_samples"' in script
    assert '--power-source-stable "$power_source_stable"' in script
    assert '--thermal-start-c "$thermal_start_c"' in script
    assert '--thermal-end-c "$thermal_end_c"' in script
    assert '--thermal-unavailable "$thermal_unavailable"' in script
    assert '--thermal-source-kind "$thermal_source_kind"' in script
    assert '--thermal-source-id "$thermal_source_id"' in script
    assert '--thermal-source-label "$thermal_source_label"' in script
    assert '--run-started-at-s "$run_started_at_s"' in script
    assert '--run-ended-at-s "$run_ended_at_s"' in script
    assert '--cooldown-rule "$COOLDOWN_RULE"' in script
    assert '--cooldown-enforced "$cooldown_enforced"' in script
    assert '--cooldown-started-at-s "$cooldown_started_at_s"' in script
    assert '--cooldown-ended-at-s "$cooldown_ended_at_s"' in script
    assert '--cooldown-elapsed-s "$cooldown_elapsed_s"' in script


def test_profile_wrapper_rejects_unsupported_ab_shapes_until_pair_grouping_supported():
    script = (ROOT / "scripts/profile-game-power-on-device.sh").read_text()

    assert 'if [ "$AB_ORDER_STRATEGY" != "paired-baseline" ]; then' in script
    assert 'if [ "$COOLDOWN_RULE" != "fixed-60s" ]; then' in script
    assert 'if [ "$non_off_count" -ne 1 ] || [ "$off_count" -lt 1 ]; then' in script
    assert 'if [ "$candidate_policy" = "gpu-priority-cpu-cap" ]' in script
    assert (
        "paired-baseline supports exactly one non-off candidate, one effective "
        "CPU-cap variant, and fixed-60s cooldown in the first V3 implementation"
    ) in script
    assert "Cpus_allowed_list" in script
    assert "app-steam-client" in script
    assert "gamescope" in script
    assert "mangoapp" in script
    assert "cpu.uclamp.min" in script
    assert "cpu.uclamp.max" in script
    assert "cpu.weight" in script
    assert "cpuset.cpus" in script
    assert "cpuset.cpus.effective" in script


def test_device_verifier_checks_profile_aware_tdp_policy_and_tau():
    script = (ROOT / "scripts/verify-on-device.sh").read_text()

    assert "steamosctl_user" in script
    assert "provider_tdp" in script
    assert "set_provider_tdp" in script
    assert "SteamOS Manager TDP remote works" in script
    assert 'VERIFY_TDP_POLICY_MODE="${VERIFY_TDP_POLICY_MODE:-battery-maxq}"' in script
    assert "battery-maxq:17) echo 25" in script
    assert "battery-maxq:18) echo 25" in script
    assert "battery-maxq:30) echo 35" in script
    assert "watts * 125 + 99" in script
    assert "watts * 145 + 99" in script
    assert "ac-performance:12) echo 25" in script
    assert "ac-performance:17|ac-performance:18" in script
    assert 'elif [ "$watts" -lt 17 ]; then' in script
    assert "rapl_constraint_time_window_us" in script
    assert "expected_pl2_tau_us" in script
    assert "assert_time_window_close" in script
    assert "RAPL_TIME_WINDOW_TOLERANCE_US" in script


def test_device_verifier_reports_msi_claw_ec_tdp_bytes():
    script = (ROOT / "scripts/verify-on-device.sh").read_text()

    assert "report_msi_claw_ec_tdp_bytes" in script
    assert "MSI EC PL1/PL2 bytes" in script
    assert "MSI EC shift byte" in script


def test_gamescope_workaround_script_can_enable_and_disable():
    script = (ROOT / "scripts/configure-gamescope-display-workaround.sh").read_text()
    enable_block = script.split('if [ "$action" = "enable" ]; then', 1)[1].split(
        "else", 1
    )[0]

    assert "enable|disable" in script
    assert "COPYFILE_DISABLE=1" in script
    assert "tar --no-xattrs" in script
    assert "/opt/steamos-intel-handheld/bin/steamos-intel-handheld-gamescope-display" in script
    assert 'remote_helper="/etc/rivoreo/bin/steamos-intel-handheld-gamescope-display"' not in script
    assert "/etc/systemd/user/steamos-intel-handheld-gamescope-display.service" in script
    assert (
        "/etc/systemd/user/gamescope-session.service.wants/"
        "steamos-intel-handheld-gamescope-display.service"
        in script
    )
    assert (
        "/home/deck/.config/systemd/user/gamescope-session.service.wants/"
        "steamos-intel-handheld-gamescope-display.service"
        in script
    )
    assert "ln -sfn ../'$remote_service_name' '$remote_service_wants'" in script
    assert "rm -f '$deck_user_service_wants'" in script
    assert "gamescope-force-composition-wrapper" in script
    assert "gamescope-session.service.d/10-force-composition.conf" in script
    assert "systemctl --user daemon-reload" in script
    assert "systemctl --user enable steamos-intel-handheld-gamescope-display.service" not in script
    assert (
        "systemctl --user restart --no-block steamos-intel-handheld-gamescope-display.service"
        not in script
    )
    assert (
        "systemctl --user restart --no-block '$remote_service_name'"
        in script
    )
    assert (
        "systemctl --user enable --now steamos-intel-handheld-gamescope-display.service"
        not in enable_block
    )
    assert (
        "systemctl --user disable --now steamos-intel-handheld-gamescope-display.service"
        not in script
    )
    assert (
        "systemctl --user stop '$remote_service_name'"
        in script
    )


def test_mangoapp_dropin_script_installs_custom_binary_without_replacing_system_file():
    script = (ROOT / "scripts/configure-mangoapp-dropin.sh").read_text()
    dropin = (
        ROOT / "data/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf"
    ).read_text()

    assert "enable|disable" in script
    assert "/opt/steamos-intel-handheld/bin/mangoapp" in script
    assert 'remote_mangoapp="/etc/rivoreo/bin/mangoapp"' not in script
    assert "/etc/systemd/user/gamescope-mangoapp.service.d/10-rivoreo-mangoapp.conf" in script
    assert "/usr/bin/mangoapp" not in script
    assert "systemctl --user restart gamescope-mangoapp.service" in script
    assert "ExecStart=" in dropin
    assert "ExecStart=/opt/steamos-intel-handheld/bin/mangoapp" in dropin
    assert "/opt/steamos-intel-handheld/share/etc-artifacts/manifest.d" in script
    assert "10-mangoapp.toml" in script


def test_arch_pkgbuild_installs_restore_payload_and_durable_units():
    pkgbuild = (ROOT / "packaging/arch/PKGBUILD").read_text()
    install_script = (ROOT / "packaging/arch/steamos-intel-handheld.install").read_text()
    mangoapp_pkgbuild = (
        ROOT / "packaging/arch/steamos-intel-handheld-mangoapp/PKGBUILD"
    ).read_text()

    assert "steamos-intel-handheld-restore.service" in pkgbuild
    assert "$pkgdir/usr/lib/systemd/system/steamos-intel-handheld-restore.service" in pkgbuild
    assert "$pkgdir/etc/systemd/system/steamos-intel-handheld-restore.service" in pkgbuild
    assert "$pkgdir/etc/systemd/system/steamos-intel-handheld-power-control.service" in pkgbuild
    assert "steamos-intel-handheld-steamos-manager-remote.service" not in pkgbuild
    assert "steamos-intel-handheld-steamos-manager-remote" not in pkgbuild
    assert "$pkgdir/opt/steamos-intel-handheld/share/etc-artifacts/manifest.toml" in pkgbuild
    assert 'artifact_root="$pkgdir/opt/steamos-intel-handheld/share/etc-artifacts"' in pkgbuild
    assert "$pkgdir/etc/steamos-manager/remotes.d/99-rivoreo-power-control.toml" not in pkgbuild
    assert (
        "$artifact_root/steamos-manager/remotes.d/99-rivoreo-power-control.toml"
        in pkgbuild
    )
    assert (
        "rm -f /etc/steamos-manager/remotes.d/99-rivoreo-power-control.toml"
        not in install_script
    )
    assert "systemctl start steamos-intel-handheld-power-control.service" in install_script
    assert "systemctl stop steamos-intel-handheld-power-control.service" in install_script
    assert "_steamos_intel_handheld_restart_user_manager_without_provider" in install_script
    assert "systemctl --user restart --no-block steamos-manager.service" not in install_script
    assert (
        "systemctl enable steamos-intel-handheld-steamos-manager-remote.service"
        not in install_script
    )
    assert (
        "systemctl start steamos-intel-handheld-steamos-manager-remote.service"
        not in install_script
    )
    assert (
        "systemctl disable steamos-intel-handheld-steamos-manager-remote.service"
        in install_script
    )
    assert (
        "$artifact_root/"
        "NetworkManager/dispatcher.d/90-rncn-steamdeck-wg"
    ) in pkgbuild
    assert "systemctl enable steamos-intel-handheld-restore.service" in install_script
    assert "steamos-intel-handheld-restore-etc --apply" in install_script
    assert "manifest.d/10-mangoapp.toml" in mangoapp_pkgbuild


def test_local_check_does_not_lint_external_submodules():
    script = (ROOT / "scripts/check-local.sh").read_text()

    assert "ruff check src tests scripts" in script
    assert "ruff check ." not in script


def test_docs_describe_game_power_governor_default_epp_only_and_reversible():
    readme = (ROOT / "README.md").read_text()
    design = (ROOT / "docs/design.md").read_text()
    readme_text = " ".join(readme.split())
    design_text = " ".join(design.split())

    assert "Game power governor" in readme
    # The packaged unit ships target-balance; the README used to describe a
    # rollout gate that had already been passed and still called gpu-priority
    # the default.
    assert "--game-power-mode target-balance" in readme
    assert "the packaged unit keeps the" not in readme
    unit = (ROOT / "data/systemd/steamos-intel-handheld-power-control.service").read_text()
    assert "--game-power-mode target-balance" in unit
    assert "restores the previous CPU EPP and frequency limits" in readme_text
    assert "--game-power-cpu-cap off" in readme
    assert "default `--game-power-cpu-cap on`" not in readme
    assert "--game-power-pcore-max-mhz 3000" in readme
    assert "--game-power-ecore-max-mhz 2400" in readme
    assert "--game-power-cpu-cap-core-share-threshold 0.30" in readme
    assert "scripts/verify-game-power-on-device.sh --allow-device root@10.100.0.19" in readme
    assert "12W and 22W" in readme
    assert "PROFILE_GAME_POWER_CAPTURE_MODE=controlled" in readme
    assert "PROFILE_GAME_POWER_REPEATS=3" in readme
    assert 'PROFILE_GAME_POWER_SCENE_EVIDENCE="save:<stable-scene>"' in readme
    assert 'PROFILE_GAME_POWER_POLICIES="off gpu-priority"' in readme
    assert 'PROFILE_GAME_POWER_POLICIES="off gpu-priority-cpu-cap"' in readme
    assert (
        'PROFILE_GAME_POWER_POLICIES="off gpu-priority gpu-priority-cpu-cap"'
        not in readme
    )
    assert "runtime-telemetry-contract.json" in readme
    assert "profile-runtime-telemetry-contract.json" in readme
    assert "action-equivalence.json" in readme
    assert "fps_target_confidence" in readme
    assert "post_run_classification" in readme
    assert (
        'PROFILE_GAME_POWER_CPU_CAP_VARIANTS="balanced:3000:2400:0.30"'
        in readme
    )
    assert "conservative:2600:2200:0.30" not in readme
    assert "PROFILE_GAME_POWER_PCORE_MAX_MHZ=3000" in readme
    assert "PROFILE_GAME_POWER_CPU_CAP_CORE_SHARE_THRESHOLD=0.30" in readme
    assert "steamos-intel-handheld-game-power-profile aggregate" in readme
    assert "--candidate-policy gpu-priority \\" in readme
    assert "--candidate-policy gpu-priority-cpu-cap \\" in readme
    assert "--duration-s 60" in readme
    assert "--min-runs 3" in readme
    assert "claim_scope" in readme
    assert (
        "BETTER (scene/profile-specific controlled result; not a general performance claim)"
        in readme
    )
    assert (
        "guarded foreground-game artifacts are required for this captured profile only"
        in readme
    )
    assert "Game power governor" in design
    assert "enabled by default" in design
    assert "does not raise PL1 automatically" in design_text
    assert "reversible CPU EPP hints" in design_text


def test_mangohud_submodule_tracks_fork_branch():
    gitmodules = (ROOT / ".gitmodules").read_text()

    assert "https://github.com/JohnnySun/MangoHud.git" in gitmodules
    assert "branch = intel-rapl-gpu-power" in gitmodules


def test_mangohud_intel_integrated_gtt_feeds_steam_overlay_vram():
    fdinfo_header = (ROOT / "external/MangoHud/src/gpu_fdinfo.h").read_text()
    fdinfo_source = (ROOT / "external/MangoHud/src/gpu_fdinfo.cpp").read_text()
    gpu_header = (ROOT / "external/MangoHud/src/gpu.h").read_text()
    hud_source = (ROOT / "external/MangoHud/src/hud_elements.cpp").read_text()

    assert "uses_integrated_memory() const" in fdinfo_header
    assert "bool uses_integrated_memory()" in gpu_header
    assert "metrics.gtt_used = memory_used" in fdinfo_source
    assert "gpu->uses_integrated_memory()" in hud_source


def test_mangohud_hides_unavailable_gpu_sensor_values():
    metrics_header = (ROOT / "external/MangoHud/src/gpu_metrics_util.h").read_text()
    fdinfo_source = (ROOT / "external/MangoHud/src/gpu_fdinfo.cpp").read_text()
    hud_source = (ROOT / "external/MangoHud/src/hud_elements.cpp").read_text()

    assert "temp(-1)" in metrics_header
    assert "junction_temp(-1)" in metrics_header
    assert "memory_temp(-1)" in metrics_header
    assert "MemClock(-1)" in metrics_header
    assert "CoreClock(-1)" in metrics_header
    assert "powerUsage(-1.0f)" in metrics_header
    assert "powerLimit(-1.0f)" in metrics_header
    assert "fan_speed(-1)" in metrics_header
    assert "voltage(-1)" in metrics_header

    assert 'has_hwmon_sensor("temp")' in fdinfo_source
    assert 'has_hwmon_sensor("vram_temp")' in fdinfo_source
    assert 'has_hwmon_sensor("power_limit")' in fdinfo_source
    assert 'has_hwmon_sensor("fan_speed")' in fdinfo_source
    assert 'has_hwmon_sensor("voltage")' in fdinfo_source
    assert "return -1.0f" in fdinfo_source
    assert "return -1;" in fdinfo_source
    assert "metrics.temp = -1" in fdinfo_source
    assert "metrics.memory_temp = -1" in fdinfo_source

    assert "gpu->metrics.temp > -1" in hud_source
    assert "gpu->metrics.MemClock > 0" in hud_source
    assert "gpu->metrics.CoreClock > -1" in hud_source
    assert "gpu->metrics.powerUsage > -1" in hud_source
    assert "gpu->metrics.fan_speed > -1" in hud_source
    assert "gpu->metrics.voltage > -1" in hud_source


def test_steamos_qemu_build_env_uses_official_recovery_image():
    script = (ROOT / "scripts/steamos-qemu-build-env.sh").read_text()
    docs = (ROOT / "docs/steamos-qemu-build-env.md").read_text()

    assert "https://steamdeck-images.steamos.cloud/recovery/" in script
    assert "qemu-system-x86_64" in script
    assert "qemu-img convert -f raw -O qcow2" in script
    assert "edk2-x86_64-code.fd" in script
    assert "STEAMOS_QEMU_DISPLAY" in script
    assert "mount_tag=workspace" in script
    assert "hostfwd=tcp:127.0.0.1:$ssh_port-:22" in script
    assert "provision)" in script
    assert "run-build)" in script
    assert "build-mangoapp)" in script
    assert "fetch-raw)" in script
    assert "prepare-rootfs)" in script
    assert "build-mangoapp-rootfs)" in script
    assert "STEAMOS_ROOTFS_DIR" in script
    assert "chroot" in script
    assert 'mount_for_rootfs "$rootfs_dir" "$rootfs_dir" bind' in script
    assert "gpgconf --kill all" in script
    assert "qemu_args=(" in script
    assert 'if [ "${#extra_args[@]}" -gt 0 ]; then' in script
    assert "STEAMOS_QEMU_CLEAN_BUILD" in script
    assert "STEAMOS_QEMU_MESON_OPTIMIZATION" in script
    assert "STEAMOS_QEMU_CLEAN_BUILD=${STEAMOS_QEMU_CLEAN_BUILD:-}" in script
    assert "STEAMOS_QEMU_MESON_OPTIMIZATION=${STEAMOS_QEMU_MESON_OPTIMIZATION:-}" in script
    assert "meson setup --reconfigure /home/build/mangohud" in script
    assert "python-mako" in script
    assert "libxrandr libxinerama libxcursor libxi libxrender libxfixes" in script
    assert "SteamOS rootfs chroot" in docs
    assert "scripts/steamos-qemu-build-env.sh --allow-qemu build-mangoapp-rootfs" in docs
    assert ".cache/steamos-qemu/mangoapp" in docs
    assert "scripts/steamos-qemu-build-env.sh --allow-qemu build-mangoapp" in docs
    assert "scripts/configure-mangoapp-dropin.sh" in docs


def test_every_install_path_ships_the_steamos_manager_device_profile():
    """Valve's profile for this board declares no TDP method, so without our
    profile Steam's own TDP slider does nothing. A path that installs everything
    else but omits this ships a machine missing the feature, silently."""
    fragment = "steamos-manager/devices/99-rivoreo-msi-claw-tdp.toml"
    assert (ROOT / "data" / fragment).is_file()

    payload = (ROOT / "scripts/install-payload.sh").read_text()
    pkgbuild = (ROOT / "packaging/arch/PKGBUILD").read_text()
    manifest = (ROOT / "data/restore/manifest.toml").read_text()

    for name, text in (("install-payload.sh", payload), ("PKGBUILD", pkgbuild)):
        assert fragment in text, name

    # Both install paths place only the artifact copy. The live copy is on the
    # read-only system partition, which only the restore service may write.
    assert '/usr/share/steamos-manager/devices/99-rivoreo-msi-claw-tdp.toml' in manifest
    assert "/usr/share/steamos-manager" not in payload
    assert "/usr/share/steamos-manager" not in pkgbuild


def test_device_profile_declares_the_remote_tdp_method_valve_omits():
    profile = (ROOT / "data/steamos-manager/devices/99-rivoreo-msi-claw-tdp.toml").read_text()
    parsed = tomllib.loads(profile)

    assert parsed["tdp_limit"]["method"] == "remote"
    # The range has to match what the daemon actually clamps to, or Steam offers
    # a slider position the hardware will never honour.
    assert parsed["tdp_limit"]["range"] == {"min": 8, "max": 30}
    unit = (ROOT / "data/systemd/steamos-intel-handheld-power-control.service").read_text()
    assert "--min-w 8" in unit
    assert "--max-w 30" in unit
    # Matched to this board only; a wrong DMI match would apply Claw power
    # behaviour to somebody else's hardware.
    assert [d["dmi"]["board_name"] for d in parsed["device"]] == ["MS-1T52"]


def test_no_restore_artifact_restarts_steamos_manager():
    """The provider uses wait-and-serve because the user steamos-manager cannot
    finish starting while org.rivoreo.SteamOSManager.PowerControl is already
    owned. A restore-triggered restart puts it straight back into that deadlock:
    measured, the user unit hung in "activating" and steamosctl answered
    NameHasNoOwner until the provider was stopped."""
    manifests = [ROOT / "data/restore/manifest.toml"]
    manifests.extend(sorted((ROOT / "data/restore/manifest.d").glob("*.toml")))

    for path in manifests:
        payload = tomllib.loads(path.read_text())
        for artifact in payload.get("artifact", []):
            restarts = artifact.get("service_restarts", [])
            assert "steamos-manager.service" not in restarts, (path.name, artifact["destination"])
            assert "steamos-manager" not in restarts, (path.name, artifact["destination"])
