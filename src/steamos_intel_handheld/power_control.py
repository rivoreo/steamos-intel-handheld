#!/usr/bin/env python3
"""SteamOS Manager remote TDP provider for Intel RAPL devices."""

from __future__ import annotations

import argparse
import asyncio
import os
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

BUS_NAME = "org.rivoreo.SteamOSManager.PowerControl"
OBJ_PATH = "/org/rivoreo/SteamOSManager/PowerControl"
STEAMOS_MANAGER_OBJ_PATH = "/com/steampowered/SteamOSManager1"
IFACE_REMOTE = "com.steampowered.SteamOSManager1.RemoteInterface1"
IFACE_TDP = "com.steampowered.SteamOSManager1.TdpLimit1"

MICROWATTS_PER_WATT = 1_000_000
DEFAULT_MIN_W = 8
DEFAULT_MAX_W = 30
DEFAULT_SHORT_LIMIT_MAX_W = 37
HANDHELD_PL2_DELTA_W = 2
HANDHELD_PL2_MAX_W = 32
DEFAULT_STATE_FILE = "/var/lib/steamos-intel-handheld/tdp_w"
RAPL_DOMAIN_NAMES = ("intel-rapl:0", "intel-rapl-mmio:0")
MANGOHUD_RAPL_SENSOR_NAMES = ("package-0", "uncore")
MSI_CLAW_8_AI_PLUS_DMI = {
    "sys_vendor": "Micro-Star International Co., Ltd.",
    "product_name": "Claw 8 AI+ A2VM",
    "board_name": "MS-1T52",
}
MSI_CLAW_8_AI_PLUS_EC_FIRMWARE_PREFIX = "1T52EMS1.109"
MSI_CLAW_EC_PL1_OFFSET = 0x50
MSI_CLAW_EC_PL2_OFFSET = 0x51
MSI_CLAW_EC_PL1_MAX_W = 30
MSI_CLAW_EC_SHIFT_MODE_OFFSET = 0xD2
MSI_CLAW_EC_SHIFT_MODE_COMFORT = 0xC1
MSI_CLAW_EC_SHIFT_MODE_TURBO = 0xC4
MSI_CLAW_EC_SHIFT_MODE_TURBO_THRESHOLD_W = 17
MSI_CLAW_EC_FIRMWARE_OFFSET = 0xA0
MSI_CLAW_EC_FIRMWARE_LENGTH = 32


class TdpRangeError(ValueError):
    """Raised when a requested TDP is outside the configured range."""


class EcSafetyError(RuntimeError):
    """Raised when EC writes are unsafe or cannot be verified."""


@dataclass(frozen=True)
class TdpLimits:
    pl1_uw: int
    pl2_uw: int


@dataclass(frozen=True)
class TdpPolicy:
    pl1_w: int
    pl2_w: int
    pl1_tau_us: int | None
    pl2_tau_us: int | None
    requested_mode: "TdpPolicyMode"
    resolved_mode: "TdpPolicyMode"
    power_source: "PowerSource"


class PowerSource(str, Enum):
    AC = "ac"
    BATTERY = "battery"
    UNKNOWN = "unknown"


class TdpPolicyMode(str, Enum):
    AUTO = "auto"
    BATTERY_LOW_POWER = "battery-low-power"
    BATTERY_MAXQ = "battery-maxq"
    AC_QUIET = "ac-quiet"
    AC_PERFORMANCE = "ac-performance"


class MsiClawEcShiftPolicy(str, Enum):
    TDP_THRESHOLD = "tdp-threshold"
    PROFILE = "profile"


@dataclass(frozen=True)
class RaplConstraint:
    power_limit_file: Path
    max_power_file: Path | None = None
    time_window_file: Path | None = None
    min_time_window_file: Path | None = None
    max_time_window_file: Path | None = None


@dataclass(frozen=True)
class WattLimits:
    pl1_w: int
    pl2_w: int


def compute_tdp_limits(
    watts: int,
    short_limit_max_w: int = DEFAULT_SHORT_LIMIT_MAX_W,
    pl2_w: int | None = None,
    min_w: int = DEFAULT_MIN_W,
    max_w: int = DEFAULT_MAX_W,
) -> TdpLimits:
    """Return PL1/PL2 limits in microwatts for a requested TDP."""

    pl1_w = max(int(min_w), min(int(max_w), int(watts)))
    if pl2_w is None:
        short_term_w = min(pl1_w + HANDHELD_PL2_DELTA_W, HANDHELD_PL2_MAX_W)
    else:
        short_term_w = int(pl2_w)
    short_term_w = min(int(short_limit_max_w), max(pl1_w, short_term_w))
    return TdpLimits(
        pl1_uw=pl1_w * MICROWATTS_PER_WATT,
        pl2_uw=short_term_w * MICROWATTS_PER_WATT,
    )


def resolve_tdp_policy_mode(
    mode: TdpPolicyMode | str,
    power_source: PowerSource | str,
) -> TdpPolicyMode:
    mode = TdpPolicyMode(mode)
    power_source = PowerSource(power_source)
    if mode != TdpPolicyMode.AUTO:
        return mode
    if power_source == PowerSource.AC:
        return TdpPolicyMode.AC_PERFORMANCE
    return TdpPolicyMode.BATTERY_MAXQ


