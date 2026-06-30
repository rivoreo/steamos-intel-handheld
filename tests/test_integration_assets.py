from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


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


def test_manual_installer_installs_ec_control_wrapper():
    script = (ROOT / "scripts/install-on-device.sh").read_text()

    assert "/opt/steamos-intel-handheld/bin/steamos-intel-handheld-ec-control" in script
    assert r"python3 -m steamos_intel_handheld.ec_charge_control \"\$@\"" in script


def test_manual_installer_installs_restore_service_and_canonical_artifacts():
    script = (ROOT / "scripts/install-on-device.sh").read_text()

    assert "/opt/steamos-intel-handheld/bin/steamos-intel-handheld-restore-etc" in script
    assert r"python3 -m steamos_intel_handheld.restore_etc \"\$@\"" in script
    assert "steamos-intel-handheld-steamos-manager-remote" in script
    assert (
        "rm -f /opt/steamos-intel-handheld/bin/"
        "steamos-intel-handheld-steamos-manager-remote"
    ) in script
    assert "artifact_root=/opt/steamos-intel-handheld/share/etc-artifacts" in script
    assert "/opt/steamos-intel-handheld/share/etc-artifacts/manifest.toml" in script
    assert "\\$artifact_root/dbus-1/system.d" in script
    assert "\\$artifact_root/steamos-manager/remotes.d" in script
    assert "rm -f /etc/steamos-manager/remotes.d/99-rivoreo-power-control.toml" not in script
    assert (
        "install -m 0644 '$remote_tmp/data/steamos-manager/remotes.d/"
        "99-rivoreo-power-control.toml'"
    in script
    )
    assert (
        "install -m 0644 '$remote_tmp/data/steamos-manager/remotes.d/"
        "99-rivoreo-power-control.toml' /etc/steamos-manager/remotes.d/"
        "99-rivoreo-power-control.toml"
        not in script
    )
    assert "\\$artifact_root/systemd/system" in script
    assert "\\$artifact_root/NetworkManager/dispatcher.d" in script
    assert "/etc/systemd/system/steamos-intel-handheld-restore.service" in script
    assert (
        "rm -f /etc/systemd/system/"
        "steamos-intel-handheld-steamos-manager-remote.service"
    ) in script
    assert "systemctl enable --now steamos-intel-handheld-restore.service" in script
    assert "steamos-intel-handheld-restore-etc --apply" in script
    assert "restart_user_steamos_manager_without_provider" in script
    assert "systemctl stop steamos-intel-handheld-power-control.service" in script
    assert (
        "systemctl enable --now steamos-intel-handheld-steamos-manager-remote.service"
        not in script
    )
    assert "systemctl disable steamos-intel-handheld-steamos-manager-remote.service" in script


def test_manual_installer_installs_decky_charge_limit_plugin():
    script = (ROOT / "scripts/install-on-device.sh").read_text()

    assert "decky/steamos-intel-handheld-ec/plugin.json" in script
    assert "/home/deck/homebrew/plugins/steamos-intel-handheld-ec" in script
    assert "install -m 0644" in script
    assert "decky_src/plugin.json" in script
    assert "decky_src/dist/index.js" in script
    assert "report_decky_loader_status" in script
    assert "/home/deck/homebrew/services/PluginLoader" in script
    assert "Decky Loader not detected" in script


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
    assert "TimeoutStartSec=360" in service
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
    assert "drm-resident-vram0" in script


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


def test_gamescope_workaround_harness_can_enable_and_disable():
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


def test_mangoapp_dropin_harness_installs_custom_binary_without_replacing_system_file():
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
    assert "scripts/steamos-qemu-build-env.sh build-mangoapp-rootfs" in docs
    assert ".cache/steamos-qemu/mangoapp" in docs
    assert "scripts/steamos-qemu-build-env.sh build-mangoapp" in docs
    assert "scripts/configure-mangoapp-dropin.sh" in docs
