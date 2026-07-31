import asyncio
import json
import math
import os
import pwd
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
# V10 persona intents (plan section 0). Validated locally so an unsupported
# value fails closed in the backend, exactly like validate_mode, before any CLI
# process is spawned.
SUPPORTED_PERSONAS = ("battery", "ac-quiet", "ac-performance")
FRAME_FEED_STATES = ("live", "stale", "absent")
# The frame limiter helper (control CLI `limiter` subcommand, contract 1.6)
# drives gamescope's own control channel and MUST run inside the gamescope
# session user's bus, like the display-workaround service. The Decky backend
# runs as root, so it hops to the session user with the same runuser + env
# shape the on-device scripts use.
SESSION_USER = "deck"
SESSION_USER_FALLBACK_UID = 1000


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


def validate_persona(persona) -> str:
    if isinstance(persona, str) and persona in SUPPORTED_PERSONAS:
        return persona
    raise ValueError(
        "unsupported persona: expected one of " + ", ".join(SUPPORTED_PERSONAS)
    )


def _session_runtime_dir() -> str:
    try:
        uid = pwd.getpwnam(SESSION_USER).pw_uid
    except KeyError:
        uid = SESSION_USER_FALLBACK_UID
    return f"/run/user/{uid}"


def _limiter_command(action: str, fps: int | None = None) -> tuple[str, ...]:
    runtime_dir = _session_runtime_dir()
    cmd = [
        "runuser",
        "-u",
        SESSION_USER,
        "--",
        "env",
        f"XDG_RUNTIME_DIR={runtime_dir}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime_dir}/bus",
        GAME_POWER_CONTROL,
        "limiter",
        action,
    ]
    if fps is not None:
        cmd.append(str(fps))
    cmd += ["--source", "decky", "--json"]
    return tuple(cmd)


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
        "phase": None,
        "phase_reason_codes": [],
        "ladder_step": None,
        "color_ledger": None,
        "verdict_ledger_health": None,
        "gated_lanes": None,
        **_blank_v10_fields(),
        "stale": True,
        "error": reason,
    }


def _dict_or_default(value, default: dict) -> dict:
    return value if isinstance(value, dict) else default


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _public_phase(row: dict):
    phase = row.get("phase")
    return phase if isinstance(phase, str) else None


def _public_ladder_step(row: dict):
    step = row.get("ladder_step")
    return step if _is_int(step) else None


def _compact_color_ledger(row: dict):
    ledger = row.get("color_ledger")
    if not isinstance(ledger, dict):
        return None
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        entries = []
    summary: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        color = entry.get("color")
        if not isinstance(color, str):
            continue
        bucket = summary.setdefault(
            color,
            {"color": color, "entry_count": 0, "tid_count": 0, "actuator_states": {}},
        )
        bucket["entry_count"] += 1
        tid_count = entry.get("tid_count")
        if _is_int(tid_count):
            bucket["tid_count"] += tid_count
        state = entry.get("actuator_state")
        if isinstance(state, str):
            states = bucket["actuator_states"]
            states[state] = states.get(state, 0) + 1
    return {
        "truncated": bool(ledger.get("truncated")),
        "colors": [summary[color] for color in sorted(summary)],
    }


def _public_verdict_ledger_health(row: dict):
    health = row.get("verdict_ledger_health")
    if not isinstance(health, dict):
        return None
    status = health.get("status")
    if not isinstance(status, str):
        return None
    reason = health.get("reason")
    entry_count = health.get("entry_count")
    path = health.get("path")
    return {
        "status": status,
        "reason": reason if isinstance(reason, str) else None,
        "entry_count": entry_count if _is_int(entry_count) else None,
        "path": path if isinstance(path, str) else None,
    }


def _public_gated_lanes(row: dict):
    lanes = row.get("gated_lanes")
    if not isinstance(lanes, dict):
        return None
    result: dict[str, dict] = {}
    for name, lane in lanes.items():
        if not isinstance(name, str) or not isinstance(lane, dict):
            continue
        state = lane.get("state")
        if not isinstance(state, str):
            continue
        public_lane = {"state": state, "reason_codes": _str_list(lane.get("reason_codes"))}
        variants = lane.get("variants")
        if isinstance(variants, list):
            public_lane["variants"] = _str_list(variants)
        step = lane.get("step")
        if _is_int(step):
            public_lane["step"] = step
        result[name] = public_lane
    return result or None


def _blank_v10_fields() -> dict:
    return {
        "persona": None,
        "soft_pl1_w": None,
        "gpu_freq_caps": None,
        "boost_active": None,
        "boost_reason": None,
        "trim_rungs_active": None,
        "frame_feed_status": None,
        "limiter_state": None,
        "p95_baseline_ms": None,
        "p95_budget_ms": None,
        "auto_target": None,
    }


def _public_gpu_freq_caps(row: dict):
    caps = row.get("gpu_freq_caps")
    if not isinstance(caps, dict):
        return None
    min_mhz = caps.get("min_mhz")
    max_mhz = caps.get("max_mhz")
    public = {
        "min_mhz": min_mhz if _is_int(min_mhz) else None,
        "max_mhz": max_mhz if _is_int(max_mhz) else None,
    }
    if public["min_mhz"] is None and public["max_mhz"] is None:
        return None
    return public