def compute_tdp_policy(
    watts: int,
    *,
    mode: TdpPolicyMode | str = TdpPolicyMode.AUTO,
    power_source: PowerSource | str = PowerSource.UNKNOWN,
    short_limit_max_w: int = DEFAULT_SHORT_LIMIT_MAX_W,
    min_w: int = DEFAULT_MIN_W,
    max_w: int = DEFAULT_MAX_W,
) -> TdpPolicy:
    """Return the profile-aware PL1/PL2/Tau policy for a requested SteamOS TDP."""

    requested_mode = TdpPolicyMode(mode)
    source = PowerSource(power_source)
    pl1_w = max(int(min_w), min(int(max_w), int(watts)))
    resolved_mode = resolve_tdp_policy_mode(requested_mode, source)
    computed_pl2_w = _compute_policy_pl2_w(pl1_w, resolved_mode)
    pl2_w = _clamp_policy_pl2_w(pl1_w, computed_pl2_w, int(short_limit_max_w))
    return TdpPolicy(
        pl1_w=pl1_w,
        pl2_w=pl2_w,
        pl1_tau_us=None,
        pl2_tau_us=_compute_policy_pl2_tau_us(pl1_w, resolved_mode),
        requested_mode=requested_mode,
        resolved_mode=resolved_mode,
        power_source=source,
    )


def compute_tdp_limits_from_policy(policy: TdpPolicy) -> TdpLimits:
    return TdpLimits(
        pl1_uw=policy.pl1_w * MICROWATTS_PER_WATT,
        pl2_uw=policy.pl2_w * MICROWATTS_PER_WATT,
    )


def _compute_policy_pl2_w(pl1_w: int, mode: TdpPolicyMode) -> int:
    if mode == TdpPolicyMode.BATTERY_LOW_POWER:
        return _battery_low_power_pl2_w(pl1_w)
    if mode == TdpPolicyMode.BATTERY_MAXQ:
        return _battery_maxq_pl2_w(pl1_w)
    if mode == TdpPolicyMode.AC_QUIET:
        return _ac_quiet_pl2_w(pl1_w)
    if mode == TdpPolicyMode.AC_PERFORMANCE:
        return _ac_performance_pl2_w(pl1_w)
    raise ValueError(f"unsupported TDP policy mode: {mode}")


def _ceil_percent(watts: int, percent: int) -> int:
    return (int(watts) * int(percent) + 99) // 100


def _clamp_policy_pl2_w(pl1_w: int, computed_pl2_w: int, short_limit_max_w: int) -> int:
    # Prefer short-term headroom over PL1 when possible; the hardware PL2 ceiling wins.
    preferred_pl2_w = max(int(pl1_w) + 1, int(computed_pl2_w))
    return min(int(short_limit_max_w), preferred_pl2_w)


def _battery_low_power_pl2_w(pl1_w: int) -> int:
    if pl1_w <= 8:
        return pl1_w + 2
    if pl1_w <= 12:
        return min(15, pl1_w + 3)
    if pl1_w <= 18:
        return min(24, pl1_w + 6)
    if pl1_w <= 25:
        return min(28, max(25, pl1_w + 3))
    return min(33, pl1_w + 3)


def _battery_maxq_pl2_w(pl1_w: int) -> int:
    if pl1_w <= 12:
        return min(15, max(pl1_w + 1, _ceil_percent(pl1_w, 125)))
    if pl1_w <= 18:
        return min(25, max(pl1_w + 1, _ceil_percent(pl1_w, 145)))
    if pl1_w <= 25:
        return min(30, max(25, pl1_w + 5))
    return min(35, pl1_w + 5)


def _ac_quiet_pl2_w(pl1_w: int) -> int:
    if pl1_w <= 8:
        return 12
    if pl1_w <= 12:
        return 18
    if pl1_w <= 18:
        return 25
    if pl1_w <= 25:
        return 30
    return 35


def _ac_performance_pl2_w(pl1_w: int) -> int:
    if pl1_w >= 17:
        return 37
    if pl1_w <= 8:
        return 18
    return 25


def _compute_policy_pl2_tau_us(pl1_w: int, mode: TdpPolicyMode) -> int:
    if mode == TdpPolicyMode.BATTERY_LOW_POWER:
        if pl1_w <= 8:
            return 1_000_000
        if pl1_w <= 12:
            return 2_000_000
        return 3_000_000
    if mode == TdpPolicyMode.BATTERY_MAXQ:
        if pl1_w <= 8:
            return 2_000_000
        if pl1_w <= 12:
            return 3_000_000
        if pl1_w <= 20:
            return 5_000_000
        return 8_000_000
    if mode == TdpPolicyMode.AC_QUIET:
        if pl1_w <= 12:
            return 5_000_000
        if pl1_w <= 20:
            return 8_000_000
        return 10_000_000
    if mode == TdpPolicyMode.AC_PERFORMANCE:
        if pl1_w <= 8:
            return 8_000_000
        if pl1_w < 17:
            return 10_000_000
        return 28_000_000
    raise ValueError(f"unsupported TDP policy mode: {mode}")


