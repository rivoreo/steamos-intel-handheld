import asyncio
import importlib.util
import json
import math
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


def ready_evidence_readiness() -> dict:
    return {
        "status": "target-aware-live",
        "target_ready": True,
        "frame_ready": True,
        "learning_ready": False,
        "claim_ready": True,
        "control_ready": True,
        "write_policy": "epp-only",
        "reasons": ["control ready", "fps target known", "frame data ready"],
    }


def runtime_snapshot_row(**updates) -> dict:
    row = {
        "schema_version": "game-power-runtime-snapshot-v1",
        "timestamp_monotonic_s": 100.0,
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
            "status": "known",
            "source": "manual",
            "confidence": "high",
            "fps": 40.0,
            "target_frame_ms": 25.0,
            "raw": None,
        },
        "frame_source": {
            "status": "live",
            "source": "mangohud-csv",
            "confidence": "high",
            "avg_fps": 44.0,
            "p95_ms": 24.0,
            "p99_ms": None,
            "sample_count": 12,
            "window_s": 6.0,
        },
        "package_w": 24.0,
        "core_w": 7.0,
        "uncore_w": 10.0,
        "pl1_w": 30,
        "render_busy": 0.91,
        "learning": {
            "status": "unknown",
            "session_samples": None,
            "positive_samples": None,
            "required_samples": None,
            "required_sessions": None,
            "reusable_next_launch": False,
            "skip_reason": "unavailable",
            "hint_key": None,
        },
        "evidence_readiness": ready_evidence_readiness(),
        "stale": False,
        "error": None,
    }
    row.update(updates)
    return row


def target_balance_v9_fields() -> dict:
    return {
        "phase": "at-target",
        "phase_reason_codes": ["target-satisfied", "p95-guard-ok"],
        "ladder_step": 3,
        "color_ledger": {
            "truncated": True,
            "entries": [
                {
                    "role_key": "foreground-game:worker-thread",
                    "color": "A",
                    "tid_count": 2,
                    "cpu_time_ms_per_s": 640.0,
                    "runqueue_wait_ms_per_s": 31.5,
                    "cpus_seen": [0, 1, 2, 3],
                    "actuator": "uclamp-min",
                    "actuator_state": "active",
                    "blocking_reason_codes": [],
                },
                {
                    "role_key": "foreground-game:render-thread",
                    "color": "A",
                    "tid_count": 1,
                    "cpu_time_ms_per_s": 210.0,
                    "runqueue_wait_ms_per_s": 4.0,
                    "cpus_seen": [2, 3],
                    "actuator": "observe-only",
                    "actuator_state": "advisory",
                    "blocking_reason_codes": [],
                },
                {
                    "role_key": "background-helper:updater",
                    "color": "D",
                    "tid_count": 3,
                    "cpu_time_ms_per_s": 12.0,
                    "runqueue_wait_ms_per_s": 0.0,
                    "cpus_seen": [4, 5],
                    "actuator": "bg-weight",
                    "actuator_state": "blocked",
                    "blocking_reason_codes": ["no-verdict-for-context"],
                },
            ],
        },
        "verdict_ledger_health": {
            "status": "ready",
            "reason": None,
            "entry_count": 4,
            "path": "/var/lib/steamos-intel-handheld/game-power-verdicts.json",
        },
        "gated_lanes": {
            "foreground_uclamp_min": {"state": "active", "reason_codes": []},
            "background_shaping": {
                "state": "blocked",
                "reason_codes": ["no-verdict-for-context"],
                "variants": ["cpu-weight-80"],
            },
            "ladder_deep_step": {"state": "blocked", "reason_codes": ["no-verdict-for-context"]},
        },
    }


def target_balance_v10_fields() -> dict:
    return {
        "persona": "battery",
        "soft_pl1_w": 11,
        "gpu_freq_caps": {"min_mhz": None, "max_mhz": 1350},
        "boost_active": False,
        "boost_reason": None,
        "trim_rungs_active": ["G1", "P1"],
        "frame_feed_status": "live",
        "limiter_state": "unknown",
    }