def _public_v10_fields(row: dict) -> dict:
    """Extract the telemetry v3 fields (contract 1.7) defensively.

    Populated only by target-balance decisions, so the gpu-priority default,
    stale snapshots, and off/observe leave them blank (same evidence-gating
    pattern as the V9 additive fields). ``persona`` gates the whole block: a
    missing persona means a gpu-priority snapshot, so everything stays blank.
    """

    persona = row.get("persona")
    if not isinstance(persona, str):
        return _blank_v10_fields()
    soft_pl1_w = row.get("soft_pl1_w")
    boost_active = row.get("boost_active")
    boost_reason = row.get("boost_reason")
    trim_rungs = row.get("trim_rungs_active")
    frame_feed_status = row.get("frame_feed_status")
    limiter_state = row.get("limiter_state")
    return {
        "persona": persona,
        "soft_pl1_w": soft_pl1_w if _is_int(soft_pl1_w) else None,
        "gpu_freq_caps": _public_gpu_freq_caps(row),
        "boost_active": boost_active if isinstance(boost_active, bool) else None,
        "boost_reason": boost_reason if isinstance(boost_reason, str) else None,
        "trim_rungs_active": _str_list(trim_rungs) if isinstance(trim_rungs, list) else None,
        "frame_feed_status": (
            frame_feed_status if frame_feed_status in FRAME_FEED_STATES else None
        ),
        "limiter_state": limiter_state if isinstance(limiter_state, str) else None,
        "p95_baseline_ms": _float_or_none(row.get("p95_baseline_ms")),
        "p95_budget_ms": _float_or_none(row.get("p95_budget_ms")),
        "auto_target": _public_auto_target(row),
    }


def _float_or_none(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def _public_auto_target(row: dict):
    """Auto frame-target state, validated defensively.

    The panel builds its frame-target choices from ``candidates`` here. Anything
    missing must degrade to None rather than to a half-populated dict, because a
    silently empty candidate list is what left the control greyed out and
    unusable.
    """
    auto = row.get("auto_target")
    if not isinstance(auto, dict):
        return None
    proposal = auto.get("proposal")
    public_proposal = None
    if isinstance(proposal, dict) and _is_int(proposal.get("fps")):
        public_proposal = {
            "fps": int(proposal["fps"]),
            "reason": proposal.get("reason") if isinstance(proposal.get("reason"), str) else None,
            "sustainable_fps": _float_or_none(proposal.get("sustainable_fps")),
            "samples": int(proposal["samples"]) if _is_int(proposal.get("samples")) else None,
        }
    gpu = auto.get("gpu")
    public_gpu = None
    if isinstance(gpu, dict):
        saturated = gpu.get("saturated")
        public_gpu = {
            "render_busy": _float_or_none(gpu.get("render_busy")),
            "c6_ms": _float_or_none(gpu.get("c6_ms")),
            "actual_mhz": _float_or_none(gpu.get("actual_mhz")),
            "saturated": saturated if isinstance(saturated, bool) else None,
        }
    candidates = auto.get("candidates")
    return {
        "status": auto.get("status") if isinstance(auto.get("status"), str) else None,
        "refresh_hz": _float_or_none(auto.get("refresh_hz")),
        "candidates": [
            int(value) for value in candidates if _is_int(value)
        ] if isinstance(candidates, list) else [],
        "input_idle_s": _float_or_none(auto.get("input_idle_s")),
        "cap_applied_fps": int(auto["cap_applied_fps"]) if _is_int(auto.get("cap_applied_fps")) else None,
        "cap_reason": auto.get("cap_reason") if isinstance(auto.get("cap_reason"), str) else None,
        "drops_this_session": (
            int(auto["drops_this_session"]) if _is_int(auto.get("drops_this_session")) else None
        ),
        "proposal": public_proposal,
        "gpu": public_gpu,
    }


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
        phase = None
        phase_reason_codes: list[str] = []
        ladder_step = None
        color_ledger = None
        verdict_ledger_health = None
        gated_lanes = None
        v10_fields = _blank_v10_fields()
    else:
        fps_target = _dict_or_default(row.get("fps_target"), _default_target_state())
        frame_source = _dict_or_default(
            row.get("frame_source"),
            _default_frame_source_state(),
        )
        phase = _public_phase(row)
        phase_reason_codes = _str_list(row.get("phase_reason_codes"))
        ladder_step = _public_ladder_step(row)
        color_ledger = _compact_color_ledger(row)
        verdict_ledger_health = _public_verdict_ledger_health(row)
        gated_lanes = _public_gated_lanes(row)
        v10_fields = _public_v10_fields(row)
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
        "phase": phase,
        "phase_reason_codes": phase_reason_codes,
        "ladder_step": ladder_step,
        "color_ledger": color_ledger,
        "verdict_ledger_health": verdict_ledger_health,
        "gated_lanes": gated_lanes,
        **v10_fields,
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

    async def set_persona(self, persona: str) -> dict:
        persona = validate_persona(persona)
        output = await _run_command(
            GAME_POWER_CONTROL,
            "set-persona",
            persona,
            "--source",
            "decky",
            "--json",
        )
        return json.loads(output)

    async def clear_persona(self) -> dict:
        output = await _run_command(GAME_POWER_CONTROL, "clear-persona", "--json")
        return json.loads(output)

    async def limiter_status(self) -> dict:
        output = await _run_command(*_limiter_command("status"))
        return json.loads(output)

    async def set_limiter(self, fps) -> dict:
        fps = validate_fps_target(fps)
        output = await _run_command(*_limiter_command("set", fps))
        return json.loads(output)

    async def clear_limiter(self) -> dict:
        output = await _run_command(*_limiter_command("clear"))
        return json.loads(output)

    async def restore_defaults(self) -> dict:
        output = await _run_command(GAME_POWER_CONTROL, "restore-defaults", "--json")
        result = json.loads(output)
        result["restored"] = True
        return result
