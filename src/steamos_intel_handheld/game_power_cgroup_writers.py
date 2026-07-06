#!/usr/bin/env python3
"""Guarded cgroup writers shared by the game-power daemon and profiler.

This module is the single source of the V8 background-shaping apply/restore
code (relocated from ``game_power_profile`` so the daemon and profiler use
literally the same guarded writes) plus the new V9 foreground ``cpu.uclamp.min``
floor writer. Every write here follows the snapshot/restore/verify pattern:
the original value is recorded, the write is verified after the fact, and any
mismatch or failure is reported so callers can fail closed and restore.
"""

from __future__ import annotations

import json
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any

BACKGROUND_SHAPING_WRITE_VARIANTS = {
    "cpu-weight-80": ("cpu.weight", "80"),
    "uclamp-max-85": ("cpu.uclamp.max", "85.00"),
}

# Default probe value for the gated foreground cpu.uclamp.min floor (design
# section 5: "foreground cgroup cpu.uclamp.min floor (default probe value 25)").
FOREGROUND_UCLAMP_MIN_FLOOR = "25.00"


# ---------------------------------------------------------------------------
# Tiny JSON helpers (self-contained so this module has no import cycle with
# game_power_profile, which re-imports the public writers below).
# ---------------------------------------------------------------------------
def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Background shaping (relocated V8 guarded writers). ``*_writes`` functions keep
# their exact file-based contract; the ``*_to_cgroups`` / ``*_from_report`` cores
# expose the same logic to in-process callers (the daemon).
# ---------------------------------------------------------------------------
def apply_background_shaping_writes(
    restore_affinity_json: str | Path,
    output: str | Path,
    *,
    appid: str,
    variant: str,
    command_runner: Any | None = None,
) -> dict[str, object]:
    payload = json.loads(Path(restore_affinity_json).read_text())
    cgroups = payload.get("cgroups") if isinstance(payload, dict) else None
    report = apply_background_shaping_to_cgroups(
        cgroups if isinstance(cgroups, list) else [],
        appid=appid,
        variant=variant,
        command_runner=command_runner,
    )
    Path(output).write_text(json.dumps(_json_ready(report), indent=2, sort_keys=True) + "\n")
    return report


def apply_background_shaping_to_cgroups(
    cgroups: list[object],
    *,
    appid: str,
    variant: str,
    command_runner: Any | None = None,
) -> dict[str, object]:
    """Apply the guarded background-shaping write to an in-memory cgroup list.

    Each ``cgroups`` item is ``{"cgroup": name, "path": abs_path}`` (the same
    shape the profiler's restore-affinity snapshot uses). Only helper cgroups on
    the V8 allowlist that would be *lowered* by the write are touched.
    """

    control_file, proposed_value = _background_write_variant(variant)
    writes: list[dict[str, object]] = []
    for cgroup in cgroups:
        if not isinstance(cgroup, dict):
            continue
        cgroup_name = _optional_str(cgroup.get("cgroup"))
        cgroup_path = _optional_str(cgroup.get("path"))
        if cgroup_name is None or cgroup_path is None:
            continue
        if not _is_background_shaping_write_target(cgroup_name, appid=appid):
            continue
        path = Path(cgroup_path)
        control_path = path / control_file
        if _should_use_systemd_user_property(cgroup_name, control_file):
            write = _apply_systemd_user_background_write(
                cgroup_name,
                path,
                control_file,
                proposed_value,
                command_runner=command_runner,
            )
        elif control_path.is_file():
            write = _apply_direct_cgroup_background_write(
                cgroup_name,
                path,
                control_file,
                proposed_value,
            )
        else:
            write = _apply_systemd_user_background_write(
                cgroup_name,
                path,
                control_file,
                proposed_value,
                command_runner=command_runner,
            )
        if write is not None:
            writes.append(write)

    return {
        "mode": "background-shaping-writes",
        "write_policy": "guarded-background-shaping",
        "appid": appid,
        "variant": variant,
        "control_file": control_file,
        "proposed_value": proposed_value,
        "writes": writes,
    }