def test_game_power_backend_unavailable_snapshot_includes_blank_v10_fields():
    backend = load_game_power_backend()

    result = backend._runtime_snapshot_unavailable("missing-runtime-snapshot")

    assert result["persona"] is None
    assert result["soft_pl1_w"] is None
    assert result["gpu_freq_caps"] is None
    assert result["boost_active"] is None
    assert result["boost_reason"] is None
    assert result["trim_rungs_active"] is None
    assert result["frame_feed_status"] is None
    assert result["limiter_state"] is None


def test_game_power_backend_public_snapshot_exposes_v10_target_balance_fields(monkeypatch):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend.time, "monotonic", lambda: 101.0)

    result = backend._public_runtime_snapshot(
        runtime_snapshot_row(**target_balance_v10_fields())
    )

    assert result["persona"] == "battery"
    assert result["soft_pl1_w"] == 11
    assert result["gpu_freq_caps"] == {"min_mhz": None, "max_mhz": 1350}
    assert result["boost_active"] is False
    assert result["boost_reason"] is None
    assert result["trim_rungs_active"] == ["G1", "P1"]
    assert result["frame_feed_status"] == "live"
    assert result["limiter_state"] == "unknown"


def test_game_power_backend_public_snapshot_degrades_without_v10_fields(monkeypatch):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend.time, "monotonic", lambda: 101.0)

    result = backend._public_runtime_snapshot(runtime_snapshot_row())

    assert result["persona"] is None
    assert result["soft_pl1_w"] is None
    assert result["gpu_freq_caps"] is None
    assert result["boost_active"] is None
    assert result["trim_rungs_active"] is None
    assert result["frame_feed_status"] is None
    assert result["limiter_state"] is None


def test_game_power_backend_public_snapshot_hides_v10_fields_when_stale(monkeypatch):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend.time, "monotonic", lambda: 120.1)

    result = backend._public_runtime_snapshot(
        runtime_snapshot_row(**target_balance_v10_fields())
    )

    assert result["stale"] is True
    assert result["persona"] is None
    assert result["soft_pl1_w"] is None
    assert result["gpu_freq_caps"] is None
    assert result["boost_active"] is None
    assert result["trim_rungs_active"] is None
    assert result["frame_feed_status"] is None
    assert result["limiter_state"] is None


def test_game_power_backend_public_snapshot_hides_v10_fields_when_off(monkeypatch):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend.time, "monotonic", lambda: 101.0)

    result = backend._public_runtime_snapshot(
        runtime_snapshot_row(mode="off", **target_balance_v10_fields())
    )

    assert result["persona"] is None
    assert result["frame_feed_status"] is None
    assert result["limiter_state"] is None


def test_game_power_backend_public_snapshot_sanitizes_malformed_v10_fields(monkeypatch):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend.time, "monotonic", lambda: 101.0)

    result = backend._public_runtime_snapshot(
        runtime_snapshot_row(
            persona="battery",
            soft_pl1_w="eleven",
            gpu_freq_caps={"min_mhz": "x", "max_mhz": "y"},
            boost_active="yes",
            boost_reason=5,
            trim_rungs_active=["G1", 7, None, "P2"],
            frame_feed_status="glowing",
            limiter_state=42,
        )
    )

    assert result["persona"] == "battery"
    assert result["soft_pl1_w"] is None
    assert result["gpu_freq_caps"] is None
    assert result["boost_active"] is None
    assert result["boost_reason"] is None
    assert result["trim_rungs_active"] == ["G1", "P2"]
    assert result["frame_feed_status"] is None
    assert result["limiter_state"] is None


