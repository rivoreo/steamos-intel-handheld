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
    assert "--user deck" in unit
    assert "--apply-rapl" in unit
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
    assert "ExecStart=/etc/rivoreo/bin/steamos-intel-handheld-gamescope-display apply" in service
    assert "WantedBy=gamescope-session.target" in service


def test_gamescope_workaround_harness_can_enable_and_disable():
    script = (ROOT / "scripts/configure-gamescope-display-workaround.sh").read_text()

    assert "enable|disable" in script
    assert "COPYFILE_DISABLE=1" in script
    assert "tar --no-xattrs" in script
    assert "/etc/rivoreo/bin/steamos-intel-handheld-gamescope-display" in script
    assert "/etc/systemd/user/steamos-intel-handheld-gamescope-display.service" in script
    assert "gamescope-force-composition-wrapper" in script
    assert "gamescope-session.service.d/10-force-composition.conf" in script
    assert "systemctl --user daemon-reload" in script
    assert (
        "systemctl --user enable --now steamos-intel-handheld-gamescope-display.service"
        in script
    )
