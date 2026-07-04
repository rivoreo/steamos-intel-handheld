import json

import pytest

from steamos_intel_handheld import game_power_control
from steamos_intel_handheld.game_power import GamePowerConfig, GamePowerMode


def test_runtime_control_writes_only_safe_public_modes(tmp_path):
    path = tmp_path / "game-power-control.json"

    status = game_power_control.set_runtime_mode(path, "automatic", source="decky")

    assert status.mode == "automatic"
    assert status.effective_mode == GamePowerMode.GPU_PRIORITY
    assert status.override_active is True
    payload = json.loads(path.read_text())
    assert payload == {
        "schema_version": 1,
        "mode": "automatic",
        "source": "decky",
    }


@pytest.mark.parametrize("mode", ["pcore", "ecore", "threshold", "uclamp", "affinity"])
def test_runtime_control_rejects_raw_policy_knobs(tmp_path, mode):
    path = tmp_path / "game-power-control.json"

    with pytest.raises(ValueError, match="unsupported game-power mode"):
        game_power_control.set_runtime_mode(path, mode, source="decky")

    assert not path.exists()


def test_runtime_control_restore_removes_override_file(tmp_path):
    path = tmp_path / "game-power-control.json"
    game_power_control.set_runtime_mode(path, "observe", source="decky")

    status = game_power_control.restore_runtime_defaults(path)

    assert status.mode == "default"
    assert status.effective_mode is None
    assert status.override_active is False
    assert not path.exists()


def test_runtime_control_overlays_only_mode_and_preserves_measured_constants(tmp_path):
    path = tmp_path / "game-power-control.json"
    base = GamePowerConfig(
        mode=GamePowerMode.GPU_PRIORITY,
        pcore_max_khz=3_000_000,
        ecore_max_khz=2_400_000,
        cpu_cap_enabled=True,
        cpu_cap_core_share_threshold=0.30,
    )
    game_power_control.set_runtime_mode(path, "observe", source="decky")

    effective = game_power_control.effective_config_from_runtime_file(base, path)

    assert effective.mode == GamePowerMode.OBSERVE
    assert effective.pcore_max_khz == 3_000_000
    assert effective.ecore_max_khz == 2_400_000
    assert effective.cpu_cap_enabled is True
    assert effective.cpu_cap_core_share_threshold == 0.30


def test_runtime_control_ignores_corrupt_file_and_falls_back_to_base(tmp_path):
    path = tmp_path / "game-power-control.json"
    path.write_text("{not-json")
    base = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY)

    effective = game_power_control.effective_config_from_runtime_file(base, path)
    status = game_power_control.read_runtime_status(path)

    assert effective == base
    assert status.mode == "invalid"
    assert status.override_active is True
