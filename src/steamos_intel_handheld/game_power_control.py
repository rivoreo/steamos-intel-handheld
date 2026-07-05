#!/usr/bin/env python3
"""Safe runtime control surface for the game-power governor."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path

from .game_power import FrameTargetTelemetry, GamePowerConfig, GamePowerMode

DEFAULT_CONTROL_FILE = Path("/run/steamos-intel-handheld/game-power-control.json")
POLICY_LABEL = "Balanced automatic policy"
SCHEMA_VERSION = 1
FPS_TARGET_MIN = 30
FPS_TARGET_MAX = 120
FPS_TARGET_STEP = 5
PUBLIC_TO_INTERNAL_MODE = {
    "automatic": GamePowerMode.GPU_PRIORITY,
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
class RuntimeControlStatus:
    mode: str
    effective_mode: GamePowerMode | None
    override_active: bool
    policy_label: str = POLICY_LABEL
    source: str | None = None
    supported_modes: tuple[str, ...] = SUPPORTED_MODES
    fps_target_override: FpsTargetOverride = field(default_factory=FpsTargetOverride)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "effective_mode": self.effective_mode.value if self.effective_mode else None,
            "override_active": self.override_active,
            "policy_label": self.policy_label,
            "source": self.source,
            "supported_modes": list(self.supported_modes),
            "fps_target_override": self.fps_target_override.to_json_dict(),
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
    return RuntimeControlStatus(
        mode=mode,
        effective_mode=effective_mode,
        override_active=_payload_has_active_override(payload),
        source=payload.get("source") if isinstance(payload.get("source"), str) else None,
        fps_target_override=fps_target_override,
    )


def effective_config_from_runtime_file(
    base: GamePowerConfig,
    path: str | Path = DEFAULT_CONTROL_FILE,
) -> GamePowerConfig:
    status = read_runtime_status(path)
    updates: dict[str, object] = {}
    if status.effective_mode is not None:
        updates["mode"] = status.effective_mode
    if status.fps_target_override.status == "manual" and status.fps_target_override.fps:
        updates["frame_target"] = FrameTargetTelemetry(
            fps_target=float(status.fps_target_override.fps),
            source="manual",
            confidence="high",
        )
    return replace(base, **updates) if updates else base


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


def _payload_has_active_override(payload: dict[str, object]) -> bool:
    raw_mode = payload.get("mode")
    has_mode = isinstance(raw_mode, str) and raw_mode in SUPPORTED_MODES
    return has_mode or _fps_target_override_from_payload(payload).status == "manual"


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

    restore = subparsers.add_parser("restore-defaults", help="remove runtime override")
    restore.add_argument("--json", action="store_true")
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
    elif args.command == "restore-defaults":
        status = restore_runtime_defaults(path)
    else:
        raise AssertionError(f"unhandled command: {args.command}")
    _print_status(status, json_output=bool(args.json))


if __name__ == "__main__":
    main()
