from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_steamos_manager_remote_config_uses_rivoreo_bus_name():
    config = (ROOT / "data/steamos-manager/remotes.d/99-rivoreo-power-control.toml").read_text()

    assert "[TdpLimit1]" in config
    assert 'bus_name = "org.rivoreo.SteamOSManager.PowerControl"' in config
    assert 'object_path = "/org/rivoreo/SteamOSManager/PowerControl"' in config


def test_systemd_unit_waits_for_steamos_manager_before_serving():
    unit = (ROOT / "data/systemd/steamos-intel-handheld-power-control.service").read_text()

    assert "wait-and-serve" in unit
    assert "ExecStart=/opt/steamos-intel-handheld/bin/steamos-intel-handheld-power-control" in unit
    assert "PATH=/etc/rivoreo/bin" not in unit
    assert "--user deck" in unit
    assert "--apply-rapl" in unit
    assert "--prepare-mangohud-sensors" in unit
    assert "StateDirectory=steamos-intel-handheld" in unit


def test_gamescope_display_helper_sets_runtime_composite_force():
    helper = (ROOT / "data/bin/steamos-intel-handheld-gamescope-display").read_text()

    assert "gamescopectl composite_force 1" in helper
    assert "gamescopectl composite_force 0" in helper
    assert "gamescope-environment" in helper
    assert "drm_single_plane_optimizations" not in helper


def test_gamescope_display_user_service_runs_after_gamescope_session():
    service = (
        ROOT / "data/systemd/user/steamos-intel-handheld-gamescope-display.service"
    ).read_text()

    assert "After=gamescope-session.service" in service
    assert "PartOf=gamescope-session.target" in service
    assert (
        "ExecStart=/opt/steamos-intel-handheld/bin/steamos-intel-handheld-gamescope-display apply"
        in service
    )
    assert "WantedBy=gamescope-session.target" in service


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


def test_gamescope_workaround_harness_can_enable_and_disable():
    script = (ROOT / "scripts/configure-gamescope-display-workaround.sh").read_text()

    assert "enable|disable" in script
    assert "COPYFILE_DISABLE=1" in script
    assert "tar --no-xattrs" in script
    assert "/opt/steamos-intel-handheld/bin/steamos-intel-handheld-gamescope-display" in script
    assert 'remote_helper="/etc/rivoreo/bin/steamos-intel-handheld-gamescope-display"' not in script
    assert "/etc/systemd/user/steamos-intel-handheld-gamescope-display.service" in script
    assert "gamescope-force-composition-wrapper" in script
    assert "gamescope-session.service.d/10-force-composition.conf" in script
    assert "systemctl --user daemon-reload" in script
    assert (
        "systemctl --user enable --now steamos-intel-handheld-gamescope-display.service"
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


def test_local_check_does_not_lint_external_submodules():
    script = (ROOT / "scripts/check-local.sh").read_text()

    assert "ruff check src tests scripts" in script
    assert "ruff check ." not in script


def test_mangohud_submodule_tracks_fork_branch():
    gitmodules = (ROOT / ".gitmodules").read_text()

    assert "https://github.com/JohnnySun/MangoHud.git" in gitmodules
    assert "branch = intel-rapl-gpu-power" in gitmodules


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
    assert "meson setup build/steamos-qemu" in docs
    assert "scripts/configure-mangoapp-dropin.sh" in docs