def test_game_power_backend_status_surfaces_v10_fields_from_snapshot(monkeypatch, tmp_path):
    backend = load_game_power_backend()
    snapshot_path = tmp_path / "game-power-runtime.json"
    snapshot_path.write_text(
        json.dumps(runtime_snapshot_row(**target_balance_v10_fields())) + "\n"
    )
    monkeypatch.setattr(backend, "RUNTIME_SNAPSHOT", str(snapshot_path))
    monkeypatch.setattr(backend.time, "monotonic", lambda: 101.0)

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        if cmd[0] == "systemctl":
            return FakeCommandProcess(stdout=b"ActiveState=active\nSubState=running\n")
        return FakeCommandProcess(stdout=b'{"mode": "default", "override_active": false}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend.Plugin().get_status())

    assert result["runtime"]["persona"] == "battery"
    assert result["runtime"]["soft_pl1_w"] == 11
    assert result["runtime"]["frame_feed_status"] == "live"
    assert result["runtime"]["limiter_state"] == "unknown"


def test_game_power_backend_set_persona_calls_control_cli(monkeypatch):
    backend = load_game_power_backend()
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCommandProcess(
            stdout=b'{"persona_override": {"status": "manual", "persona": "ac-quiet"}}'
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend.Plugin().set_persona("ac-quiet"))

    assert result["persona_override"]["persona"] == "ac-quiet"
    assert [call[0] for call in calls] == [
        (
            backend.GAME_POWER_CONTROL,
            "set-persona",
            "ac-quiet",
            "--source",
            "decky",
            "--json",
        )
    ]


def test_game_power_backend_rejects_invalid_persona_without_spawning(monkeypatch):
    backend = load_game_power_backend()

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        raise AssertionError(f"unexpected spawn: {cmd}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    for persona in ("turbo", "", "BATTERY", 42, None):
        try:
            asyncio.run(backend.Plugin().set_persona(persona))
        except ValueError as exc:
            assert "unsupported persona" in str(exc)
        else:
            raise AssertionError(f"{persona!r} unexpectedly accepted")


def test_game_power_backend_clear_persona_calls_control_cli(monkeypatch):
    backend = load_game_power_backend()
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCommandProcess(
            stdout=b'{"persona_override": {"status": "auto", "persona": null}}'
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend.Plugin().clear_persona())

    assert result["persona_override"]["status"] == "auto"
    assert [call[0] for call in calls] == [
        (backend.GAME_POWER_CONTROL, "clear-persona", "--json")
    ]


def test_game_power_backend_limiter_runs_control_cli_as_session_user(monkeypatch):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend, "_session_runtime_dir", lambda: "/run/user/1000")
    calls = []

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCommandProcess(
            stdout=b'{"status": "unknown", "supported": true, "fps": null}'
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    status = asyncio.run(backend.Plugin().limiter_status())
    applied = asyncio.run(backend.Plugin().set_limiter(40))
    cleared = asyncio.run(backend.Plugin().clear_limiter())

    assert status["status"] == "unknown"
    assert applied["supported"] is True
    assert cleared["status"] == "unknown"
    assert [call[0] for call in calls] == [
        (
            "runuser",
            "-u",
            "deck",
            "--",
            "env",
            "XDG_RUNTIME_DIR=/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
            backend.GAME_POWER_CONTROL,
            "limiter",
            "status",
            "--source",
            "decky",
            "--json",
        ),
        (
            "runuser",
            "-u",
            "deck",
            "--",
            "env",
            "XDG_RUNTIME_DIR=/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
            backend.GAME_POWER_CONTROL,
            "limiter",
            "set",
            "40",
            "--source",
            "decky",
            "--json",
        ),
        (
            "runuser",
            "-u",
            "deck",
            "--",
            "env",
            "XDG_RUNTIME_DIR=/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
            backend.GAME_POWER_CONTROL,
            "limiter",
            "clear",
            "--source",
            "decky",
            "--json",
        ),
    ]


def test_game_power_backend_rejects_invalid_limiter_fps_without_spawning(monkeypatch):
    backend = load_game_power_backend()

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        raise AssertionError(f"unexpected spawn: {cmd}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    for fps in (0, 29, 37, 121, "40", None):
        try:
            asyncio.run(backend.Plugin().set_limiter(fps))
        except ValueError as exc:
            assert "unsupported FPS target" in str(exc)
        else:
            raise AssertionError(f"{fps!r} unexpectedly accepted")


def test_game_power_backend_session_runtime_dir_falls_back_when_no_session_user(monkeypatch):
    backend = load_game_power_backend()

    def raise_key_error(_name):
        raise KeyError("deck")

    monkeypatch.setattr(backend.pwd, "getpwnam", raise_key_error)

    assert backend._session_runtime_dir() == "/run/user/1000"


def test_game_power_backend_runtime_snapshot_defaults_evidence_readiness():
    backend = load_game_power_backend()

    result = backend._runtime_snapshot_unavailable("missing-runtime-snapshot")

    assert result["evidence_readiness"]["status"] == "unavailable"
    assert result["evidence_readiness"]["claim_ready"] is False
    assert result["evidence_readiness"]["write_policy"] == "disabled"


def test_game_power_backend_public_runtime_snapshot_passes_through_readiness(
    monkeypatch,
):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend.time, "monotonic", lambda: 101.0)

    result = backend._public_runtime_snapshot(runtime_snapshot_row())

    assert result["stale"] is False
    assert result["evidence_readiness"]["status"] == "target-aware-live"
    assert result["evidence_readiness"]["claim_ready"] is True


def test_game_power_backend_public_runtime_snapshot_overrides_stale_and_error_readiness(
    monkeypatch,
):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend.time, "monotonic", lambda: 120.1)

    stale = backend._public_runtime_snapshot(runtime_snapshot_row())
    errored = backend._public_runtime_snapshot(
        runtime_snapshot_row(timestamp_monotonic_s=119.0, error="daemon-error")
    )

    assert stale["stale"] is True
    assert stale["evidence_readiness"]["status"] == "unavailable"
    assert stale["evidence_readiness"]["claim_ready"] is False
    assert stale["fps_target"]["status"] == "unknown"
    assert stale["frame_source"]["status"] == "missing"
    assert errored["evidence_readiness"]["status"] == "unavailable"
    assert errored["evidence_readiness"]["claim_ready"] is False
    assert errored["fps_target"]["status"] == "unknown"
    assert errored["frame_source"]["status"] == "missing"


def test_game_power_backend_public_runtime_snapshot_sanitizes_malformed_readiness(
    monkeypatch,
):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend.time, "monotonic", lambda: 101.0)
    invalid_rows = (
        runtime_snapshot_row(evidence_readiness=None),
        runtime_snapshot_row(evidence_readiness=[]),
        runtime_snapshot_row(evidence_readiness={"status": "target-aware-live"}),
        runtime_snapshot_row(
            evidence_readiness={
                **ready_evidence_readiness(),
                "status": "unknown-ready-state",
            }
        ),
        runtime_snapshot_row(
            evidence_readiness={**ready_evidence_readiness(), "claim_ready": "yes"}
        ),
        runtime_snapshot_row(
            evidence_readiness={
                **ready_evidence_readiness(),
                "status": "power-signals-only",
            }
        ),
        runtime_snapshot_row(mode="off"),
        runtime_snapshot_row(mode="observe"),
        runtime_snapshot_row(
            evidence_readiness={
                **ready_evidence_readiness(),
                "status": "control-invalid",
            }
        ),
        runtime_snapshot_row(
            evidence_readiness={**ready_evidence_readiness(), "target_ready": False}
        ),
        runtime_snapshot_row(
            evidence_readiness={**ready_evidence_readiness(), "frame_ready": False}
        ),
        runtime_snapshot_row(
            evidence_readiness={**ready_evidence_readiness(), "control_ready": False}
        ),
    )

    for row in invalid_rows:
        result = backend._public_runtime_snapshot(row)
        assert result["evidence_readiness"]["status"] == "unavailable"
        assert result["evidence_readiness"]["claim_ready"] is False


