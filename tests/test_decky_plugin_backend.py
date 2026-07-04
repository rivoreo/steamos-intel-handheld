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

    assert backend.mode_to_service_args("automatic")[:2] == [
        "--game-power-mode",
        "gpu-priority",
    ]
    assert backend.mode_to_service_args("observe") == ["--game-power-mode", "observe"]
    assert backend.mode_to_service_args("off") == ["--game-power-mode", "off"]
    for blocked in ("pcore", "ecore", "threshold", "uclamp", "affinity", "custom"):
        try:
            backend.mode_to_service_args(blocked)
        except ValueError as exc:
            assert "unsupported game-power mode" in str(exc)
        else:
            raise AssertionError(f"{blocked} unexpectedly accepted")


def test_game_power_backend_writes_only_plugin_owned_runtime_dropin(monkeypatch):
    backend = load_game_power_backend()
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCommandProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend.Plugin().set_mode("automatic"))

    assert result["mode"] == "automatic"
    commands = [call[0] for call in calls]
    assert commands[0][:3] == ("install", "-d", "-m")
    assert backend.DROPIN_DIR in commands[0]
    tee_command = commands[1]
    assert tee_command == ("tee", backend.DROPIN_PATH)
    assert commands[-2] == ("systemctl", "daemon-reload")
    assert commands[-1] == (
        "systemctl",
        "restart",
        "steamos-intel-handheld-power-control.service",
    )


def test_game_power_backend_restore_removes_only_plugin_dropin(monkeypatch):
    backend = load_game_power_backend()
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCommandProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend.Plugin().restore_defaults())

    assert result["restored"] is True
    commands = [call[0] for call in calls]
    assert commands[0] == ("rm", "-f", backend.DROPIN_PATH)
    assert commands[1] == ("systemctl", "daemon-reload")
    assert commands[2] == (
        "systemctl",
        "restart",
        "steamos-intel-handheld-power-control.service",
    )
