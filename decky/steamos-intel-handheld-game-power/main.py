import asyncio
import json
import math
import os
import time
from pathlib import Path


SERVICE = "steamos-intel-handheld-power-control.service"
GAME_POWER = "/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power"
GAME_POWER_CONTROL = "/opt/steamos-intel-handheld/bin/steamos-intel-handheld-game-power-control"
RUNTIME_SNAPSHOT = "/run/steamos-intel-handheld/game-power-runtime.json"
RUNTIME_SNAPSHOT_SCHEMA = "game-power-runtime-snapshot-v1"
RUNTIME_SNAPSHOT_STALE_AFTER_S = 10.0
POLICY_LABEL = "Balanced automatic policy"
VALID_MODES = {"automatic", "observe", "off"}
FPS_TARGET_MIN = 30
FPS_TARGET_MAX = 120
FPS_TARGET_STEP = 5


def _clean_env() -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin"}
    if "LANG" in os.environ:
        env["LANG"] = os.environ["LANG"]
    return env


async def _run_command(*cmd: str, input_text: str | None = None) -> str:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        env=_clean_env(),
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(
        input_text.encode() if input_text is not None else None
    )
    if process.returncode != 0:
        message = stderr.decode().strip() or stdout.decode().strip()
        raise RuntimeError(message or f"{cmd[0]} failed with {process.returncode}")
    return stdout.decode()


def validate_mode(mode: str) -> str:
    if mode in VALID_MODES:
        return mode
    raise ValueError(f"unsupported game-power mode: {mode}")


def validate_fps_target(fps) -> int:
    if not isinstance(fps, int) or isinstance(fps, bool):
        raise ValueError(
            "unsupported FPS target: expected an integer between "
            f"{FPS_TARGET_MIN} and {FPS_TARGET_MAX} in {FPS_TARGET_STEP} FPS steps"
        )
    if fps < FPS_TARGET_MIN or fps > FPS_TARGET_MAX:
        raise ValueError(
            "unsupported FPS target: expected an integer between "
            f"{FPS_TARGET_MIN} and {FPS_TARGET_MAX} in {FPS_TARGET_STEP} FPS steps"
        )
    if (fps - FPS_TARGET_MIN) % FPS_TARGET_STEP != 0:
        raise ValueError(
            "unsupported FPS target: expected an integer between "
            f"{FPS_TARGET_MIN} and {FPS_TARGET_MAX} in {FPS_TARGET_STEP} FPS steps"
        )
    return fps


def _parse_systemctl_show(output: str) -> dict[str, str]:
    values = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _mode_from_execstart(execstart: str) -> str:
    parts = execstart.split()
    try:
        raw = parts[parts.index("--game-power-mode") + 1]
    except (ValueError, IndexError):
        return "unknown"
    if raw == "gpu-priority":
        return "automatic"
    if raw in {"off", "observe"}:
        return raw
    return "unknown"


async def _service_status() -> dict:
    output = await _run_command(
        "systemctl",
        "show",
        SERVICE,
        "-p",
        "ActiveState",
        "-p",
        "SubState",
        "-p",
        "ExecStart",
        "--no-pager",
    )
    runtime = await _runtime_status()
    return _service_status_from(output, runtime)


def _service_status_from(output: str, runtime: dict) -> dict:
    values = _parse_systemctl_show(output)
    execstart = values.get("ExecStart", "")
    runtime_mode = runtime.get("mode")
    mode = runtime_mode if runtime_mode != "default" else _mode_from_execstart(execstart)
    return {
        "active_state": values.get("ActiveState", "unknown"),
        "sub_state": values.get("SubState", "unknown"),
        "mode": mode,
        "override_active": bool(runtime.get("override_active")),
        "policy_label": runtime.get("policy_label", POLICY_LABEL),
    }


async def _service_and_runtime_status() -> tuple[dict, dict]:
    output = await _run_command(
        "systemctl",
        "show",
        SERVICE,
        "-p",
        "ActiveState",
        "-p",
        "SubState",
        "-p",
        "ExecStart",
        "--no-pager",
    )
    runtime = await _runtime_status()
    return _service_status_from(output, runtime), runtime


async def _runtime_status() -> dict:
    output = await _run_command(GAME_POWER_CONTROL, "status", "--json")
    return json.loads(output)


def _default_target_state() -> dict:
    return {
        "status": "unknown",
        "source": "none",
        "confidence": "low",
        "fps": None,
        "target_frame_ms": None,
        "raw": None,
    }


def _default_frame_source_state() -> dict:
    return {
        "status": "missing",
        "source": "none",
        "confidence": "low",
        "avg_fps": None,
        "p95_ms": None,
        "p99_ms": None,
        "sample_count": None,
        "window_s": None,
    }


def _default_learning_state() -> dict:
    return {
        "status": "unknown",
        "session_samples": None,
        "positive_samples": None,
        "required_samples": None,
        "required_sessions": None,
        "reusable_next_launch": False,
        "skip_reason": "unavailable",
        "hint_key": None,
    }