def test_game_power_backend_public_runtime_snapshot_rejects_invalid_timestamps(
    monkeypatch,
):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend.time, "monotonic", lambda: 101.0)

    for timestamp in (None, "100.0", math.nan, math.inf):
        result = backend._public_runtime_snapshot(
            runtime_snapshot_row(timestamp_monotonic_s=timestamp)
        )

        assert result["evidence_readiness"]["status"] == "unavailable"
        assert result["evidence_readiness"]["claim_ready"] is False
        assert result["fps_target"]["status"] == "unknown"
        assert result["frame_source"]["status"] == "missing"


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


def test_game_power_backend_unavailable_snapshot_includes_v9_fields():
    backend = load_game_power_backend()

    result = backend._runtime_snapshot_unavailable("missing-runtime-snapshot")

    assert result["phase"] is None
    assert result["phase_reason_codes"] == []
    assert result["ladder_step"] is None
    assert result["color_ledger"] is None
    assert result["verdict_ledger_health"] is None
    assert result["gated_lanes"] is None


def test_game_power_backend_public_snapshot_exposes_v9_target_balance_fields(monkeypatch):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend.time, "monotonic", lambda: 101.0)

    result = backend._public_runtime_snapshot(
        runtime_snapshot_row(**target_balance_v9_fields())
    )

    assert result["phase"] == "at-target"
    assert result["phase_reason_codes"] == ["target-satisfied", "p95-guard-ok"]
    assert result["ladder_step"] == 3

    ledger = result["color_ledger"]
    assert ledger["truncated"] is True
    assert ledger["colors"] == [
        {
            "color": "A",
            "entry_count": 2,
            "tid_count": 3,
            "actuator_states": {"active": 1, "advisory": 1},
        },
        {
            "color": "D",
            "entry_count": 1,
            "tid_count": 3,
            "actuator_states": {"blocked": 1},
        },
    ]

    assert result["verdict_ledger_health"] == {
        "status": "ready",
        "reason": None,
        "entry_count": 4,
        "path": "/var/lib/steamos-intel-handheld/game-power-verdicts.json",
    }

    lanes = result["gated_lanes"]
    assert lanes["foreground_uclamp_min"] == {"state": "active", "reason_codes": []}
    assert lanes["background_shaping"] == {
        "state": "blocked",
        "reason_codes": ["no-verdict-for-context"],
        "variants": ["cpu-weight-80"],
    }
    assert lanes["ladder_deep_step"] == {
        "state": "blocked",
        "reason_codes": ["no-verdict-for-context"],
    }


