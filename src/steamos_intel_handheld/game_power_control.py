#!/usr/bin/env python3
"""Safe runtime control surface for the game-power governor."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path

from .game_power import (
    FrameTargetTelemetry,
    GamePowerConfig,
    GamePowerMode,
    GamePowerPersona,
)

DEFAULT_CONTROL_FILE = Path("/var/lib/steamos-intel-handheld/game-power-control.json")
POLICY_LABEL = "Balanced automatic policy"
SCHEMA_VERSION = 1
FPS_TARGET_MIN = 30
FPS_TARGET_MAX = 120
FPS_TARGET_STEP = 5
SUPPORTED_PERSONAS = tuple(persona.value for persona in GamePowerPersona)

# --- V10 frame-limiter helper (contract 1.6) --------------------------------
# The helper shells out to gamescope's own control channel (gamescopectl), the
# same ownership model the display workaround uses. It MUST run in the session
# user's environment (XDG_RUNTIME_DIR / DBUS_SESSION_BUS_ADDRESS), exactly like
# the gamescope display workaround; the daemon never calls this -- only the
# Decky backend or a user helper service does. ``set`` writes
# ``gamescopectl debug_set_fps_limit <fps>`` and ``clear`` writes limit 0.
# ``debug_set_fps_limit`` semantics (0 == unlimited) are DEVICE-UNVERIFIED in
# this environment; implemented per the V10 plan contract 1.6.
GAMESCOPECTL_BIN = "gamescopectl"
GAMESCOPECTL_SET_LIMIT_COMMAND = "debug_set_fps_limit"
LIMITER_CLEAR_FPS = 0
# Validation range mirrors the FPS override (30-120 step 5) plus 0 for clear.
GamescopectlRunner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def validate_persona(value: object) -> str:
    if not isinstance(value, str) or value not in SUPPORTED_PERSONAS:
        raise ValueError(
            "unsupported persona: expected one of " + ", ".join(SUPPORTED_PERSONAS)
        )
    return value
PUBLIC_TO_INTERNAL_MODE = {
    # "automatic" is the V10 demand-shaping governor (target-balance): hold the
    # frame target at the lowest power the scene needs. "legacy" keeps the V9
    # EPP-only gpu-priority path reachable as a fallback.
    "automatic": GamePowerMode.TARGET_BALANCE,
    "legacy": GamePowerMode.GPU_PRIORITY,
    "observe": GamePowerMode.OBSERVE,
    "off": GamePowerMode.OFF,
}
SUPPORTED_MODES = tuple(PUBLIC_TO_INTERNAL_MODE)


@dataclass(frozen=True)
class FpsTargetOverride:
    status: str = "auto"
    fps: int | None = None
    source: str | None = None
    supported_min: int = FPS_TARGET_MIN
    supported_max: int = FPS_TARGET_MAX
    supported_step: int = FPS_TARGET_STEP

    def to_json_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "fps": self.fps,
            "source": self.source,
            "supported_min": self.supported_min,
            "supported_max": self.supported_max,
            "supported_step": self.supported_step,
        }


@dataclass(frozen=True)
class PersonaOverride:
    status: str = "auto"  # auto | manual | invalid
    persona: str | None = None
    source: str | None = None
    supported: tuple[str, ...] = SUPPORTED_PERSONAS

    def to_json_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "persona": self.persona,
            "source": self.source,
            "supported": list(self.supported),
        }


@dataclass(frozen=True)
class RuntimeControlStatus:
    mode: str
    effective_mode: GamePowerMode | None
    override_active: bool
    policy_label: str = POLICY_LABEL
    source: str | None = None
    supported_modes: tuple[str, ...] = SUPPORTED_MODES
    fps_target_override: FpsTargetOverride = field(default_factory=FpsTargetOverride)
    persona_override: PersonaOverride = field(default_factory=PersonaOverride)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "effective_mode": self.effective_mode.value if self.effective_mode else None,
            "override_active": self.override_active,
            "policy_label": self.policy_label,
            "source": self.source,
            "supported_modes": list(self.supported_modes),
            "fps_target_override": self.fps_target_override.to_json_dict(),
            "persona_override": self.persona_override.to_json_dict(),
        }


def public_mode_to_internal(mode: str) -> GamePowerMode:
    try:
        return PUBLIC_TO_INTERNAL_MODE[mode]
    except KeyError as exc:
        raise ValueError(f"unsupported game-power mode: {mode}") from exc


def set_runtime_mode(
    path: str | Path = DEFAULT_CONTROL_FILE,
    mode: str = "automatic",
    *,
    source: str = "cli",
) -> RuntimeControlStatus:
    public_mode_to_internal(mode)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _read_valid_payload(path) or {"schema_version": SCHEMA_VERSION}
    if _fps_target_override_from_payload(payload).status == "invalid":
        payload.pop("fps_target_override", None)
    payload.update({
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "source": source,
    })
    _atomic_json_write(path, payload)
    return read_runtime_status(path)


def validate_fps_target(fps: object) -> int:
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


def set_fps_target(
    path: str | Path = DEFAULT_CONTROL_FILE,
    fps: object = 40,
    *,
    source: str = "cli",
) -> RuntimeControlStatus:
    fps_value = validate_fps_target(fps)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _read_valid_payload(path) or {"schema_version": SCHEMA_VERSION}
    payload["schema_version"] = SCHEMA_VERSION
    payload["fps_target_override"] = {
        "fps": fps_value,
        "source": source,
    }
    _atomic_json_write(path, payload)
    return read_runtime_status(path)


def set_persona(
    path: str | Path = DEFAULT_CONTROL_FILE,
    persona: object = "battery",
    *,
    source: str = "cli",
) -> RuntimeControlStatus:
    persona_value = validate_persona(persona)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _read_valid_payload(path) or {"schema_version": SCHEMA_VERSION}
    payload["schema_version"] = SCHEMA_VERSION
    payload["persona"] = {"persona": persona_value, "source": source}
    _atomic_json_write(path, payload)
    return read_runtime_status(path)


def clear_persona(path: str | Path = DEFAULT_CONTROL_FILE) -> RuntimeControlStatus:
    path = Path(path)
    payload = _read_valid_payload(path)
    if payload is None:
        return read_runtime_status(path)
    payload.pop("persona", None)
    payload["schema_version"] = SCHEMA_VERSION
    if _payload_has_active_override(payload):
        _atomic_json_write(path, payload)
    else:
        with suppress(FileNotFoundError):
            path.unlink()
    return read_runtime_status(path)


def clear_fps_target(path: str | Path = DEFAULT_CONTROL_FILE) -> RuntimeControlStatus:
    path = Path(path)
    payload = _read_valid_payload(path)
    if payload is None:
        with suppress(FileNotFoundError):
            path.unlink()
        return RuntimeControlStatus(mode="default", effective_mode=None, override_active=False)
    payload.pop("fps_target_override", None)
    payload["schema_version"] = SCHEMA_VERSION
    if _payload_has_active_override(payload):
        _atomic_json_write(path, payload)
        return read_runtime_status(path)
    with suppress(FileNotFoundError):
        path.unlink()
    return RuntimeControlStatus(mode="default", effective_mode=None, override_active=False)


def restore_runtime_defaults(path: str | Path = DEFAULT_CONTROL_FILE) -> RuntimeControlStatus:
    path = Path(path)
    with suppress(FileNotFoundError):
        path.unlink()
    return RuntimeControlStatus(mode="default", effective_mode=None, override_active=False)


def read_runtime_status(path: str | Path = DEFAULT_CONTROL_FILE) -> RuntimeControlStatus:
    path = Path(path)
    if not path.exists():
        return RuntimeControlStatus(mode="default", effective_mode=None, override_active=False)
    payload = _read_valid_payload(path)
    if payload is None:
        return RuntimeControlStatus(
            mode="invalid",
            effective_mode=None,
            override_active=True,
            fps_target_override=FpsTargetOverride(status="invalid"),
        )
    raw_mode = payload.get("mode")
    if raw_mode is None:
        mode = "default"
        effective_mode = None
    else:
        try:
            mode = str(raw_mode)
            effective_mode = public_mode_to_internal(mode)
        except ValueError:
            return RuntimeControlStatus(
                mode="invalid",
                effective_mode=None,
                override_active=True,
                fps_target_override=_fps_target_override_from_payload(payload),
            )
    fps_target_override = _fps_target_override_from_payload(payload)
    persona_override = _persona_override_from_payload(payload)
    return RuntimeControlStatus(
        mode=mode,
        effective_mode=effective_mode,
        override_active=_payload_has_active_override(payload),
        source=payload.get("source") if isinstance(payload.get("source"), str) else None,
        fps_target_override=fps_target_override,
        persona_override=persona_override,
    )


def effective_config_from_runtime_file(
    base: GamePowerConfig,
    path: str | Path = DEFAULT_CONTROL_FILE,
) -> GamePowerConfig:
    status = read_runtime_status(path)
    control_health = _runtime_control_health_from_status(status)
    updates: dict[str, object] = {"runtime_control_health": control_health}
    control_valid = control_health["status"] == "ready"
    if not control_valid:
        updates["mode"] = GamePowerMode.OFF
    elif status.effective_mode is not None:
        updates["mode"] = status.effective_mode
    if (
        control_valid
        and status.fps_target_override.status == "manual"
        and status.fps_target_override.fps
    ):
        updates["frame_target"] = FrameTargetTelemetry(
            fps_target=float(status.fps_target_override.fps),
            source="manual",
            confidence="high",
        )
    if (
        control_valid
        and status.persona_override.status == "manual"
        and status.persona_override.persona is not None
    ):
        updates["persona"] = GamePowerPersona(status.persona_override.persona)
    return replace(base, **updates) if updates else base


def _runtime_control_health_from_status(
    status: RuntimeControlStatus,
) -> dict[str, object]:
    if status.mode == "invalid":
        return {
            "status": "invalid",
            "mode": status.mode,
            "override_active": status.override_active,
            "fps_target_override_status": status.fps_target_override.status,
            "reason": "invalid-control-file",
        }
    if status.fps_target_override.status == "invalid":
        return {
            "status": "invalid",
            "mode": status.mode,
            "override_active": status.override_active,
            "fps_target_override_status": status.fps_target_override.status,
            "reason": "invalid-fps-target-override",
        }
    if status.persona_override.status == "invalid":
        return {
            "status": "invalid",
            "mode": status.mode,
            "override_active": status.override_active,
            "fps_target_override_status": status.fps_target_override.status,
            "persona_override_status": status.persona_override.status,
            "reason": "invalid-persona-override",
        }
    return {
        "status": "ready",
        "mode": status.mode,
        "override_active": status.override_active,
        "fps_target_override_status": status.fps_target_override.status,
        "persona_override_status": status.persona_override.status,
        "reason": "control-ready",
    }


def _read_valid_payload(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        return None
    return dict(payload)


def _fps_target_override_from_payload(payload: dict[str, object]) -> FpsTargetOverride:
    raw = payload.get("fps_target_override")
    if raw is None:
        return FpsTargetOverride(status="auto")
    if not isinstance(raw, dict):
        return FpsTargetOverride(status="invalid")
    try:
        fps = validate_fps_target(raw.get("fps"))
    except ValueError:
        return FpsTargetOverride(status="invalid")
    return FpsTargetOverride(
        status="manual",
        fps=fps,
        source=raw.get("source") if isinstance(raw.get("source"), str) else None,
    )


def _persona_override_from_payload(payload: dict[str, object]) -> PersonaOverride:
    raw = payload.get("persona")
    if raw is None:
        return PersonaOverride(status="auto")
    if not isinstance(raw, dict):
        return PersonaOverride(status="invalid")
    try:
        persona = validate_persona(raw.get("persona"))
    except ValueError:
        return PersonaOverride(status="invalid")
    return PersonaOverride(
        status="manual",
        persona=persona,
        source=raw.get("source") if isinstance(raw.get("source"), str) else None,
    )


def _payload_has_active_override(payload: dict[str, object]) -> bool:
    raw_mode = payload.get("mode")
    has_mode = isinstance(raw_mode, str) and raw_mode in SUPPORTED_MODES
    return (
        has_mode
        or _fps_target_override_from_payload(payload).status == "manual"
        or _persona_override_from_payload(payload).status == "manual"
    )


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    text = json.dumps(payload, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()


def validate_limiter_fps(fps: object) -> int:
    """Validate a limiter FPS value: 0 (clear) or 30-120 in 5 FPS steps."""

    if not isinstance(fps, int) or isinstance(fps, bool):
        raise ValueError(_limiter_fps_range_message())
    if fps == LIMITER_CLEAR_FPS:
        return LIMITER_CLEAR_FPS
    if fps < FPS_TARGET_MIN or fps > FPS_TARGET_MAX:
        raise ValueError(_limiter_fps_range_message())
    if (fps - FPS_TARGET_MIN) % FPS_TARGET_STEP != 0:
        raise ValueError(_limiter_fps_range_message())
    return fps


def _limiter_fps_range_message() -> str:
    return (
        "unsupported limiter FPS: expected 0 (clear) or an integer between "
        f"{FPS_TARGET_MIN} and {FPS_TARGET_MAX} in {FPS_TARGET_STEP} FPS steps"
    )


@dataclass(frozen=True)
class LimiterStatus:
    # status: limited | unlimited | unknown | unsupported
    status: str
    fps: int | None = None
    supported: bool = False
    source: str | None = None
    raw: str | None = None
    supported_min: int = FPS_TARGET_MIN
    supported_max: int = FPS_TARGET_MAX
    supported_step: int = FPS_TARGET_STEP
    clear_fps: int = LIMITER_CLEAR_FPS

    def to_json_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "fps": self.fps,
            "supported": self.supported,
            "source": self.source,
            "raw": self.raw,
            "supported_min": self.supported_min,
            "supported_max": self.supported_max,
            "supported_step": self.supported_step,
            "clear_fps": self.clear_fps,
        }


def _default_gamescopectl_runner(
    argv: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    # Run gamescopectl directly. The caller (Decky backend or user helper
    # service) is responsible for the session-user environment, exactly like the
    # gamescope display workaround service; the daemon never invokes this.
    return subprocess.run(  # noqa: S603
        [GAMESCOPECTL_BIN, *argv],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _gamescopectl_supports_set_limit(
    runner: GamescopectlRunner,
) -> tuple[bool, str | None]:
    """Feature-detect the debug_set_fps_limit command from gamescopectl help.

    Returns (supported, raw_help). On any launch failure we report unsupported
    rather than guessing the command exists.
    """

    try:
        completed = runner(["--help"])
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"gamescopectl unavailable: {exc}"
    raw = (completed.stdout or "") + (completed.stderr or "")
    return (GAMESCOPECTL_SET_LIMIT_COMMAND in raw), (raw or None)


def limiter_status(*, runner: GamescopectlRunner | None = None) -> LimiterStatus:
    """Report the gamescope FPS limiter status via gamescopectl feature-detect.

    gamescopectl exposes no reliable read-back of the current limit on the
    target build, so a supported mechanism still yields ``status=unknown`` (we
    never fabricate a value); an absent command yields ``status=unsupported``.
    """

    runner = runner or _default_gamescopectl_runner
    supported, raw = _gamescopectl_supports_set_limit(runner)
    if not supported:
        return LimiterStatus(
            status="unsupported",
            supported=False,
            source="gamescopectl",
            raw=raw,
        )
    return LimiterStatus(
        status="unknown",
        supported=True,
        source="gamescopectl",
        raw=raw,
    )


def limiter_set(
    fps: object,
    *,
    source: str = "cli",
    runner: GamescopectlRunner | None = None,
) -> LimiterStatus:
    fps_value = validate_limiter_fps(fps)
    return _limiter_apply(fps_value, source=source, runner=runner)


def limiter_clear(
    *,
    source: str = "cli",
    runner: GamescopectlRunner | None = None,
) -> LimiterStatus:
    return _limiter_apply(LIMITER_CLEAR_FPS, source=source, runner=runner)


def _limiter_apply(
    fps_value: int,
    *,
    source: str,
    runner: GamescopectlRunner | None,
) -> LimiterStatus:
    runner = runner or _default_gamescopectl_runner
    supported, raw = _gamescopectl_supports_set_limit(runner)
    if not supported:
        raise RuntimeError(
            "gamescope frame limiter is unavailable: gamescopectl does not "
            f"expose {GAMESCOPECTL_SET_LIMIT_COMMAND}"
        )
    try:
        completed = runner([GAMESCOPECTL_SET_LIMIT_COMMAND, str(fps_value)])
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"gamescopectl invocation failed: {exc}") from exc
    if completed.returncode != 0:
        raise RuntimeError(
            "gamescopectl "
            f"{GAMESCOPECTL_SET_LIMIT_COMMAND} {fps_value} failed "
            f"(exit {completed.returncode}): "
            f"{(completed.stderr or completed.stdout or '').strip()}"
        )
    limited = fps_value != LIMITER_CLEAR_FPS
    return LimiterStatus(
        status="limited" if limited else "unlimited",
        fps=fps_value if limited else LIMITER_CLEAR_FPS,
        supported=True,
        source=source,
        raw=(completed.stdout or None),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--control-file",
        default=str(DEFAULT_CONTROL_FILE),
        help="runtime game-power control JSON path",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="read runtime game-power status")
    status.add_argument("--json", action="store_true")

    set_mode = subparsers.add_parser("set-mode", help="set safe public game-power mode")
    set_mode.add_argument("mode", choices=SUPPORTED_MODES)
    set_mode.add_argument("--source", default="cli")
    set_mode.add_argument("--json", action="store_true")

    set_target = subparsers.add_parser(
        "set-fps-target",
        help="set safe manual FPS target for game-power balancing",
    )
    set_target.add_argument("fps", type=int)
    set_target.add_argument("--source", default="cli")
    set_target.add_argument("--json", action="store_true")

    clear_target = subparsers.add_parser(
        "clear-fps-target",
        help="return FPS target selection to SteamOS/runtime discovery",
    )
    clear_target.add_argument("--json", action="store_true")

    set_persona_cmd = subparsers.add_parser(
        "set-persona", help="set the game-power persona override"
    )
    set_persona_cmd.add_argument("persona", choices=SUPPORTED_PERSONAS)
    set_persona_cmd.add_argument("--source", default="cli")
    set_persona_cmd.add_argument("--json", action="store_true")

    clear_persona_cmd = subparsers.add_parser(
        "clear-persona", help="return persona selection to power-source detection"
    )
    clear_persona_cmd.add_argument("--json", action="store_true")

    restore = subparsers.add_parser("restore-defaults", help="remove runtime override")
    restore.add_argument("--json", action="store_true")

    limiter = subparsers.add_parser(
        "limiter",
        help=(
            "consent-gated gamescope frame limiter helper (contract 1.6). "
            "MUST run as the session user with XDG_RUNTIME_DIR / "
            "DBUS_SESSION_BUS_ADDRESS set, like the gamescope display "
            "workaround; the daemon never calls this -- only Decky or a user "
            "helper service does."
        ),
    )
    limiter.add_argument(
        "action",
        choices=("status", "set", "clear"),
        help="status: read via gamescopectl feature-detect; set <fps>: "
        f"{GAMESCOPECTL_SET_LIMIT_COMMAND}; clear: limit 0 (unlimited)",
    )
    limiter.add_argument(
        "fps",
        type=int,
        nargs="?",
        help="target FPS for 'set' (30-120 in 5 FPS steps)",
    )
    limiter.add_argument("--source", default="cli")
    limiter.add_argument("--json", action="store_true")
    return parser


def _print_status(status: RuntimeControlStatus, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(status.to_json_dict(), sort_keys=True))
    else:
        print(
            "game-power-control "
            f"mode={status.mode} "
            f"effective_mode={status.effective_mode.value if status.effective_mode else '-'} "
            f"override_active={str(status.override_active).lower()}"
        )


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    path = Path(args.control_file)
    if args.command == "status":
        status = read_runtime_status(path)
    elif args.command == "set-mode":
        status = set_runtime_mode(path, args.mode, source=args.source)
    elif args.command == "set-fps-target":
        status = set_fps_target(path, args.fps, source=args.source)
    elif args.command == "clear-fps-target":
        status = clear_fps_target(path)
    elif args.command == "set-persona":
        status = set_persona(path, args.persona, source=args.source)
    elif args.command == "clear-persona":
        status = clear_persona(path)
    elif args.command == "restore-defaults":
        status = restore_runtime_defaults(path)
    elif args.command == "limiter":
        _run_limiter(args)
        return
    else:
        raise AssertionError(f"unhandled command: {args.command}")
    _print_status(status, json_output=bool(args.json))


def _run_limiter(args: argparse.Namespace) -> None:
    if args.action == "status":
        limiter = limiter_status()
    elif args.action == "set":
        if args.fps is None:
            raise SystemExit("limiter set requires an FPS value")
        limiter = limiter_set(args.fps, source=args.source)
    elif args.action == "clear":
        limiter = limiter_clear(source=args.source)
    else:  # pragma: no cover - argparse choices guard this
        raise AssertionError(f"unhandled limiter action: {args.action}")
    if args.json:
        print(json.dumps(limiter.to_json_dict(), sort_keys=True))
    else:
        print(
            "game-power-limiter "
            f"action={args.action} "
            f"status={limiter.status} "
            f"fps={limiter.fps if limiter.fps is not None else '-'} "
            f"supported={str(limiter.supported).lower()}"
        )


if __name__ == "__main__":
    main()