def compute_tdp_watt_limits(
    watts: int,
    short_limit_max_w: int = DEFAULT_SHORT_LIMIT_MAX_W,
    pl2_w: int | None = None,
    min_w: int = DEFAULT_MIN_W,
    max_w: int = DEFAULT_MAX_W,
) -> WattLimits:
    limits = compute_tdp_limits(watts, short_limit_max_w, pl2_w, min_w, max_w)
    return WattLimits(
        pl1_w=limits.pl1_uw // MICROWATTS_PER_WATT,
        pl2_w=limits.pl2_uw // MICROWATTS_PER_WATT,
    )


def _read_positive_int(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        value = int(path.read_text().strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def compute_msi_claw_ec_tdp_watt_limits(
    watts: int,
    short_limit_max_w: int = DEFAULT_SHORT_LIMIT_MAX_W,
    pl2_w: int | None = None,
    min_w: int = DEFAULT_MIN_W,
    max_w: int = DEFAULT_MAX_W,
) -> WattLimits:
    generic_limits = compute_tdp_watt_limits(
        watts,
        short_limit_max_w,
        pl2_w,
        min_w,
        max_w,
    )
    return WattLimits(
        pl1_w=min(generic_limits.pl1_w, MSI_CLAW_EC_PL1_MAX_W),
        pl2_w=max(
            min(generic_limits.pl1_w, MSI_CLAW_EC_PL1_MAX_W),
            min(generic_limits.pl2_w, short_limit_max_w),
        ),
    )


def msi_claw_ec_shift_mode_for_tdp(watts: int) -> int:
    if int(watts) > MSI_CLAW_EC_SHIFT_MODE_TURBO_THRESHOLD_W:
        return MSI_CLAW_EC_SHIFT_MODE_TURBO
    return MSI_CLAW_EC_SHIFT_MODE_COMFORT


class MsiClaw8AiPlusEcController:
    """Guarded EC writer for MSI Claw 8 AI+ Manual PL1/PL2 bytes."""

    def __init__(
        self,
        dmi_root: str | Path = "/sys/class/dmi/id",
        debugfs_root: str | Path = "/sys/kernel/debug",
    ) -> None:
        self.dmi_root = Path(dmi_root)
        self.debugfs_root = Path(debugfs_root)

    def preflight(self) -> None:
        self._assert_supported_dmi()
        self._assert_supported_ec_firmware()

    def apply_limits(self, limits: WattLimits, shift_mode: int) -> None:
        self._assert_watt_byte(limits.pl1_w, "PL1")
        self._assert_watt_byte(limits.pl2_w, "PL2")
        self._assert_watt_byte(shift_mode, "shift mode")
        self.preflight()

        ec_io = self._ensure_ec_io()
        ec = self._read_ec(ec_io)
        if ec[MSI_CLAW_EC_PL1_OFFSET] != limits.pl1_w:
            self._write_ec_byte(ec_io, MSI_CLAW_EC_PL1_OFFSET, limits.pl1_w)
        if ec[MSI_CLAW_EC_PL2_OFFSET] != limits.pl2_w:
            self._write_ec_byte(ec_io, MSI_CLAW_EC_PL2_OFFSET, limits.pl2_w)
        if ec[MSI_CLAW_EC_SHIFT_MODE_OFFSET] != shift_mode:
            self._write_ec_byte(ec_io, MSI_CLAW_EC_SHIFT_MODE_OFFSET, shift_mode)

        ec = self._read_ec(ec_io)
        actual_pl1 = ec[MSI_CLAW_EC_PL1_OFFSET]
        actual_pl2 = ec[MSI_CLAW_EC_PL2_OFFSET]
        actual_shift_mode = ec[MSI_CLAW_EC_SHIFT_MODE_OFFSET]
        if (
            actual_pl1 != limits.pl1_w
            or actual_pl2 != limits.pl2_w
            or actual_shift_mode != shift_mode
        ):
            raise EcSafetyError(
                "MSI Claw EC read-back mismatch: "
                f"expected PL1/PL2/mode "
                f"{limits.pl1_w}/{limits.pl2_w}/0x{shift_mode:02x}, "
                f"got {actual_pl1}/{actual_pl2}/0x{actual_shift_mode:02x}"
            )

    def _assert_supported_dmi(self) -> None:
        mismatches = []
        for filename, expected in MSI_CLAW_8_AI_PLUS_DMI.items():
            actual = self._read_dmi_value(filename)
            if actual != expected:
                mismatches.append(f"{filename}={actual!r}")
        if mismatches:
            details = ", ".join(mismatches)
            raise EcSafetyError(f"unsupported MSI Claw EC target: {details}")

    def _read_dmi_value(self, filename: str) -> str:
        try:
            return (self.dmi_root / filename).read_text().strip()
        except OSError:
            return ""

    def _assert_supported_ec_firmware(self) -> None:
        firmware = self._ec_firmware_string(self._read_ec(self._ensure_ec_io()))
        if not firmware.startswith(MSI_CLAW_8_AI_PLUS_EC_FIRMWARE_PREFIX):
            raise EcSafetyError(
                "unsupported MSI Claw EC firmware: "
                f"{firmware!r}; expected prefix "
                f"{MSI_CLAW_8_AI_PLUS_EC_FIRMWARE_PREFIX!r}"
            )

    def _ensure_ec_io(self) -> Path:
        ec_io = self.debugfs_root / "ec" / "ec0" / "io"
        if not ec_io.exists():
            self._load_ec_sys_with_write_support()
        if not ec_io.exists():
            raise EcSafetyError(f"EC debugfs io file is missing: {ec_io}")
        if not self._path_has_owner_write(ec_io):
            self._load_ec_sys_with_write_support()
        if not self._path_has_owner_write(ec_io):
            raise EcSafetyError(
                f"EC debugfs io file is not writable: {ec_io}; "
                "load ec_sys with write_support=1"
            )
        return ec_io

    def _load_ec_sys_with_write_support(self) -> None:
        try:
            subprocess.run(["modprobe", "ec_sys", "write_support=1"], check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise EcSafetyError("failed to load ec_sys with write_support=1") from exc

    def _path_has_owner_write(self, path: Path) -> bool:
        try:
            return bool(path.stat().st_mode & stat.S_IWUSR)
        except OSError:
            return False

    def _read_ec(self, ec_io: Path) -> bytes:
        try:
            data = ec_io.read_bytes()
        except OSError as exc:
            raise EcSafetyError(f"failed to read EC io file: {ec_io}") from exc
        if len(data) < 256:
            raise EcSafetyError(f"short EC io dump from {ec_io}: {len(data)} bytes")
        return data

    def _write_ec_byte(self, ec_io: Path, offset: int, value: int) -> None:
        try:
            with ec_io.open("r+b") as ec_file:
                ec_file.seek(offset, os.SEEK_SET)
                ec_file.write(bytes([value]))
                ec_file.flush()
        except OSError as exc:
            raise EcSafetyError(
                f"failed to write EC offset 0x{offset:02x} in {ec_io}"
            ) from exc

    def _ec_firmware_string(self, ec: bytes) -> str:
        firmware = ec[
            MSI_CLAW_EC_FIRMWARE_OFFSET : MSI_CLAW_EC_FIRMWARE_OFFSET
            + MSI_CLAW_EC_FIRMWARE_LENGTH
        ]
        return bytes(byte for byte in firmware if byte >= 0x20).decode(
            "ascii",
            errors="ignore",
        )

    def _assert_watt_byte(self, watts: int, label: str) -> None:
        if watts <= 0 or watts > 0xFF:
            raise EcSafetyError(f"{label} value {watts}W cannot fit in one EC byte")


class TdpBackend:
    """Hardware-facing TDP backend with an injectable sysfs root for tests."""

    def __init__(
        self,
        min_w: int = DEFAULT_MIN_W,
        max_w: int = DEFAULT_MAX_W,
        state_file: str | Path = DEFAULT_STATE_FILE,
        apply_rapl: bool = True,
        apply_msi_claw_ec: bool = False,
        ec_write_debounce_ms: int = 0,
        sysfs_root: str | Path = "/sys",
        dmi_root: str | Path = "/sys/class/dmi/id",
        debugfs_root: str | Path = "/sys/kernel/debug",
        pl2_w: int | None = None,
        short_limit_max_w: int = DEFAULT_SHORT_LIMIT_MAX_W,
        tdp_policy_mode: TdpPolicyMode | str = TdpPolicyMode.AUTO,
        power_source_override: PowerSource | str | None = None,
        power_source_poll_s: float = 2.0,
        msi_claw_ec_shift_policy: MsiClawEcShiftPolicy | str = (
            MsiClawEcShiftPolicy.TDP_THRESHOLD
        ),
    ) -> None:
        self.min_w = int(min_w)
        self.max_w = int(max_w)
        self.short_limit_max_w = int(short_limit_max_w)
        self.state_file = Path(state_file)
        self.apply_rapl = bool(apply_rapl)
        self.apply_msi_claw_ec = bool(apply_msi_claw_ec)
        self.ec_write_debounce_ms = max(0, int(ec_write_debounce_ms))
        self.sysfs_root = Path(sysfs_root)
        self.ec_controller = MsiClaw8AiPlusEcController(
            dmi_root=dmi_root,
            debugfs_root=debugfs_root,
        )
        self._pending_ec_watts: int | None = None
        self._ec_write_timer: threading.Timer | None = None
        self._ec_write_lock = threading.Lock()
        self.pl2_w = int(pl2_w) if pl2_w is not None else None
        self.tdp_policy_mode = TdpPolicyMode(tdp_policy_mode)
        self.power_source_override = (
            PowerSource(power_source_override) if power_source_override is not None else None
        )
        self.power_source_poll_s = max(0.0, float(power_source_poll_s))
        self.msi_claw_ec_shift_policy = MsiClawEcShiftPolicy(msi_claw_ec_shift_policy)
        self._last_applied_power_source: PowerSource | None = None
        if self.min_w <= 0 or self.max_w < self.min_w:
            raise ValueError(f"invalid TDP range {self.min_w}-{self.max_w}W")
        if self.short_limit_max_w < self.max_w:
            raise ValueError(
                f"invalid short-term limit {self.short_limit_max_w}W; "
                f"expected >= max TDP {self.max_w}W"
            )
        if self.pl2_w is not None and self.pl2_w <= 0:
            raise ValueError(f"invalid PL2 wattage {self.pl2_w}; expected > 0")

    def current_power_source(self) -> PowerSource:
        if self.power_source_override is not None:
            return self.power_source_override

        power_supply = self.sysfs_root / "class" / "power_supply"
        if not power_supply.exists():
            return PowerSource.UNKNOWN

        saw_battery = False
        for supply in sorted(power_supply.iterdir()):
            try:
                supply_type = (supply / "type").read_text().strip().lower()
            except OSError:
                continue
            if supply_type == "battery":
                saw_battery = True
                continue
            if supply_type in {"mains", "usb", "usb-c", "usb_c", "usb_pd", "usb_pd_drp"}:
                try:
                    if (supply / "online").read_text().strip() == "1":
                        return PowerSource.AC
                except OSError:
                    continue

        return PowerSource.BATTERY if saw_battery else PowerSource.UNKNOWN

    def read_limit_w(self) -> int:
        state_limit = self._read_state_file()
        if state_limit is not None:
            return state_limit

        for domain in self.rapl_domains():
            long_term = self._constraint_by_name(domain, "long_term", fallback_index=0)
            if long_term is None:
                continue
            try:
                return self._clamp_watts(
                    int(long_term.power_limit_file.read_text().strip()) // MICROWATTS_PER_WATT
                )
            except (OSError, ValueError):
                continue

        return self.max_w

    def write_limit_w(self, watts: int) -> int:
        watts = self._normalize_requested_watts(watts)
        if self.apply_msi_claw_ec:
            self.ec_controller.preflight()
        self._write_state_file(watts)
        if self.apply_rapl:
            self.apply_limit_to_rapl(watts)
        if self.apply_msi_claw_ec:
            self.schedule_limit_to_msi_claw_ec(watts)
        return watts

    def restore_state_to_rapl(self) -> int | None:
        watts = self._read_state_file()
        if watts is None:
            return None
        self._write_state_file(watts)
        self.apply_limit_to_rapl(watts)
        return watts

    def reapply_if_power_source_changed(self, *, force: bool = False) -> int | None:
        current_source = self.current_power_source()
        if not force and current_source == self._last_applied_power_source:
            return None

        watts = self._read_state_file()
        if watts is None:
            self._last_applied_power_source = current_source
            return None

        if self.apply_rapl:
            self.apply_limit_to_rapl(watts)
        if self.apply_msi_claw_ec:
            self.schedule_limit_to_msi_claw_ec(watts)
        self._last_applied_power_source = current_source
        return watts

    def reapply_policy_if_state_matches_current_rapl(self) -> int | None:
        if not self.apply_rapl:
            return None

        watts = self._read_state_file()
        if watts is None:
            return None

        current_rapl_watts = self._read_current_long_term_rapl_watts()
        if current_rapl_watts != watts:
            return None

        self.apply_limit_to_rapl(watts)
        if self.apply_msi_claw_ec:
            self.schedule_limit_to_msi_claw_ec(watts)
        return watts

    def apply_limit_to_rapl(self, watts: int) -> None:
        watts = self._normalize_requested_watts(watts)
        policy = self._compute_current_policy(watts)
        limits = compute_tdp_limits_from_policy(policy)

        for domain in self.rapl_domains():
            long_term = self._constraint_by_name(domain, "long_term", fallback_index=0)
            if long_term is None:
                continue
            short_term = self._constraint_by_name(domain, "short_term", fallback_index=1)
            pl1_uw = limits.pl1_uw
            long_term.power_limit_file.write_text(str(pl1_uw))
            if short_term is not None:
                pl2_uw = max(pl1_uw, self._limit_for_constraint(limits.pl2_uw, short_term))
                short_term.power_limit_file.write_text(str(pl2_uw))
                if short_term.time_window_file is not None and policy.pl2_tau_us is not None:
                    try:
                        short_term.time_window_file.write_text(
                            str(
                                self._limit_time_window_for_constraint(
                                    policy.pl2_tau_us,
                                    short_term,
                                )
                            )
                        )
                    except OSError as exc:
                        print(
                            "failed to write RAPL short-term time window "
                            f"{short_term.time_window_file}={policy.pl2_tau_us}: {exc}",
                            file=sys.stderr,
                        )
        self._last_applied_power_source = policy.power_source

    def apply_limit_to_msi_claw_ec(self, watts: int) -> None:
        watts = self._normalize_requested_watts(watts)
        policy = self._compute_current_policy(watts)
        limits = WattLimits(
            pl1_w=min(policy.pl1_w, MSI_CLAW_EC_PL1_MAX_W),
            pl2_w=max(
                min(policy.pl1_w, MSI_CLAW_EC_PL1_MAX_W),
                min(policy.pl2_w, self.short_limit_max_w),
            ),
        )
        shift_mode = self._msi_claw_ec_shift_mode_for_policy(policy)
        self.ec_controller.apply_limits(limits, shift_mode)

    def schedule_limit_to_msi_claw_ec(self, watts: int) -> None:
        watts = self._normalize_requested_watts(watts)
        if self.ec_write_debounce_ms <= 0:
            self.apply_limit_to_msi_claw_ec(watts)
            return

        with self._ec_write_lock:
            self._pending_ec_watts = watts
            if self._ec_write_timer is not None:
                self._ec_write_timer.cancel()
            timer = threading.Timer(
                self.ec_write_debounce_ms / 1000,
                self._flush_pending_ec_write_from_timer,
            )
            timer.daemon = True
            self._ec_write_timer = timer
            timer.start()

    def flush_pending_ec_write(self) -> None:
        with self._ec_write_lock:
            watts = self._pending_ec_watts
            timer = self._ec_write_timer
            self._pending_ec_watts = None
            self._ec_write_timer = None
            if timer is not None:
                timer.cancel()

        if watts is not None:
            self.apply_limit_to_msi_claw_ec(watts)

    def _flush_pending_ec_write_from_timer(self) -> None:
        try:
            self.flush_pending_ec_write()
        except Exception as exc:
            print(f"failed to apply debounced MSI Claw EC TDP: {exc}", file=sys.stderr)

    def prepare_mangohud_sensors(self) -> list[Path]:
        prepared: list[Path] = []
        read_bits = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
        for domain in self.mangohud_rapl_sensor_domains():
            energy_file = domain / "energy_uj"
            if not energy_file.exists():
                continue
            try:
                self._enable_powercap_domain(domain)
                current_mode = stat.S_IMODE(energy_file.stat().st_mode)
                energy_file.chmod(current_mode | read_bits)
            except OSError:
                continue
            prepared.append(energy_file)
        return prepared

    def mangohud_rapl_sensor_domains(self) -> Iterable[Path]:
        powercap = self.sysfs_root / "class" / "powercap"
        if not powercap.exists():
            return
        for domain in sorted(powercap.glob("intel-rapl*")):
            if not domain.is_dir():
                continue
            if self._powercap_domain_name(domain) in MANGOHUD_RAPL_SENSOR_NAMES:
                yield domain

    def _enable_powercap_domain(self, domain: Path) -> None:
        enabled_file = domain / "enabled"
        if enabled_file.exists():
            enabled_file.write_text("1")

    def rapl_domains(self) -> Iterable[Path]:
        powercap = self.sysfs_root / "class" / "powercap"
        for domain_name in RAPL_DOMAIN_NAMES:
            domain = powercap / domain_name
            if domain.exists():
                yield domain

    def _read_state_file(self) -> int | None:
        try:
            watts = int(self.state_file.read_text().strip())
        except (OSError, ValueError):
            return None
        try:
            return self._normalize_requested_watts(watts)
        except TdpRangeError:
            return None

    def _write_state_file(self, watts: int) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(str(watts))

    def _read_current_long_term_rapl_watts(self) -> int | None:
        for domain in self.rapl_domains():
            long_term = self._constraint_by_name(domain, "long_term", fallback_index=0)
            if long_term is None:
                continue
            current_uw = _read_positive_int(long_term.power_limit_file)
            if current_uw is None:
                continue
            return current_uw // MICROWATTS_PER_WATT
        return None

    def _normalize_requested_watts(self, watts: int) -> int:
        watts = int(watts)
        if watts <= 0 or watts > self.short_limit_max_w:
            raise TdpRangeError(
                f"TDP {watts}W outside hardware sanity range 1-{self.short_limit_max_w}W"
            )
        return self._clamp_watts(watts)

    def _clamp_watts(self, watts: int) -> int:
        return max(self.min_w, min(self.max_w, watts))

    def _compute_current_policy(self, watts: int) -> TdpPolicy:
        policy = compute_tdp_policy(
            watts,
            mode=self.tdp_policy_mode,
            power_source=self.current_power_source(),
            short_limit_max_w=self.short_limit_max_w,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        if self.pl2_w is None:
            return policy

        override_pl2_w = min(
            self.short_limit_max_w,
            max(policy.pl1_w + 1, int(self.pl2_w)),
        )
        return TdpPolicy(
            pl1_w=policy.pl1_w,
            pl2_w=override_pl2_w,
            pl1_tau_us=policy.pl1_tau_us,
            pl2_tau_us=policy.pl2_tau_us,
            requested_mode=policy.requested_mode,
            resolved_mode=policy.resolved_mode,
            power_source=policy.power_source,
        )

    def _msi_claw_ec_shift_mode_for_policy(self, policy: TdpPolicy) -> int:
        if self.msi_claw_ec_shift_policy == MsiClawEcShiftPolicy.TDP_THRESHOLD:
            return msi_claw_ec_shift_mode_for_tdp(policy.pl1_w)
        if policy.resolved_mode == TdpPolicyMode.BATTERY_LOW_POWER:
            return MSI_CLAW_EC_SHIFT_MODE_COMFORT
        if policy.resolved_mode == TdpPolicyMode.AC_QUIET and policy.pl1_w <= 17:
            return MSI_CLAW_EC_SHIFT_MODE_COMFORT
        if policy.pl1_w >= 17:
            return MSI_CLAW_EC_SHIFT_MODE_TURBO
        return MSI_CLAW_EC_SHIFT_MODE_COMFORT

    def _constraint_by_name(
        self,
        domain: Path,
        constraint_name: str,
        fallback_index: int,
    ) -> RaplConstraint | None:
        found_named_constraints = False
        for name_file in sorted(domain.glob("constraint_*_name")):
            try:
                name = name_file.read_text().strip()
            except OSError:
                continue
            found_named_constraints = True
            if name != constraint_name:
                continue
            prefix = name_file.name.removesuffix("_name")
            power_limit_file = domain / f"{prefix}_power_limit_uw"
            if not power_limit_file.exists():
                return None
            max_power_file = domain / f"{prefix}_max_power_uw"
            time_window_file = domain / f"{prefix}_time_window_us"
            min_time_window_file = domain / f"{prefix}_min_time_window_us"
            max_time_window_file = domain / f"{prefix}_max_time_window_us"
            return RaplConstraint(
                power_limit_file=power_limit_file,
                max_power_file=max_power_file if max_power_file.exists() else None,
                time_window_file=time_window_file if time_window_file.exists() else None,
                min_time_window_file=(
                    min_time_window_file if min_time_window_file.exists() else None
                ),
                max_time_window_file=(
                    max_time_window_file if max_time_window_file.exists() else None
                ),
            )

        if found_named_constraints:
            return None

        power_limit_file = domain / f"constraint_{fallback_index}_power_limit_uw"
        if not power_limit_file.exists():
            return None
        max_power_file = domain / f"constraint_{fallback_index}_max_power_uw"
        time_window_file = domain / f"constraint_{fallback_index}_time_window_us"
        min_time_window_file = domain / f"constraint_{fallback_index}_min_time_window_us"
        max_time_window_file = domain / f"constraint_{fallback_index}_max_time_window_us"
        return RaplConstraint(
            power_limit_file=power_limit_file,
            max_power_file=max_power_file if max_power_file.exists() else None,
            time_window_file=time_window_file if time_window_file.exists() else None,
            min_time_window_file=min_time_window_file if min_time_window_file.exists() else None,
            max_time_window_file=max_time_window_file if max_time_window_file.exists() else None,
        )

    def _powercap_domain_name(self, domain: Path) -> str | None:
        try:
            return (domain / "name").read_text().strip()
        except OSError:
            return None

    def _limit_for_constraint(self, limit_uw: int, constraint: RaplConstraint) -> int:
        max_power_uw = self._constraint_max_power_uw(constraint)
        if max_power_uw is None:
            return limit_uw
        return min(limit_uw, max_power_uw)

    def _limit_time_window_for_constraint(
        self,
        requested_us: int,
        constraint: RaplConstraint,
    ) -> int:
        min_us = _read_positive_int(constraint.min_time_window_file)
        max_us = _read_positive_int(constraint.max_time_window_file)
        limited_us = int(requested_us)
        if min_us is not None:
            limited_us = max(min_us, limited_us)
        if max_us is not None:
            limited_us = min(max_us, limited_us)
        return limited_us

    def _constraint_max_power_uw(self, constraint: RaplConstraint) -> int | None:
        return _read_positive_int(constraint.max_power_file)


def wait_for_user_steamos_manager(user: str, timeout_s: int, interval_s: float) -> None:
    uid = _uid_for_user(user)
    runtime_dir = Path("/run/user") / str(uid)
    bus_address = f"unix:path={runtime_dir}/bus"
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        if (runtime_dir / "bus").is_socket() and _user_service_active(
            user=user,
            runtime_dir=runtime_dir,
            bus_address=bus_address,
            service="steamos-manager",
        ):
            return
        time.sleep(interval_s)

    raise TimeoutError(f"timed out waiting for {user} user steamos-manager")


def _uid_for_user(user: str) -> int:
    result = subprocess.run(
        ["id", "-u", user],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def _user_service_active(user: str, runtime_dir: Path, bus_address: str, service: str) -> bool:
    result = subprocess.run(
        [
            "runuser",
            "-u",
            user,
            "--",
            "env",
            f"XDG_RUNTIME_DIR={runtime_dir}",
            f"DBUS_SESSION_BUS_ADDRESS={bus_address}",
            "systemctl",
            "--user",
            "is-active",
            "--quiet",
            service,
        ],
        check=False,
    )
    return result.returncode == 0


async def poll_power_source_changes(backend: TdpBackend) -> None:
    if backend.power_source_poll_s <= 0:
        return
    while True:
        await asyncio.sleep(backend.power_source_poll_s)
        try:
            reapplied = backend.reapply_if_power_source_changed()
        except Exception as exc:
            print(
                f"failed to reapply TDP policy after power-source check: {exc}",
                file=sys.stderr,
            )
            continue
        if reapplied is not None:
            print(
                f"reapplied TDP policy for {reapplied}W after power-source change",
                file=sys.stderr,
            )


async def serve(args: argparse.Namespace) -> None:
    from dbus_next.aio import MessageBus
    from dbus_next.constants import BusType, PropertyAccess
    from dbus_next.service import ServiceInterface, dbus_property

    backend = build_backend(args)
    if args.restore_on_start and args.apply_rapl:
        restored = backend.restore_state_to_rapl()
        if restored is not None:
            print(f"restored TDP limit to {restored}W", flush=True, file=sys.stderr)
    elif args.apply_rapl:
        reapplied = backend.reapply_policy_if_state_matches_current_rapl()
        if reapplied is not None:
            print(
                f"reapplied TDP policy envelope for current {reapplied}W",
                flush=True,
                file=sys.stderr,
            )

    class RemoteInterface(ServiceInterface):
        def __init__(self) -> None:
            super().__init__(IFACE_REMOTE)

        @dbus_property(access=PropertyAccess.READ)
        def RemoteInterfaces(self) -> "as":
            return [IFACE_TDP]

    class TdpLimitInterface(ServiceInterface):
        def __init__(self, tdp_backend: TdpBackend) -> None:
            super().__init__(IFACE_TDP)
            self.backend = tdp_backend

        @dbus_property(access=PropertyAccess.READWRITE)
        def TdpLimit(self) -> "u":
            watts = self.backend.read_limit_w()
            print(f"get TdpLimit -> {watts}", flush=True, file=sys.stderr)
            return watts

        @TdpLimit.setter
        def TdpLimit(self, value: "u") -> None:
            applied_watts = self.backend.write_limit_w(int(value))
            print(
                f"set TdpLimit <- {int(value)}; applied {applied_watts}",
                flush=True,
                file=sys.stderr,
            )
            self.emit_properties_changed({"TdpLimit": applied_watts})

        @dbus_property(access=PropertyAccess.READ)
        def TdpLimitMin(self) -> "u":
            return self.backend.min_w

        @dbus_property(access=PropertyAccess.READ)
        def TdpLimitMax(self) -> "u":
            return self.backend.max_w

    bus_type = BusType.SYSTEM if args.bus == "system" else BusType.SESSION
    bus = await MessageBus(bus_type=bus_type).connect()
    for object_path in (OBJ_PATH, STEAMOS_MANAGER_OBJ_PATH):
        bus.export(object_path, RemoteInterface())
        bus.export(object_path, TdpLimitInterface(backend))
    await bus.request_name(BUS_NAME)
    asyncio.create_task(poll_power_source_changes(backend))
    await asyncio.Future()


def build_backend(args: argparse.Namespace) -> TdpBackend:
    return TdpBackend(
        min_w=args.min_w,
        max_w=args.max_w,
        state_file=args.state_file,
        apply_rapl=args.apply_rapl,
        apply_msi_claw_ec=args.apply_msi_claw_ec,
        ec_write_debounce_ms=args.ec_write_debounce_ms,
        sysfs_root=args.sysfs_root,
        dmi_root=args.dmi_root,
        debugfs_root=args.debugfs_root,
        pl2_w=args.pl2_w,
        short_limit_max_w=args.short_limit_max_w,
        tdp_policy_mode=args.tdp_policy,
        power_source_override=args.power_source_override,
        power_source_poll_s=args.power_source_poll_s,
        msi_claw_ec_shift_policy=args.msi_claw_ec_shift_policy,
    )


def prepare_mangohud_sensors_from_args(args: argparse.Namespace) -> list[Path]:
    prepared = build_backend(args).prepare_mangohud_sensors()
    if prepared:
        paths = ", ".join(str(path) for path in prepared)
        print(f"prepared MangoHud sensor access for {paths}", flush=True, file=sys.stderr)
    else:
        print("no MangoHud RAPL energy sensors prepared", flush=True, file=sys.stderr)
    return prepared


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["serve", "wait-and-serve"],
        nargs="?",
        default="serve",
        help="serve immediately or wait for the deck user's steamos-manager first",
    )
    parser.add_argument("--bus", choices=["system", "session"], default="system")
    parser.add_argument("--min-w", type=int, default=DEFAULT_MIN_W)
    parser.add_argument("--max-w", type=int, default=DEFAULT_MAX_W)
    parser.add_argument("--short-limit-max-w", type=int, default=DEFAULT_SHORT_LIMIT_MAX_W)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--sysfs-root", default="/sys")
    parser.add_argument("--dmi-root", default="/sys/class/dmi/id")
    parser.add_argument("--debugfs-root", default="/sys/kernel/debug")
    parser.add_argument("--pl2-w", type=int)
    parser.add_argument(
        "--tdp-policy",
        choices=[mode.value for mode in TdpPolicyMode],
        default=TdpPolicyMode.AUTO.value,
    )
    parser.add_argument(
        "--power-source-override",
        choices=[source.value for source in PowerSource],
    )
    parser.add_argument("--power-source-poll-s", type=float, default=2.0)
    parser.add_argument("--apply-rapl", action="store_true")
    parser.add_argument("--apply-msi-claw-ec", action="store_true")
    parser.add_argument(
        "--msi-claw-ec-shift-policy",
        choices=[policy.value for policy in MsiClawEcShiftPolicy],
        default=MsiClawEcShiftPolicy.TDP_THRESHOLD.value,
    )
    parser.add_argument("--ec-write-debounce-ms", type=int, default=0)
    parser.add_argument("--prepare-mangohud-sensors", action="store_true")
    parser.add_argument("--restore-on-start", action="store_true")
    parser.add_argument("--user", default="deck")
    parser.add_argument("--wait-timeout-s", type=int, default=600)
    parser.add_argument("--wait-interval-s", type=float, default=2.0)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.prepare_mangohud_sensors:
        prepare_mangohud_sensors_from_args(args)
    if args.command == "wait-and-serve":
        wait_for_user_steamos_manager(args.user, args.wait_timeout_s, args.wait_interval_s)
    asyncio.run(serve(args))


if __name__ == "__main__":
    main()