def restore_background_shaping_writes(
    writes_json: str | Path,
    output: str | Path,
    *,
    command_runner: Any | None = None,
) -> dict[str, object]:
    payload = json.loads(Path(writes_json).read_text())
    report = restore_background_shaping_from_report(
        payload if isinstance(payload, dict) else {},
        command_runner=command_runner,
    )
    Path(output).write_text(json.dumps(_json_ready(report), indent=2, sort_keys=True) + "\n")
    return report


def restore_background_shaping_from_report(
    payload: dict[str, object],
    *,
    command_runner: Any | None = None,
) -> dict[str, object]:
    """Restore original values from an apply-report produced above."""

    restores: list[dict[str, object]] = []
    restored = True
    for item in payload.get("writes") or []:
        if not isinstance(item, dict) or item.get("status") != "written":
            continue
        cgroup = _optional_str(item.get("cgroup")) or ""
        path = _optional_str(item.get("path")) or ""
        control_file = _optional_str(item.get("control_file")) or ""
        original_value = _optional_str(item.get("original_value")) or ""
        method = _optional_str(item.get("method")) or "direct-cgroup-file"
        if method == "systemd-user-property":
            restore_item = _restore_systemd_user_background_write(
                item,
                command_runner=command_runner,
            )
            status = _optional_str(restore_item.get("status")) or "restore-failed"
            restores.append(restore_item)
            if status != "restored":
                restored = False
            continue

        control_path = Path(path) / control_file
        status = "restored"
        try:
            _write_control_value(control_path, original_value)
            current_value = _read_control_value(control_path)
        except OSError:
            current_value = None
            status = "restore-failed"
        if current_value != original_value:
            restored = False
            status = "restore-mismatch" if status == "restored" else status
        restores.append(
            {
                "cgroup": cgroup,
                "path": path,
                "control_file": control_file,
                "restored_value": original_value,
                "current_value": current_value,
                "status": status,
                "method": method,
            }
        )

    return {
        "mode": "background-shaping-restore",
        "write_policy": "restore-background-shaping",
        "restored": restored,
        "restores": restores,
    }


def _background_write_variant(variant: str) -> tuple[str, str]:
    try:
        return BACKGROUND_SHAPING_WRITE_VARIANTS[variant]
    except KeyError as exc:
        choices = ", ".join(sorted(BACKGROUND_SHAPING_WRITE_VARIANTS))
        raise ValueError(
            f"unsupported background shaping variant {variant}; choices: {choices}"
        ) from exc


def _apply_direct_cgroup_background_write(
    cgroup: str,
    path: Path,
    control_file: str,
    proposed_value: str,
) -> dict[str, object] | None:
    control_path = path / control_file
    current_value = _read_control_value(control_path)
    if current_value is None:
        return None
    if not _background_write_lowers_value(control_file, current_value, proposed_value):
        return None
    _write_control_value(control_path, proposed_value)
    written_value = _read_control_value(control_path)
    return {
        "cgroup": cgroup,
        "path": str(path),
        "control_file": control_file,
        "original_value": current_value,
        "proposed_value": proposed_value,
        "status": "written" if written_value == proposed_value else "write-mismatch",
        "method": "direct-cgroup-file",
    }


def _apply_systemd_user_background_write(
    cgroup: str,
    path: Path,
    control_file: str,
    proposed_value: str,
    *,
    command_runner: Any | None,
) -> dict[str, object] | None:
    if control_file != "cpu.weight":
        return None
    unit = _systemd_user_unit_from_cgroup(cgroup)
    if unit is None:
        return None
    current_value = _systemd_user_show_property(
        unit,
        "CPUWeight",
        command_runner=command_runner,
    )
    if not _background_write_lowers_value(control_file, current_value, proposed_value):
        return None
    try:
        _systemd_user_set_property(
            unit,
            f"CPUWeight={proposed_value}",
            command_runner=command_runner,
        )
        written_value = _systemd_user_show_property(
            unit,
            "CPUWeight",
            command_runner=command_runner,
        )
    except (OSError, subprocess.CalledProcessError):
        written_value = None
    return {
        "cgroup": cgroup,
        "path": str(path),
        "control_file": control_file,
        "original_value": current_value,
        "proposed_value": proposed_value,
        "status": (
            "written"
            if written_value == proposed_value
            else "write-failed"
            if written_value is None
            else "write-mismatch"
        ),
        "method": "systemd-user-property",
        "unit": unit,
        "property": "CPUWeight",
    }


