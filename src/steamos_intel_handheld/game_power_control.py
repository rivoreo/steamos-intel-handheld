#!/usr/bin/env python3
"""Safe runtime control surface for the game-power governor."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path

from .game_power import GamePowerConfig, GamePowerMode

DEFAULT_CONTROL_FILE = Path("/run/steamos-intel-handheld/game-power-control.json")
POLICY_LABEL = "Balanced automatic policy"
SCHEMA_VERSION = 1
PUBLIC_TO_INTERNAL_MODE = {
    "automatic": GamePowerMode.GPU_PRIORITY,
    "observe": GamePowerMode.OBSERVE,
    "off": GamePowerMode.OFF,
}
SUPPORTED_MODES = tuple(PUBLIC_TO_INTERNAL_MODE)


@dataclass(frozen=True)
class RuntimeControlStatus:
    mode: str
    effective_mode: GamePowerMode | None
    override_active: bool
    policy_label: str = POLICY_LABEL
    source: str | None = None
    supported_modes: tuple[str, ...] = SUPPORTED_MODES

    def to_json_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "effective_mode": self.effective_mode.value if self.effective_mode else None,
            "override_active": self.override_active,
            "policy_label": self.policy_label,
            "source": self.source,
            "supported_modes": list(self.supported_modes),
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
    effective_mode = public_mode_to_internal(mode)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "source": source,
    }
    _atomic_json_write(path, payload)
    return RuntimeControlStatus(
        mode=mode,
        effective_mode=effective_mode,
        override_active=True,
        source=source,
    )


def restore_runtime_defaults(path: str | Path = DEFAULT_CONTROL_FILE) -> RuntimeControlStatus:
    path = Path(path)
    with suppress(FileNotFoundError):
        path.unlink()
    return RuntimeControlStatus(mode="default", effective_mode=None, override_active=False)


def read_runtime_status(path: str | Path = DEFAULT_CONTROL_FILE) -> RuntimeControlStatus:
    path = Path(path)
    if not path.exists():
        return RuntimeControlStatus(mode="default", effective_mode=None, override_active=False)
    try:
        payload = json.loads(path.read_text())
        mode = str(payload["mode"])
        effective_mode = public_mode_to_internal(mode)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return RuntimeControlStatus(mode="invalid", effective_mode=None, override_active=True)
    return RuntimeControlStatus(
        mode=mode,
        effective_mode=effective_mode,
        override_active=True,
        source=payload.get("source") if isinstance(payload.get("source"), str) else None,
    )


def effective_config_from_runtime_file(
    base: GamePowerConfig,
    path: str | Path = DEFAULT_CONTROL_FILE,
) -> GamePowerConfig:
    status = read_runtime_status(path)
    if status.effective_mode is None:
        return base
    return replace(base, mode=status.effective_mode)


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
    elif args.command == "restore-defaults":
        status = restore_runtime_defaults(path)
    else:
        raise AssertionError(f"unhandled command: {args.command}")
    _print_status(status, json_output=bool(args.json))


if __name__ == "__main__":
    main()
