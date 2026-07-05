import json

import pytest

from steamos_intel_handheld import game_power_control
from steamos_intel_handheld.game_power import (
    FrameTargetTelemetry,
    GamePowerConfig,
    GamePowerMode,
)


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


def test_runtime_control_preserves_mode_when_setting_manual_fps_target(tmp_path):
    path = tmp_path / "game-power-control.json"
    game_power_control.set_runtime_mode(path, "observe", source="decky")

    status = game_power_control.set_fps_target(path, 45, source="decky")

    assert status.mode == "observe"
    assert status.effective_mode == GamePowerMode.OBSERVE
    assert status.fps_target_override is not None
    assert status.fps_target_override.status == "manual"
    assert status.fps_target_override.fps == 45
    payload = json.loads(path.read_text())
    assert payload == {
        "schema_version": 1,
        "mode": "observe",
        "source": "decky",
        "fps_target_override": {
            "fps": 45,
            "source": "decky",
        },
    }


def test_runtime_control_clear_fps_target_preserves_mode_override(tmp_path):
    path = tmp_path / "game-power-control.json"
    game_power_control.set_runtime_mode(path, "automatic", source="decky")
    game_power_control.set_fps_target(path, 40, source="decky")

    status = game_power_control.clear_fps_target(path)

    assert status.mode == "automatic"
    assert status.effective_mode == GamePowerMode.GPU_PRIORITY
    assert status.override_active is True
    assert status.fps_target_override.status == "auto"
    assert json.loads(path.read_text()) == {
        "schema_version": 1,
        "mode": "automatic",
        "source": "decky",
    }


@pytest.mark.parametrize("fps", [0, 12, 29, 31, 121, 999, 37, 40.5, "forty"])
def test_runtime_control_rejects_invalid_fps_target_without_mutating_file(tmp_path, fps):
    path = tmp_path / "game-power-control.json"
    game_power_control.set_runtime_mode(path, "automatic", source="decky")
    before = path.read_text()

    with pytest.raises(ValueError, match="unsupported FPS target"):
        game_power_control.set_fps_target(path, fps, source="decky")

    assert path.read_text() == before


def test_runtime_control_manual_fps_target_overlays_base_frame_target(tmp_path):
    path = tmp_path / "game-power-control.json"
    base = GamePowerConfig(
        mode=GamePowerMode.GPU_PRIORITY,
        frame_target=FrameTargetTelemetry(
            fps_target=60.0,
            source="gamescope",
            confidence="medium",
        ),
    )
    game_power_control.set_fps_target(path, 45, source="decky")

    effective = game_power_control.effective_config_from_runtime_file(base, path)

    assert effective.mode == GamePowerMode.GPU_PRIORITY
    assert effective.frame_target == FrameTargetTelemetry(
        fps_target=45.0,
        source="manual",
        confidence="high",
    )


def test_runtime_control_fails_closed_for_corrupt_file(tmp_path):
    path = tmp_path / "game-power-control.json"
    path.write_text("{not-json")
    base = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY)

    effective = game_power_control.effective_config_from_runtime_file(base, path)
    status = game_power_control.read_runtime_status(path)

    assert effective.mode == GamePowerMode.OFF
    assert status.mode == "invalid"
    assert status.override_active is True


def test_runtime_control_fails_closed_for_invalid_mode(tmp_path):
    path = tmp_path / "game-power-control.json"
    path.write_text(json.dumps({"schema_version": 1, "mode": "uclamp"}))
    base = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY)

    effective = game_power_control.effective_config_from_runtime_file(base, path)
    status = game_power_control.read_runtime_status(path)

    assert effective.mode == GamePowerMode.OFF
    assert status.mode == "invalid"
    assert status.override_active is True


def test_runtime_control_fails_closed_for_invalid_fps_target_override(tmp_path):
    path = tmp_path / "game-power-control.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "automatic",
                "fps_target_override": {"fps": 37, "source": "decky"},
            }
        )
    )
    base = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY)

    effective = game_power_control.effective_config_from_runtime_file(base, path)
    status = game_power_control.read_runtime_status(path)

    assert effective.mode == GamePowerMode.OFF
    assert status.mode == "automatic"
    assert status.fps_target_override.status == "invalid"
    assert effective.runtime_control_health == {
        "status": "invalid",
        "mode": "automatic",
        "override_active": True,
        "fps_target_override_status": "invalid",
        "reason": "invalid-fps-target-override",
    }


def test_runtime_control_fails_closed_for_unsupported_schema_version(tmp_path):
    path = tmp_path / "game-power-control.json"
    path.write_text(json.dumps({"schema_version": 99, "mode": "automatic"}))
    base = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY)

    effective = game_power_control.effective_config_from_runtime_file(base, path)
    status = game_power_control.read_runtime_status(path)

    assert effective.mode == GamePowerMode.OFF
    assert status.mode == "invalid"
    assert status.fps_target_override.status == "invalid"


def test_runtime_control_fails_closed_for_non_object_json(tmp_path):
    path = tmp_path / "game-power-control.json"
    path.write_text(json.dumps(["automatic"]))
    base = GamePowerConfig(mode=GamePowerMode.GPU_PRIORITY)

    effective = game_power_control.effective_config_from_runtime_file(base, path)
    status = game_power_control.read_runtime_status(path)

    assert effective.mode == GamePowerMode.OFF
    assert status.mode == "invalid"
    assert status.fps_target_override.status == "invalid"


def test_set_runtime_mode_drops_invalid_saved_fps_target_override(tmp_path):
    path = tmp_path / "game-power-control.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "observe",
                "fps_target_override": {"fps": 37, "source": "decky"},
            }
        )
    )

    status = game_power_control.set_runtime_mode(path, "automatic", source="decky")
    raw = json.loads(path.read_text())

    assert status.mode == "automatic"
    assert status.fps_target_override.status == "auto"
    assert "fps_target_override" not in raw