def _restore_systemd_user_background_write(
    item: dict[str, object],
    *,
    command_runner: Any | None,
) -> dict[str, object]:
    unit = _optional_str(item.get("unit")) or ""
    property_name = _optional_str(item.get("property")) or "CPUWeight"
    original_value = _optional_str(item.get("original_value")) or ""
    restored_assignment = (
        f"{property_name}="
        if original_value == "[not set]"
        else f"{property_name}={original_value}"
    )
    status = "restored"
    try:
        _systemd_user_set_property(
            unit,
            restored_assignment,
            command_runner=command_runner,
        )
        current_value = _systemd_user_show_property(
            unit,
            property_name,
            command_runner=command_runner,
        )
    except (OSError, subprocess.CalledProcessError):
        current_value = None
        status = "restore-failed"
    if current_value != original_value and status == "restored":
        status = "restore-mismatch"
    return {
        "cgroup": item.get("cgroup"),
        "path": item.get("path"),
        "control_file": item.get("control_file"),
        "restored_value": original_value,
        "current_value": current_value,
        "status": status,
        "method": "systemd-user-property",
        "unit": unit,
        "property": property_name,
    }


def _systemd_user_unit_from_cgroup(cgroup: str) -> str | None:
    for part in reversed(cgroup.split("/")):
        if part.endswith((".service", ".scope", ".slice")):
            return part
    return None


def _systemd_user_show_property(
    unit: str,
    property_name: str,
    *,
    command_runner: Any | None,
) -> str:
    output = _run_systemd_user_command(
        ["show", unit, "-p", property_name],
        command_runner=command_runner,
    )
    prefix = f"{property_name}="
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix)
    return "[not set]"


def _systemd_user_set_property(
    unit: str,
    assignment: str,
    *,
    command_runner: Any | None,
) -> None:
    _run_systemd_user_command(
        ["set-property", "--runtime", unit, assignment],
        command_runner=command_runner,
    )


def _run_systemd_user_command(
    args: list[str],
    *,
    command_runner: Any | None,
) -> str:
    command = [
        "runuser",
        "-u",
        "deck",
        "--",
        "env",
        "XDG_RUNTIME_DIR=/run/user/1000",
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
        "systemctl",
        "--user",
        *args,
    ]
    if command_runner is not None:
        return str(command_runner(command))
    return subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=True,
    ).stdout


def _is_background_shaping_write_target(cgroup: str, *, appid: str) -> bool:
    lowered = cgroup.lower()
    if f"app-steam-app{appid}".lower() in lowered:
        return False
    relative = lowered.removeprefix("0::").rstrip("/")
    if relative in {"/user.slice", "/system.slice"}:
        return False
    helper_tokens = (
        "app-steam-client",
        "steam-launcher",
        "steamwebhelper",
        "gamescope-session.service",
        "gamescope-mangoapp.service",
    )
    return any(token in lowered for token in helper_tokens)


def _should_use_systemd_user_property(cgroup: str, control_file: str) -> bool:
    if control_file != "cpu.weight":
        return False
    lowered = cgroup.lower()
    relative = lowered.removeprefix("0::")
    unit = _systemd_user_unit_from_cgroup(cgroup)
    return relative.startswith("/user.slice/") and unit is not None and unit.endswith(
        ".service"
    )


def _background_write_lowers_value(
    control_file: str,
    current_value: str,
    proposed_value: str,
) -> bool:
    if control_file == "cpu.weight":
        if current_value == "[not set]":
            current_value = "100"
        current = _float(current_value)
        proposed = _float(proposed_value)
        return current is not None and proposed is not None and current > proposed
    if control_file == "cpu.uclamp.max":
        if current_value == "max":
            return True
        current = _float(current_value)
        proposed = _float(proposed_value)
        return current is not None and proposed is not None and current > proposed
    return False


