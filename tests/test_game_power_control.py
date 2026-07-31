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
    # "automatic" is the V10 demand-shaping governor; the V9 EPP-only path is
    # still reachable as the explicit "legacy" mode.
    assert status.effective_mode == GamePowerMode.TARGET_BALANCE
    legacy = game_power_control.set_runtime_mode(path, "legacy", source="decky")
    assert legacy.effective_mode == GamePowerMode.GPU_PRIORITY
    game_power_control.set_runtime_mode(path, "automatic", source="decky")
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
    assert status.effective_mode == GamePowerMode.TARGET_BALANCE
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


# ---------------------------------------------------------------------------
# V10 frame-limiter helper (contract 1.6)
# ---------------------------------------------------------------------------
import subprocess  # noqa: E402


def _fake_runner(*, help_text="", set_returncode=0, set_stdout="ok", set_stderr=""):
    calls = []

    def runner(argv):
        argv = list(argv)
        calls.append(argv)
        if argv == ["--help"]:
            return subprocess.CompletedProcess(argv, 0, stdout=help_text, stderr="")
        return subprocess.CompletedProcess(
            argv, set_returncode, stdout=set_stdout, stderr=set_stderr
        )

    runner.calls = calls
    return runner


HELP_WITH_LIMIT = "commands:\n  debug_set_fps_limit <n>\n  composite_force <n>\n"
HELP_WITHOUT_LIMIT = "commands:\n  composite_force <n>\n"


def test_validate_limiter_fps_accepts_range_and_clear():
    assert game_power_control.validate_limiter_fps(0) == 0
    assert game_power_control.validate_limiter_fps(30) == 30
    assert game_power_control.validate_limiter_fps(60) == 60
    assert game_power_control.validate_limiter_fps(120) == 120


@pytest.mark.parametrize("bad", [25, 33, 125, -5, True, 60.0, "60"])
def test_validate_limiter_fps_rejects_off_step_and_types(bad):
    with pytest.raises(ValueError, match="unsupported limiter FPS"):
        game_power_control.validate_limiter_fps(bad)


def test_limiter_status_unsupported_when_command_absent():
    runner = _fake_runner(help_text=HELP_WITHOUT_LIMIT)
    status = game_power_control.limiter_status(runner=runner)
    assert status.status == "unsupported"
    assert status.supported is False
    assert runner.calls == [["--help"]]


def test_limiter_status_unknown_when_supported_but_unreadable():
    runner = _fake_runner(help_text=HELP_WITH_LIMIT)
    status = game_power_control.limiter_status(runner=runner)
    assert status.status == "unknown"
    assert status.supported is True
    assert status.fps is None


def test_limiter_set_invokes_debug_set_fps_limit():
    runner = _fake_runner(help_text=HELP_WITH_LIMIT)
    status = game_power_control.limiter_set(60, source="decky", runner=runner)
    assert status.status == "limited"
    assert status.fps == 60
    assert status.supported is True
    assert ["debug_set_fps_limit", "60"] in runner.calls


def test_limiter_clear_sets_zero():
    runner = _fake_runner(help_text=HELP_WITH_LIMIT)
    status = game_power_control.limiter_clear(runner=runner)
    assert status.status == "unlimited"
    assert status.fps == 0
    assert ["debug_set_fps_limit", "0"] in runner.calls


def test_limiter_set_rejects_invalid_fps_before_shelling_out():
    runner = _fake_runner(help_text=HELP_WITH_LIMIT)
    with pytest.raises(ValueError, match="unsupported limiter FPS"):
        game_power_control.limiter_set(33, runner=runner)
    assert runner.calls == []


def test_limiter_set_raises_when_command_absent():
    runner = _fake_runner(help_text=HELP_WITHOUT_LIMIT)
    with pytest.raises(RuntimeError, match="does not expose"):
        game_power_control.limiter_set(60, runner=runner)


def test_limiter_set_raises_on_nonzero_gamescopectl_exit():
    runner = _fake_runner(
        help_text=HELP_WITH_LIMIT, set_returncode=1, set_stderr="boom"
    )
    with pytest.raises(RuntimeError, match="failed"):
        game_power_control.limiter_set(60, runner=runner)


def test_limiter_cli_status_json_smoke(capsys, monkeypatch):
    monkeypatch.setattr(
        game_power_control,
        "_default_gamescopectl_runner",
        _fake_runner(help_text=HELP_WITHOUT_LIMIT),
    )
    game_power_control.main(["limiter", "status", "--json"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["status"] == "unsupported"
    assert payload["supported"] is False
