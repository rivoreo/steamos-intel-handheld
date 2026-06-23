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
