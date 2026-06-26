import asyncio
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "decky" / "steamos-intel-handheld-ec" / "main.py"


def load_backend():
    spec = importlib.util.spec_from_file_location("decky_charge_limit_backend", BACKEND)
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