def test_game_power_backend_public_snapshot_degrades_without_v9_fields(monkeypatch):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend.time, "monotonic", lambda: 101.0)

    result = backend._public_runtime_snapshot(runtime_snapshot_row())

    assert result["phase"] is None
    assert result["phase_reason_codes"] == []
    assert result["ladder_step"] is None
    assert result["color_ledger"] is None
    assert result["verdict_ledger_health"] is None
    assert result["gated_lanes"] is None


def test_game_power_backend_public_snapshot_hides_v9_fields_when_stale(monkeypatch):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend.time, "monotonic", lambda: 120.1)

    result = backend._public_runtime_snapshot(
        runtime_snapshot_row(**target_balance_v9_fields())
    )

    assert result["stale"] is True
    assert result["phase"] is None
    assert result["phase_reason_codes"] == []
    assert result["ladder_step"] is None
    assert result["color_ledger"] is None
    assert result["verdict_ledger_health"] is None
    assert result["gated_lanes"] is None


def test_game_power_backend_public_snapshot_sanitizes_malformed_v9_fields(monkeypatch):
    backend = load_game_power_backend()
    monkeypatch.setattr(backend.time, "monotonic", lambda: 101.0)

    result = backend._public_runtime_snapshot(
        runtime_snapshot_row(
            phase=123,
            phase_reason_codes="not-a-list",
            ladder_step=True,
            color_ledger={"truncated": 0, "entries": [None, {"color": 5}, "junk"]},
            verdict_ledger_health={"reason": "x"},
            gated_lanes={
                "foreground_uclamp_min": {"reason_codes": ["only-codes"]},
                "background_shaping": "not-a-dict",
                "ladder_deep_step": {"state": "blocked", "reason_codes": [1, "ok"]},
            },
        )
    )

    assert result["phase"] is None
    assert result["phase_reason_codes"] == []
    assert result["ladder_step"] is None
    assert result["color_ledger"] == {"truncated": False, "colors": []}
    assert result["verdict_ledger_health"] is None
    assert result["gated_lanes"] == {
        "ladder_deep_step": {"state": "blocked", "reason_codes": ["ok"]},
    }


def test_game_power_backend_status_surfaces_v9_fields_from_snapshot(monkeypatch, tmp_path):
    backend = load_game_power_backend()
    snapshot_path = tmp_path / "game-power-runtime.json"
    snapshot_path.write_text(
        json.dumps(runtime_snapshot_row(**target_balance_v9_fields())) + "\n"
    )
    monkeypatch.setattr(backend, "RUNTIME_SNAPSHOT", str(snapshot_path))
    monkeypatch.setattr(backend.time, "monotonic", lambda: 101.0)

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        if cmd[0] == "systemctl":
            return FakeCommandProcess(stdout=b"ActiveState=active\nSubState=running\n")
        return FakeCommandProcess(stdout=b'{"mode": "default", "override_active": false}')

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(backend.Plugin().get_status())

    assert result["runtime"]["phase"] == "at-target"
    assert result["runtime"]["ladder_step"] == 3
    assert result["runtime"]["color_ledger"]["colors"][0]["color"] == "A"
    assert result["runtime"]["gated_lanes"]["foreground_uclamp_min"]["state"] == "active"