def _default_evidence_readiness(reason: str = "runtime-unavailable") -> dict:
    return {
        "status": "unavailable",
        "target_ready": False,
        "frame_ready": False,
        "learning_ready": False,
        "claim_ready": False,
        "control_ready": False,
        "write_policy": "disabled",
        "reasons": [reason],
    }


def _runtime_snapshot_unavailable(reason: str) -> dict:
    return {
        "schema_version": RUNTIME_SNAPSHOT_SCHEMA,
        "timestamp_monotonic_s": None,
        "source": "daemon",
        "mode": None,
        "control_active": False,
        "sample_source": "governor",
        "appid": None,
        "last_action": None,
        "last_reason": None,
        "classification_primary": None,
        "classification_confidence": None,
        "fps_target": _default_target_state(),
        "frame_source": _default_frame_source_state(),
        "package_w": None,
        "core_w": None,
        "uncore_w": None,
        "pl1_w": None,
        "render_busy": None,
        "learning": _default_learning_state(),
        "evidence_readiness": _default_evidence_readiness(reason),
        "stale": True,
        "error": reason,
    }


def _dict_or_default(value, default: dict) -> dict:
    return value if isinstance(value, dict) else default


def _valid_timestamp(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _public_evidence_readiness(
    row: dict,
    *,
    stale: bool,
    error,
    timestamp_valid: bool,
) -> dict:
    if not timestamp_valid:
        return _default_evidence_readiness("runtime-timestamp-invalid")
    if stale:
        return _default_evidence_readiness("runtime-stale")
    if error:
        return _default_evidence_readiness("runtime-error")

    readiness = row.get("evidence_readiness")
    if not isinstance(readiness, dict):
        return _default_evidence_readiness("runtime-readiness-invalid")

    status = readiness.get("status")
    write_policy = readiness.get("write_policy")
    reasons = readiness.get("reasons")
    allowed_statuses = {
        "unavailable",
        "control-invalid",
        "stopped",
        "view-data-only",
        "target-aware-live",
        "power-signals-only",
    }
    if status not in allowed_statuses or not isinstance(write_policy, str):
        return _default_evidence_readiness("runtime-readiness-invalid")
    if not isinstance(reasons, list):
        return _default_evidence_readiness("runtime-readiness-invalid")

    bool_fields = (
        "target_ready",
        "frame_ready",
        "learning_ready",
        "claim_ready",
        "control_ready",
    )
    if any(not isinstance(readiness.get(field), bool) for field in bool_fields):
        return _default_evidence_readiness("runtime-readiness-invalid")

    mode = row.get("mode")
    claim_ready = readiness["claim_ready"]
    if claim_ready and status != "target-aware-live":
        return _default_evidence_readiness("runtime-readiness-invalid")
    if claim_ready and mode in {"off", "observe"}:
        return _default_evidence_readiness("runtime-readiness-invalid")
    if (
        status == "target-aware-live"
        and claim_ready
        and (
            mode != "automatic"
            or not readiness["target_ready"]
            or not readiness["frame_ready"]
            or not readiness["control_ready"]
        )
    ):
        return _default_evidence_readiness("runtime-readiness-invalid")
    if status == "control-invalid" and (
        readiness["control_ready"] or readiness["claim_ready"]
    ):
        return _default_evidence_readiness("runtime-readiness-invalid")

    return {
        "status": status,
        "target_ready": readiness["target_ready"],
        "frame_ready": readiness["frame_ready"],
        "learning_ready": readiness["learning_ready"],
        "claim_ready": readiness["claim_ready"],
        "control_ready": readiness["control_ready"],
        "write_policy": write_policy,
        "reasons": [item for item in reasons if isinstance(item, str)],
    }


def _public_runtime_snapshot(row: dict) -> dict:
    timestamp = row.get("timestamp_monotonic_s")
    timestamp_valid = _valid_timestamp(timestamp)
    stale = bool(row.get("stale"))
    if timestamp_valid:
        stale = stale or (time.monotonic() - float(timestamp)) > RUNTIME_SNAPSHOT_STALE_AFTER_S
    else:
        stale = True
    error = row.get("error")
    evidence_readiness = _public_evidence_readiness(
        row,
        stale=stale,
        error=error,
        timestamp_valid=timestamp_valid,
    )
    if evidence_readiness.get("status") in {
        "unavailable",
        "control-invalid",
        "stopped",
        "view-data-only",
    }:
        fps_target = _default_target_state()
        frame_source = _default_frame_source_state()
    else:
        fps_target = _dict_or_default(row.get("fps_target"), _default_target_state())
        frame_source = _dict_or_default(
            row.get("frame_source"),
            _default_frame_source_state(),
        )
    return {
        "schema_version": row.get("schema_version", RUNTIME_SNAPSHOT_SCHEMA),
        "timestamp_monotonic_s": timestamp,
        "source": row.get("source", "daemon"),
        "mode": row.get("mode"),
        "control_active": bool(row.get("control_active")),
        "sample_source": row.get("sample_source", "governor"),
        "appid": row.get("appid"),
        "last_action": row.get("last_action"),
        "last_reason": row.get("last_reason"),
        "classification_primary": row.get("classification_primary"),
        "classification_confidence": row.get("classification_confidence"),
        "fps_target": fps_target,
        "frame_source": frame_source,
        "package_w": row.get("package_w"),
        "core_w": row.get("core_w"),
        "uncore_w": row.get("uncore_w"),
        "pl1_w": row.get("pl1_w"),
        "render_busy": row.get("render_busy"),
        "learning": _dict_or_default(row.get("learning"), _default_learning_state()),
        "evidence_readiness": evidence_readiness,
        "stale": stale,
        "error": error,
    }


def _read_runtime_snapshot() -> dict:
    try:
        payload = json.loads(Path(RUNTIME_SNAPSHOT).read_text())
    except FileNotFoundError:
        return _runtime_snapshot_unavailable("missing-runtime-snapshot")
    except (OSError, json.JSONDecodeError) as exc:
        return _runtime_snapshot_unavailable(f"invalid-runtime-snapshot: {exc}")
    if not isinstance(payload, dict):
        return _runtime_snapshot_unavailable("invalid-runtime-snapshot-shape")
    if payload.get("schema_version") != RUNTIME_SNAPSHOT_SCHEMA:
        return _runtime_snapshot_unavailable("unsupported-runtime-snapshot-schema")
    return _public_runtime_snapshot(payload)


def _target_state_from_legacy_row(row: dict) -> dict:
    nested = row.get("fps_target")
    if isinstance(nested, dict):
        return nested
    if isinstance(nested, (int, float)):
        fps = round(float(nested), 3)
        target_frame_ms = round(1000.0 / fps, 3) if fps > 0 else None
        return {
            "status": "known",
            "source": row.get("fps_target_source") or "manual",
            "confidence": row.get("fps_target_confidence") or "medium",
            "fps": fps,
            "target_frame_ms": target_frame_ms,
            "raw": None,
        }
    return _default_target_state()


def _frame_source_from_legacy_row(row: dict) -> dict:
    nested = row.get("frame_source")
    if isinstance(nested, dict):
        return nested
    sample_count = row.get("frame_performance_sample_count")
    avg_fps = row.get("frame_avg_fps")
    p95_ms = row.get("frame_p95_ms")
    if sample_count is None and avg_fps is None and p95_ms is None:
        return _default_frame_source_state()
    return {
        "status": "live" if avg_fps is not None and p95_ms is not None else "malformed",
        "source": row.get("frame_performance_source") or "unknown",
        "confidence": row.get("frame_performance_confidence") or "low",
        "avg_fps": avg_fps,
        "p95_ms": p95_ms,
        "p99_ms": None,
        "sample_count": sample_count,
        "window_s": row.get("frame_performance_window_s"),
    }


def _public_sample(row: dict) -> dict:
    return {
        "appid": row.get("appid"),
        "sample_source": "probe",
        "action": row.get("action"),
        "reason": row.get("reason"),
        "package_w": row.get("package_w"),
        "core_w": row.get("core_w"),
        "uncore_w": row.get("uncore_w"),
        "pl1_w": row.get("pl1_w"),
        "render_busy": row.get("render_busy"),
        "fps_target": _target_state_from_legacy_row(row),
        "frame_source": _frame_source_from_legacy_row(row),
    }


async def _sample_once() -> dict:
    output = await _run_command(
        GAME_POWER,
        "--mode",
        "observe",
        "--duration-s",
        "2",
        "--poll-s",
        "1",
        "--output-format",
        "jsonl",
    )
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line:
            continue
        return _public_sample(json.loads(line))
    return _public_sample({
        "appid": None,
        "action": "observe-only",
        "reason": "no foreground game sample",
        "package_w": None,
        "core_w": None,
        "uncore_w": None,
        "pl1_w": None,
        "render_busy": None,
    })


class Plugin:
    async def _main(self) -> None:
        pass

    async def _unload(self) -> None:
        pass

    async def get_status(self) -> dict:
        service, control = await _service_and_runtime_status()
        return {
            "service": service,
            "runtime": _read_runtime_snapshot(),
            "control": control,
        }

    async def sample_once(self) -> dict:
        return await _sample_once()

    async def set_mode(self, mode: str) -> dict:
        mode = validate_mode(mode)
        output = await _run_command(
            GAME_POWER_CONTROL,
            "set-mode",
            mode,
            "--source",
            "decky",
            "--json",
        )
        return json.loads(output)

    async def set_fps_target(self, fps) -> dict:
        if fps is None:
            output = await _run_command(GAME_POWER_CONTROL, "clear-fps-target", "--json")
            return json.loads(output)
        fps = validate_fps_target(fps)
        output = await _run_command(
            GAME_POWER_CONTROL,
            "set-fps-target",
            str(fps),
            "--source",
            "decky",
            "--json",
        )
        return json.loads(output)

    async def restore_defaults(self) -> dict:
        output = await _run_command(GAME_POWER_CONTROL, "restore-defaults", "--json")
        result = json.loads(output)
        result["restored"] = True
        return result