def _read_control_value(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _write_control_value(path: Path, value: str) -> None:
    path.write_text(f"{value}\n")


# Public alias so the coloring module can classify color D (background-helper-
# shapable) with the exact same allowlist the writers enforce.
def is_background_shaping_write_target(cgroup: str, *, appid: str) -> bool:
    return _is_background_shaping_write_target(cgroup, appid=appid)


# ---------------------------------------------------------------------------
# Foreground cpu.uclamp.min floor writer (NEW, daemon-facing, gated lane a).
# ---------------------------------------------------------------------------
class ForegroundUclampMinWriter:
    """Snapshot/restore/verify writer for the foreground ``cpu.uclamp.min`` floor.

    Records the original ``cpu.uclamp.min`` value on first apply, verifies the
    written floor, restores the original on :meth:`restore`, and latches
    :attr:`failed` on any write failure or mismatch so the caller fails closed.
    """

    def __init__(self, *, floor_value: str = FOREGROUND_UCLAMP_MIN_FLOOR) -> None:
        self.floor_value = floor_value
        self._active_path: Path | None = None
        self._original_value: str | None = None
        self._failed = False

    @property
    def active(self) -> bool:
        return self._active_path is not None

    @property
    def failed(self) -> bool:
        return self._failed

    def apply(self, cgroup_path: str | Path) -> dict[str, object]:
        if self._failed:
            return {"status": "disabled", "control_file": "cpu.uclamp.min"}
        control_path = Path(cgroup_path) / "cpu.uclamp.min"
        if self._active_path == control_path and self._original_value is not None:
            return {
                "status": "held",
                "path": str(control_path.parent),
                "control_file": "cpu.uclamp.min",
                "proposed_value": self.floor_value,
                "original_value": self._original_value,
            }
        # C8: switching to a different foreground cgroup while active must
        # restore the previously-floored path first, or its floor leaks.
        if self._active_path is not None and self._active_path != control_path:
            self.restore()
            if self._failed:
                return {"status": "disabled", "control_file": "cpu.uclamp.min"}
        current_value = _read_control_value(control_path)
        if current_value is None:
            self._failed = True
            return {
                "status": "write-unavailable",
                "path": str(control_path.parent),
                "control_file": "cpu.uclamp.min",
            }
        try:
            _write_control_value(control_path, self.floor_value)
            written_value = _read_control_value(control_path)
        except OSError:
            self._failed = True
            return {
                "status": "write-failed",
                "path": str(control_path.parent),
                "control_file": "cpu.uclamp.min",
                "original_value": current_value,
            }
        if written_value != self.floor_value:
            # C8: record state and attempt to restore this file before latching
            # failed, so a partial mismatched write is not left applied silently.
            self._active_path = control_path
            self._original_value = current_value
            restore_report = self.restore()
            self._failed = True
            return {
                "status": "write-mismatch",
                "path": str(control_path.parent),
                "control_file": "cpu.uclamp.min",
                "original_value": current_value,
                "proposed_value": self.floor_value,
                "restore": restore_report,
            }
        self._active_path = control_path
        self._original_value = current_value
        return {
            "status": "written",
            "path": str(control_path.parent),
            "control_file": "cpu.uclamp.min",
            "original_value": current_value,
            "proposed_value": self.floor_value,
        }

    def restore(self) -> dict[str, object]:
        if self._active_path is None or self._original_value is None:
            return {"status": "no-op", "control_file": "cpu.uclamp.min"}
        control_path = self._active_path
        original_value = self._original_value
        status = "restored"
        try:
            _write_control_value(control_path, original_value)
            current_value = _read_control_value(control_path)
        except OSError:
            current_value = None
            status = "restore-failed"
        if current_value != original_value and status == "restored":
            status = "restore-mismatch"
        report = {
            "status": status,
            "path": str(control_path.parent),
            "control_file": "cpu.uclamp.min",
            "restored_value": original_value,
            "current_value": current_value,
        }
        if status == "restored":
            self._active_path = None
            self._original_value = None
        else:
            # C9: keep the record and latch failed so the caller fails closed and
            # surfaces the unrestored floor in gated-lane telemetry.
            self._failed = True
        return report


# ---------------------------------------------------------------------------
# File-based foreground uclamp.min lane for the profiler (C16,
# target-balance-uclampmin candidate policy). Same evidence discipline as the
# background-shaping lane: apply writes an evidence file with the original
# value, restore verifies and reports; any mismatch invalidates the run.
# ---------------------------------------------------------------------------
def _foreground_cgroup_from_snapshot(
    cgroups: list[object], *, appid: str
) -> str | None:
    token = f"app-steam-app{appid}-".lower()
    for cgroup in cgroups:
        if not isinstance(cgroup, dict):
            continue
        name = _optional_str(cgroup.get("cgroup"))
        path = _optional_str(cgroup.get("path"))
        if name is None or path is None:
            continue
        if token in name.lower():
            return path
    return None


def apply_foreground_uclamp_min_writes(
    restore_affinity_json: str | Path,
    output: str | Path,
    *,
    appid: str,
    floor_value: str = FOREGROUND_UCLAMP_MIN_FLOOR,
) -> dict[str, object]:
    """Force-apply the foreground ``cpu.uclamp.min`` floor for a profiler run.

    Locates the foreground game cgroup in the restore-affinity snapshot and
    applies the floor via :class:`ForegroundUclampMinWriter` (literally the same
    guarded write the daemon's gated lane uses). The evidence file records the
    original value so a separate restore invocation can verify exact restore.
    """

    payload = json.loads(Path(restore_affinity_json).read_text())
    cgroups = payload.get("cgroups") if isinstance(payload, dict) else None
    report: dict[str, object] = {
        "mode": "foreground-uclamp-min-writes",
        "write_policy": "guarded-foreground-uclamp-min",
        "appid": appid,
        "floor_value": floor_value,
        "write": None,
        "applied": False,
        "restored": False,
        "valid": False,
    }
    fg_path = _foreground_cgroup_from_snapshot(
        cgroups if isinstance(cgroups, list) else [], appid=appid
    )
    if fg_path is None:
        report["skip_reason"] = "foreground-cgroup-not-found"
    else:
        writer = ForegroundUclampMinWriter(floor_value=floor_value)
        write = writer.apply(fg_path)
        report["write"] = write
        report["applied"] = write.get("status") == "written"
        report["valid"] = report["applied"]
    Path(output).write_text(json.dumps(_json_ready(report), indent=2, sort_keys=True) + "\n")
    return report


def restore_foreground_uclamp_min_writes(
    writes_json: str | Path,
    output: str | Path,
) -> dict[str, object]:
    """Restore the floor recorded by :func:`apply_foreground_uclamp_min_writes`.

    Rebuilds the writer state from the persisted evidence so restore/verify run
    through the exact same code path the in-process writer uses. ``valid`` is
    true only when the floor was applied AND restored exactly.
    """

    payload = json.loads(Path(writes_json).read_text())
    report = dict(payload) if isinstance(payload, dict) else {}
    write = report.get("write") if isinstance(report.get("write"), dict) else None
    restored = False
    restore_report: dict[str, object] | None = None
    if report.get("applied") is True and write is not None:
        path = _optional_str(write.get("path"))
        original_value = _optional_str(write.get("original_value"))
        floor_value = _optional_str(report.get("floor_value")) or (
            FOREGROUND_UCLAMP_MIN_FLOOR
        )
        if path is not None and original_value is not None:
            writer = ForegroundUclampMinWriter(floor_value=floor_value)
            writer._active_path = Path(path) / "cpu.uclamp.min"
            writer._original_value = original_value
            restore_report = writer.restore()
            restored = restore_report.get("status") == "restored"
    elif report.get("applied") is not True:
        # Nothing was applied (skip); restore is a no-op and stays honest.
        restored = "skip_reason" in report
    report["restore"] = restore_report
    report["restored"] = restored
    report["valid"] = bool(report.get("applied")) and restored
    Path(output).write_text(json.dumps(_json_ready(report), indent=2, sort_keys=True) + "\n")
    return report
