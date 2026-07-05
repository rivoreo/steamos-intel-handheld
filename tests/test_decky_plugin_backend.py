import asyncio
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "decky" / "steamos-intel-handheld-ec" / "main.py"
GAME_POWER_BACKEND = ROOT / "decky" / "steamos-intel-handheld-game-power" / "main.py"


def load_backend():
    spec = importlib.util.spec_from_file_location("decky_charge_limit_backend", BACKEND)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_game_power_backend():
    spec = importlib.util.spec_from_file_location(
        "decky_game_power_backend", GAME_POWER_BACKEND
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeProcess:
    returncode = 0

    async def communicate(self):
        return json.dumps({"raw_hex": "0xd0"}).encode(), b""


def test_backend_calls_python_module_directly_with_clean_environment(monkeypatch):
    backend = load_backend()
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend._run_ec_control("status"))

    assert result["raw_hex"] == "0xd0"
    cmd, kwargs = calls[0]
    assert cmd == (
        "/usr/bin/python3",
        "-m",
        "steamos_intel_handheld.ec_charge_control",
        "status",
        "--json",
    )
    assert kwargs["env"]["PYTHONPATH"] == "/opt/steamos-intel-handheld/src"
    assert kwargs["env"]["PATH"] == "/usr/bin:/bin"
    assert "LD_LIBRARY_PATH" not in kwargs["env"]


def test_backend_exposes_apply_limit_callable(monkeypatch):
    backend = load_backend()
    calls = []

    async def fake_run_ec_control(*args):
        calls.append(args)
        return {"applied": {"raw_hex": "0xbc"}}

    monkeypatch.setattr(backend, "_run_ec_control", fake_run_ec_control)

    result = asyncio.run(backend.Plugin().apply_limit(60))

    assert result["applied"]["raw_hex"] == "0xbc"
    assert calls == [("apply", "60")]


class FakeCommandProcess:
    def __init__(self, stdout: bytes = b"{}", stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self, input=None):
        return self._stdout, self._stderr


def test_game_power_backend_accepts_only_safe_modes():
    backend = load_game_power_backend()

    assert backend.validate_mode("automatic") == "automatic"
    assert backend.validate_mode("observe") == "observe"
    assert backend.validate_mode("off") == "off"
    for blocked in ("pcore", "ecore", "threshold", "uclamp", "affinity", "custom"):
        try:
            backend.validate_mode(blocked)
        except ValueError as exc:
            assert "unsupported game-power mode" in str(exc)
        else:
            raise AssertionError(f"{blocked} unexpectedly accepted")


def test_game_power_backend_calls_control_cli_for_mode_changes(monkeypatch):
    backend = load_game_power_backend()
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCommandProcess(stdout=b'{"mode": "automatic"}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend.Plugin().set_mode("automatic"))

    assert result["mode"] == "automatic"
    commands = [call[0] for call in calls]
    assert commands == [
        (
            backend.GAME_POWER_CONTROL,
            "set-mode",
            "automatic",
            "--source",
            "decky",
            "--json",
        )
    ]


def test_game_power_backend_calls_control_cli_for_manual_fps_target(monkeypatch):
    backend = load_game_power_backend()
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCommandProcess(
            stdout=(
                b'{"fps_target_override": {"status": "manual", "fps": 45}, '
                b'"mode": "default"}'
            )
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend.Plugin().set_fps_target(45))

    assert result["fps_target_override"]["fps"] == 45
    assert [call[0] for call in calls] == [
        (
            backend.GAME_POWER_CONTROL,
            "set-fps-target",
            "45",
            "--source",
            "decky",
            "--json",
        )
    ]


def test_game_power_backend_clears_manual_fps_target(monkeypatch):
    backend = load_game_power_backend()
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCommandProcess(
            stdout=b'{"fps_target_override": {"status": "auto"}, "mode": "default"}'
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend.Plugin().set_fps_target(None))

    assert result["fps_target_override"]["status"] == "auto"
    assert [call[0] for call in calls] == [
        (backend.GAME_POWER_CONTROL, "clear-fps-target", "--json")
    ]


def test_game_power_backend_rejects_invalid_fps_target_without_spawning(monkeypatch):
    backend = load_game_power_backend()

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        raise AssertionError(f"unexpected spawn: {cmd}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    for fps in (0, 29, 37, 121, "45"):
        try:
            asyncio.run(backend.Plugin().set_fps_target(fps))
        except ValueError as exc:
            assert "unsupported FPS target" in str(exc)
        else:
            raise AssertionError(f"{fps!r} unexpectedly accepted")


def test_game_power_backend_restore_calls_control_cli_without_service_restart(monkeypatch):
    backend = load_game_power_backend()
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCommandProcess(stdout=b'{"mode": "default", "override_active": false}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend.Plugin().restore_defaults())

    assert result["restored"] is True
    assert [call[0] for call in calls] == [
        (backend.GAME_POWER_CONTROL, "restore-defaults", "--json")
    ]


def test_game_power_backend_status_combines_service_and_runtime_control(monkeypatch):
    backend = load_game_power_backend()
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[0] == "systemctl":
            return FakeCommandProcess(
                stdout=(
                    b"ActiveState=active\n"
                    b"SubState=running\n"
                    b"ExecStart={ path=/opt/steamos-intel-handheld/bin/"
                    b"steamos-intel-handheld-power-control ; argv[]=/opt/... ; }\n"
                )
            )
        return FakeCommandProcess(
            stdout=(
                b'{"mode": "automatic", "effective_mode": "gpu-priority", '
                b'"override_active": true, "policy_label": "Balanced automatic policy"}'
            )
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend.Plugin().get_status())

    assert result["service"]["active_state"] == "active"
    assert result["service"]["mode"] == "automatic"
    assert result["service"]["override_active"] is True
    assert result["control"]["mode"] == "automatic"
    assert result["control"]["policy_label"] == "Balanced automatic policy"
    assert [call[0] for call in calls] == [
        (
            "systemctl",
            "show",
            "steamos-intel-handheld-power-control.service",
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "ExecStart",
            "--no-pager",
        ),
        (backend.GAME_POWER_CONTROL, "status", "--json"),
    ]


def test_game_power_backend_status_includes_authoritative_runtime_snapshot(
    monkeypatch,
    tmp_path,
):
    backend = load_game_power_backend()
    snapshot_path = tmp_path / "game-power-runtime.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": "game-power-runtime-snapshot-v1",
                "timestamp_monotonic_s": 10.0,
                "source": "daemon",
                "mode": "automatic",
                "control_active": True,
                "sample_source": "governor",
                "appid": "1091500",
                "last_action": "gpu-priority-epp",
                "last_reason": "package limited with GPU activity",
                "classification_primary": "gpu-package-bound",
                "classification_confidence": "high",
                "fps_target": {
                    "status": "unknown",
                    "source": "none",
                    "confidence": "low",
                    "fps": None,
                    "target_frame_ms": None,
                    "raw": None,
                },
                "frame_source": {
                    "status": "missing",
                    "source": "none",
                    "confidence": "low",
                    "avg_fps": None,
                    "p95_ms": None,
                    "p99_ms": None,
                    "sample_count": None,
                    "window_s": None,
                },
                "package_w": 24.0,
                "core_w": 7.0,
                "uncore_w": 10.0,
                "pl1_w": 30,
                "render_busy": 0.91,
                "stale": False,
                "error": None,
            }
        )
        + "\n"
    )
    monkeypatch.setattr(backend, "RUNTIME_SNAPSHOT", str(snapshot_path))

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        if cmd[0] == "systemctl":
            return FakeCommandProcess(stdout=b"ActiveState=active\nSubState=running\n")
        return FakeCommandProcess(stdout=b'{"mode": "default", "override_active": false}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend.Plugin().get_status())

    assert result["runtime"]["schema_version"] == "game-power-runtime-snapshot-v1"
    assert result["runtime"]["source"] == "daemon"
    assert result["runtime"]["sample_source"] == "governor"
    assert result["runtime"]["fps_target"]["status"] == "unknown"
    assert result["runtime"]["frame_source"]["status"] == "missing"


def test_game_power_backend_sample_once_returns_public_subset(monkeypatch):
    backend = load_game_power_backend()
    private_row = {
        "appid": "1091500",
        "action": "observe-only",
        "reason": "sample",
        "package_w": 22.0,
        "core_w": 7.0,
        "uncore_w": 9.0,
        "pl1_w": 22.0,
        "render_busy": 0.86,
        "classification": {"value": "foreground-game", "evidence": {"pid": 123}},
        "pressure": {"cpu": {"some": {"source_path": "/proc/pressure/cpu"}}},
        "ab_pair_id": "pair-1",
        "thermal_start_c": 61.0,
        "cooldown_rule": "fixed-60s",
        "unknown_extra": "internal",
    }

    async def fake_run_command(*cmd, **kwargs):
        return json.dumps(private_row) + "\n"

    monkeypatch.setattr(backend, "_run_command", fake_run_command)

    result = asyncio.run(backend.Plugin().sample_once())

    assert result == {
        "appid": "1091500",
        "sample_source": "probe",
        "action": "observe-only",
        "reason": "sample",
        "package_w": 22.0,
        "core_w": 7.0,
        "uncore_w": 9.0,
        "pl1_w": 22.0,
        "render_busy": 0.86,
        "fps_target": {
            "status": "unknown",
            "source": "none",
            "confidence": "low",
            "fps": None,
            "target_frame_ms": None,
            "raw": None,
        },
        "frame_source": {
            "status": "missing",
            "source": "none",
            "confidence": "low",
            "avg_fps": None,
            "p95_ms": None,
            "p99_ms": None,
            "sample_count": None,
            "window_s": None,
        },
    }


def test_game_power_backend_sample_once_fallback_uses_public_subset(monkeypatch):
    backend = load_game_power_backend()

    async def fake_run_command(*cmd, **kwargs):
        return "\n"

    monkeypatch.setattr(backend, "_run_command", fake_run_command)

    result = asyncio.run(backend.Plugin().sample_once())

    assert result == {
        "appid": None,
        "sample_source": "probe",
        "action": "observe-only",
        "reason": "no foreground game sample",
        "package_w": None,
        "core_w": None,
        "uncore_w": None,
        "pl1_w": None,
        "render_busy": None,
        "fps_target": {
            "status": "unknown",
            "source": "none",
            "confidence": "low",
            "fps": None,
            "target_frame_ms": None,
            "raw": None,
        },
        "frame_source": {
            "status": "missing",
            "source": "none",
            "confidence": "low",
            "avg_fps": None,
            "p95_ms": None,
            "p99_ms": None,
            "sample_count": None,
            "window_s": None,
        },
    }


def test_game_power_backend_no_longer_exposes_dropin_constants():
    backend = load_game_power_backend()

    assert not hasattr(backend, "DROPIN_DIR")
    assert not hasattr(backend, "DROPIN_PATH")


def test_game_power_backend_restore_removes_only_plugin_dropin(monkeypatch):
    backend = load_game_power_backend()
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCommandProcess(stdout=b'{"mode": "default", "override_active": false}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend.Plugin().restore_defaults())

    assert result["restored"] is True
    commands = [call[0] for call in calls]
    assert commands[0] == (
        backend.GAME_POWER_CONTROL,
        "restore-defaults",
        "--json",
    )
